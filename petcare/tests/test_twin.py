"""
DogTwin 조립 · 14일 전향 시뮬레이션 검증.

시뮬레이션에서 지키려는 것은 "예측이 맞는가"가 아니다.
그건 검증할 방법이 없다. 지키려는 것은:

    - Safety 가 이미 보는 걸 다시 보지 않는다 (역할 분리)
    - 실제로 먹은 양을 반영한다 (처방량이 아니라)
    - 가짜 정밀 예측을 내놓지 않는다 (방향만)
    - 위험한 계획을 통과시키지 않는다
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from core import simulation as sim
from core import twin as twin_mod
from core.constants import CARTRIDGE_BY_ID, SAFETY_MARGIN, UPPER_LIMIT_PER_KG, Nutrient
from core.inference import evaluate_axes
from core.prescribe import prescribe
from mock.generator import generate


@pytest.fixture(scope="module")
def skin_ds():
    return generate("skin")


# ---------------------------------------------------------------------------
# 트윈 조립
# ---------------------------------------------------------------------------

def test_트윈이_흩어진_상태를_하나로_모은다(skin_ds):
    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    t = twin_mod.build(skin_ds.profile, skin_ds.days, rx.axes, rx)

    assert t.dog_id == skin_ds.profile.dog_id
    assert t.data_days == len(skin_ds.days)
    assert t.baseline.days_observed > 0
    assert len(t.wellness) == 5
    assert "skin" in t.attention


def test_기준선이_안_여물면_처방_준비가_안_된_것이다(skin_ds):
    """
    비교 대상이 없는 상태로 처방하면 '평소와 다르다'가 아니라
    '다른 개와 다르다'가 되어 버린다.
    """
    young = twin_mod.build(skin_ds.profile, skin_ds.days[:5])
    grown = twin_mod.build(skin_ds.profile, skin_ds.days)

    assert young.baseline.stage == "provisional"
    assert young.ready_to_prescribe is False

    assert grown.baseline.stage == "mature"
    assert grown.ready_to_prescribe is True


def test_기준선_단계가_날짜에_따라_올라간다(skin_ds):
    stages = [
        twin_mod.build(skin_ds.profile, skin_ds.days[:n]).baseline.stage
        for n in (5, 10, 20, 44)
    ]
    assert stages == ["provisional", "intermediate", "maturing", "mature"]


def test_로드셀이_없으면_모른다고_말한다(skin_ds):
    """추측해서 채우면 에너지 수지가 조용히 틀어진다."""
    days = [d.model_copy(update={"food_offered_g": 0, "food_eaten_g": 0})
            for d in skin_ds.days]
    t = twin_mod.build(skin_ds.profile, days)

    assert t.intake.measured is False
    assert t.intake.recent_ratio is None


def test_섭취량이_있으면_반영한다(skin_ds):
    t = twin_mod.build(skin_ds.profile, skin_ds.days)
    assert t.intake.measured is True
    assert 0.0 < t.intake.recent_ratio <= 1.0


def test_트윈은_판단을_다시_하지_않는다(skin_ds):
    """
    같은 판단을 두 군데서 하면 언젠가 갈라진다.
    트윈은 inference 가 낸 결과를 받아 담기만 한다.
    """
    axes = evaluate_axes(skin_ds.profile, skin_ds.days)
    t = twin_mod.build(skin_ds.profile, skin_ds.days, axes)

    assert [a.axis for a in t.wellness] == [a.axis for a in axes]
    assert [a.z_score for a in t.wellness] == [a.z_score for a in axes]


# ---------------------------------------------------------------------------
# 시뮬레이션 — 역할 분리
# ---------------------------------------------------------------------------

def test_다_먹으면_에너지가_맞아떨어진다(skin_ds):
    r = sim.run(skin_ds.profile, der_kcal=356, food_grams=99, items=[],
                intake_ratio=1.0)
    assert r.energy is sim.Energy.BALANCED
    assert r.daily_balance_kcal == 0.0


def test_처방량이_아니라_먹은_양으로_계산한다(skin_ds):
    """
    100g을 줘도 70%만 먹으면 실제로는 70g이다.
    처방량으로만 보면 '충분히 주는데 왜 빠지지'가 된다.
    """
    full = sim.run(skin_ds.profile, 356, 99, [], intake_ratio=1.0)
    half = sim.run(skin_ds.profile, 356, 99, [], intake_ratio=0.55)

    assert full.energy is sim.Energy.BALANCED
    assert half.energy is sim.Energy.STRONG_DEFICIT
    assert half.passed is False
    assert half.projected_kg < full.projected_kg


def test_방향만_말하고_가짜_정밀_예측을_하지_않는다(skin_ds):
    """'14일 뒤 정확히 4.213kg' 같은 건 근거가 없다."""
    r = sim.run(skin_ds.profile, 356, 99, [], intake_ratio=0.9)

    assert r.weight in set(sim.Direction)
    assert r.energy in set(sim.Energy)
    # 숫자를 아예 안 주는 건 아니다. 다만 판정은 방향으로 한다.
    assert isinstance(r.projected_kg, float)
    assert r.summary.count("·") == 3


def test_상한을_넘는_계획은_통과시키지_않는다(skin_ds):
    """Safety 가 막아야 할 것이지만, 시뮬레이션도 독립적으로 잡아야 한다."""
    per = CARTRIDGE_BY_ID["omega3"].nutrients_per_pellet[Nutrient.EPA_DHA]
    limit = UPPER_LIMIT_PER_KG[Nutrient.EPA_DHA] * skin_ds.profile.weight_kg * SAFETY_MARGIN
    too_many = int(limit / per) + 5

    r = sim.run(skin_ds.profile, 356, 99,
                [{"cartridge_id": "omega3", "pellets": too_many}])

    assert r.exposure is sim.Exposure.OVER_LIMIT
    assert r.passed is False
    assert any("상한" in x for x in r.reasons)


def test_상한에_바짝_붙으면_경고하되_막지는_않는다(skin_ds):
    """
    오늘 통과해도 2주 내내 상한의 80%를 쓰는 계획은 여유가 없다.
    Safety 는 이걸 못 본다 - 하루만 보기 때문이다.

    알갱이 단위가 거칠어서(1알 80mg / 상한 416mg) 정확히 85%에 맞출 수 없다.
    상한을 넘지 않는 최대 알 수를 쓴다.
    """
    import math

    per = CARTRIDGE_BY_ID["omega3"].nutrients_per_pellet[Nutrient.EPA_DHA]
    limit = UPPER_LIMIT_PER_KG[Nutrient.EPA_DHA] * skin_ds.profile.weight_kg * SAFETY_MARGIN
    near = int(limit // per)
    assert 0.80 <= near * per / limit <= 1.0, "알갱이 단위가 너무 거칠어 구간을 못 맞춘다"

    r = sim.run(skin_ds.profile, 356, 99,
                [{"cartridge_id": "omega3", "pellets": near}])

    assert r.exposure is sim.Exposure.NEAR_LIMIT
    assert r.passed is True
    assert any("여유가 없다" in x for x in r.reasons)


def test_급여량이_크게_흔들리면_불안정으로_본다(skin_ds):
    r = sim.run(skin_ds.profile, 356, 99, [], prev_food_grams=70)
    assert r.stability is sim.Stability.SWINGING
    assert any("흔들린다" in x for x in r.reasons)


def test_영양제가_없으면_노출은_문제되지_않는다(skin_ds):
    r = sim.run(skin_ds.profile, 356, 99, [], intake_ratio=1.0)
    assert r.exposure is sim.Exposure.ACCEPTABLE
    assert r.nutrients == []
    assert r.passed is True


def test_실제_처방을_돌려도_통과한다(skin_ds):
    """알고리즘이 내놓은 계획이 자기 시뮬레이션을 통과해야 한다."""
    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    t = twin_mod.build(skin_ds.profile, skin_ds.days, rx.axes, rx)

    r = sim.run(
        skin_ds.profile, rx.der_kcal, rx.food_grams,
        [{"cartridge_id": i.cartridge_id, "pellets": i.pellets} for i in rx.items],
        current_kg=t.weight.current_kg,
        intake_ratio=t.intake.recent_ratio or 1.0,
    )
    assert r.passed is True, r.reasons


def test_하루_결측이_영양제를_통째로_없애지_않는다():
    """
    실제로 겪은 버그다.

    일일 배치가 수집보다 먼저 돌면 빈 날이 하나 생긴다. 착용률을
    '마지막 날' 로 읽으면 그 하루 때문에 0이 되고, ready_to_prescribe 가
    False 가 되면서 44일치 근거가 통째로 무효가 됐다.
    화면에는 "평소를 파악했습니다" 가 뜬 채 사료만 나갔다.

    하루 비는 건 드문 일이 아니다 - 배터리, BLE, 목욕, 배치 순서.
    """
    from core.plan import build as build_plan
    from mock.generator import generate

    ds = generate("skin")
    base, _, _ = build_plan(ds.profile, ds.days)
    assert sum(p.count for m in base.meals for p in m.pellets) > 0

    # 데이터가 없는 하루를 뒤에 붙인다
    empty = ds.days[-1].model_copy(deep=True)
    empty.date = ds.days[-1].date + timedelta(days=1)
    empty.wear_ratio = 0.0

    after, _, _ = build_plan(ds.profile, ds.days + [empty])
    assert sum(p.count for m in after.meals for p in m.pellets) > 0, \
        "하루 결측으로 영양제가 전부 사라졌다"


def test_며칠째_안_차면_여전히_막는다():
    """
    하루 결측은 흡수하되, 이 게이트가 원래 잡으려던 것은 그대로 잡아야 한다.
    며칠째 목줄을 안 차고 있으면 판단할 근거가 없는 게 맞다.
    """
    from core.plan import build as build_plan
    from mock.generator import generate

    ds = generate("skin")
    days = list(ds.days)
    for i in range(5):
        gap = days[-1].model_copy(deep=True)
        gap.date = days[-1].date + timedelta(days=1)
        gap.wear_ratio = 0.0
        days.append(gap)

    plan, _, _ = build_plan(ds.profile, days)
    assert sum(p.count for m in plan.meals for p in m.pellets) == 0
    assert "착용률" in plan.blocked_reason, "막힌 이유를 정확히 말해야 한다"
