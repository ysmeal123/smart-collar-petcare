"""
DogTwin — 개체의 현재 상태를 한 객체로 모은 것.

**무엇이 아닌지 먼저.**
의료/생리학적 디지털 트윈이 아니다. 장기 모델도, 대사 시뮬레이터도 없다.
행동·섭취·체중·영양 이력을 동기화해 유지하는 **행동·영양 트윈**이다.

트윈이라고 부를 수 있는 근거 네 가지:

    1. 가상 표현      DogTwin 객체
    2. 지속 동기화    행동·섭취·체중이 매일 반영된다
    3. 사전 검증      새 계획을 실제로 주기 전에 14일 돌려본다 (simulation.py)
    4. 되먹임        실제 결과로 개체 계수를 교정한다 (energy.py 외부 루프)

이 파일은 1·2를 맡는다. 판단은 하지 않는다 - 조립만 한다.
판단은 inference / energy / safety 가 이미 하고 있고, 여기서 다시 하면
로직이 두 벌이 된다.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Optional

from pydantic import BaseModel, Field, computed_field

from core.constants import BASELINE_DAYS, RECENT_DAYS
from core.models import AxisScore, DailySummary, DogProfile, HealthAxis, Prescription
from core.signal import extract_metrics, valid_mask

# 기준선이 얼마나 여물었는가. 이 단계에 따라 처방 강도를 제한한다.
BASELINE_STAGES = [
    (7, "provisional", "아직 평소를 모릅니다"),
    (14, "intermediate", "평소를 익히는 중입니다"),
    (BASELINE_DAYS, "maturing", "거의 파악했습니다"),
]


class Baseline(BaseModel):
    """이 개의 '평소'. 남과 비교하지 않고 이 값과 비교한다."""

    days_observed: int
    stage: str
    stage_note: str
    values: dict[str, float] = Field(default_factory=dict)

    @computed_field
    @property
    def mature(self) -> bool:
        """기준선이 여물었는가. 그 전에는 영양제 처방을 제한한다."""
        return self.stage == "mature"


class IntakeState(BaseModel):
    """먹는 쪽. 로드셀이 없으면 전부 기본값으로 남는다."""

    recent_ratio: Optional[float] = None
    skipped_meals_7d: int = 0
    avg_duration_min: Optional[float] = None
    measured: bool = False


class WeightState(BaseModel):
    current_kg: Optional[float] = None
    target_kg: Optional[float] = None
    trend_pct_per_week: Optional[float] = None
    measurements_30d: int = 0

    @computed_field
    @property
    def direction(self) -> str:
        if self.trend_pct_per_week is None:
            return "unknown"
        if self.trend_pct_per_week > 0.5:
            return "gaining"
        if self.trend_pct_per_week < -0.5:
            return "losing"
        return "stable"


class DogTwin(BaseModel):
    """
    한 마리의 현재 상태 전부.

    API·시뮬레이션·에이전트가 전부 이 객체를 입력으로 받는다.
    그래야 "무엇을 보고 판단했는가"가 한 군데로 모인다.
    """

    dog_id: str
    as_of: date
    profile: DogProfile

    baseline: Baseline
    wellness: list[AxisScore] = Field(default_factory=list)
    weight: WeightState = Field(default_factory=WeightState)
    intake: IntakeState = Field(default_factory=IntakeState)

    activity_today_sec: int = 0
    wear_ratio: float = 0.0
    data_days: int = 0

    # 최근 처방. 시뮬레이션의 '현재 계획'이 된다.
    prescription: Optional[Prescription] = None

    @computed_field
    @property
    def ready_to_prescribe(self) -> bool:
        """
        영양제를 처방해도 되는 상태인가.

        기준선이 안 여물었으면 비교 대상이 없다. 그 상태로 처방하면
        '평소와 다르다'가 아니라 '다른 개와 다르다'가 되어 버린다.
        """
        return self.baseline.mature and self.wear_ratio >= 0.60

    @computed_field
    @property
    def attention(self) -> list[str]:
        """지금 주의를 요하는 축들. 앱 상단 요약에 쓴다."""
        return [a.axis.value for a in self.wellness if a.active]


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------

def _stage(days: int) -> tuple[str, str]:
    for threshold, name, note in BASELINE_STAGES:
        if days < threshold:
            return name, note
    return "mature", "평소를 파악했습니다"


def _recent_wear(days: list[DailySummary]) -> float:
    """
    최근 착용률. **마지막 하루가 아니라 최근 구간의 중앙값**이다.

    마지막 날만 보면 하루 비어 있는 것만으로 0이 된다. 그리고 하루 비는 건
    드문 일이 아니다 — 목줄 배터리가 나갔거나, BLE 가 안 붙었거나,
    목욕시키느라 벗겨뒀거나, 일일 배치가 데이터보다 먼저 돌았거나.

    그때 `ready_to_prescribe` 가 False 가 되면서 **영양제가 통째로 사라진다.**
    44일을 잘 모았는데 하루 빈 것으로 전부 무효가 되는 건 과하다.
    게다가 화면에는 "평소를 파악했습니다" 가 뜬 채로 사료만 나가서,
    보호자는 왜 영양제가 없어졌는지 알 수 없다.

    중앙값을 쓰면 하루 결측은 흡수하고, 이 게이트가 원래 잡으려던 것
    (며칠째 목줄을 안 차고 있다)은 그대로 잡는다.
    """
    if not days:
        return 0.0
    recent = [d.wear_ratio for d in days[-RECENT_DAYS:]]
    return round(float(median(recent)), 3)


def _baseline(days: list[DailySummary]) -> Baseline:
    valid = valid_mask(days)
    n_valid = sum(valid)
    stage, note = _stage(n_valid)

    values: dict[str, float] = {}
    if n_valid >= 3:
        metrics = extract_metrics(days)
        window = min(BASELINE_DAYS, len(days))
        for key, series in metrics.items():
            usable = [v for v, ok in zip(series[:window], valid[:window]) if ok]
            if usable:
                values[key] = round(float(sorted(usable)[len(usable) // 2]), 2)

    return Baseline(
        days_observed=n_valid, stage=stage, stage_note=note, values=values
    )


def _weight(profile: DogProfile, days: list[DailySummary]) -> WeightState:
    measured = [(d.date, d.weight_kg) for d in days if d.weight_kg is not None]
    if not measured:
        return WeightState(target_kg=profile.target_weight_kg)

    recent = [kg for _, kg in measured[-5:]]
    current = float(sorted(recent)[len(recent) // 2])

    trend = None
    if len(measured) >= 8:
        first = float(sorted([kg for _, kg in measured[:5]])[2])
        weeks = max(len(days) / 7.0, 1.0)
        trend = round((current - first) / first / weeks * 100, 2)

    return WeightState(
        current_kg=round(current, 2),
        target_kg=profile.target_weight_kg,
        trend_pct_per_week=trend,
        measurements_30d=len(measured),
    )


def _intake(days: list[DailySummary]) -> IntakeState:
    recent = days[-7:]
    offered = [d for d in recent if d.food_offered_g > 0]
    if not offered:
        # 로드셀이 없거나 아직 한 끼도 안 올라왔다.
        # 추측하지 않는다 - measured=False 가 "모른다"는 뜻이다.
        return IntakeState()

    ratios = [d.intake_ratio for d in offered]
    return IntakeState(
        recent_ratio=round(sum(ratios) / len(ratios), 3),
        skipped_meals_7d=sum(1 for r in ratios if r < 0.10),
        measured=True,
    )


def build(
    profile: DogProfile,
    days: list[DailySummary],
    axes: list[AxisScore] | None = None,
    prescription: Prescription | None = None,
    as_of: date | None = None,
) -> DogTwin:
    """
    흩어진 값을 트윈 하나로 모은다.

    여기서 새로 판단하지 않는다. axes 는 inference 가 이미 낸 결과를 받는다.
    같은 판단을 두 군데서 하면 언젠가 갈라진다.
    """
    today = as_of or (days[-1].date if days else date.today())
    last = days[-1] if days else None

    return DogTwin(
        dog_id=profile.dog_id,
        as_of=today,
        profile=profile,
        baseline=_baseline(days),
        wellness=axes or [],
        weight=_weight(profile, days),
        intake=_intake(days),
        activity_today_sec=(
            last.summary.walk_sec + last.summary.run_sec + last.summary.vigorous_sec
            if last else 0
        ),
        wear_ratio=_recent_wear(days),
        data_days=len(days),
        prescription=prescription,
    )
