"""
안전 필터 검증.

"위험한 처방이 정말 막히는가"를 코드로 증명한다.
Mock 시나리오만으로는 커버되지 않는 경로(알러지 대체, 약물 상호작용,
상한 초과, 다축 결합)를 여기서 직접 만들어 확인한다.

실행:
    cd petcare
    python -m pytest tests -v
"""

from __future__ import annotations

import copy

import pytest

from core.constants import CARTRIDGE_BY_ID, SAFETY_MARGIN, UPPER_LIMIT_PER_KG, Nutrient
from core.inference import PrescriptionState
from core.models import (
    Allergen,
    BodyCondition,
    Condition,
    DogProfile,
    DogSize,
    HealthAxis,
    MealSlot,
    Medication,
    Sex,
    Surgery,
    SurgeryType,
)
from core.prescribe import prescribe
from core.safety import headroom, resolve_blocks, totals_from_pellets
from mock.generator import generate


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def skin_case():
    """피부축이 확실히 발화하는 데이터셋 (초코)."""
    return generate("skin")


def with_profile(ds, **changes) -> DogProfile:
    """기존 프로필을 복사해 일부만 바꾼다."""
    p = copy.deepcopy(ds.profile)
    for k, v in changes.items():
        setattr(p, k, v)
    return p


def cart_ids(rx) -> set[str]:
    return {i.cartridge_id for i in rx.items}


# ---------------------------------------------------------------------------
# Layer 1 — 절대 금기
# ---------------------------------------------------------------------------

def test_생선알러지면_어유대신_조류오메가3(skin_case):
    """
    가장 중요한 대체 경로.

    어유는 우리 핵심 성분이라, 대체가 없으면 피부·관절 처방이 통째로 막힌다.
    """
    profile = with_profile(skin_case, allergies=[Allergen.FISH])
    rx, _ = prescribe(profile, skin_case.days)

    assert "omega3" not in cart_ids(rx), "생선 알러지인데 어유가 처방됐다"
    assert "algae_omega3" in cart_ids(rx), "조류 오메가3로 대체되지 않았다"

    trace = " ".join(t.detail for t in rx.trace)
    assert "대체" in trace


def test_항응고제면_오메가3가_대체없이_완전차단(skin_case):
    """
    항응고제 + 오메가3 고용량은 출혈 위험이 겹친다.
    이 경우엔 조류 오메가3도 똑같이 위험하므로 대체하면 안 된다.
    """
    profile = with_profile(skin_case, medications=[Medication.ANTICOAGULANT])
    rx, _ = prescribe(profile, skin_case.days)

    assert "omega3" not in cart_ids(rx)
    assert "algae_omega3" not in cart_ids(rx), "대체하면 안 되는 경우에 대체했다"

    blocks = resolve_blocks(profile)
    assert blocks["omega3"].substitute is None


def test_췌장염이면_고지방_성분_차단(skin_case):
    """췌장염 병력에 고지방(오메가3·MCT)을 얹으면 재발 위험이 있다."""
    profile = with_profile(skin_case, conditions=[Condition.PANCREATITIS])
    rx, _ = prescribe(profile, skin_case.days)

    assert "omega3" not in cart_ids(rx)
    assert "algae_omega3" not in cart_ids(rx)
    assert "cognition" not in cart_ids(rx)


def test_NSAID는_차단이_아니라_용량절반(skin_case):
    """차단까지는 아니고 용량만 조이는 경로."""
    base, _ = prescribe(skin_case.profile, skin_case.days)
    profile = with_profile(skin_case, medications=[Medication.NSAID])
    limited, _ = prescribe(profile, skin_case.days)

    base_omega = next(i.pellets for i in base.items if i.cartridge_id == "omega3")
    lim_omega = next(i.pellets for i in limited.items if i.cartridge_id == "omega3")

    assert 0 < lim_omega < base_omega, "NSAID 복용 시 오메가3가 줄어야 한다"


# ---------------------------------------------------------------------------
# Layer 2 — 상한 검증
# ---------------------------------------------------------------------------

def test_처방이_상한을_넘지_않는다(skin_case):
    """모든 시나리오에서 최종 사출량이 안전 상한 안에 있어야 한다."""
    for scenario in ("skin", "joint", "normal", "acute"):
        ds = generate(scenario)
        rx, _ = prescribe(ds.profile, ds.days)

        pellets = {i.cartridge_id: i.pellets for i in rx.items}
        totals = totals_from_pellets(pellets)
        room = headroom(ds.profile, rx.food_grams)

        for nutrient, amount in totals.items():
            if nutrient in room:
                assert amount <= room[nutrient] + 1e-6, (
                    f"[{scenario}] {nutrient} {amount:.1f} > 헤드룸 {room[nutrient]:.1f}"
                )


def test_헤드룸은_사료기여를_뺀_값이다(skin_case):
    """
    사료량이 활동량에 따라 매일 변하므로,
    많이 먹는 날은 사료에서 오는 영양소도 함께 늘어난다.
    이 커플링이 반영되지 않으면 상한을 조용히 넘는다.
    """
    p = skin_case.profile
    small = headroom(p, 50)
    large = headroom(p, 300)

    assert large[Nutrient.ZINC] < small[Nutrient.ZINC], (
        "사료를 많이 먹으면 영양제 여유분이 줄어야 한다"
    )


def test_상한이_안전계수만큼_보수적이다(skin_case):
    p = skin_case.profile
    room = headroom(p, 0)          # 사료 0g 가정
    raw = UPPER_LIMIT_PER_KG[Nutrient.ZINC] * p.weight_kg

    assert room[Nutrient.ZINC] == pytest.approx(raw * SAFETY_MARGIN)


def test_상한초과시_알갱이를_줄인다():
    """
    억지로 상한을 낮춰서, 감량 로직이 실제로 작동하는지 본다.
    반올림 후에도 상한을 넘을 수 있으므로 최종 알갱이 수로 검증해야 한다.
    """
    ds = generate("skin")
    profile = copy.deepcopy(ds.profile)

    original = UPPER_LIMIT_PER_KG[Nutrient.EPA_DHA]
    try:
        UPPER_LIMIT_PER_KG[Nutrient.EPA_DHA] = 15.0     # 극단적으로 조임
        rx, _ = prescribe(profile, ds.days)

        totals = totals_from_pellets({i.cartridge_id: i.pellets for i in rx.items})
        room = headroom(profile, rx.food_grams)
        assert totals.get(Nutrient.EPA_DHA, 0) <= room[Nutrient.EPA_DHA] + 1e-6

        trace = " ".join(t.step for t in rx.trace)
        assert "상한 제한" in trace
    finally:
        UPPER_LIMIT_PER_KG[Nutrient.EPA_DHA] = original


# ---------------------------------------------------------------------------
# Layer 0 — 긴급 정지
# ---------------------------------------------------------------------------

def test_급성이상시_영양제가_전부_중단된다():
    ds = generate("acute")
    rx, _ = prescribe(ds.profile, ds.days)

    assert rx.escalated
    assert rx.items == [], "긴급 정지 상태인데 영양제가 나갔다"
    assert "병원" in rx.escalation_reason
    assert rx.food_grams > 0, "사료까지 끊으면 안 된다"


def test_긴급정지중에는_체중_되먹임을_멈춘다():
    """
    병으로 살이 빠지는 것을 '사료 부족'으로 해석해
    급여량을 늘리면 위험하다.
    """
    ds = generate("acute")
    rx, _ = prescribe(ds.profile, ds.days)

    trace = " ".join(t.detail for t in rx.trace)
    assert "체중 되먹임을 정지" in trace


def test_만성악화는_급성으로_오판하지_않는다():
    """
    관절염이 충분히 진행되면 baseline 대비 z가 커진다.
    급성 판정을 baseline 기준으로 하면 여기서 오발동한다.
    """
    ds = generate("joint")
    rx, _ = prescribe(ds.profile, ds.days)

    assert not rx.escalated, "만성 관절염을 급성으로 오판했다"


# ---------------------------------------------------------------------------
# 처방 0 — 이해상충 방어
# ---------------------------------------------------------------------------

def test_건강한_개에게는_아무것도_처방하지_않는다():
    """
    알고리즘이 0을 처방할 수 있어야 한다.
    이게 안 되면 시스템은 그냥 판매 도구다.
    """
    ds = generate("normal")
    rx, _ = prescribe(ds.profile, ds.days)

    assert rx.items == [], f"건강한 개에게 {cart_ids(rx)}가 처방됐다"
    assert not any(a.active for a in rx.axes)
    assert rx.food_grams > 0


def test_모든_처방에_근거가_남는다():
    for scenario in ("skin", "joint", "normal", "acute"):
        ds = generate(scenario)
        rx, _ = prescribe(ds.profile, ds.days)

        assert rx.trace, f"[{scenario}] 근거 로그가 비어 있다"
        for item in rx.items:
            assert item.reason, f"[{scenario}] {item.name}에 처방 사유가 없다"


# ---------------------------------------------------------------------------
# 추론 규칙
# ---------------------------------------------------------------------------

def test_소화축은_처방권한이_없다():
    """근거가 약하고 피부축과 교차 오염되므로 참고 지표로만 쓴다."""
    for scenario in ("skin", "joint", "normal", "acute"):
        ds = generate(scenario)
        rx, _ = prescribe(ds.profile, ds.days)

        digest = next(a for a in rx.axes if a.axis is HealthAxis.DIGEST)
        assert not digest.active, f"[{scenario}] 소화축이 처방을 발생시켰다"


def test_수면축은_원인축이_있으면_억제된다(skin_case):
    """
    가려워서 잠을 못 자는 것이라면 진정제가 아니라 피부를 치료해야 한다.
    수면축 점수가 임계를 넘어도 피부축이 처방 중이면 나가지 않는다.
    """
    rx, _ = prescribe(skin_case.profile, skin_case.days)

    skin = next(a for a in rx.axes if a.axis is HealthAxis.SKIN)
    sleep = next(a for a in rx.axes if a.axis is HealthAxis.SLEEP)

    assert skin.active
    assert not sleep.active
    assert "calm" not in cart_ids(rx)


def test_장건강은_문진으로만_처방된다(skin_case):
    """센서가 아니라 항생제 복용·설사 보고 같은 문진 항목이 트리거다."""
    plain, _ = prescribe(skin_case.profile, skin_case.days)
    assert "gut" not in cart_ids(plain)

    on_abx = with_profile(skin_case, medications=[Medication.ANTIBIOTIC])
    rx, _ = prescribe(on_abx, skin_case.days)
    assert "gut" in cart_ids(rx)
    assert "항생제" in next(i.reason for i in rx.items if i.cartridge_id == "gut")

    reported = with_profile(skin_case, gi_symptom_reported=True)
    rx2, _ = prescribe(reported, skin_case.days)
    assert "gut" in cart_ids(rx2)


def test_오메가3는_두_축이_요구해도_합산하지_않는다():
    """
    피부축과 관절축이 동시에 발화하면 오메가3를 양쪽에서 요구한다.
    합산하면 두 배가 되므로 max()로 결합해야 한다.
    """
    from core.constants import AXIS_DOSE

    skin_dose = AXIS_DOSE[HealthAxis.SKIN]["omega3"]
    joint_dose = AXIS_DOSE[HealthAxis.JOINT]["omega3"]

    ds = generate("skin")
    rx, _ = prescribe(ds.profile, ds.days)
    pellets = next(i.pellets for i in rx.items if i.cartridge_id == "omega3")

    per = CARTRIDGE_BY_ID["omega3"].nutrients_per_pellet[Nutrient.EPA_DHA]
    max_possible = max(skin_dose, joint_dose) * ds.profile.weight_kg / per

    assert pellets <= max_possible + 1, "두 축의 요구가 합산된 것으로 보인다"


# ---------------------------------------------------------------------------
# 사료량
# ---------------------------------------------------------------------------

def test_DER은_RER_아래로_내려가지_않는다():
    """
    RER은 가만히 있어도 필요한 최소 에너지다.

    der_kcal은 표시용으로 소수점 1자리에서 반올림되므로 그만큼만 허용한다.
    (0.05 kcal는 물리적으로 무의미하다)
    """
    for scenario in ("skin", "joint", "normal", "acute"):
        ds = generate(scenario)
        rx, _ = prescribe(ds.profile, ds.days)

        assert rx.der_kcal >= ds.profile.rer - 0.05, (
            f"[{scenario}] DER {rx.der_kcal:.1f} < RER {ds.profile.rer:.1f}"
        )


def test_급여량_일일_변동이_제한된다():
    """급여량이 하루 만에 크게 흔들리면 위장에 부담이 된다."""
    ds = generate("joint")
    first, state = prescribe(ds.profile, ds.days)

    # 전일 급여량을 절반으로 조작해도 10% 이상 못 올라간다
    state.prev_food_grams = first.food_grams // 2
    second, _ = prescribe(ds.profile, ds.days, state)

    assert second.food_grams <= state.prev_food_grams * 1.10 + 1


def test_목표체중보다_무거우면_계수가_내려간다():
    """변화율이 작아도 목표 대비 편차가 크면 감량이 시작되어야 한다."""
    ds = generate("joint")          # 목표 25kg, 실제 28.5kg
    rx, _ = prescribe(ds.profile, ds.days)

    trace = " ".join(t.detail for t in rx.trace)
    assert "목표 대비" in trace


# ---------------------------------------------------------------------------
# 끼니 배분
# ---------------------------------------------------------------------------

def test_길항성분은_다른_끼니로_분리된다(skin_case):
    """식이섬유가 아연 흡수를 방해하므로 장건강과 피부장벽을 떼어놓는다."""
    profile = with_profile(skin_case, medications=[Medication.ANTIBIOTIC])
    rx, _ = prescribe(profile, skin_case.days)

    slots = {i.cartridge_id: i.meal_slot for i in rx.items}
    if "gut" in slots and "skin_barrier" in slots:
        assert slots["gut"] is not slots["skin_barrier"]


def test_1끼_급여시_흡수보정이_적용된다(skin_case):
    """끼니가 하나면 길항 성분을 분리할 수 없으므로 용량을 올려 보정한다."""
    profile = with_profile(
        skin_case, meals_per_day=1, medications=[Medication.ANTIBIOTIC]
    )
    rx, _ = prescribe(profile, skin_case.days)

    assert all(i.meal_slot is MealSlot.MORNING for i in rx.items)
    trace = " ".join(t.step for t in rx.trace)
    assert "1끼 흡수 보정" in trace


# ---------------------------------------------------------------------------
# 프로필 반영
# ---------------------------------------------------------------------------

def test_견종을_몰라도_동작한다():
    """잡종·유기견 입양 케이스. 견종은 선택 항목이다."""
    ds = generate("joint")
    assert ds.profile.is_mixed

    rx, _ = prescribe(ds.profile, ds.days)
    assert rx.food_grams > 0
    assert any(a.active for a in rx.axes)


def test_관절수술이력이_기준선을_낮춘다(skin_case):
    """
    십자인대 수술한 개는 원래 덜 뛴다.
    이걸 모르면 '관절 악화 중'으로 오판해 영양제를 계속 늘린다.
    """
    plain = with_profile(skin_case, surgeries=[])
    operated = with_profile(
        skin_case,
        surgeries=[Surgery(type=SurgeryType.CRUCIATE, years_ago=2.0)],
    )

    rx_plain, _ = prescribe(plain, skin_case.days)
    rx_op, _ = prescribe(operated, skin_case.days)

    z_plain = next(a.z_score for a in rx_plain.axes if a.axis is HealthAxis.JOINT)
    z_op = next(a.z_score for a in rx_op.axes if a.axis is HealthAxis.JOINT)

    assert z_op < z_plain, "수술 이력이 관절축 기준선을 낮추지 않았다"
