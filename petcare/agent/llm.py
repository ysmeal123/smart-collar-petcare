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

#: 요청 제한 시간. 짧게 잡는다 — 여기서 막히면 화면이 멈춘다.
#: 초과하면 규칙 사전으로 떨어진다.
TIMEOUT_S = 6

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

DEFAULT_MODEL = {
    "gemini": "gemini-2.0-flash",
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


def _post(url: str, payload: dict, headers: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_gemini(prompt: str, key: str) -> str:
    url = GEMINI_URL.format(model=_model()) + f"?key={key}"
    data = _post(
        url,
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0,
            },
        },
        {"Content-Type": "application/json"},
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
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError) as e:
        print(f"[pebble] LLM 추출 실패 ({type(e).__name__}) — 규칙 사전으로 대체합니다")
        return None

    parsed = _parse(raw)
    if parsed is None:
        print("[pebble] LLM 응답을 JSON 으로 읽지 못했습니다 — 규칙 사전으로 대체합니다")
        return None

    return sanitize(parsed)
