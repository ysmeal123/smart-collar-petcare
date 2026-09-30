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
    ("shampoo_changed", "yes", ("샴푸", "목욕제품", "바디워시", "린스")),
    ("food_changed", "yes", (r"사료.{0,3}(바꾸|바꿨|바뀌|변경|새로)",
                             r"간식.{0,3}(바꾸|바꿨|바뀌|변경|새로)",
                             "새 사료", "다른 사료")),
    ("env_exposure", "yes", ("풀밭", "잔디", "흙", "모래", "산에", "등산",
                             "뒹굴", "개천", "계곡", "바닷")),
    ("skin_visible", "보여요", ("붉", "빨갛", "빨개", "발진", "뾰루지",
                                "털이 빠지", "탈모", "상처", "진물", "벗겨")),

    # --- 귀 ---
    ("ear_smell", "yes", ("귀에서 냄새", "귀 냄새", "귀가 냄새")),
    ("ear_discharge", "yes", ("귀지", "귀에서 분비물", "귀 분비물",
                              "귀에 갈색", "귀에 검은")),
    ("recent_bath", "yes", ("목욕", "수영", "물놀이", "미용")),

    # --- 관절 ---
    ("limping", "yes", ("절", "다리를 들", "다리를 절", "절뚝", "짝짝이로 걷")),
    # 부정형이 곧 증상이다. 애매한 "계단" 단독 키워드는 넣지 않는다 —
    # "계단 잘 올라가요" 까지 잡힌다.
    ("stairs_avoid", "yes", ("계단을 안", "계단을 못", "계단 안", "계단 못",
                             "소파에 안", "소파를 안", "소파에 못",
                             "안 올라가", "못 올라가", "안아달라", "안아 달라")),
    ("weather_cold", "yes", ("산책을 줄", "산책 줄", "산책을 못", "산책 못",
                             "산책을 안", "추워", "더워", "비가", "바빠", "바빠서")),

    # --- 수면 ---
    ("env_changed", "yes", ("이사", "잠자리", "가구", "새 강아지", "새 고양이",
                            "집을 옮", "방을 바꾸")),
    ("noise", "yes", ("시끄러", "공사", "손님", "폭죽", "천둥")),

    # --- 식욕 ---
    ("treats", "yes", ("간식을 많이", "간식 많이", "간식을 더", "사람 음식")),
    ("vomit", "yes", ("토했", "토하", "구토", "설사", "묽은 변", "변이 묽")),
]

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
NEGATION_EXEMPT = frozenset({"stairs_avoid", "weather_cold"})


class Extraction(BaseModel):
    """
    추출 결과.

    `unmatched` 가 핵심이다. 못 알아들은 말을 조용히 버리면 보호자는
    "전달했는데 아무 일도 안 일어났다"를 겪는다. 그건 침묵보다 나쁘다.
    """

    answers: dict[str, str] = Field(default_factory=dict)
    matched: list[str] = Field(default_factory=list, description="무엇을 알아들었는지")
    unmatched: bool = False


def _negated(text: str, at: int, length: int) -> bool:
    """
    키워드 뒤에 부정어가 붙었는가.

    한국어는 부정이 서술어에 붙어 뒤에 온다. "토는 안 했어요", "절지 않아요".
    그래서 키워드 **뒤쪽** 창을 본다.
    """
    tail = text[at + length: at + length + NEG_WINDOW]
    return any(n in tail for n in NEGATIONS)


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


def extract(text: str) -> Extraction:
    """
    자유 텍스트에서 구조화된 맥락을 뽑는다.

    **새 키를 만들지 않는다.** QUESTIONS 에 있는 key 만 내놓는다.
    """
    out = Extraction()
    if not text or not text.strip():
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
                break

    out.unmatched = not out.answers
    return out
