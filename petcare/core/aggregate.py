"""
이벤트 -> 하루치 요약.

목줄이 올린 행동 이벤트를 알고리즘의 입력 단위인 DailySummary로 접는다.

이 파일이 중요한 이유: mock 생성기와 실제 센서가 **같은 집계 코드**를 탄다.
생성기 안에 집계 로직이 따로 있으면 가짜 데이터로 아무리 검증해도
실제 파이프라인이 검증되지 않는다.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from core.constants import (
    CONFIDENCE_GATE,
    NIGHT_HOURS,
    SLEEP_HOURS,
    STEPS_PER_WALK_SEC,
)
from core.models import (
    BehaviorEvent,
    BehaviorType,
    DailySummary,
    DaySummary,
    HourlyBins,
    SleepSummary,
)

# 하루 수면 시간의 상·하한. 개는 보통 9시간 안팎 잔다.
SLEEP_WINDOW_MIN = 540
SLEEP_MIN_FLOOR = 180
# 뒤척임 1회를 각성 몇 분으로 칠지
RESTLESS_TO_AWAKE_MIN = 1.5
# 한 시간에 이 초 이상 움직였으면 '야간 각성' 1회로 센다
NIGHT_WAKE_SEC = 60


class Bins:
    """시간대별 누적. 24칸씩."""

    def __init__(self) -> None:
        self.activity = [0] * 24
        self.scratch = [0] * 24
        self.shake = [0] * 24
        self.posture = [0] * 24
        self.walk_sec = 0.0
        self.run_sec = 0.0


def bin_events(events: list[BehaviorEvent]) -> tuple[Bins, float]:
    """
    이벤트를 시간대별로 접는다.

    confidence가 낮은 이벤트는 버린다. 버린 비율을 함께 돌려주는데,
    이 값이 높은 날은 분류기가 헤맸다는 뜻이라 그 날 자체를 의심해야 한다.

    반환: (집계, 버린 비율)
    """
    bins = Bins()
    if not events:
        return bins, 0.0

    kept = [e for e in events if e.confidence >= CONFIDENCE_GATE]
    low_ratio = 1.0 - len(kept) / len(events)

    for e in kept:
        h = e.ts.hour
        if e.type is BehaviorType.SCRATCH:
            bins.scratch[h] += 1
        elif e.type is BehaviorType.SHAKE:
            bins.shake[h] += 1
        elif e.type is BehaviorType.POSTURE_CHANGE:
            bins.posture[h] += 1
        elif e.type is BehaviorType.WALK:
            bins.activity[h] += int(e.duration_s)
            bins.walk_sec += e.duration_s
        elif e.type is BehaviorType.RUN:
            bins.activity[h] += int(e.duration_s)
            bins.run_sec += e.duration_s

    return bins, low_ratio


def estimate_sleep(activity: list[int], posture: list[int]) -> SleepSummary:
    """
    수면 시간을 추정한다.

    목줄에 수면 센서는 없다. 밤 시간대(22~07시)에 움직이지 않은 시간을
    잔 것으로 본다. 뒤척임은 완전한 각성은 아니지만 수면의 질을 깎으므로
    1회당 1.5분을 차감한다.
    """
    night_activity_sec = sum(activity[h] for h in SLEEP_HOURS)
    restless = sum(posture[h] for h in SLEEP_HOURS)

    awake_min = night_activity_sec / 60.0 + restless * RESTLESS_TO_AWAKE_MIN
    total = int(np.clip(SLEEP_WINDOW_MIN - awake_min, SLEEP_MIN_FLOOR, SLEEP_WINDOW_MIN))
    wakes = sum(1 for h in SLEEP_HOURS if activity[h] > NIGHT_WAKE_SEC)

    return SleepSummary(
        total_min=total,
        restless_count=restless,
        night_wake_count=wakes,
    )


def build_daily_summary(
    day: date,
    events: list[BehaviorEvent],
    wear_ratio: float,
    *,
    steps: int | None = None,
    weight_kg: float | None = None,
    food_offered_g: int = 0,
    food_eaten_g: int = 0,
) -> DailySummary:
    """
    하루치 이벤트를 알고리즘 입력으로 접는다.

    wear_ratio는 이벤트에서 나오지 않는다. 목줄의 착용 감지 신호가 있어야 한다.
    안 움직인 것과 안 찬 것을 구분하지 못하면 착용률 게이트가 무력해진다.

    steps를 주지 않으면 걷기 시간에서 추정한다. 실기기는 보수계 값을 주는 게
    정확하므로 그쪽을 쓴다.
    """
    bins, low_ratio = bin_events(events)

    if steps is None:
        steps = int(bins.walk_sec * STEPS_PER_WALK_SEC)

    return DailySummary(
        date=day,
        hourly=HourlyBins(
            activity_sec=bins.activity,
            scratch=bins.scratch,
            shake=bins.shake,
            posture_change=bins.posture,
        ),
        sleep=estimate_sleep(bins.activity, bins.posture),
        summary=DaySummary(
            steps=steps,
            walk_sec=int(bins.walk_sec),
            run_sec=int(bins.run_sec),
            # 알고리즘은 초를 쓰고 화면만 분을 쓴다.
            # 분으로 반올림해 저장하면 하루 2~5분 뛰는 개의 신호가 뭉개진다.
            walk_min=int(bins.walk_sec / 60),
            run_min=int(bins.run_sec / 60),
            scratch_total=sum(bins.scratch),
            scratch_night=sum(bins.scratch[h] for h in NIGHT_HOURS),
            shake_total=sum(bins.shake),
        ),
        wear_ratio=round(wear_ratio, 3),
        low_confidence_ratio=round(low_ratio, 3),
        weight_kg=weight_kg,
        food_offered_g=food_offered_g,
        food_eaten_g=food_eaten_g,
    )


def wear_ratio_from_seconds(worn_sec: int, covered_sec: int) -> float:
    """
    착용 초 -> 착용률.

    분모는 '하루 86400초'가 아니라 '상태 표본이 실제로 설명한 초'다.
    기기가 꺼져 있던 시간까지 '안 찼다'로 치면 착용률이 부당하게 낮아지고,
    멀쩡한 날이 통계에서 빠진다.

    표본이 하나도 없으면 판단할 근거가 없다는 뜻이라 0을 돌려준다.
    추측해서 채우는 것보다 그 날을 빼는 게 낫다.
    """
    if covered_sec <= 0:
        return 0.0
    return float(np.clip(worn_sec / covered_sec, 0.0, 1.0))
