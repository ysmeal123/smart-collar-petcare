"""
Mock 데이터 생성기.

실제 목줄이 아직 없으므로, 진짜처럼 보이는 행동 데이터를 만들어
알고리즘을 개발/검증한다. 나중에 실물이 생기면 이 파일만 빼고
그 자리에 실제 수집 파이프라인을 꽂으면 된다.

핵심: 단순 난수가 아니라 "시간대별 강도함수"를 가진 생성 모델을 쓴다.

    λ(t) = 기저강도 x 일주기리듬(t) x 병태변조(t, 시나리오) x 개체편차

    난수를 그냥 뿌리면 시간 구조가 없어서
    "야간 긁기", "식후 몸털기" 같은 우리 추론 축을 전혀 검증하지 못한다.

기간은 44일이다.
    Day  1~30  baseline 구간 (정상)
    Day 31~37  관찰 1주차
    Day 38~44  관찰 2주차                <- 앱에 보여줄 1주일
    알고리즘이 직전 30일 baseline을 쓰므로 7일치만으로는 z-score를 못 만든다.
    또 '2주 연속' 규칙을 검증하려면 관찰 구간이 2주 있어야 한다.

실행:
    cd petcare
    python -m mock.generator
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from core.constants import (
    AXIS_PERSISTENCE,
    AXIS_SUPPRESSED_BY,
    AXIS_WEIGHTS,
    AXIS_WINDOW_DAYS,
    CONFIDENCE_GATE,
    ESCALATION_RULES,
    OBSERVATION_ONLY_AXES,
    MAD_TO_SIGMA,
    MEAL_HOURS,
    MEDIAN_SE_FACTOR,
    MIN_MAD,
    NIGHT_HOURS,
    Z_ENTER,
)
from core.aggregate import build_daily_summary
from core.models import HealthAxis
from core.models import (
    Allergen,
    BehaviorEvent,
    BehaviorType,
    BodyCondition,
    Condition,
    DailySummary,
    DaySummary,
    DogProfile,
    DogSize,
    HourlyBins,
    Medication,
    SensorDataset,
    Sex,
    SleepSummary,
    Surgery,
    SurgeryType,
)

TOTAL_DAYS = 44
BASELINE_END = 30          # 여기까지가 baseline 구간
OBSERVE_FROM = 31          # 관찰 구간 시작
END_DATE = date(2026, 8, 11)
SLEEP_HOURS = [22, 23, 0, 1, 2, 3, 4, 5, 6]


# ---------------------------------------------------------------------------
# 일주기 리듬 (24시간 상대 강도)
#
# 이 배열이 "개는 언제 무엇을 하는가"를 결정한다.
# 정규화해서 하루 총합이 기저강도와 같아지도록 맞춘다.
# ---------------------------------------------------------------------------

CIRCADIAN: dict[BehaviorType, list[float]] = {
    # 긁기: 저녁~밤에 완만히 증가 (야간 소양감)
    BehaviorType.SCRATCH: [
        0.5, 0.4, 0.3, 0.3, 0.3, 0.4, 0.7, 1.0, 1.1, 0.9, 0.8, 0.8,
        0.9, 0.9, 0.9, 1.0, 1.1, 1.2, 1.3, 1.3, 1.4, 1.5, 1.2, 0.8,
    ],
    # 몸 털기: 식후(8시, 19시)와 기상 직후에 집중
    BehaviorType.SHAKE: [
        0.2, 0.1, 0.1, 0.1, 0.1, 0.2, 0.8, 1.5, 2.0, 1.0, 0.6, 0.5,
        0.6, 0.6, 0.6, 0.7, 0.8, 1.0, 1.8, 2.0, 1.0, 0.6, 0.4, 0.3,
    ],
    # 걷기: 아침/저녁 산책 시간대
    BehaviorType.WALK: [
        0.1, 0.05, 0.05, 0.05, 0.05, 0.1, 0.5, 1.5, 2.0, 1.2, 0.8, 0.7,
        0.8, 0.8, 0.8, 0.9, 1.2, 1.8, 2.2, 1.8, 1.0, 0.6, 0.3, 0.2,
    ],
    # 뛰기: 산책 중 피크에서만
    BehaviorType.RUN: [
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.2, 1.2, 1.8, 0.8, 0.4, 0.3,
        0.4, 0.4, 0.4, 0.5, 0.8, 1.5, 2.0, 1.4, 0.6, 0.3, 0.1, 0.0,
    ],
    # 자세 변경 / 뒤척임: 수면 시간대에 집중
    BehaviorType.POSTURE_CHANGE: [
        1.2, 1.3, 1.2, 1.1, 1.0, 1.0, 0.8, 0.2, 0.1, 0.1, 0.3, 0.3,
        0.4, 0.4, 0.3, 0.3, 0.2, 0.2, 0.2, 0.2, 0.3, 0.6, 1.0, 1.2,
    ],
}

# 건강한 개의 하루 평균 발생 횟수
BASE_RATE: dict[BehaviorType, float] = {
    BehaviorType.SCRATCH: 25.0,
    BehaviorType.SHAKE: 12.0,
    BehaviorType.WALK: 25.0,
    BehaviorType.RUN: 10.0,
    BehaviorType.POSTURE_CHANGE: 20.0,
}

# 이벤트 1건의 지속시간 범위(초)
DURATION_RANGE: dict[BehaviorType, tuple[float, float]] = {
    BehaviorType.SCRATCH: (3.0, 12.0),
    BehaviorType.SHAKE: (1.0, 3.0),
    BehaviorType.WALK: (60.0, 240.0),
    BehaviorType.RUN: (20.0, 90.0),
    BehaviorType.POSTURE_CHANGE: (2.0, 6.0),
}


# 크기에 따라 기본 활동량이 다르다. 대형견이 더 많이 걷고 뛴다.
SIZE_ACTIVITY_SCALE = {
    DogSize.SMALL: 0.75,
    DogSize.MEDIUM: 1.00,
    DogSize.LARGE: 1.35,
}

# 시나리오마다 다른 난수 계열을 쓴다.
# 같은 seed를 쓰면 세 마리가 사실상 같은 개가 되어 버린다.
SCENARIO_SEED_OFFSET = {"skin": 0, "joint": 101, "normal": 202, "acute": 303}


def activity_scale(profile: DogProfile) -> dict[BehaviorType, float]:
    """
    프로필이 기본 활동량에 미치는 영향.

    십자인대/슬개골 수술 이력이 있는 개는 '원래부터' 덜 뛴다.
    이 상태에서 더 나빠지는 것을 잡아내야 하므로,
    데이터도 처음부터 낮은 수준에서 출발해야 현실적이다.
    """
    s = SIZE_ACTIVITY_SCALE[profile.size]
    scale = {b: 1.0 for b in BASE_RATE}
    scale[BehaviorType.WALK] = s
    scale[BehaviorType.RUN] = s

    for surg in profile.surgeries:
        if surg.type in (SurgeryType.CRUCIATE, SurgeryType.PATELLA, SurgeryType.DISC):
            scale[BehaviorType.RUN] *= 0.60
            scale[BehaviorType.WALK] *= 0.85

    if profile.is_senior:
        scale[BehaviorType.RUN] *= 0.70

    return scale


def _normalized(profile: list[float]) -> np.ndarray:
    """하루 총합이 기저강도와 일치하도록 24칸을 정규화한다."""
    arr = np.asarray(profile, dtype=float)
    return arr * (24.0 / arr.sum())


NORM_CIRCADIAN = {b: _normalized(p) for b, p in CIRCADIAN.items()}


# ---------------------------------------------------------------------------
# 시나리오 정의
# ---------------------------------------------------------------------------

@dataclass
class DayNoise:
    """그날 하루에 걸리는 외란."""
    wear_ratio: float
    low_conf_rate: float
    car_trip: bool = False


# 시나리오별 체중 추세 (일일 변화율) 와 기본 섭취율
#
# 뭉치는 목표 25kg인데 오히려 늘고 있다.
# DER 캐스케이드의 외부 루프가 이걸 잡아 활동계수를 낮춰야 한다.
WEIGHT_TREND = {"skin": 0.0000, "joint": +0.0004, "normal": 0.0000, "acute": -0.0015}
BASE_INTAKE = {"skin": 0.97, "joint": 0.99, "normal": 0.96, "acute": 0.97}


def _ramp(day: int, onset: int, ramp_days: int, max_mult: float) -> float:
    """onset일부터 ramp_days에 걸쳐 1.0 -> max_mult로 서서히 올라간다."""
    if day < onset:
        return 1.0
    t = min(1.0, (day - onset + 1) / ramp_days)
    return 1.0 + (max_mult - 1.0) * t


def _decay(day: int, onset: int, rate: float) -> float:
    """onset일부터 매일 rate배씩 곱해진다. 완만한 악화를 표현."""
    if day < onset:
        return 1.0
    return rate ** (day - onset + 1)


def modulate(scenario: str, day: int, hour: int, behavior: BehaviorType) -> float:
    """시나리오가 특정 날짜/시간대/행동의 강도를 얼마나 왜곡하는가."""
    night = hour in NIGHT_HOURS

    # --- A. 피부염 의심 -------------------------------------------------
    # Day 31부터 밤 긁기가 3배로. 몸털기와 뒤척임도 함께 오른다.
    if scenario == "skin":
        if behavior is BehaviorType.SCRATCH:
            return _ramp(day, 31, 3, 3.0 if night else 1.4)
        if behavior is BehaviorType.SHAKE:
            return _ramp(day, 31, 3, 1.8)
        if behavior is BehaviorType.POSTURE_CHANGE:
            return _ramp(day, 31, 3, 1.5)
        return 1.0

    # --- B. 노령 관절통 -------------------------------------------------
    # Day 25부터 매일 조금씩 덜 뛴다. 하루 단위로는 2~4%라 눈에 안 보인다.
    # 20일 누적이면 뛰기 -56%, 걷기 -36%, 야간 각성 +81%가 된다.
    # 7일 중앙값 vs 30일 중앙값 비교가 이걸 잡아내는지가 검증 포인트.
    if scenario == "joint":
        if behavior is BehaviorType.RUN:
            return _decay(day, 25, 0.960)
        if behavior is BehaviorType.WALK:
            return _decay(day, 25, 0.978)
        if behavior is BehaviorType.POSTURE_CHANGE and night:
            return _decay(day, 25, 1.030)
        return 1.0

    # --- D. 급성 이상 ---------------------------------------------------
    # Day 41에 갑자기 무너진다. 활동이 급락하고 밤새 뒤척인다.
    # 이건 영양제로 대응할 상황이 아니다.
    # Layer 0(긴급 정지)이 발동해 전 처방을 동결하고 수의사 내원을 권고해야 한다.
    if scenario == "acute":
        if day < 41:
            return 1.0
        if behavior is BehaviorType.RUN:
            return 0.15
        if behavior is BehaviorType.WALK:
            return 0.35
        if behavior is BehaviorType.POSTURE_CHANGE and night:
            return 2.6
        if behavior is BehaviorType.SCRATCH:
            return 0.7          # 아파서 긁을 여력도 없다
        return 1.0

    # --- C. 정상 --------------------------------------------------------
    return 1.0


def day_noise(scenario: str, day: int, rng: np.random.Generator) -> DayNoise:
    """
    센서 쪽 외란.

    시나리오 C에는 일부러 오탐 유발 요인을 심는다.
    이걸 걸러내고 '처방 0'이 나와야 필터가 제대로 동작하는 것이다.
    """
    wear = float(np.clip(rng.normal(0.94, 0.03), 0.80, 0.99))
    low_conf = 0.08

    if scenario == "normal":
        low_conf = 0.15                        # 분류 난이도가 높은 개체라고 가정
        if day in (29, 33, 41):
            return DayNoise(wear, low_conf, car_trip=True)    # 차량 이동
        if day in (35, 43):
            return DayNoise(0.22, low_conf)                   # 목줄 미착용

    return DayNoise(wear, low_conf)


# ---------------------------------------------------------------------------
# 강아지 프로필
# ---------------------------------------------------------------------------

def build_profile(scenario: str) -> DogProfile:
    if scenario == "skin":
        return DogProfile(
            dog_id="dog_choco", name="초코",
            age_months=24, sex=Sex.MALE, neutered=True,
            weight_kg=5.2, body_condition=BodyCondition.IDEAL, size=DogSize.SMALL,
            meals_per_day=2, food_id="core_chicken",
            breeds=["말티즈"],
            allergies=[Allergen.ENVIRONMENTAL],   # 꽃가루 -> 피부축 민감도 상향
            surgeries=[Surgery(type=SurgeryType.NEUTER, years_ago=1.5)],
            conditions=[], medications=[],
        )

    if scenario == "joint":
        return DogProfile(
            dog_id="dog_mungchi", name="뭉치",
            age_months=132, sex=Sex.FEMALE, neutered=True,
            weight_kg=28.5, body_condition=BodyCondition.OVERWEIGHT, size=DogSize.LARGE,
            meals_per_day=2, food_id="core_chicken",
            breeds=[],                            # 잡종 - 견종 없이도 동작해야 한다
            allergies=[],
            surgeries=[
                Surgery(type=SurgeryType.NEUTER, years_ago=9.0),
                # 십자인대 수술 이력 -> "원래 덜 뛰는 개"로 기준선을 낮춰 잡아야 한다
                Surgery(type=SurgeryType.CRUCIATE, years_ago=3.0),
            ],
            conditions=[Condition.ARTHRITIS],
            medications=[],
            target_weight_kg=25.0,
        )

    if scenario == "acute":
        return DogProfile(
            dog_id="dog_byeol", name="별이",
            age_months=72, sex=Sex.FEMALE, neutered=True,
            weight_kg=15.0, body_condition=BodyCondition.IDEAL, size=DogSize.MEDIUM,
            meals_per_day=2, food_id="core_chicken",
            breeds=["비글"],
            allergies=[],
            surgeries=[Surgery(type=SurgeryType.NEUTER, years_ago=5.0)],
            conditions=[],
            # 항생제 복용 중 -> 장건강 카트리지가 문진 트리거로 처방되어야 한다
            medications=[Medication.ANTIBIOTIC],
        )

    return DogProfile(
        dog_id="dog_kong", name="콩이",
        age_months=48, sex=Sex.MALE, neutered=True,
        weight_kg=11.0, body_condition=BodyCondition.IDEAL, size=DogSize.MEDIUM,
        meals_per_day=2, food_id="core_salmon",
        breeds=["웰시코기"],
        # 생선 알러지: 어유 오메가3가 차단되고 조류 오메가3로 대체되어야 한다
        allergies=[Allergen.FISH],
        surgeries=[Surgery(type=SurgeryType.NEUTER, years_ago=3.0)],
        conditions=[], medications=[],
    )


# ---------------------------------------------------------------------------
# 이벤트 생성
# ---------------------------------------------------------------------------

def _sample_confidence(rng: np.random.Generator, low_rate: float) -> float:
    if rng.random() < low_rate:
        return float(rng.uniform(0.30, CONFIDENCE_GATE - 0.01))
    return float(np.clip(rng.normal(0.88, 0.06), CONFIDENCE_GATE, 0.99))


def _make_event(
    rng: np.random.Generator, day_date: date, hour: int,
    behavior: BehaviorType, low_conf_rate: float,
) -> BehaviorEvent:
    lo, hi = DURATION_RANGE[behavior]
    ts = datetime(
        day_date.year, day_date.month, day_date.day,
        hour, int(rng.integers(0, 60)), int(rng.integers(0, 60)),
    )
    return BehaviorEvent(
        ts=ts,
        type=behavior,
        confidence=_sample_confidence(rng, low_conf_rate),
        duration_s=float(rng.uniform(lo, hi)),
    )


def generate_events(
    scenario: str, profile: DogProfile, rng: np.random.Generator, start: date
) -> tuple[list[BehaviorEvent], dict[int, DayNoise]]:
    """44일치 행동 이벤트를 비균질 포아송 과정으로 생성한다."""

    # 개체 편차: 같은 건강 상태라도 개마다 기본 빈도가 다르다
    individual = {b: float(rng.lognormal(0.0, 0.15)) for b in BASE_RATE}
    # 크기/수술 이력에 따른 기본 활동 수준
    prof_scale = activity_scale(profile)

    events: list[BehaviorEvent] = []
    noises: dict[int, DayNoise] = {}

    for day in range(1, TOTAL_DAYS + 1):
        day_date = start + timedelta(days=day - 1)
        noise = day_noise(scenario, day, rng)
        noises[day] = noise

        # 날짜별 변동: 개도 컨디션이 매일 조금씩 다르다
        day_factor = {b: float(rng.lognormal(0.0, 0.12)) for b in BASE_RATE}

        for behavior, base in BASE_RATE.items():
            profile_curve = NORM_CIRCADIAN[behavior]

            for hour in range(24):
                lam = (
                    base / 24.0
                    * profile_curve[hour]
                    * individual[behavior]
                    * prof_scale[behavior]
                    * day_factor[behavior]
                    * modulate(scenario, day, hour, behavior)
                    * noise.wear_ratio        # 목줄을 안 차면 그만큼 덜 잡힌다
                )
                for _ in range(int(rng.poisson(lam))):
                    events.append(
                        _make_event(rng, day_date, hour, behavior, noise.low_conf_rate)
                    )

        # 차량 이동: 목줄이 계속 흔들려 몸털기/걷기로 오분류된다.
        # 신뢰도가 낮게 나오므로 게이팅에서 대부분 걸러져야 한다.
        if noise.car_trip:
            for hour in (14, 15):
                for _ in range(int(rng.integers(14, 22))):
                    events.append(_make_event(rng, day_date, hour, BehaviorType.SHAKE, 1.0))
                for _ in range(int(rng.integers(5, 9))):
                    events.append(_make_event(rng, day_date, hour, BehaviorType.WALK, 1.0))

    events.sort(key=lambda e: e.ts)
    return events, noises


# ---------------------------------------------------------------------------
# 일별 집계
# ---------------------------------------------------------------------------

def generate_weight_and_intake(
    scenario: str, profile: DogProfile, rng: np.random.Generator
) -> tuple[list[float | None], list[tuple[int, int]]]:
    """
    체중계와 로드셀 데이터를 만든다.

    체중은 개가 급식기 앞 발판에 올라간 날만 기록된다(주 5회 정도).
    섭취량은 '준 양'과 '먹은 양'을 따로 둔다 - 처방과 실제 섭취는 다르다.
    """
    trend = WEIGHT_TREND[scenario]
    base_intake = BASE_INTAKE[scenario]

    weights: list[float | None] = []
    intakes: list[tuple[int, int]] = []

    for day in range(1, TOTAL_DAYS + 1):
        w = profile.weight_kg * (1.0 + trend * day)
        # 측정 노이즈: 개가 몸을 흔들고 네 발을 다 안 올린다
        measured = w * (1.0 + rng.normal(0.0, 0.012))
        # 주 2회 정도는 체중계에 안 올라간다
        weights.append(round(float(measured), 2) if rng.random() > 0.28 else None)

        offered = int(round(profile.rer * 1.6 / 3.6))     # 대략적인 기본 급여량
        ratio = base_intake + rng.normal(0.0, 0.03)
        if scenario == "acute" and day >= 41:
            ratio = 0.52 + rng.normal(0.0, 0.05)          # 식욕 급감
        ratio = float(np.clip(ratio, 0.0, 1.0))
        intakes.append((offered, int(round(offered * ratio))))

    return weights, intakes


def summarize_day(
    day_date: date, events: list[BehaviorEvent], noise: DayNoise,
    weight: float | None = None, intake: tuple[int, int] = (0, 0),
) -> DailySummary:
    """
    하루치 이벤트를 알고리즘 입력으로 집계한다.

    집계 자체는 core.aggregate 가 한다. 실제 목줄에서 올라온 이벤트도
    같은 함수를 타므로, 이 생성기로 하는 검증이 곧 실제 파이프라인 검증이 된다.
    """
    return build_daily_summary(
        day_date,
        events,
        wear_ratio=noise.wear_ratio,
        weight_kg=weight,
        food_offered_g=intake[0],
        food_eaten_g=intake[1],
    )


def generate(scenario: str, seed: int = 42) -> SensorDataset:
    """시나리오 하나에 대한 44일치 데이터셋을 만든다."""
    rng = np.random.default_rng(seed + SCENARIO_SEED_OFFSET[scenario])
    start = END_DATE - timedelta(days=TOTAL_DAYS - 1)

    profile = build_profile(scenario)
    events, noises = generate_events(scenario, profile, rng, start)
    weights, intakes = generate_weight_and_intake(scenario, profile, rng)

    by_day: dict[date, list[BehaviorEvent]] = {}
    for e in events:
        by_day.setdefault(e.ts.date(), []).append(e)

    days = [
        summarize_day(
            start + timedelta(days=i),
            by_day.get(start + timedelta(days=i), []),
            noises[i + 1],
            weights[i],
            intakes[i],
        )
        for i in range(TOTAL_DAYS)
    ]

    return SensorDataset(profile=profile, scenario=scenario, days=days, events=events)


# ---------------------------------------------------------------------------
# 눈으로 확인하기
# ---------------------------------------------------------------------------

_BLOCKS = "▁▂▃▄▅▆▇█"


def robust_z(baseline: list[float], recent: list[float]) -> tuple[float, float, float]:
    """
    Step 4에서 쓸 z-score를 미리 계산해 데이터가 제대로 만들어졌는지 검증한다.
    (정식 구현은 core/signal.py로 옮긴다)

        z = (최근 중앙값 - baseline 중앙값) / 중앙값의 표준오차

    분모에 표본 수 보정(MEDIAN_SE_FACTOR / sqrt(n))을 넣지 않으면
    분모가 과대평가되어 신호가 절반으로 깎인다.
    """
    if not baseline or not recent:
        return 0.0, 0.0, 0.0

    base_arr = np.asarray(baseline, dtype=float)
    base_med = float(np.median(base_arr))
    recent_med = float(np.median(recent))

    mad = float(np.median(np.abs(base_arr - base_med))) * MAD_TO_SIGMA
    se = max(mad, MIN_MAD) * MEDIAN_SE_FACTOR / np.sqrt(len(recent))

    return (recent_med - base_med) / se, base_med, recent_med


def spark(values, vmax: float | None = None) -> str:
    vals = list(values)
    top = max(vals) if vmax is None else vmax
    if top <= 0:
        return "▁" * len(vals)
    return "".join(_BLOCKS[min(7, int(v / top * 7.999))] for v in vals)


def extract_metrics(days: list[DailySummary]) -> dict[str, list[float]]:
    """AXIS_WEIGHTS가 참조하는 지표들을 일별 시계열로 뽑아낸다."""
    post_meal = [h for m in MEAL_HOURS for h in (m, m + 1)]

    return {
        "scratch_night":      [d.summary.scratch_night for d in days],
        "shake":              [d.summary.shake_total for d in days],
        "restless":           [d.sleep.restless_count for d in days],
        "run_sec":            [d.summary.run_sec for d in days],
        "walk_sec":           [d.summary.walk_sec for d in days],
        # 깊은 밤(22~04시)의 뒤척임. 통증성 각성에 더 민감하다.
        "restless_night":     [sum(d.hourly.posture_change[h] for h in NIGHT_HOURS)
                               for d in days],
        # 알려진 한계: 이 두 지표는 피부축과 교차 오염된다.
        # 피부염으로 하루 종일 몸을 털면 식후 몸털기도 함께 늘어난다.
        # '전체 대비 비율'로 정규화해봤으나, 비율은 baseline 변동폭이 작아
        # z가 과민해지면서 오히려 악화됐다(+1.69 -> +9.99).
        # 지금은 절대량 + 2주 연속 규칙으로 막고 있으나 근본 해결은 아니다.
        # -> 소화축을 처방 트리거에서 제외하는 방안을 검토 중.
        "shake_post_meal":    [sum(d.hourly.shake[h] for h in post_meal) for d in days],
        "posture_post_meal":  [sum(d.hourly.posture_change[h] for h in post_meal)
                               for d in days],
        "sleep_min":          [d.sleep.total_min for d in days],
        "night_activity":     [sum(d.hourly.activity_sec[h] for h in SLEEP_HOURS)
                               for d in days],
    }


def axis_score(
    metrics: dict[str, list[float]], valid: list[bool],
    axis: HealthAxis, lo: int, hi: int,
) -> tuple[float, dict[str, float]]:
    """
    축 하나의 가중 z-합을 구한다.

    개별 지표가 우연히 임계를 넘어도, 가중합이 넘지 않으면 처방하지 않는다.
    이 구조 자체가 오탐에 대한 1차 방어선이다.
    """
    contributions: dict[str, float] = {}
    total = 0.0

    for key, weight in AXIS_WEIGHTS[axis].items():
        vals = metrics[key]
        base = [v for v, ok in zip(vals[:BASELINE_END], valid[:BASELINE_END]) if ok]
        window = [v for v, ok in zip(vals[lo:hi], valid[lo:hi]) if ok]
        z, _, _ = robust_z(base, window)

        contributions[key] = weight * z
        total += weight * z

    return total, contributions


def print_report(ds: SensorDataset) -> None:
    p = ds.profile
    days = ds.days

    print()
    print("=" * 76)
    print(f"  [{ds.scenario}]  {p.name} · {p.age_months // 12}세 · {p.weight_kg}kg "
          f"· {p.size.value} · 견종 {p.breeds or '잡종/모름'}")
    print("=" * 76)

    # 알고리즘이 실제로 보는 지표를 그대로 쓴다 (활동은 초 단위)
    series = {
        "야간 긁기 (22~04시)": [d.summary.scratch_night for d in days],
        "뛴 시간 (초)":        [d.summary.run_sec for d in days],
        "걸은 시간 (초)":       [d.summary.walk_sec for d in days],
        "수면 뒤척임":          [d.sleep.restless_count for d in days],
    }

    print(f"\n  {TOTAL_DAYS}일 추이   (│ 왼쪽 = baseline 30일 / 오른쪽 = 관찰 2주)\n")
    for label, vals in series.items():
        head, tail = vals[:BASELINE_END], vals[BASELINE_END:]
        top = max(vals) or 1
        print(f"    {label:<20} {spark(head, top)}│{spark(tail, top)}   max={top}")

    # 축 점수 판정.
    #
    # 개별 지표 z가 아니라 '가중합'이 임계를 넘어야 처방한다.
    # 축마다 창 길이와 연속 요구가 다르다 (관절은 14일 x 1회, 나머지는 7일 x 2회).
    # 착용률 미달인 날은 baseline/관찰 양쪽에서 모두 제외한다.
    valid = [d.valid for d in days]
    metrics = extract_metrics(days)
    n_dropped = sum(1 for ok in valid[BASELINE_END:] if not ok)

    note = f"   (착용률 미달 {n_dropped}일 제외)" if n_dropped else ""
    print(f"\n  축 점수 판정   |가중 z합| >= {Z_ENTER} 이어야 처방{note}")

    axis_labels = {
        HealthAxis.SKIN: "피부", HealthAxis.JOINT: "관절",
        HealthAxis.SLEEP: "수면·인지", HealthAxis.DIGEST: "소화",
    }
    fired_axes: set[HealthAxis] = set()

    for axis, axis_label in axis_labels.items():
        win = AXIS_WINDOW_DAYS[axis]
        need = AXIS_PERSISTENCE[axis]

        scores, details = [], []
        for k in range(need):
            # 최근 구간부터 거꾸로 자른다
            hi = TOTAL_DAYS - k * win
            lo = hi - win
            s, c = axis_score(metrics, valid, axis, lo, hi)
            scores.append(s)
            details.append(c)

        fired = all(abs(s) >= Z_ENTER for s in scores) and \
            len({s > 0 for s in scores}) == 1

        # 처방 권한 판정
        if axis in OBSERVATION_ONLY_AXES:
            mark = "— 참고 지표 (처방 권한 없음)"
        elif not fired:
            mark = "(임계 미달)" if max(map(abs, scores)) < Z_ENTER else "(연속성 미달)"
        else:
            # 원인 축이 이미 발화 중이면 수면축은 억제한다
            blockers = [a for a in AXIS_SUPPRESSED_BY.get(axis, []) if a in fired_axes]
            if blockers:
                names = "/".join(axis_labels[a] for a in blockers)
                mark = f"◀ 발화했으나 억제 ({names}축이 원인 - 그쪽부터 치료)"
            else:
                mark = "◀ 발화"
                fired_axes.add(axis)

        cells = "  ".join(f"{s:+.2f}" for s in reversed(scores))
        print(f"\n    {axis_label:<8} 창 {win}일 x {need}회   점수 {cells}   {mark}")

        # 어떤 지표가 얼마나 밀어올렸는지
        latest = details[0]
        parts = sorted(latest.items(), key=lambda kv: -abs(kv[1]))
        detail = "  ".join(f"{k} {v:+.2f}" for k, v in parts)
        print(f"             {detail}")

    # 긴급 정지(Layer 0) 판정.
    #
    # 급성 이상은 영양제로 대응할 상황이 아니다. 전 처방을 동결하고 내원을 권고한다.
    # 판정 기준은 30일 baseline이 아니라 '직전 14일'이다 - 급성은 '급변'이지
    # '평소보다 나쁨'이 아니기 때문이다. (constants.ESCALATION_RULES 주석 참조)
    rule = ESCALATION_RULES["acute_pain"]
    w, r = rule["window_days"], rule["ref_days"]

    def _acute_z(vals: list[float]) -> float:
        ref = [v for v, ok in zip(vals[-(w + r):-w], valid[-(w + r):-w]) if ok]
        win = [v for v, ok in zip(vals[-w:], valid[-w:]) if ok]
        z, _, _ = robust_z(ref, win)
        return z

    activity = [d.summary.run_sec + d.summary.walk_sec for d in days]
    act_z = _acute_z(activity)
    wake_z = _acute_z(metrics["restless_night"])
    pain = act_z <= rule["activity_z"] and wake_z >= rule["night_wake_z"]

    drop_rule = ESCALATION_RULES["appetite_drop"]
    tail = [d.intake_ratio for d in days[-drop_rule["days"]:]]
    appetite = all(r < drop_rule["ratio"] for r in tail)

    print(f"\n  긴급 정지 판정 (Layer 0)   최근 {w}일 vs 직전 {r}일")
    print(f"    급성 통증  활동 z={act_z:+.2f} (기준 {rule['activity_z']}) · "
          f"야간각성 z={wake_z:+.2f} (기준 {rule['night_wake_z']})  "
          f"{'🚨 발동' if pain else '정상'}")
    print(f"    식욕 저하  최근 3일 섭취율 {[f'{r:.0%}' for r in tail]}  "
          f"{'🚨 발동' if appetite else '정상'}")
    if pain or appetite:
        print("    → 전 처방 동결 + 수의사 내원 권고")

    # 체중 추세 (DER 캐스케이드 외부 루프의 입력)
    measured = [(i + 1, d.weight_kg) for i, d in enumerate(days) if d.weight_kg]
    if len(measured) >= 8:
        first = float(np.median([w for _, w in measured[:5]]))
        last = float(np.median([w for _, w in measured[-5:]]))
        pct_week = (last - first) / first / (TOTAL_DAYS / 7) * 100
        target = p.target_weight_kg or p.weight_kg
        gap_pct = (last - target) / target * 100

        # 변화율만 보면 "목표보다 계속 무거운" 상태를 놓친다.
        # 외부 루프는 변화율(미분항)과 목표 편차(비례항)를 함께 봐야 한다.
        flags = []
        if abs(pct_week) >= 1.0:
            flags.append(f"변화율 {pct_week:+.2f}%/주")
        if abs(gap_pct) >= 5.0:
            flags.append(f"목표 대비 {gap_pct:+.0f}%")
        flag = "  ← 활동계수 보정 필요: " + " · ".join(flags) if flags else ""

        print(f"\n  체중   {first:.1f}kg → {last:.1f}kg  "
              f"({pct_week:+.2f}%/주, 목표 {target:.1f}kg){flag}")
        print(f"         측정 {len(measured)}/{TOTAL_DAYS}일 (개가 안 올라간 날은 결측)")

    # 시간대별 비교. 야간 구간을 표시해야 "밤에 긁는다"가 눈에 보인다.
    a = days[BASELINE_END - 1].hourly.scratch    # Day 30 (평소)
    b = days[TOTAL_DAYS - 2].hourly.scratch      # Day 43 (관찰 2주차)
    top = max(max(a), max(b)) or 1
    night_mask = "".join("▓" if h in NIGHT_HOURS else "·" for h in range(24))
    print("\n  긁기 시간대 분포        0h        6h        12h       18h    23h")
    print(f"    Day 30 (평소)        {spark(a, top)}")
    print(f"    Day 43 (관찰)        {spark(b, top)}")
    print(f"    야간 구간            {night_mask}")

    # 야간만 모아서 보기 (18시 -> 05시 순으로 재배열)
    order = list(range(18, 24)) + list(range(0, 6))
    na = [a[h] for h in order]
    nb = [b[h] for h in order]
    ntop = max(max(na), max(nb)) or 1
    print("\n  밤 시간만 (18h ~ 05h)   18  20  22  00  02  04")
    print(f"    Day 30 (평소)        {spark(na, ntop)}")
    print(f"    Day 43 (관찰)        {spark(nb, ntop)}")

    # 노이즈가 실제로 걸러졌는지.
    # 평상시 폐기율은 8~15% 수준이므로, 그보다 뚜렷하게 높은 날만 표시한다.
    print("\n  데이터 품질")
    flagged = [
        (i + 1, d) for i, d in enumerate(days)
        if not d.valid or d.low_confidence_ratio > 0.35
    ]
    if flagged:
        for day_no, d in flagged:
            tag = "착용률 미달 → 통계 제외" if not d.valid else "저신뢰 이벤트 다수"
            print(f"    Day {day_no:<2}  착용률 {d.wear_ratio:.0%}  "
                  f"버린 비율 {d.low_confidence_ratio:.0%}   ← {tag}")
    else:
        print("    특이사항 없음")

    avg_drop = float(np.mean([d.low_confidence_ratio for d in days]))
    print(f"    전체 평균 폐기율 {avg_drop:.1%}")


def print_noise_filter_demo(ds: SensorDataset) -> None:
    """차량 이동 노이즈가 신뢰도 게이팅으로 제거되는 과정을 보여준다."""
    by_day: dict[date, list[BehaviorEvent]] = {}
    for e in ds.events:
        by_day.setdefault(e.ts.date(), []).append(e)

    start = ds.days[0].date
    raw, kept = [], []
    for i in range(TOTAL_DAYS):
        evs = by_day.get(start + timedelta(days=i), [])
        raw.append(sum(1 for e in evs if e.type is BehaviorType.SHAKE))
        kept.append(ds.days[i].summary.shake_total)

    top = max(raw) or 1
    print("\n  ── 노이즈 필터 동작 (몸털기 기준) ──")
    print(f"    필터 전   {spark(raw, top)}   ← Day 29·33·41 차량 이동")
    print(f"    필터 후   {spark(kept, top)}   ← 신뢰도 게이팅으로 제거됨")
    for d in (29, 33, 41):
        print(f"    Day {d}: {raw[d - 1]}건 → {kept[d - 1]}건")


# ---------------------------------------------------------------------------

SCENARIOS = ["skin", "joint", "normal", "acute"]


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "out"
    out.mkdir(exist_ok=True)

    for scenario in SCENARIOS:
        ds = generate(scenario, seed=42)
        print_report(ds)
        if scenario == "normal":
            print_noise_filter_demo(ds)

        path = out / f"{scenario}.json"
        path.write_text(
            json.dumps(ds.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  → {path}  ({len(ds.events)} events / {len(ds.days)} days)")

    print("\n" + "=" * 76)
    print("  완료. out/ 폴더에 JSON 3개 생성됨.")
    print("=" * 76 + "\n")


if __name__ == "__main__":
    main()
