"""
최종 급여 계획 · Guardian Agent 검증.

급여 계획은 이 시스템의 최종 출력이다. 하드웨어 앞에서 멈춘다.
여기서 지키려는 것:

    - 기준선이 안 여물면 영양제를 주지 않는다
    - 끼니로 쪼개도 총량이 보존된다
    - 맥락(보호자 답변)은 처방을 늘리는 쪽으로 작동하지 않는다
    - 묻지 않아도 되는 건 묻지 않는다
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agent.wellness_agent import (
    ASK_COOLDOWN_DAYS,
    Context,
    ask,
    brief,
    interpret,
)
from core import twin as twin_mod
from core.models import HealthAxis
from core.plan import build, split
from core.prescribe import prescribe
from mock.generator import generate


@pytest.fixture(scope="module")
def skin_ds():
    return generate("skin")


@pytest.fixture(scope="module")
def normal_ds():
    return generate("normal")


# ---------------------------------------------------------------------------
# 급여 계획
# ---------------------------------------------------------------------------

def test_센서_데이터를_넣으면_급여_계획이_나온다(skin_ds):
    """이 한 줄이 시스템 전체다."""
    plan, _, _ = build(skin_ds.profile, skin_ds.days)

    assert plan.dog_name == "초코"
    assert plan.total_food_g > 0
    assert len(plan.meals) == skin_ds.profile.meals_per_day
    assert plan.simulation is not None
    assert plan.trace, "근거가 비어 있으면 안 된다"


def test_끼니로_쪼개도_총량이_보존된다(skin_ds):
    plan, _, _ = build(skin_ds.profile, skin_ds.days)

    assert sum(m.food_g for m in plan.meals) == plan.total_food_g

    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    planned = sum(p.count for m in plan.meals for p in m.pellets)
    assert planned == sum(i.pellets for i in rx.items)


def test_길항_성분이_다른_끼니로_갈라진다(skin_ds):
    """알고리즘이 정한 끼니 배치를 계획이 그대로 따라야 한다."""
    plan, _, _ = build(skin_ds.profile, skin_ds.days)

    morning = {p.cartridge_id for p in plan.meals[0].pellets}
    evening = {p.cartridge_id for p in plan.meals[-1].pellets}
    assert "omega3" in morning
    assert "skin_barrier" in evening


def test_기준선이_안_여물면_사료만_준다(skin_ds):
    """
    비교 대상이 없는 상태의 판정으로 영양제를 주는 건 근거가 없다.
    굶기지는 않으므로 사료는 정상 급여한다.
    """
    plan, _, _ = build(skin_ds.profile, skin_ds.days[:10])

    assert plan.ready is False
    assert "관찰" in plan.blocked_reason
    assert plan.total_food_g > 0
    assert all(not m.pellets for m in plan.meals), "기준선 전에 영양제가 나갔다"


def test_건강한_개에게는_영양제가_없다(normal_ds):
    plan, _, _ = build(normal_ds.profile, normal_ds.days)

    assert plan.ready is True
    assert plan.attention == []
    assert all(not m.pellets for m in plan.meals)
    assert plan.total_food_g > 0


def test_급성이면_영양제를_끊고_사료는_준다():
    ds = generate("acute")
    plan, _, _ = build(ds.profile, ds.days)

    assert plan.escalated is True
    assert "병원" in plan.escalation_reason
    assert all(not m.pellets for m in plan.meals)
    assert plan.total_food_g > 0


def test_명령에_만료시각이_붙는다(skin_ds):
    """아침 급여가 저녁에 실행되면 안 된다."""
    plan, _, _ = build(skin_ds.profile, skin_ds.days)

    for m in plan.meals:
        assert m.expires_at > m.at
        assert (m.expires_at - m.at) <= timedelta(hours=3)


def test_한_끼만_먹는_개는_전부_같이_받는다(skin_ds):
    one = skin_ds.profile.model_copy(update={"meals_per_day": 1})
    plan, _, _ = build(one, skin_ds.days)

    assert len(plan.meals) == 1
    assert plan.meals[0].food_g == plan.total_food_g


def test_사람이_읽는_형태로_뽑힌다(skin_ds):
    plan, _, _ = build(skin_ds.profile, skin_ds.days)
    text = plan.render()

    assert "초코" in text
    assert "사료" in text
    assert "시뮬레이션" in text


# ---------------------------------------------------------------------------
# Guardian Agent
# ---------------------------------------------------------------------------

def test_아무_일도_없으면_묻지_않는다(normal_ds):
    """알림 피로가 쌓이면 정작 중요할 때 답을 안 한다."""
    t = twin_mod.build(normal_ds.profile, normal_ds.days)
    assert ask(t, Context()) == []


def test_발화한_축에_대해서만_묻는다(skin_ds):
    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    t = twin_mod.build(skin_ds.profile, skin_ds.days, rx.axes, rx)

    qs = ask(t, Context())
    assert qs
    assert {q.axis for q in qs} == {HealthAxis.SKIN}


def test_센서가_아는_건_묻지_않는다():
    """'밤에 긁었나요'는 이미 안다. 물으면 사용자가 앱을 닫는다."""
    from agent.wellness_agent import QUESTIONS

    banned = ("긁", "산책 얼마", "몇 번", "활동량")
    for questions in QUESTIONS.values():
        for q in questions:
            assert not any(b in q.text for b in banned[:1]), q.text
            assert q.why, "왜 묻는지 없으면 사용자가 답할 이유가 없다"


def test_이미_답한_질문은_다시_묻지_않는다(skin_ds):
    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    t = twin_mod.build(skin_ds.profile, skin_ds.days, rx.axes, rx)

    ctx = Context(
        answers={"shampoo_changed": "no", "food_changed": "no", "skin_visible": "없어요"},
        updated_at=t.as_of,
    )
    assert ask(t, ctx) == []


def test_오래되면_다시_묻는다(skin_ds):
    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    t = twin_mod.build(skin_ds.profile, skin_ds.days, rx.axes, rx)

    old = t.as_of - timedelta(days=ASK_COOLDOWN_DAYS + 1)
    ctx = Context(answers={"shampoo_changed": "no"}, updated_at=old)
    assert ask(t, ctx)


# ---------------------------------------------------------------------------
# 맥락 -> 처방 조정
# ---------------------------------------------------------------------------

def test_맥락은_영양제를_늘리지_않는다():
    """
    보호자 답변으로 용량이 올라가면, 답변을 유도해 매출을 늘리는 경로가 열린다.
    맥락은 멈추거나 미루는 쪽으로만 작동해야 한다.
    """
    from agent.wellness_agent import ContextEffect

    fields = set(ContextEffect.model_fields)
    assert fields == {"defer_axes", "block_all", "vet_referral", "notes"}
    assert not any("increase" in f or "boost" in f for f in fields)


def test_원인이_설명되면_처방을_미룬다():
    eff = interpret(Context(answers={"shampoo_changed": "yes"}))

    assert HealthAxis.SKIN in eff.defer_axes
    assert any("샴푸" in n for n in eff.notes)


def test_눈에_보이는_병변은_진료로_넘긴다():
    """영양제로 덮으면 진료가 늦어진다."""
    eff = interpret(Context(answers={"skin_visible": "보여요"}))

    assert eff.vet_referral is True
    assert HealthAxis.SKIN in eff.defer_axes


def test_구토_설사는_전부_멈춘다():
    eff = interpret(Context(answers={"vomit": "yes"}))

    assert eff.block_all is True
    assert eff.vet_referral is True


def test_산책을_줄였다면_이동성_변화가_설명된다():
    """보호자 사정으로 줄어든 걸 관절 문제로 읽으면 안 된다."""
    eff = interpret(Context(answers={"weather_cold": "yes"}))
    assert HealthAxis.MOBILITY in eff.defer_axes


def test_아무_답도_없으면_아무것도_바꾸지_않는다():
    eff = interpret(Context())

    assert eff.defer_axes == []
    assert eff.block_all is False
    assert eff.vet_referral is False


def test_상태_설명에_진단_용어를_쓰지_않는다(skin_ds):
    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    t = twin_mod.build(skin_ds.profile, skin_ds.days, rx.axes, rx)

    text = brief(t)
    assert "초코" in text
    for word in ("염", "진단", "질환", "병입니다"):
        assert word not in text, f"진단 언어가 들어갔다: {word}"


# ---------------------------------------------------------------------------
# 문진 -> 계획 (연결 자체를 검증한다)
#
# 위의 테스트들은 interpret() 를 단독으로만 확인한다. 그래서 interpret() 가
# 파이프라인에 연결돼 있지 않아도 전부 통과했다. 실제로 그런 상태였다 —
# 답변은 저장되고 앱에 되돌아왔지만 처방은 한 톨도 안 바뀌었다.
#
# 아래는 "답변이 최종 출력까지 닿는가"를 본다.
# ---------------------------------------------------------------------------

def _pellets(plan) -> int:
    return sum(p.count for m in plan.meals for p in m.pellets)


def test_문진_답변이_계획까지_닿는다(skin_ds):
    """이게 깨지면 문진은 장식이다."""
    base, _, _ = build(skin_ds.profile, skin_ds.days, context=Context())
    assert _pellets(base) > 0, "피부 시나리오인데 영양제가 안 나왔다"

    after, _, _ = build(
        skin_ds.profile, skin_ds.days,
        context=Context(answers={"shampoo_changed": "yes"}),
    )
    assert _pellets(after) == 0, "샴푸를 바꿨다는데 피부 처방이 그대로다"
    assert after.total_food_g == base.total_food_g, "사료는 건드리면 안 된다"


def test_구토_설사는_영양제를_전부_뺀다(skin_ds):
    plan, _, _ = build(
        skin_ds.profile, skin_ds.days,
        context=Context(answers={"vomit": "yes"}),
    )
    assert _pellets(plan) == 0
    assert plan.total_food_g > 0, "밥까지 끊으면 안 된다"
    assert any("진료" in a for a in plan.attention)


def test_처방은_보류해도_관찰은_남는다(skin_ds):
    """
    센서가 본 사실을 지우지 않는다.

    보호자에게는 '긁기가 늘었다'를 계속 보여주고, 처방만 미룬다.
    관찰까지 지우면 보호자가 상태를 모른 채 넘어간다.
    """
    plan, _, _ = build(
        skin_ds.profile, skin_ds.days,
        context=Context(answers={"shampoo_changed": "yes"}),
    )
    assert plan.observations, "관찰이 사라졌다"
    assert any("샴푸" in o for o in plan.observations), "보류 사유가 안 보인다"


def test_문진은_용량을_올릴_수_없다(skin_ds):
    """
    구조적 보장.

    답변으로 용량이 오르는 경로가 생기면, 답변을 유도해 매출을 늘릴 수 있다.
    모든 질문에 '예'를 찍어도 기준선을 넘지 못해야 한다.
    """
    from agent.wellness_agent import QUESTIONS

    base = _pellets(build(skin_ds.profile, skin_ds.days, context=Context())[0])

    keys = {q.key for qs in QUESTIONS.values() for q in qs}
    for key in sorted(keys):
        for value in ("yes", "보여요"):
            plan, _, _ = build(
                skin_ds.profile, skin_ds.days,
                context=Context(answers={key: value}),
            )
            assert _pellets(plan) <= base, f"{key}={value} 가 용량을 올렸다"


def test_답변_표기가_달라도_인식한다():
    """
    정규화가 앱에만 있으면, 다른 클라이언트가 '예' 를 보낼 때
    아무 에러 없이 조용히 무시된다. 서버에서도 받아준다.
    """
    for value in ("yes", "예", "네", "Yes", " yes "):
        eff = interpret(Context(answers={"limping": value}))
        assert HealthAxis.MOBILITY in eff.defer_axes, f"{value!r} 를 놓쳤다"

    for value in ("no", "아니요", ""):
        eff = interpret(Context(answers={"limping": value}))
        assert eff.defer_axes == [], f"{value!r} 를 긍정으로 읽었다"


def test_알림전용_축의_알림이_사용자에게_도달한다():
    """
    `AxisScore.active` 는 '처방 중'을 뜻한다. 관찰 여부가 아니다.

    이걸로 관찰 목록을 걸렀더니, 귀축(알림 전용)과 식욕축(차단 전용)의
    메시지가 사용자에게 한 번도 도달하지 않았다.
    알림만 하는 축의 알림이 안 나가면 그 축은 존재 이유가 없다.
    """
    from mock.generator import generate

    ds = generate("acute")
    plan, rx, _ = build(ds.profile, ds.days)

    appetite = next(a for a in rx.axes if a.axis is HealthAxis.APPETITE)
    assert appetite.message, "식욕축이 할 말이 있어야 하는 시나리오다"
    assert appetite.active is False, "식욕축은 처방 권한이 없다"

    assert appetite.message in plan.observations, (
        "처방 권한이 없는 축의 관찰이 사용자에게 도달하지 않는다"
    )
