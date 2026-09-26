"""
체중 측정 정제.

체중은 급여량 캐스케이드 제어의 **외부 루프 입력**이다.
이 값이 오염되면 활동계수가 잘못 교정되고, 그 오차가 몇 달에 걸쳐 누적돼
개를 비만이나 저체중으로 몰고 간다. 그래서 의심스러운 값은 채우지 말고 버린다.

소스가 둘이고 성격이 다르다.

    급식판 체중계   자동이지만 잡음투성이다.
                   부분적으로 올라가거나, 계속 움직이거나,
                   다른 개나 사람이 올라갈 수 있다.

    보호자 입력     정확하지만 드물고 불규칙하다.
                   대신 오타 가능성이 있다(5.8 -> 58).

둘 다 결측이 기본이다. 외부 루프는 8회 이상 측정됐을 때만 작동한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

# 세션 하나로 인정할 최소 표본 수.
# 개가 스쳐 지나간 것과 올라선 것을 구분한다.
MIN_SAMPLES = 5

# 세션 안에서 값이 이 이상 흔들리면 버린다.
# 개가 가만히 서 있지 않았다는 뜻이라 어떤 값을 골라도 믿을 수 없다.
MAX_SESSION_CV = 0.04

# 직전 신뢰값 대비 이 이상 벗어나면 버린다.
# 5kg 개가 갑자기 8kg으로 찍히면 다른 개가 올라간 것이다.
MAX_JUMP_RATIO = 0.15

# 물리적으로 가능한 범위. 오타와 로드셀 고장을 거른다.
PLAUSIBLE_KG = (0.5, 120.0)

# 보호자 입력은 더 넓게 본다. 실제로 몇 달 만에 입력할 수 있다.
MANUAL_JUMP_RATIO = 0.40


@dataclass
class ScaleSession:
    """개가 체중계에 한 번 올라간 동안 찍힌 표본들."""

    started_at: datetime
    samples_kg: list[float]


@dataclass
class WeightResult:
    kg: float | None
    source: str
    reason: str


def filter_session(session: ScaleSession) -> float | None:
    """
    세션 하나 -> 대표값 하나.

    평균이 아니라 중앙값을 쓴다. 개가 발을 뗐다 붙였다 하면
    극단값이 섞이는데 평균은 그걸 그대로 먹는다.
    """
    vals = [v for v in session.samples_kg if PLAUSIBLE_KG[0] <= v <= PLAUSIBLE_KG[1]]
    if len(vals) < MIN_SAMPLES:
        return None

    arr = np.asarray(vals, dtype=float)
    med = float(np.median(arr))
    if med <= 0:
        return None

    # 변동계수 - 개가 가만히 있었는지 본다
    cv = float(np.std(arr)) / med
    if cv > MAX_SESSION_CV:
        return None

    return round(med, 2)


def daily_weight(
    sessions: list[ScaleSession],
    manual_kg: float | None,
    last_known_kg: float | None,
) -> WeightResult:
    """
    하루치 측정 -> 그날의 체중 하나.

    보호자 입력이 있으면 그것을 쓴다. 사람이 직접 잰 값이 체중계 추정보다 낫다.
    다만 오타는 걸러야 하므로 직전 값 대비 타당성은 본다.

    last_known_kg 는 직전에 채택된 체중이다. 첫 측정이면 None이고,
    그때는 비교할 기준이 없으므로 타당성 검사를 건너뛴다.
    """
    if manual_kg is not None:
        if not (PLAUSIBLE_KG[0] <= manual_kg <= PLAUSIBLE_KG[1]):
            return WeightResult(None, "manual", "물리적으로 불가능한 값")
        if last_known_kg and _jump(manual_kg, last_known_kg) > MANUAL_JUMP_RATIO:
            # 버리지 않고 남기되 채택하지 않는다. 오타일 수도, 진짜일 수도 있다.
            return WeightResult(
                None, "manual",
                f"직전 {last_known_kg}kg 대비 변화가 커서 보류 (입력 {manual_kg}kg)",
            )
        return WeightResult(round(manual_kg, 2), "manual", "보호자 입력")

    picked = [w for w in (filter_session(s) for s in sessions) if w is not None]
    if not picked:
        return WeightResult(None, "scale", "유효한 세션 없음")

    # 하루에 여러 번 올라갔으면 중앙값. 한 번 잘못 찍힌 세션을 흡수한다.
    kg = round(float(np.median(picked)), 2)

    if last_known_kg and _jump(kg, last_known_kg) > MAX_JUMP_RATIO:
        return WeightResult(
            None, "scale",
            f"직전 {last_known_kg}kg 대비 {_jump(kg, last_known_kg):.0%} 변화 — 다른 개체로 의심",
        )

    return WeightResult(kg, "scale", f"세션 {len(picked)}건의 중앙값")


def _jump(a: float, b: float) -> float:
    return abs(a - b) / b if b else 0.0
