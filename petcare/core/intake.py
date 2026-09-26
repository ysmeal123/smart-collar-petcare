"""
실제 섭취량.

밥그릇 아래 로드셀이 잰다. 배식 직후와 식사 종료 후의 차이가 먹은 양이다.

    배식량 100g  →  식후 잔량 18g  →  먹은 양 82g  →  섭취율 82%

**이 값이 왜 중요한가.**
처방한 양과 실제 먹은 양은 다르다. 이 차이를 모르면

    - 밥을 안 먹는데 영양제를 늘리는 일이 생긴다
    - 체중이 빠지는 이유를 '활동량 증가'로 잘못 읽는다
    - 긴급 정지 규칙(3일 연속 섭취 70% 미만)이 영영 발동하지 않는다

목줄이 Energy OUT을 재고, 밥통이 Energy IN을 잰다.
둘이 있어야 에너지 수지가 닫힌다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# 로드셀 잡음. 이보다 작은 변화는 측정 오차로 본다.
NOISE_G = 3.0

# 식사로 인정할 최소 배식량. 이보다 적으면 간식이나 오측정이다.
MIN_MEAL_G = 5.0

# 섭취율이 이 값을 넘으면 1.0으로 자른다.
# 그릇에 남아 있던 걸 같이 먹었거나 로드셀이 흔들린 경우다.
MAX_RATIO = 1.0


@dataclass
class MealReading:
    """식사 한 번. 밥통이 배식 직후와 종료 후를 재서 올린다."""

    started_at: datetime
    ended_at: datetime | None
    dispensed_g: float
    leftover_g: float | None

    @property
    def duration_min(self) -> float | None:
        if self.ended_at is None:
            return None
        return round((self.ended_at - self.started_at).total_seconds() / 60.0, 1)


@dataclass
class MealResult:
    eaten_g: int
    offered_g: int
    ratio: float
    duration_min: float | None
    skipped: bool
    note: str


def evaluate(reading: MealReading) -> MealResult:
    """
    한 끼를 판정한다.

    잔량을 못 쟀으면(개가 그릇을 건드렸거나 로드셀 오류) 섭취량을 추정하지
    않는다. 모르는 값을 채우면 에너지 수지가 조용히 틀어진다.
    """
    offered = int(round(reading.dispensed_g))

    if reading.dispensed_g < MIN_MEAL_G:
        return MealResult(0, offered, 1.0, None, False, "배식량이 너무 적다 — 무시")

    if reading.leftover_g is None:
        return MealResult(0, offered, 1.0, reading.duration_min, False,
                          "잔량 측정 실패 — 섭취량을 추정하지 않는다")

    eaten = reading.dispensed_g - reading.leftover_g

    # 로드셀 잡음 범위면 다 먹은 것으로 본다
    if abs(reading.leftover_g) <= NOISE_G:
        eaten = reading.dispensed_g

    eaten = max(0.0, eaten)
    ratio = min(MAX_RATIO, eaten / reading.dispensed_g)

    # 거의 손도 안 댔다
    skipped = ratio < 0.10

    note = "완식" if ratio >= 0.95 else (
        "거의 먹지 않음" if skipped else f"{ratio:.0%} 섭취"
    )
    return MealResult(
        eaten_g=int(round(eaten)),
        offered_g=offered,
        ratio=round(ratio, 3),
        duration_min=reading.duration_min,
        skipped=skipped,
        note=note,
    )


def daily_intake(meals: list[MealResult]) -> tuple[int, int]:
    """
    하루치 (준 양, 먹은 양).

    잔량을 못 잰 끼니는 양쪽에서 모두 뺀다. 준 것만 세고 먹은 것을 0으로 두면
    '굶었다'로 오판해 긴급 정지가 잘못 발동한다.
    """
    usable = [m for m in meals if "실패" not in m.note and m.offered_g >= MIN_MEAL_G]
    if not usable:
        return 0, 0
    return sum(m.offered_g for m in usable), sum(m.eaten_g for m in usable)
