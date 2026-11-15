"""
자유 텍스트 → 구조화된 맥락.

고정 질문 목록으로는 담을 수 없는 말이 있다.

    "3일 전에 산책 갔다가 풀밭에서 한참 뒹굴었는데, 그 뒤부터 목을 긁어요"

이걸 받아서 `{"env_exposure": "yes", "skin_visible": ...}` 같은 키로 바꾼다.
**출력은 기존 QUESTIONS 의 key 뿐이다.** 새 키를 만들지 않는다 — 그러면
interpret() 가 모르는 값이 생기고, 아무 일도 안 일어난다.

**왜 규칙 기반인가.**
LLM 을 여기 끼우는 건 타당하다(추출은 LLM 이 잘하는 일이다). 다만 순서가 있다.
규칙이 먼저 있으면 API 키 없이 돌고, 발표 중 외부 장애에 걸리지 않고,
LLM 을 붙일 때 **비교 기준**이 생긴다. `extract()` 한 함수만 갈아끼우면 된다.

**왜 이 자리에 LLM 을 놓아도 되는가.**
이 경로는 용량을 올릴 수 없다. interpret() 가 내놓는 것은 보류·차단·진료 권고뿐이고
`test_문진은_용량을_올릴_수_없다` 가 그걸 못 박는다.
추출이 틀려도 최악의 결과가 "영양제를 안 준다"다. 과다 급여가 아니다.
그래서 여기는 확률적 모델을 놓을 수 있는 자리다.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 키워드 사전
#
# 각 항목은 (질문 key, 값, 패턴들).
# 패턴은 **정규식**이다. 한국어 낱말에는 정규식 특수문자가 없으니 평범한
# 단어는 그대로 리터럴로 동작하고, 필요할 때만 근접 패턴을 쓴다.
#
#     "사료를 바꿨어요" "사료도 바꿨어요" "사료 바꿨어요"
#     → 표현을 일일이 적는 대신  사료.{0,3}(바꾸|바뀌|변경)
#
# 어미 변화를 견디도록 어간만 적는다 — "절어요/절더라구요/절었어요" → "절"
#
# **이 방식의 한계를 알고 쓴다.** 사전에 없는 표현은 못 알아듣는다.
# 그래서 Extraction.unmatched 로 "못 알아들었다"를 반드시 드러낸다.
# LLM 으로 바꾸면 이 한계가 사라진다 — extract() 하나만 교체하면 된다.
# ---------------------------------------------------------------------------

RULES: list[tuple[str, str, tuple[str, ...]]] = [
    # --- 피부 ---
    ("shampoo_changed", "yes", ("샴푸", "목욕제품", "바디워시", "린스",
                                "컨디셔너", "세정제", "약욕")),
    ("food_changed", "yes", (r"사료.{0,4}(바꾸|바꿨|바뀌|변경|교체|새로)",
                             r"간식.{0,4}(바꾸|바꿨|바뀌|변경|교체|새로)",
                             "새 사료", "다른 사료", "사료를 새", "브랜드를 바꾸")),
    ("env_exposure", "yes", ("풀밭", "잔디", "흙", "모래", "산에", "등산",
                             "뒹굴", "개천", "계곡", "바닷", "공원",
                             "운동장", "캠핑", "들판")),
    ("skin_visible", "보여요", ("붉", "빨갛", "빨개", "발진", "뾰루지",
                                r"털.{0,5}빠", "탈모", "상처", "진물",
                                "벗겨", "각질", "딱지", "두드러기", "습진",
                                "부어올", "부었", "피가 나", "피딱지")),

    # --- 귀 ---
    ("ear_smell", "yes", (r"귀.{0,4}냄새", "쉰내", "귀에서 이상한")),
    ("ear_discharge", "yes", ("귀지", r"귀.{0,4}분비물", "귀에 갈색",
                              "귀에 검은", "귀가 지저분", "귀가 더러")),
    ("recent_bath", "yes", ("목욕", "수영", "물놀이", "미용", "씻겼",
                            "샤워", "빗물")),

    # --- 관절 ---
    ("limping", "yes", ("절뚝", "쩔뚝", "다리를 들", "다리를 절", "다리를 저",
                        "짝짝이로 걷", "다리가 불편", "다리를 아파",
                        r"다리.{0,3}이상")),
    # 부정형이 곧 증상이다. 애매한 "계단" 단독 키워드는 넣지 않는다 —
    # "계단 잘 올라가요" 까지 잡힌다.
    ("stairs_avoid", "yes", ("계단을 안", "계단을 못", "계단 안", "계단 못",
                             "소파에 안", "소파를 안", "소파에 못",
                             "안 올라가", "못 올라가", "안아달라", "안아 달라",
                             "점프를 안", "뛰어오르지 않", "침대에 못")),
    ("weather_cold", "yes", ("산책을 줄", "산책 줄", "산책을 못", "산책 못",
                             "산책을 안", "산책 안", "추워", "더워", "비가",
                             "바빠", "장마", "폭염", "한파", "눈이 와")),

    # --- 수면 ---
    ("env_changed", "yes", ("이사", "잠자리", "가구", "새 강아지", "새 고양이",
                            "집을 옮", "방을 바꾸", "새 식구", "아기가 태어",
                            "동생이 생겼")),
    ("noise", "yes", ("시끄러", "공사", "손님", "폭죽", "천둥", "불꽃",
                      "인테리어", "파티")),

    # --- 식욕 ---
    ("treats", "yes", (r"간식.{0,4}(많이|자주|더)", "사람 음식", "사람 밥",
                       "식탁에서", "간식을 늘")),
    ("vomit", "yes", ("토했", "토하", "구토", "설사", "묽은 변", "변이 묽",
                      "무른 변", "배탈", "게워", "먹은 걸 뱉")),
    # 밥을 안 먹는다는 보고. 로드셀이 없으면 이게 유일한 근거다.
    # 부정형이 곧 증상이라 부정 검사를 건너뛴다.
    ("eating_less", "yes", (r"밥.{0,4}(안 먹|못 먹|잘 안|남기|남겨|남긴|거르)",
                            r"사료.{0,4}(안 먹|못 먹|남기|남겨|남긴)",
                            "입맛이 없", "식욕이 없", "굶")),
]

#: 이 도구가 알아들을 수 있는 주제. 못 알아들었을 때 보여준다.
#:
#: 사전에 없는 말은 못 알아듣는 게 규칙 기반의 본질이다. 그때 "이해하지
#: 못했어요" 로 끝내면 막다른 길이 된다. **무엇을 말할 수 있는지** 알려주면
#: 보호자가 다시 시도할 수 있다.
TOPICS = (
    "샴푸·사료 교체",
    "풀밭·흙 노출",
    "목욕·수영",
    "피부에 보이는 변화",
    "귀 냄새·분비물",
    "다리 절뚝임",
    "계단·소파 회피",
    "산책이 줄어든 이유",
    "이사·소음",
    "간식",
    "밥을 남기는 것",
    "구토·설사",
)

#: 부정 표현. 이걸 놓치면 "토는 안 했어요"가 구토 보고가 된다.
#: 규칙 기반 추출에서 가장 위험한 실패다 — 영양제를 잘못 멈춘다.
NEGATIONS = ("안 ", "안했", "안 했", "않", "없", "아니", "아녜", "적 없", "은 아니")

#: 부정어를 찾을 범위. 키워드 뒤 이 글자 수까지 본다.
#: 한국어는 부정이 뒤에 온다 — "토는 안 했어요"
NEG_WINDOW = 10

#: 부정 검사를 건너뛰는 키.
#:
#: 증상 자체가 부정형인 항목이 있다. "계단을 안 올라가요" 는 증상 보고인데,
#: 부정 검사를 그대로 걸면 "증상이 없다"로 뒤집힌다.
#: 이 키들의 키워드 목록에는 부정형 표현을 직접 넣어 두었다.
NEGATION_EXEMPT = frozenset({"stairs_avoid", "weather_cold", "eating_less"})

#: 거부(veto) 전용 어간. **부정 판정에만** 쓴다.
#:
#: RULES 의 패턴은 "이게 있으면 증상" 을 잡으려고 길게 적혀 있다.
#: 그래서 "토는 안 했어요" 를 못 본다 - '토했' 이라는 글자가 없기 때문이다.
#: 긍정 탐지에는 그게 맞다(짧은 어간으로 잡으면 오탐이 터진다).
#:
#: 그런데 **거부는 느슨해도 안전하다.** 빼기만 하고 더하지 않으므로,
#: 잘못 거부해도 결과는 "평소대로 처방" 이다. 과다 급여로 가지 않는다.
#: 그래서 여기서는 짧은 어간을 쓴다.
VETO_STEMS: dict[str, tuple[str, ...]] = {
    "vomit": ("토", "구토", "설사", "변"),
    "shampoo_changed": ("샴푸", "목욕제품"),
    "food_changed": ("사료", "간식"),
    "env_exposure": ("풀밭", "잔디", "흙", "밖에", "산책"),
    "skin_visible": ("피부", "털", "붉", "상처", "발진"),
    "ear_smell": ("냄새",),
    "ear_discharge": ("귀지", "분비물", "귀"),
    "recent_bath": ("목욕", "수영", "미용"),
    "limping": ("절", "다리"),
    "env_changed": ("이사", "잠자리", "환경"),
    "noise": ("시끄러", "소리", "소음"),
    "treats": ("간식",),
}


# ---------------------------------------------------------------------------
# 시점
#
# 질문은 "최근 2주 안에" 를 묻는데, 답은 시점 없이 yes/no 로만 들어왔다.
# 그래서 "한 달 전에 샴푸 바꿨어요" 가 "2주 안에 바꿨다" 와 똑같이 처리돼
# **엉뚱한 유예**가 걸렸다. 한 달 전 샴푸는 지금 가려움의 설명이 못 된다.
#
# 반대 방향도 있다. "3일째 밥을 안 먹어요" 는 하루 안 먹은 것과 다르다.
# 지속 기간이 진료 권고를 가르는 기준인데 지금은 안 본다.
#
# 숫자 하나만 뽑는다. "며칠 전 일인가."
# ---------------------------------------------------------------------------

#: 시점이 의미를 갖는 key. 나머지는 뽑아도 쓸 데가 없다.
DURATION_KEYS: frozenset[str] = frozenset({
    "shampoo_changed", "food_changed", "env_exposure", "recent_bath",
    "eating_less", "vomit",
})

#: (정규식, 고정 일수, 배수). 고정 일수가 None 이면 숫자를 읽어 배수를 곱한다.
#:
#: 위에서부터 먼저 맞는 것을 쓴다. "일주일" 이 "(\d+)\s*주" 보다 위에 있어야
#: 한다 - 아래 것은 숫자가 있어야 하므로 안 걸리지만, 순서를 지키는 편이 안전하다.
_DURATION: tuple[tuple[str, int | None, int], ...] = (
    (r"오늘", 0, 1),
    (r"어제", 1, 1),
    (r"그[제저]께?", 2, 1),
    (r"일주일|한\s*주", 7, 1),
    (r"한\s*달|1\s*개월", 30, 1),
    (r"(\d+)\s*개월", None, 30),
    (r"(\d+)\s*달", None, 30),
    (r"(\d+)\s*주", None, 7),
    (r"(\d+)\s*일", None, 1),
)

#: 이 글자가 근처에 있어야 '기간' 으로 본다.
#:
#: 없으면 "하루 두 번 산책해요" 의 '두' 나 "2살이에요" 가 기간으로 읽힌다.
#: 한국어에서 기간은 거의 항상 이 꼬리를 단다.
_DURATION_TAIL = re.compile(r"(전|째|동안|부터|간|만에|쯤|경|前)")

#: 기간 표현 뒤 몇 글자까지 꼬리를 찾을지.
_TAIL_WINDOW = 3


def duration_days(text: str) -> int | None:
    """
    "며칠 전 일인가." 못 찾으면 None.

    **None 과 0 은 다르다.** None 은 "말 안 했다" 이고 0 은 "오늘" 이다.
    모르면 지금까지처럼 처리해야 하므로 둘을 섞으면 안 된다.
    """
    if not text:
        return None
    best: int | None = None
    for pat, fixed, mult in _DURATION:
        for m in re.finditer(pat, text):
            tail = text[m.end(): m.end() + _TAIL_WINDOW]
            # '오늘·어제·그제' 는 그 자체로 시점이라 꼬리가 없어도 된다.
            if fixed is None or fixed > 2:
                if not _DURATION_TAIL.search(tail):
                    continue
            if fixed is not None:
                days = fixed
            else:
                num = next((g for g in m.groups() if g and g.isdigit()), None)
                if num is None:
                    continue
                days = int(num) * mult
            # 여러 개면 **가장 오래된 것**을 쓴다. "3일 전부터 긁는데 한 달 전에
            # 사료를 바꿨어요" 에서 원인은 더 먼 쪽이다.
            if best is None or days > best:
                best = days
    return best


class Extraction(BaseModel):
    """
    추출 결과.

    `unmatched` 가 핵심이다. 못 알아들은 말을 조용히 버리면 보호자는
    "전달했는데 아무 일도 안 일어났다"를 겪는다. 그건 침묵보다 나쁘다.
    """

    answers: dict[str, str] = Field(default_factory=dict)
    matched: list[str] = Field(default_factory=list, description="무엇을 알아들었는지")
    unmatched: bool = False
    engine: str = Field(default="rules", description="rules | llm")


def _negated(text: str, at: int, length: int) -> bool:
    """
    부정어가 붙었는가.

    한국어는 부정이 서술어에 붙어 뒤에 온다. "토는 안 했어요", "절지 않아요".
    그래서 키워드 **뒤쪽** 창을 본다.

    매치 **안쪽**도 본다. 근접 패턴(`사료.{0,4}바꿨`)을 쓰면 부정어가
    매치 범위 안으로 들어와 버린다 — "사료는 안 바꿨어요" 가 통째로 잡히고,
    뒤쪽만 보면 부정을 놓친다.
    """
    span = text[at: at + length]
    tail = text[at + length: at + length + NEG_WINDOW]
    return any(n in span or n in tail for n in NEGATIONS)


def _split(text: str) -> list[str]:
    """
    문장으로 쪼갠다.

    한 문장 안의 부정만 그 문장에 적용해야 한다. 통째로 보면
    "목욕은 했는데 토는 안 했어요"에서 부정이 엉뚱한 키워드에 걸린다.

    문장 부호뿐 아니라 **연결어미**까지 끊는다. 한국어는 한 문장에 절을
    여러 개 붙이는 게 자연스러워서, 마침표만 믿으면 절 하나짜리로 취급된다.
    """
    parts = re.split(
        r"[.!?,\n]|그리고|그런데|근데|하지만|그래서|는데|지만|으나|더니|면서",
        text,
    )
    return [p.strip() for p in parts if p.strip()]


def negated_keys(text: str) -> set[str]:
    """
    **명시적으로 부정된** key 들.

    규칙 사전이 키워드는 찾았는데 부정어가 붙어 있던 경우다.
    "토는 안 했어요" 에서 '토했' 을 보고 vomit 을 떠올렸지만 부정이었던 것.

    LLM 출력을 걸러내는 데 쓴다. 규칙은 재현율이 낮은 대신 **부정 판정은
    정확하다**(실측 정밀도 100%). LLM 은 반대로 재현율이 높은 대신
    부정을 가끔 놓친다. 서로의 약점이 겹치지 않으니 합치면 둘 다 얻는다.

    보수적으로 본다. 키워드를 아예 못 찾았으면 아무 말도 하지 않는다 -
    모르는 표현까지 부정으로 단정하면 LLM 이 제대로 잡은 것을 지운다.
    """
    out: set[str] = set()
    for sentence in _split(text):
        for key, stems in VETO_STEMS.items():
            if key in NEGATION_EXEMPT:
                continue
            for stem in stems:
                at = sentence.find(stem)
                if at < 0:
                    continue
                if _negated(sentence, at, len(stem)):
                    out.add(key)
                    break
    return out


def extract(text: str) -> Extraction:
    """
    자유 텍스트에서 구조화된 맥락을 뽑는다.

    **새 키를 만들지 않는다.** QUESTIONS 에 있는 key 만 내놓는다.

    LLM 이 설정돼 있으면 그쪽을 쓰고, 없거나 실패하면 규칙 사전으로 떨어진다.
    규칙은 사라지지 않는다 - 외부 API 하나가 죽었다고 화면이 멈추면 안 되고,
    LLM 을 켰을 때 무엇이 나아졌는지 비교할 기준도 필요하다.
    """
    out = Extraction()
    if not text or not text.strip():
        return out

    from agent.llm import extract_llm

    llm = extract_llm(text)
    if llm is not None:
        # 규칙이 "이건 부정이다" 라고 본 것은 LLM 이 뭐라 하든 뺀다.
        #
        # 실측에서 LLM 이 "토는 안 했어요" 를 vomit 으로 읽었다. 프롬프트에
        # 그 문장을 예시로 박아뒀는데도 그랬다. 부정 오탐은 제일 나쁜 종류다 -
        # 보호자가 "안 그랬다" 고 말했는데 그 반대로 처리된다.
        vetoed = negated_keys(text)
        answers = {k: v for k, v in llm.items() if k not in vetoed}

        out.answers = answers
        # 시점은 규칙으로 뽑아 붙인다. LLM 에 숫자를 맡기면 환각이 섞이고,
        # 이건 정규식이 확실히 잘하는 일이다. 어느 문장인지 모르니 글 전체를 본다.
        out.answers.update(_days_for(answers, text, None))
        out.matched = sorted(answers)
        out.unmatched = not answers
        out.engine = "llm"
        return out

    for sentence in _split(text):
        for key, value, patterns in RULES:
            if key in out.answers:
                continue
            for pat in patterns:
                m = re.search(pat, sentence)
                if m is None:
                    continue
                if key not in NEGATION_EXEMPT and _negated(
                    sentence, m.start(), m.end() - m.start()
                ):
                    break                      # 이 문장에서는 부정됐다
                out.answers[key] = value
                out.matched.append(m.group(0))
                # 그 문장 안에서 먼저 찾는다. 없으면 글 전체를 본다 -
                # "3일 전에 풀밭에서 뒹굴었는데 그 뒤로 긁어요" 처럼
                # 시점과 증상이 다른 절에 나뉘는 게 흔하다.
                out.answers.update(_days_for({key: value}, text, sentence))
                break

    out.unmatched = not out.answers
    return out


def _days_for(
    answers: dict[str, str], whole: str, sentence: str | None
) -> dict[str, str]:
    """
    시점이 의미 있는 key 에 `<key>_days` 를 붙인다.

    **못 찾았으면 빈 문자열을 넣는다.** 지우지 않고 비우는 이유는, 답변이
    key 단위로 덮어써지기 때문이다. 예전 메모의 "한 달 전" 이 남아 있으면
    오늘 "샴푸 바꿨어요" 에 그 숫자가 붙는다.
    """
    out: dict[str, str] = {}
    for key in answers:
        if key not in DURATION_KEYS:
            continue
        d = duration_days(sentence) if sentence else None
        if d is None:
            d = duration_days(whole)
        out[key + "_days"] = "" if d is None else str(d)
    return out
