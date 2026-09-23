"""
사출 — mg를 알갱이로 바꾸고 끼니에 배분한다.

디스펜서는 0.5알을 뱉지 못한다. 여기서 연속량이 정수로 떨어진다.
알갱이 1개당 함량을 '3kg 소형견이 하루 1~2알'이 되도록 설계했기 때문에,
단순 반올림만으로도 오차가 허용 범위에 들어온다(잔차 이월 로직 불필요).
"""

from __future__ import annotations

from core.constants import (
    ANTAGONIST_PAIRS,
    AXIS_DOSE_NUTRIENT,
    CARTRIDGE_BY_ID,
    SINGLE_MEAL_ABSORPTION_PENALTY,
)
from core.models import DispenseItem, MealSlot, TraceStep


def to_pellets(cart_id: str, target_mg: float) -> int:
    """목표 용량 -> 알갱이 수. 기계적 상한도 여기서 건다."""
    cart = CARTRIDGE_BY_ID[cart_id]
    nutrient = AXIS_DOSE_NUTRIENT[cart_id]
    per = cart.nutrients_per_pellet[nutrient]

    n = int(round(target_mg / per))
    return max(0, min(n, cart.max_pellets_per_day))


def apply_single_meal_penalty(
    targets: dict[str, float], meals_per_day: int, trace: list[TraceStep]
) -> dict[str, float]:
    """
    1끼만 먹는 개는 길항 성분을 다른 끼니로 분리할 수 없다.

    같이 들어가면 흡수가 떨어지므로 그만큼 용량을 올려 보정한다.
    (식이섬유가 아연 흡수를 방해하는 것이 대표적)
    """
    if meals_per_day > 1:
        return targets

    result = dict(targets)
    for a, b, reason in ANTAGONIST_PAIRS:
        if a in result and b in result:
            boost = 1.0 / (1.0 - SINGLE_MEAL_ABSORPTION_PENALTY)
            result[b] *= boost
            trace.append(TraceStep(
                step="1끼 흡수 보정",
                detail=f"{CARTRIDGE_BY_ID[b].name} 용량 {boost:.0%}로 상향 — "
                       f"{reason}인데 1끼라 분리 불가",
                changed=True,
            ))
    return result


def assign_meals(
    pellets: dict[str, int], meals_per_day: int, trace: list[TraceStep]
) -> list[DispenseItem]:
    """
    끼니 배분.

    디스펜서만 할 수 있는 일이다. 알약 요법으로는 불가능한
    '길항 성분을 다른 끼니로 분리'가 여기서 이뤄진다.
    """
    items: list[DispenseItem] = []

    for cart_id, n in pellets.items():
        cart = CARTRIDGE_BY_ID[cart_id]
        slot = cart.meal_slot if meals_per_day > 1 else MealSlot.MORNING
        items.append(DispenseItem(
            cartridge_id=cart_id,
            name=cart.name,
            color=cart.color,
            pellets=n,
            meal_slot=slot,
            reason="",
        ))

    if meals_per_day > 1:
        for a, b, reason in ANTAGONIST_PAIRS:
            if a in pellets and b in pellets:
                sa = CARTRIDGE_BY_ID[a].meal_slot
                sb = CARTRIDGE_BY_ID[b].meal_slot
                if sa is not sb:
                    trace.append(TraceStep(
                        step="길항 분리",
                        detail=f"{CARTRIDGE_BY_ID[a].name}({sa.value}) / "
                               f"{CARTRIDGE_BY_ID[b].name}({sb.value}) — {reason}",
                    ))

    return items
