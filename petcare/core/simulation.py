"""
14일 전향 시뮬레이션.

**Safety 와 역할이 다르다. 겹치지 않는다.**

    Safety       오늘 이 계획을 실행해도 안전한가?
                 알러지 · 금기 · 사료 기여분 · 영양소 상한 · 정수화 후 재검증

    Simulation   이 계획을 2주 유지하면 목표 방향과 맞는가?
                 에너지 수지 · 체중 궤적 · 누적 노출 · 계획 안정성

Safety 는 `safety.py` 가 단독으로 책임진다. 여기서 같은 검사를 반복하지 않는다.
안전 로직이 두 벌이 되면 언젠가 갈라지고, 어느 쪽이 진짜인지 아무도 모르게 된다.

**가짜 정밀 예측은 하지 않는다.**
"14일 뒤 4.213kg" 같은 숫자는 근거가 없다. 개의 대사는 활동·기온·컨디션에
따라 크게 흔들리고, 우리가 가진 건 행동 데이터뿐이다.
방향(TOWARD_TARGET / AWAY_FROM_TARGET / STABLE)만 말한다.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from core.constants import (
    CARTRIDGE_BY_ID,
    SAFETY_MARGIN,
    UPPER_LIMIT_PER_KG,
)
from core.energy import target_weight
from core.models import DogProfile

#: 기본 지평. 영양제 효과 발현(onset_weeks)이 대개 1~9주라
#: 2주는 "방향이 맞는지" 보기에 적당하고, 그 이상은 불확실성이 너무 커진다.
HORIZON_DAYS = 14

#: 지방 1kg 을 태우거나 쌓는 데 드는 열량(kcal). 개 기준 통용값.
KCAL_PER_KG = 7000.0

#: 체중 목표 대비 이 비율 안이면 '도달'로 본다
TARGET_BAND = 0.03

#: 하루 에너지 수지가 DER 의 이 비율을 넘으면 과하다고 본다
DEFICIT_MILD = 0.10
DEFICIT_STRONG = 0.25


class Direction(str, Enum):
    TOWARD_TARGET = "TOWARD_TARGET"
    AWAY_FROM_TARGET = "AWAY_FROM_TARGET"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class Energy(str, Enum):
    BALANCED = "BALANCED"
    MILD_DEFICIT = "MILD_DEFICIT"
    STRONG_DEFICIT = "STRONG_DEFICIT"
    MILD_SURPLUS = "MILD_SURPLUS"
    STRONG_SURPLUS = "STRONG_SURPLUS"


class Exposure(str, Enum):
    ACCEPTABLE = "ACCEPTABLE"
    NEAR_LIMIT = "NEAR_LIMIT"
    OVER_LIMIT = "OVER_LIMIT"


class Stability(str, Enum):
    STABLE = "STABLE"
    SWINGING = "SWINGING"


class NutrientExposure(BaseModel):
    nutrient: str
    daily_mg: float
    limit_mg: float
    ratio: float
    verdict: Exposure


class SimulationResult(BaseModel):
    """
    14일 뒤 어디로 가는가. 숫자가 아니라 방향이다.

    passed=False 면 이 계획을 실제로 주지 않고 후보를 다시 만든다.
    """

    horizon_days: int = HORIZON_DAYS
    energy: Energy
    weight: Direction
    exposure: Exposure
    stability: Stability

    projected_kg: float | None = None
    target_kg: float | None = None
    daily_balance_kcal: float = 0.0
    food_change_pct: float = 0.0
    nutrients: list[NutrientExposure] = Field(default_factory=list)

    passed: bool = True
    reasons: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"에너지 {self.energy.value} · 체중 {self.weight.value} · "
            f"노출 {self.exposure.value} · 안정성 {self.stability.value}"
        )


def _energy_verdict(balance: float, der: float) -> Energy:
    if der <= 0:
        return Energy.BALANCED
    r = balance / der
    if r <= -DEFICIT_STRONG:
        return Energy.STRONG_DEFICIT
    if r <= -DEFICIT_MILD:
        return Energy.MILD_DEFICIT
    if r >= DEFICIT_STRONG:
        return Energy.STRONG_SURPLUS
    if r >= DEFICIT_MILD:
        return Energy.MILD_SURPLUS
    return Energy.BALANCED


def _weight_verdict(
    current: float | None, projected: float | None, target: float | None
) -> Direction:
    if current is None or projected is None or target is None:
        return Direction.UNKNOWN

    now_gap = abs(current - target)
    then_gap = abs(projected - target)

    # 이미 목표 근처면 유지가 정답이다
    if now_gap / target <= TARGET_BAND and then_gap / target <= TARGET_BAND:
        return Direction.STABLE
    if then_gap < now_gap - 0.005:
        return Direction.TOWARD_TARGET
    if then_gap > now_gap + 0.005:
        return Direction.AWAY_FROM_TARGET
    return Direction.STABLE


def _exposure(
    profile: DogProfile, items: list[dict], horizon: int
) -> tuple[Exposure, list[NutrientExposure]]:
    """
    누적 노출.

    Safety 는 '하루 상한'을 본다. 여기서는 그 상한 대비 어디쯤에서
    2주를 보내게 되는지를 본다. 상한의 80%를 계속 쓰는 계획은
    오늘은 통과해도 길게는 여유가 없다.
    """
    daily: dict[str, float] = {}
    for it in items:
        cart = CARTRIDGE_BY_ID.get(it["cartridge_id"])
        if cart is None:
            continue
        for nutrient, per in cart.nutrients_per_pellet.items():
            daily[nutrient] = daily.get(nutrient, 0.0) + per * it["pellets"]

    out: list[NutrientExposure] = []
    worst = Exposure.ACCEPTABLE

    for nutrient, amount in daily.items():
        cap = UPPER_LIMIT_PER_KG.get(nutrient)
        if cap is None:
            continue
        limit = cap * profile.weight_kg * SAFETY_MARGIN
        ratio = amount / limit if limit > 0 else 0.0

        if ratio > 1.0:
            verdict = Exposure.OVER_LIMIT
        elif ratio >= 0.80:
            verdict = Exposure.NEAR_LIMIT
        else:
            verdict = Exposure.ACCEPTABLE

        if verdict is Exposure.OVER_LIMIT:
            worst = Exposure.OVER_LIMIT
        elif verdict is Exposure.NEAR_LIMIT and worst is Exposure.ACCEPTABLE:
            worst = Exposure.NEAR_LIMIT

        out.append(NutrientExposure(
            nutrient=nutrient,
            daily_mg=round(amount, 2),
            limit_mg=round(limit, 2),
            ratio=round(ratio, 3),
            verdict=verdict,
        ))

    return worst, sorted(out, key=lambda e: -e.ratio)


def run(
    profile: DogProfile,
    der_kcal: float,
    food_grams: int,
    items: list[dict],
    *,
    current_kg: float | None = None,
    intake_ratio: float = 1.0,
    prev_food_grams: int | None = None,
    horizon: int = HORIZON_DAYS,
) -> SimulationResult:
    """
    이 계획을 horizon 일 유지하면 어디로 가는가.

    intake_ratio 가 핵심이다. 100g을 줘도 70%만 먹으면 실제로는 70g이다.
    처방량으로만 계산하면 '충분히 주고 있는데 왜 빠지지'가 된다.
    """
    weight_now = current_kg if current_kg is not None else profile.weight_kg
    target = target_weight(profile)

    # 실제로 들어가는 열량
    kcal_in = der_kcal * max(0.0, min(1.2, intake_ratio))
    balance = kcal_in - der_kcal

    projected = round(weight_now + (balance * horizon) / KCAL_PER_KG, 2)

    energy = _energy_verdict(balance, der_kcal)
    weight_dir = _weight_verdict(weight_now, projected, target)
    exposure, nutrients = _exposure(profile, items, horizon)

    change_pct = 0.0
    if prev_food_grams:
        change_pct = round((food_grams - prev_food_grams) / prev_food_grams * 100, 1)
    stability = (
        Stability.SWINGING if abs(change_pct) > 10.0 else Stability.STABLE
    )

    reasons: list[str] = []
    passed = True

    if exposure is Exposure.OVER_LIMIT:
        passed = False
        reasons.append("영양소 상한을 넘는다 — 계획을 다시 만들어야 한다")
    elif exposure is Exposure.NEAR_LIMIT:
        reasons.append("상한의 80%를 계속 쓰게 된다 — 여유가 없다")

    if energy is Energy.STRONG_DEFICIT:
        passed = False
        reasons.append(
            f"2주간 하루 {abs(balance):.0f}kcal 부족 — 체중이 계속 빠진다"
        )
    elif energy is Energy.STRONG_SURPLUS:
        passed = False
        reasons.append(f"2주간 하루 {balance:.0f}kcal 과잉 — 체중이 계속 는다")

    if weight_dir is Direction.AWAY_FROM_TARGET:
        reasons.append(
            f"목표 {target:.1f}kg 에서 멀어진다 ({weight_now:.1f} → {projected:.1f})"
        )

    if stability is Stability.SWINGING:
        reasons.append(f"전일 대비 급여량이 {change_pct:+.0f}% 흔들린다")

    if not reasons:
        reasons.append("2주간 목표 방향과 맞는다")

    return SimulationResult(
        horizon_days=horizon,
        energy=energy,
        weight=weight_dir,
        exposure=exposure,
        stability=stability,
        projected_kg=projected,
        target_kg=round(target, 2),
        daily_balance_kcal=round(balance, 1),
        food_change_pct=change_pct,
        nutrients=nutrients,
        passed=passed,
        reasons=reasons,
    )
