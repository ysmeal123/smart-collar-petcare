"""
수의학적 안전 필터.

추론 엔진은 '제안'만 하고, 여기서 룰이 거부권을 행사한다.
AI가 아무리 그럴듯한 이유를 대도 이 파일을 통과하지 못하면 사출되지 않는다.

    Layer 0  긴급 정지   (inference.check_escalation — 다른 모든 것에 우선)
    Layer 1  절대 금기   기저질환·알러지·복용약 -> 성분 차단
    Layer 2  상한 검증   헤드룸 = 상한x0.8 - 사료기여 - 간식예비

계산 순서가 중요하다. 사료량이 활동량에 따라 매일 변하므로,
활동량이 많은 날은 사료에서 오는 영양소도 함께 늘어난다.
이 커플링을 무시하면 상한을 조용히 넘는다.
"""

from __future__ import annotations

from pydantic import BaseModel

from core.constants import (
    BLOCKED_BY_ALLERGY,
    BLOCKED_BY_CONDITION,
    BLOCKED_BY_MEDICATION,
    CARTRIDGE_BY_ID,
    CARTRIDGE_SUBSTITUTE,
    FOOD_BY_ID,
    SAFETY_MARGIN,
    SOFT_LIMIT_BY_MEDICATION,
    TREAT_RESERVE_RATIO,
    UPPER_LIMIT_PER_KG,
    ZINC_COPPER_MAX_RATIO,
    Nutrient,
)
from core.models import DogProfile, TraceStep


class Block(BaseModel):
    cartridge_id: str
    reason: str
    substitute: str | None = None


def resolve_blocks(profile: DogProfile) -> dict[str, Block]:
    """
    Layer 1 — 절대 금기.

    차단된 카트리지에 대체재가 있으면 함께 알려준다.
    가장 중요한 케이스: 생선 알러지 -> 어유 오메가3 차단 -> 조류 오메가3로 대체.
    어유는 우리 핵심 성분이라, 대체가 없으면 피부·관절 처방이 통째로 막힌다.
    """
    blocks: dict[str, Block] = {}

    def add(cart_id: str, reason: str) -> None:
        if cart_id in blocks:
            return
        alt = CARTRIDGE_SUBSTITUTE.get(cart_id)
        # 대체재도 같은 이유로 막히면 대체가 아니다
        if alt and _is_blocked_raw(alt, profile):
            alt = None
        blocks[cart_id] = Block(cartridge_id=cart_id, reason=reason, substitute=alt)

    for cond in profile.conditions:
        for cid in BLOCKED_BY_CONDITION.get(cond, []):
            add(cid, f"기저질환({cond.value})")

    for med in profile.medications:
        for cid in BLOCKED_BY_MEDICATION.get(med, []):
            add(cid, f"복용약({med.value})")

    for allergen in profile.allergies:
        for cid in BLOCKED_BY_ALLERGY.get(allergen, []):
            add(cid, f"알러지({allergen.value})")

    return blocks


def _is_blocked_raw(cart_id: str, profile: DogProfile) -> bool:
    """대체재 판정용. 재귀를 피하려고 대체 로직 없이 확인만 한다."""
    for cond in profile.conditions:
        if cart_id in BLOCKED_BY_CONDITION.get(cond, []):
            return True
    for med in profile.medications:
        if cart_id in BLOCKED_BY_MEDICATION.get(med, []):
            return True
    for allergen in profile.allergies:
        if cart_id in BLOCKED_BY_ALLERGY.get(allergen, []):
            return True
    return False


def soft_limits(profile: DogProfile) -> dict[str, float]:
    """차단까지는 아니고 용량만 조이는 경우 (예: NSAID + 오메가3)."""
    limits: dict[str, float] = {}
    for med in profile.medications:
        for cid, factor in SOFT_LIMIT_BY_MEDICATION.get(med, {}).items():
            limits[cid] = min(limits.get(cid, 1.0), factor)
    return limits


def food_contribution(food_id: str, grams: float) -> dict[str, float]:
    """오늘 사료에서 이미 들어오는 영양소."""
    food = FOOD_BY_ID[food_id]
    return {k: v * grams for k, v in food.nutrients_per_g.items()}


def headroom(profile: DogProfile, food_grams: float) -> dict[str, float]:
    """
    Layer 2 — 오늘 영양제로 더 넣을 수 있는 여유분.

        헤드룸 = 상한 x 0.8 - 사료기여 - 간식예비

    30일 누적 상한 검사는 두지 않았다. 대신 상한 자체를 80%로 조여
    안전계수 하나로 대체했다(서브시스템 하나 -> 상수 하나).
    간식은 측정할 방법이 없으므로 DER의 10%만큼 미리 비워둔다.
    """
    from_food = food_contribution(profile.food_id, food_grams)
    # 간식도 사료와 비슷한 조성이라고 보수적으로 가정한다
    from_treats = food_contribution(profile.food_id, food_grams * TREAT_RESERVE_RATIO)

    room: dict[str, float] = {}
    for nutrient, per_kg in UPPER_LIMIT_PER_KG.items():
        limit = per_kg * profile.weight_kg * SAFETY_MARGIN
        room[nutrient] = max(
            0.0, limit - from_food.get(nutrient, 0.0) - from_treats.get(nutrient, 0.0)
        )
    return room


def totals_from_pellets(pellets: dict[str, int]) -> dict[str, float]:
    """알갱이 수 -> 영양소 총량."""
    totals: dict[str, float] = {}
    for cart_id, n in pellets.items():
        for nutrient, per in CARTRIDGE_BY_ID[cart_id].nutrients_per_pellet.items():
            totals[nutrient] = totals.get(nutrient, 0.0) + per * n
    return totals


def enforce_upper_limits(
    pellets: dict[str, int], profile: DogProfile, food_grams: float,
    trace: list[TraceStep],
) -> dict[str, int]:
    """
    상한을 넘는 알갱이를 줄인다.

    mg 단위가 아니라 '알갱이 수'로 검증하는 이유:
    반올림해서 정수로 만든 뒤에 다시 상한을 넘을 수 있기 때문이다.
    최종 사출량 그대로 검증해야 빈틈이 없다.
    """
    room = headroom(profile, food_grams)
    result = dict(pellets)

    for nutrient, limit in room.items():
        # 이 영양소를 담고 있는 카트리지들
        holders = [
            cid for cid in result
            if nutrient in CARTRIDGE_BY_ID[cid].nutrients_per_pellet
        ]
        if not holders:
            continue

        guard = 0
        while totals_from_pellets(result).get(nutrient, 0.0) > limit and guard < 200:
            # 해당 영양소를 가장 많이 담은 카트리지부터 한 알씩 줄인다
            target = max(
                (c for c in holders if result[c] > 0),
                key=lambda c: CARTRIDGE_BY_ID[c].nutrients_per_pellet[nutrient],
                default=None,
            )
            if target is None:
                break
            result[target] -= 1
            guard += 1

        if guard:
            total = totals_from_pellets(result).get(nutrient, 0.0)
            trace.append(TraceStep(
                step="상한 제한",
                detail=f"{nutrient} 헤드룸 {limit:.1f} 초과 → {guard}알 감량 "
                       f"(최종 {total:.1f})",
                changed=True,
            ))

    return {k: v for k, v in result.items() if v > 0}


def check_zinc_copper(pellets: dict[str, int], trace: list[TraceStep]) -> dict[str, int]:
    """
    아연을 장기 고용량으로 주면 구리가 결핍된다.

    우리 카트리지에는 구리가 없으므로 사료에서 오는 양이 전부다.
    비율이 한계를 넘으면 아연을 줄인다.
    """
    totals = totals_from_pellets(pellets)
    zinc = totals.get(Nutrient.ZINC, 0.0)
    if zinc <= 0 or "skin_barrier" not in pellets:
        return pellets

    per_pellet = CARTRIDGE_BY_ID["skin_barrier"].nutrients_per_pellet[Nutrient.ZINC]
    max_zinc = ZINC_COPPER_MAX_RATIO * 1.0   # 사료 유래 구리 1mg 기준(보수적)

    if zinc > max_zinc:
        allowed = int(max_zinc // per_pellet)
        if allowed < pellets["skin_barrier"]:
            trace.append(TraceStep(
                step="아연·구리 비율",
                detail=f"아연 {zinc:.1f}mg → {allowed * per_pellet:.1f}mg로 감량 "
                       f"(장기 고용량 시 구리 결핍 유발)",
                changed=True,
            ))
            pellets = {**pellets, "skin_barrier": allowed}

    return {k: v for k, v in pellets.items() if v > 0}
