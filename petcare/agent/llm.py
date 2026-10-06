"""
LLM 추출기.

규칙 사전은 사전에 없는 표현을 못 알아듣는다. "털이 빠져요"는 잡는데
"털이 많이 빠져요"는 놓치는 식이다. 표현을 늘리는 건 끝이 없다.

**여기가 LLM 을 놓기에 안전한 자리다.**
이 경로가 내놓는 것은 정해진 key 몇 개뿐이고, 그 key 들이 할 수 있는 일은
`interpret()` 에서 **보류·차단·진료 권고**가 전부다. 용량을 올리는 경로가
없다. 그래서 추출이 틀려도 최악의 결과가 "영양제를 안 준다" 이지
과다 급여가 아니다.

    자유 텍스트 → [LLM] → 정해진 key/value → interpret() → 보류·차단
                           ^^^^^^^^^^^^^^^
                           여기서 자연어가 끊긴다

**모르는 key 는 전부 버린다.** LLM 이 무엇을 내놓든 아래 ALLOWED 에 없으면
통과하지 못한다. 그래야 프롬프트 인젝션이든 환각이든 시스템 바깥으로 못 나간다.

키가 없거나, 네트워크가 죽거나, 응답이 이상하면 **규칙 사전으로 떨어진다.**
발표 중 외부 API 하나 때문에 화면이 멈추면 안 된다.

    PEBBLE_LLM_PROVIDER   gemini | anthropic   (없으면 LLM 끔)
    PEBBLE_LLM_KEY        API 키
    PEBBLE_LLM_MODEL      모델 이름 (선택)
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

#: 허용되는 key 와 값. 여기 없는 것은 무엇이든 버린다.
#:
#: QUESTIONS 의 key 와 일치해야 한다. 어긋나면 interpret() 가 모르는 값이
#: 생기고, 저장은 되는데 아무 일도 안 일어난다.
ALLOWED: dict[str, tuple[str, ...]] = {
    "shampoo_changed": ("yes", "no"),
    "food_changed": ("yes", "no"),
    "env_exposure": ("yes", "no"),
    "skin_visible": ("보여요", "잘 모르겠어요", "없어요"),
    "ear_smell": ("yes", "no"),
    "ear_discharge": ("yes", "no"),
    "recent_bath": ("yes", "no"),
    "limping": ("yes", "no"),
    "stairs_avoid": ("yes", "no"),
    "weather_cold": ("yes", "no"),
    "env_changed": ("yes", "no"),
    "noise": ("yes", "no"),
    "treats": ("yes", "no"),
    "eating_less": ("yes", "no"),
    "vomit": ("yes", "no"),
}

#: 각 key 가 무엇을 뜻하는지. 프롬프트에 그대로 들어간다.
MEANING: dict[str, str] = {
    "shampoo_changed": "샴푸·목욕제품을 최근에 바꿨다",
    "food_changed": "사료나 간식을 최근에 바꿨다",
    "env_exposure": "풀밭·흙·모래 등 야외에서 놀았다",
    "skin_visible": "피부에 눈으로 보이는 이상(붉음·발진·탈모·상처)이 있다",
    "ear_smell": "귀에서 평소와 다른 냄새가 난다",
    "ear_discharge": "귀에 분비물·귀지가 있다",
    "recent_bath": "최근에 목욕·수영·미용을 했다",
    "limping": "다리를 절거나 아파한다",
    "stairs_avoid": "계단·소파 등 오르는 것을 피한다",
    "weather_cold": "날씨나 보호자 사정으로 산책을 줄였다",
    "env_changed": "이사·가구 이동·새 식구 등 집 환경이 바뀌었다",
    "noise": "밤에 공사·손님·천둥 등 시끄러운 일이 있었다",
    "treats": "간식을 평소보다 많이 줬다",
    "eating_less": "밥을 남기거나 잘 안 먹는다",
    "vomit": "구토나 설사가 있었다",
}

#: 요청 제한 시간.
#:
#: 실측 3~5초다(gemini-3.5-flash, 한국어 프롬프트 + JSON 출력).
#: 6초로 잡았다가 첫 호출이 5.4초에 걸려 타임아웃이 났다. 여유를 둔다.
#:
#: 너무 길게 잡아도 안 된다. 여기서 막히는 동안 보호자는 "계획을 다시
#: 세우고 있어요" 를 보고 있다. 넘기면 규칙 사전으로 떨어지는 편이,
#: 화면이 멈춰 있는 것보다 낫다.
TIMEOUT_S = 15

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

#: 기본 모델.
#:
#: **모델 이름은 생각보다 자주 죽는다.** gemini-2.0-flash 를 기본값으로
#: 뒀다가 종료된 것을 뒤늦게 알았다. 그 상태로 키를 넣으면 호출이 실패하고
#: 조용히 규칙 사전으로 떨어져서, "키를 넣었는데 왜 그대로지" 가 된다.
#: 그래서 `python -m check_llm` 으로 한 번 실제로 찔러보고 확인한다.
#:
#: 추출은 단순한 작업이라 제일 작은 모델로 충분하다. 실측 1.0초.
#:
#: **무료 한도가 모델마다 크게 다르다.** gemini-3.5-flash 는 하루 20건이라
#: 쓸 수가 없다(실제로 벤치마크 한 번에 소진했다). lite 는 넉넉하다.
#: 정확도가 아쉬우면 PEBBLE_LLM_MODEL 로 올리되 한도를 먼저 확인하라.
DEFAULT_MODEL = {
    "gemini": "gemini-3.5-flash-lite",
    "anthropic": "claude-haiku-4-5-20251001",
}


def enabled() -> bool:
    return bool(_provider() and os.environ.get("PEBBLE_LLM_KEY", "").strip())


def _provider() -> str:
    return os.environ.get("PEBBLE_LLM_PROVIDER", "").strip().lower()


def _model() -> str:
    env = os.environ.get("PEBBLE_LLM_MODEL", "").strip()
    return env or DEFAULT_MODEL.get(_provider(), "")


def build_prompt(text: str) -> str:
    """
    프롬프트.

    **판단하지 말고 추출만 하라고 한다.** 진단이나 권고를 시키면 그 말이
    화면에 뜨고, 그때부터 이 서비스는 진단을 하는 서비스가 된다.
    LLM 이 하는 일은 문장을 정해진 칸에 넣는 것뿐이다.
    """
    lines = [f"- {k}: {v}" for k, v in MEANING.items()]
    keys = "\n".join(lines)
    return (
        "너는 반려견 보호자가 쓴 메모에서 **사실만** 뽑아내는 추출기다.\n"
        "진단하지 말고, 조언하지 말고, 설명하지 마라. JSON 만 출력한다.\n\n"
        "아래 항목 중 메모에 **명시적으로 나타난 것만** 뽑는다.\n"
        f"{keys}\n\n"
        "규칙:\n"
        "1. 확실하지 않으면 넣지 마라. 빠뜨리는 것이 잘못 넣는 것보다 낫다.\n"
        "2. 부정문에 주의하라. '토는 안 했어요' 는 vomit 이 아니다.\n"
        "3. '계단을 안 올라가요' 는 부정문이지만 stairs_avoid=yes 다.\n"
        "   증상 자체가 부정형인 경우가 있다.\n"
        "4. 값은 yes 또는 no 다. 단 skin_visible 은 "
        "'보여요' / '잘 모르겠어요' / '없어요' 중 하나다.\n"
        "5. 해당 없으면 빈 객체 {} 를 반환한다.\n"
        "6. 위 목록에 없는 key 는 절대 만들지 마라.\n\n"
        f"메모:\n{text}\n\n"
        'JSON:'
    )


def sanitize(raw: object) -> dict[str, str]:
    """
    **모르는 것은 전부 버린다.**

    LLM 이 무엇을 내놓든 ALLOWED 를 통과해야 시스템 안으로 들어온다.
    환각이든 프롬프트 인젝션이든 여기서 멈춘다.
    """
    if not isinstance(raw, dict):
        return {}

    out: dict[str, str] = {}
    for key, value in raw.items():
        if key not in ALLOWED or not isinstance(value, str):
            continue
        v = value.strip()
        if v in ALLOWED[key]:
            out[key] = v
        elif v.lower() in ("true", "y", "네", "예"):
            out[key] = "yes"
    return out


#: 다시 걸어볼 만한 상태 코드. 서버가 "지금은 안 되니 나중에" 라고 한 것이다.
#:
#: 503 은 모델 과부하다. 실제로 겪었고, 바로 다시 걸면 대개 된다.
#: 429(할당량)는 넣지 않는다 - 재시도해도 같은 답이 오고 한도만 더 깎인다.
RETRY_CODES = frozenset({500, 502, 503, 504})

#: 재시도 횟수와 간격. 한 번이면 충분하다.
#: 여러 번 걸면 보호자가 기다리는 시간만 늘어나고, 어차피 규칙 사전이 받는다.
RETRIES = 1
RETRY_WAIT_S = 1.0


def _post(url: str, payload: dict, headers: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")

    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_CODES or attempt == RETRIES:
                raise
            time.sleep(RETRY_WAIT_S)

    raise RuntimeError("unreachable")


def _call_gemini(prompt: str, key: str) -> str:
    # 키를 **헤더**로 보낸다. ?key= 쿼리로도 되지만 그러면 URL 에 키가 박히고,
    # URL 은 예외 메시지·스택트레이스·프록시 로그 어디로든 샌다.
    # 헤더는 그렇게 새지 않는다.
    data = _post(
        GEMINI_URL.format(model=_model()),
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
            },
        },
        {"Content-Type": "application/json", "x-goog-api-key": key},
    )
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_anthropic(prompt: str, key: str) -> str:
    data = _post(
        ANTHROPIC_URL,
        {
            "model": _model(),
            "max_tokens": 400,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
        },
        {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    return data["content"][0]["text"]


def probe() -> tuple[bool, str]:
    """
    실제로 한 번 호출해서 설정이 맞는지 본다.

    **조용한 폴백이 좋기만 한 건 아니다.** 서비스가 안 멈추는 건 맞지만,
    설정이 틀렸을 때도 똑같이 조용해서 알아챌 방법이 없다.
    그래서 "지금 확인한다"는 경로를 따로 둔다.

    HTTP 상태 코드로 원인을 가른다. 코드 자체는 비밀이 아니다 -
    **응답 본문과 URL 은 여전히 찍지 않는다.**

    반환: (성공 여부, 사람이 읽을 설명)
    """
    if not _provider():
        return False, "PEBBLE_LLM_PROVIDER 가 비어 있습니다 (gemini 또는 anthropic)"
    if not os.environ.get("PEBBLE_LLM_KEY", "").strip():
        return False, "PEBBLE_LLM_KEY 가 비어 있습니다"

    caller = {"gemini": _call_gemini, "anthropic": _call_anthropic}.get(_provider())
    if caller is None:
        return False, f"모르는 공급자입니다: {_provider()} (gemini 또는 anthropic)"

    key = os.environ["PEBBLE_LLM_KEY"].strip()
    try:
        raw = caller("토했어요", key)
    except urllib.error.HTTPError as e:
        # Gemini 는 키가 잘못돼도 401 이 아니라 400 을 준다.
        # 둘 다 짚어줘야 엉뚱한 곳을 고치지 않는다.
        hint = {
            400: "키가 잘못됐거나 모델 이름이 틀렸습니다",
            401: "키가 잘못됐습니다",
            403: "키가 거부됐습니다. 권한이나 API 활성화를 확인하세요",
            404: f"모델을 찾을 수 없습니다: {_model()}",
            429: "할당량을 넘었습니다. 잠시 뒤 다시 시도하세요",
            500: "서버 오류입니다. 잠시 뒤 다시 시도하세요",
            503: "모델이 혼잡합니다. 잠시 뒤 다시 시도하세요 (설정 문제 아님)",
        }.get(e.code, "호출이 거부됐습니다")
        return False, f"HTTP {e.code} — {hint}"
    except urllib.error.URLError:
        return False, "네트워크에 연결하지 못했습니다"
    except Exception as e:
        return False, f"호출 실패 ({type(e).__name__})"

    if _parse(raw) is None:
        return False, "응답을 JSON 으로 읽지 못했습니다"
    return True, f"{_provider()} / {_model()} 정상"


def _parse(text: str) -> object:
    """
    JSON 을 꺼낸다.

    responseMimeType 를 줘도 모델이 코드펜스를 붙이는 경우가 있다.
    첫 '{' 부터 마지막 '}' 까지만 잘라서 시도한다.
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(text[i: j + 1])
    except json.JSONDecodeError:
        return None


def extract_llm(text: str) -> dict[str, str] | None:
    """
    LLM 으로 추출한다.

    **실패하면 None 을 돌려준다.** 호출하는 쪽이 규칙 사전으로 떨어진다.
    키가 없든, 네트워크가 죽든, 응답이 이상하든 화면은 계속 돌아야 한다.
    """
    if not enabled():
        return None

    key = os.environ["PEBBLE_LLM_KEY"].strip()
    provider = _provider()
    caller = {"gemini": _call_gemini, "anthropic": _call_anthropic}.get(provider)
    if caller is None:
        return None

    try:
        raw = caller(build_prompt(text), key)
    except Exception as e:
        # **예외 메시지를 찍지 않는다.** 타입 이름만 남긴다.
        # HTTP 라이브러리의 예외는 요청 URL·헤더를 문자열에 담는 경우가 있고,
        # 그게 로그로 나가면 키가 평문으로 남는다.
        #
        # Exception 을 통째로 잡는 것도 의도다. 여기서 못 잡은 예외는
        # FastAPI 가 스택트레이스로 찍는데, 거기에 요청 정보가 섞여 나올 수 있다.
        # 추출이 실패해도 서비스는 규칙 사전으로 계속 돌아야 한다.
        print(f"[pebble] LLM 추출 실패 ({type(e).__name__}) — 규칙 사전으로 대체합니다")
        return None

    parsed = _parse(raw)
    if parsed is None:
        print("[pebble] LLM 응답을 JSON 으로 읽지 못했습니다 — 규칙 사전으로 대체합니다")
        return None

    return sanitize(parsed)
