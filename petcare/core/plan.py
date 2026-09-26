"""
급여 계획 — 이 시스템의 최종 출력.

하드웨어 앞에서 멈춘다. 모터를 돌리는 건 밥통 담당이고,
여기서는 **"언제 무엇을 얼마나"** 까지만 정한다.

    목줄 이벤트 → 집계 → 추론 → 안전필터 → 처방 → [급여 계획] → 사출
                                                    ^^^^^^^^^
                                                    여기가 끝

계획 하나에 들어가는 것:

    끼니별 사료 그램 · 슬롯별 알 수 · 시각
    왜 그렇게 나왔는지 (trace)
    14일 시뮬레이션 결과
    보호자에게 물을 것

전 구간을 한 번에 도는 `build()` 가 이 파일의 전부다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field, computed_field

from core import simulation as sim
from core import twin as twin_mod
from core.constants import CARTRIDGE_BY_ID
from core.inference import PrescriptionState
from core.models import DailySummary, DogProfile, Prescription, TraceStep
from core.prescribe import prescribe

# 끼니 시각 기본값. 프로필에 급여 시각이 있으면 그걸 쓴다.
DEFAULT_MEAL_HOURS = [8, 19]

# 명령 유효 시간. 아침 급여가 저녁에 나가면 안 된다.
MEAL_TTL = timedelta(hours=2)


class Pellet(BaseModel):
    """슬롯 하나에서 나갈 알갱이."""

    slot: int
    cartridge_id: str
    name: str
    count: int
    color: str
    reason: str = ""


class Meal(BaseModel):
    """한 끼. 밥통이 이 단위로 받아서 사출한다."""

    index: int
    hour: int
    at: datetime
    expires_at: datetime
    food_g: int
    pellets: list[Pellet] = Field(default_factory=list)

    @computed_field
    @property
    def label(self) -> str:
        return {0: "아침", 1: "점심", 2: "저녁"}.get(self.index, f"{self.index + 1}번째")

    @computed_field
    @property
    def line(self) -> str:
        """사람이 읽는 한 줄. 앱도 이걸 그대로 쓸 수 있다."""
        if not self.pellets:
            return f"{self.hour:02d}시  사료 {self.food_g}g"
        items = " · ".join(f"{p.name} {p.count}알" for p in self.pellets)
        return f"{self.hour:02d}시  사료 {self.food_g}g + {items}"


class FeedingPlan(BaseModel):
    """
    오늘 이 개에게 줄 것 전부.

    이 객체가 시스템의 최종 산출물이다.
    밥통은 meals 만 보면 되고, 앱은 나머지를 보여준다.
    """

    dog_id: str
    dog_name: str
    date: date

    meals: list[Meal] = Field(default_factory=list)
    total_food_g: int = 0
    der_kcal: float = 0.0

    # 왜 이렇게 나왔는가
    attention: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)

    escalated: bool = False
    escalation_reason: str = ""

    # 2주 뒤 어디로 가는가
    simulation: sim.SimulationResult | None = None

    # 보호자에게 물을 것
    questions: list[dict] = Field(default_factory=list)

    baseline_stage: str = "mature"
    ready: bool = True
    blocked_reason: str = ""

    def render(self) -> str:
        """터미널·로그용 한 화면 요약."""
        head = f"{self.dog_name} · {self.date}"
        lines = [head, "=" * len(head) * 2, ""]

        if self.escalated:
            lines += [f"🚨 {self.escalation_reason}", "   영양제를 중단했습니다.", ""]
        if not self.ready:
            lines += [f"⏳ {self.blocked_reason}", ""]

        lines.append(f"하루 {self.total_food_g}g / {self.der_kcal:.0f} kcal")
        lines += [f"  {m.line}" for m in self.meals]

        if self.observations:
            lines += ["", "관찰"]
            lines += [f"  · {o}" for o in self.observations]

        if self.simulation:
            s = self.simulation
            lines += ["", f"{s.horizon_days}일 시뮬레이션   {s.summary}"]
            lines += [f"  · {r}" for r in s.reasons]

        if self.questions:
            lines += ["", "보호자에게 물어볼 것"]
            lines += [f"  · {q['text']}" for q in self.questions]

        return "\n".join(lines)


def _meal_hours(profile: DogProfile) -> list[int]:
    n = profile.meals_per_day
    if n == 1:
        return [8]
    if n == 3:
        return [7, 13, 19]
    return DEFAULT_MEAL_HOURS


def split(rx: Prescription, profile: DogProfile, day: date) -> list[Meal]:
    """
    처방을 끼니별로 쪼갠다.

    영양제의 끼니 배치는 알고리즘이 이미 정했다(길항 성분 분리).
    여기서는 그 결정을 따라 담기만 한다.
    사료는 균등 분할하고 나머지 그램은 첫 끼니에 붙인다.
    """
    hours = _meal_hours(profile)
    n = len(hours)

    base = rx.food_grams // n
    per = [base] * n
    per[0] += rx.food_grams - base * n

    meals: list[Meal] = []
    for i, hour in enumerate(hours):
        if n == 1:
            picked = rx.items                      # 한 끼면 전부 같이
        elif n == 3 and i == 1:
            picked = []                            # 점심은 사료만
        else:
            slot = "morning" if i == 0 else "evening"
            picked = [it for it in rx.items if it.meal_slot.value == slot]

        at = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
        meals.append(Meal(
            index=i, hour=hour, at=at, expires_at=at + MEAL_TTL,
            food_g=per[i],
            pellets=[
                Pellet(
                    slot=CARTRIDGE_BY_ID[it.cartridge_id].slot,
                    cartridge_id=it.cartridge_id,
                    name=it.name, count=it.pellets,
                    color=it.color, reason=it.reason,
                )
                for it in picked
            ],
        ))
    return meals


def build(
    profile: DogProfile,
    days: list[DailySummary],
    *,
    state: PrescriptionState | None = None,
    context=None,
    today: date | None = None,
) -> tuple[FeedingPlan, PrescriptionState]:
    """
    전 구간을 한 번에 돈다.

        추론 → 안전필터 → 처방 → 트윈 → 시뮬레이션 → 질문 → 계획

    이 함수 하나가 "센서 데이터 넣으면 급여 계획이 나온다"를 실현한다.
    """
    day = today or (days[-1].date if days else date.today())

    rx, next_state = prescribe(profile, days, state=state, today=day)
    t = twin_mod.build(profile, days, rx.axes, rx, as_of=day)

    plan = FeedingPlan(
        dog_id=profile.dog_id,
        dog_name=profile.name,
        date=day,
        total_food_g=rx.food_grams,
        der_kcal=rx.der_kcal,
        escalated=rx.escalated,
        escalation_reason=rx.escalation_reason,
        trace=rx.trace,
        attention=t.attention,
        observations=[a.message for a in rx.axes if a.active and a.message],
        baseline_stage=t.baseline.stage,
        ready=t.ready_to_prescribe,
    )

    # 기준선이 안 여물었으면 사료만 준다.
    # 비교 대상이 없는 상태의 판정으로 영양제를 주는 건 근거가 없다.
    if not t.ready_to_prescribe:
        plan.blocked_reason = (
            f"{t.baseline.stage_note} "
            f"(관찰 {t.baseline.days_observed}일). 사료만 정상 급여합니다."
        )
        rx = rx.model_copy(update={"items": []})

    plan.meals = split(rx, profile, day)

    plan.simulation = sim.run(
        profile, rx.der_kcal, rx.food_grams,
        [{"cartridge_id": i.cartridge_id, "pellets": i.pellets} for i in rx.items],
        current_kg=t.weight.current_kg,
        intake_ratio=t.intake.recent_ratio if t.intake.measured else 1.0,
        prev_food_grams=(state.prev_food_grams if state else None),
    )

    if context is not None:
        from agent.wellness_agent import ask

        plan.questions = [
            {"key": q.key, "axis": q.axis.value, "text": q.text,
             "type": q.type.value, "choices": q.choices, "why": q.why}
            for q in ask(t, context, day)
        ]

    return plan, next_state
