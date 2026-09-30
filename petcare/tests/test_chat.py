"""
자유 텍스트 해석 · 대화록 검증.

여기서 지키려는 것:

    - 부정을 놓치지 않는다 ("토는 안 했어요"가 구토 보고가 되면 안 된다)
    - 증상이 부정형인 표현을 뒤집지 않는다 ("계단을 안 올라가요"는 증상이다)
    - 새 key 를 만들지 않는다 (interpret() 가 모르는 값은 아무 일도 안 한다)
    - 못 알아들으면 못 알아들었다고 말한다
    - 자유 텍스트가 **처방까지** 닿는다
    - 자유 텍스트로도 용량을 올릴 수 없다
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agent import chat as chat_mod
from agent.extract import RULES, extract
from agent.wellness_agent import QUESTIONS, Context, interpret
from core import twin as twin_mod
from core.models import HealthAxis
from core.plan import build
from core.prescribe import prescribe
from mock.generator import generate


@pytest.fixture(scope="module")
def skin_ds():
    return generate("skin")


# ---------------------------------------------------------------------------
# 추출
# ---------------------------------------------------------------------------

def test_평범한_보고를_알아듣는다():
    cases = {
        "샴푸를 새로 바꿨어요": "shampoo_changed",
        "사료도 바꿨어요": "food_changed",
        "풀밭에서 한참 뒹굴었어요": "env_exposure",
        "피부가 빨갛게 됐어요": "skin_visible",
        "귀에서 냄새가 나요": "ear_smell",
        "다리를 절어요": "limping",
        "날이 추워서 산책을 줄였어요": "weather_cold",
        "이사를 했어요": "env_changed",
        "어제 토했어요": "vomit",
    }
    for text, key in cases.items():
        assert key in extract(text).answers, f"{text!r} 에서 {key} 를 놓쳤다"


@pytest.mark.parametrize("text", [
    "토는 안 했어요",
    "설사는 없어요",
    "절지 않아요",
    "사료는 안 바꿨어요",
    "샴푸 바꾼 적 없어요",
])
def test_부정을_긍정으로_읽지_않는다(text):
    """
    규칙 기반 추출에서 가장 위험한 실패다.

    "토는 안 했어요"를 구토 보고로 읽으면 영양제를 잘못 멈춘다.
    보호자는 왜 멈췄는지 알 수 없다.
    """
    assert extract(text).answers == {}, f"{text!r} 를 긍정으로 읽었다"


def test_증상이_부정형인_표현은_뒤집지_않는다():
    """
    "계단을 안 올라가요"는 부정문이지만 **증상 보고**다.
    부정 검사를 그대로 걸면 뜻이 반대가 된다.
    """
    assert "stairs_avoid" in extract("요즘 계단을 안 올라가려고 해요").answers
    assert "weather_cold" in extract("산책을 안 하고 있어요").answers

    # 그렇다고 아무 문장이나 잡으면 안 된다
    assert extract("계단 잘 올라가요").answers == {}


def test_한_문장에_섞여_있어도_각각_판정한다():
    """
    절 단위로 쪼개지 않으면 부정어가 엉뚱한 키워드에 걸린다.
    "목욕은 했는데 토는 안 했어요" 에서 목욕까지 부정되면 안 된다.
    """
    got = extract("목욕은 했는데 토는 안 했어요").answers
    assert "recent_bath" in got
    assert "vomit" not in got


def test_여러_건을_한꺼번에_알아듣는다():
    got = extract("목욕을 시켰고 사료도 바꿨어요").answers
    assert {"recent_bath", "food_changed"} <= set(got)


def test_새_key_를_만들지_않는다():
    """
    QUESTIONS 에 없는 key 를 내놓으면 interpret() 가 그걸 모른다.
    저장은 되는데 아무 일도 일어나지 않는, 찾기 어려운 실패가 된다.
    """
    known = {q.key for qs in QUESTIONS.values() for q in qs}
    for key, _, _ in RULES:
        assert key in known, f"{key} 는 질문 목록에 없다"


def test_못_알아들으면_그렇다고_말한다():
    """조용히 버리면 보호자는 전달했다고 믿는다. 침묵보다 나쁘다."""
    ex = extract("오늘 기분이 좋아 보여요")
    assert ex.answers == {}
    assert ex.unmatched is True


def test_빈_입력은_아무것도_하지_않는다():
    for text in ("", "   ", "\n"):
        assert extract(text).answers == {}


# ---------------------------------------------------------------------------
# 추출 -> 처방
# ---------------------------------------------------------------------------

def _pellets(plan) -> int:
    return sum(p.count for m in plan.meals for p in m.pellets)


def test_자유_텍스트가_처방까지_닿는다(skin_ds):
    """대화가 장식이 아니라는 것을 여기서 못 박는다."""
    base, _, _ = build(skin_ds.profile, skin_ds.days, context=Context())
    assert _pellets(base) > 0

    ex = extract("3일 전에 풀밭에서 한참 뒹굴었어요. 샴푸도 새로 바꿨고요.")
    assert ex.answers, "이 문장은 알아들어야 한다"

    after, _, _ = build(
        skin_ds.profile, skin_ds.days, context=Context(answers=ex.answers)
    )
    assert _pellets(after) == 0, "원인이 설명됐는데 피부 처방이 그대로다"
    assert after.total_food_g == base.total_food_g, "사료는 건드리면 안 된다"


def test_자유_텍스트로도_용량을_올릴_수_없다(skin_ds):
    """
    LLM 으로 바꿔도 이 보장이 유지되어야 한다.
    추출이 틀려도 최악의 결과가 '영양제를 안 준다' 여야 한다.
    """
    base = _pellets(build(skin_ds.profile, skin_ds.days, context=Context())[0])

    texts = [
        "아주 건강해요",
        "영양제를 더 주세요",
        "긁는 게 심해졌어요 많이요 아주 많이",
        "샴푸도 바꾸고 사료도 바꾸고 목욕도 시켰어요",
        "토했어요",
    ]
    for text in texts:
        ex = extract(text)
        plan, _, _ = build(
            skin_ds.profile, skin_ds.days, context=Context(answers=ex.answers)
        )
        assert _pellets(plan) <= base, f"{text!r} 가 용량을 올렸다"


def test_해석이_안_되는_말은_처방을_바꾸지_않는다(skin_ds):
    base, _, _ = build(skin_ds.profile, skin_ds.days, context=Context())
    ex = extract("오늘 산책하면서 사진 많이 찍었어요")

    after, _, _ = build(
        skin_ds.profile, skin_ds.days, context=Context(answers=ex.answers)
    )
    assert _pellets(after) == _pellets(base)


# ---------------------------------------------------------------------------
# 대화록
# ---------------------------------------------------------------------------

@pytest.fixture
def twin(skin_ds):
    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    return twin_mod.build(skin_ds.profile, skin_ds.days, rx.axes, rx)


def test_대화록은_인사와_관찰로_시작한다(twin):
    turns = chat_mod.build(twin, Context(), [])

    assert turns[0].role == "agent"
    assert twin.profile.name in turns[0].text
    assert any("긁" in t.text for t in turns), "관찰한 사실이 안 보인다"


def test_발화한_축에_대해서만_묻는다(twin):
    turns = chat_mod.build(twin, Context(), [])
    asked = {t.key for t in turns if t.kind == "question"}
    assert asked, "피부축이 발화한 상태다"

    skin_keys = {q.key for q in QUESTIONS[HealthAxis.SKIN]}
    assert asked <= skin_keys, "발화하지 않은 축까지 묻고 있다"


def test_질문에는_선택지와_이유가_붙는다(twin):
    for t in chat_mod.build(twin, Context(), []):
        if t.kind != "question":
            continue
        assert t.choices, f"{t.key} 에 선택지가 없다"
        assert t.why, f"{t.key} 에 묻는 이유가 없다"


def test_메모에서_나온_답은_문답으로_중복되지_않는다(twin):
    """
    메모에서 추출한 답을 문답으로 또 보여주면, 보호자가 쓴 문장보다 위에
    "풀밭에서 놀았나요? → 네"가 먼저 나온다. 순서가 거꾸로 읽힌다.
    """
    note = chat_mod.Note(
        text="풀밭에서 뒹굴었어요",
        at=datetime(2026, 9, 30, 10, 0),
        understood={"env_exposure": "yes"},
    )
    # updated_at 을 반드시 준다. 실제 경로는 db.load_context 가 답변 시각을
    # 함께 돌려주고, 그게 없으면 ask() 가 쿨다운을 쿨다운으로 보지 않는다.
    turns = chat_mod.build(
        twin,
        Context(answers={"env_exposure": "yes"}, updated_at=twin.as_of),
        [note],
    )

    questions = [t for t in turns if t.text == "최근에 풀밭이나 흙에서 놀았나요?"]
    assert not questions, "메모로 답한 질문이 문답으로 또 나왔다"
    assert any(t.kind == "understood" for t in turns)


def test_못_알아들은_메모도_대화록에_남는다(twin):
    note = chat_mod.Note(
        text="오늘 기분이 좋아 보여요",
        at=datetime(2026, 9, 30, 10, 0),
        understood={},
    )
    turns = chat_mod.build(twin, Context(), [note])

    assert any(t.role == "guardian" and "기분" in t.text for t in turns), \
        "보호자가 쓴 문장이 사라졌다"
    assert any(t.kind == "understood" and "이해하지 못" in t.text for t in turns), \
        "못 알아들었다는 말을 안 했다"


def test_저장된_yes_를_그대로_보여주지_않는다(twin):
    """화면에 'yes' 가 뜨면 안 된다. 사람이 읽는 말로 바꿔야 한다."""
    turns = chat_mod.build(twin, Context(answers={"shampoo_changed": "yes"}), [])
    guardian = [t.text for t in turns if t.role == "guardian"]
    assert "yes" not in guardian
    assert "네" in guardian


def test_기준선이_안_여물면_그걸_먼저_말한다(skin_ds):
    """
    비교할 평소가 없는 상태에서 "이상 없습니다"는 거짓말이다.
    모를 뿐이라는 것을 먼저 말해야 한다.
    """
    short = skin_ds.days[:5]
    rx, _ = prescribe(skin_ds.profile, short)
    t = twin_mod.build(skin_ds.profile, short, rx.axes, rx)

    turns = chat_mod.build(t, Context(), [])
    assert any("사료만" in x.text for x in turns)


def test_대화가_판단하지_않는다():
    """
    처방을 바꾸는 것은 interpret() 뿐이다. chat 모듈에 판단이 들어가면
    같은 결정이 두 군데 생기고 반드시 갈라진다.
    """
    import inspect

    src = inspect.getsource(chat_mod)
    for banned in ("defer_axes", "block_all", "AXIS_DOSE", "severity("):
        assert banned not in src, f"대화 모듈이 처방을 만지고 있다: {banned}"


def test_계획_요약은_숫자를_틀리지_않는다(skin_ds):
    plan, _, _ = build(skin_ds.profile, skin_ds.days, context=Context())
    turn = chat_mod.plan_summary(plan)

    assert str(plan.total_food_g) in turn.text
    pellets = _pellets(plan)
    if pellets:
        assert str(pellets) in turn.text


def test_해석_결과는_사람_말로_보여준다():
    """'env_exposure' 같은 내부 key 가 화면에 뜨면 안 된다."""
    note = chat_mod.Note(
        text="풀밭에서 놀았어요",
        at=datetime(2026, 9, 30, 10, 0),
        understood={"env_exposure": "yes"},
    )
    text = chat_mod.understood(note).text
    assert "env_exposure" not in text
    assert "풀밭" in text
