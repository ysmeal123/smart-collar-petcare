"""
데이터 모델 정의.

시스템 전체에서 주고받는 데이터의 '모양'을 여기서 한 번만 정한다.
Pydantic을 쓰므로 타입 검증 + JSON 변환 + FastAPI 스키마가 전부 자동으로 따라온다.

흐름:
    DogProfile + DailySummary[]  ->  [알고리즘]  ->  Prescription + PrescriptionTrace
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field


# ---------------------------------------------------------------------------
# 열거형
# ---------------------------------------------------------------------------

class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"


class DogSize(str, Enum):
    """온보딩 필수 항목. 견종을 몰라도(잡종) 이건 답할 수 있다."""
    SMALL = "small"    # 소형견
    MEDIUM = "medium"  # 중형견
    LARGE = "large"    # 대형견


class BodyCondition(str, Enum):
    """실루엣 그림 3장 중 선택. 숫자(BCS)로 물으면 보호자가 답하지 못한다."""
    THIN = "thin"              # 마른 편
    IDEAL = "ideal"            # 적당
    OVERWEIGHT = "overweight"  # 통통


class BehaviorType(str, Enum):
    """목줄 IMU가 온보드에서 분류해 올려주는 행동 이벤트."""
    SCRATCH = "scratch"                  # 긁기
    SHAKE = "shake"                      # 몸 털기
    WALK = "walk"                        # 걷기
    RUN = "run"                          # 뛰기
    POSTURE_CHANGE = "posture_change"    # 자세 변경 / 수면 중 뒤척임


class Allergen(str, Enum):
    FISH = "fish"                    # 연어/생선 -> 어유 오메가3 차단
    CHICKEN = "chicken"
    BEEF = "beef"
    LAMB = "lamb"
    EGG = "egg"
    DAIRY = "dairy"
    WHEAT = "wheat"
    CORN = "corn"
    SOY = "soy"
    ENVIRONMENTAL = "environmental"  # 꽃가루/집먼지 -> 피부축 민감도 상향
    OTHER = "other"


class SurgeryType(str, Enum):
    NEUTER = "neuter"          # 중성화
    PATELLA = "patella"        # 슬개골
    CRUCIATE = "cruciate"      # 십자인대
    DISC = "disc"              # 디스크
    STONE = "stone"            # 결석
    TUMOR = "tumor"            # 종양
    GI = "gi"                  # 소화기
    OTHER = "other"


class Condition(str, Enum):
    """기저질환. 안전 필터 Layer 1의 입력."""
    KIDNEY = "kidney"              # 신장질환
    PANCREATITIS = "pancreatitis"  # 췌장염 병력
    LIVER = "liver"
    HEART = "heart"
    DIABETES = "diabetes"
    EPILEPSY = "epilepsy"
    ARTHRITIS = "arthritis"        # 관절염 진단
    ATOPY = "atopy"                # 아토피
    OBESITY = "obesity"


class Medication(str, Enum):
    """복용 중인 약. 상호작용 차단에 사용."""
    NSAID = "nsaid"                  # 소염진통제
    ANTICOAGULANT = "anticoagulant"  # 항응고제 -> 오메가3 고용량 금지
    STEROID = "steroid"
    ANTICONVULSANT = "anticonvulsant"
    ANTIBIOTIC = "antibiotic"        # 장내 세균총 교란 -> 장건강 카트리지 트리거


class HealthAxis(str, Enum):
    """센서 융합 추론의 4개 축."""
    SKIN = "skin"        # 피부
    JOINT = "joint"      # 관절
    DIGEST = "digest"    # 소화
    SLEEP = "sleep"      # 수면/인지


class MealSlot(str, Enum):
    MORNING = "morning"
    EVENING = "evening"


# ---------------------------------------------------------------------------
# 온보딩 입력 (정적 데이터)
# ---------------------------------------------------------------------------

class Surgery(BaseModel):
    """과거 수술 이력."""
    type: SurgeryType
    years_ago: float = Field(description="대략 몇 년 전인지 (0.5 / 2 / 5 버킷)")


class DogProfile(BaseModel):
    """온보딩 12문항의 결과물."""

    model_config = ConfigDict(use_enum_values=False)

    dog_id: str
    name: str

    # --- 필수 ---
    age_months: int = Field(ge=0, le=300, description="나이(개월)")
    sex: Sex
    neutered: bool
    weight_kg: float = Field(gt=0, le=120)
    body_condition: BodyCondition
    size: DogSize
    meals_per_day: int = Field(ge=1, le=3)
    food_id: str

    # --- 선택 (잡종/유기견이면 비어 있을 수 있음) ---
    breeds: list[str] = Field(default_factory=list, description="최대 2개. 비어 있으면 잡종/모름")

    # --- 안전 필터 입력 ---
    allergies: list[Allergen] = Field(default_factory=list)
    surgeries: list[Surgery] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)

    # 소화축은 IMU로 판정하지 않는다(근거 취약 + 피부축과 교차 오염).
    # 장건강 카트리지는 이 문진 항목으로만 처방된다.
    gi_symptom_reported: bool = Field(
        default=False, description="최근 설사·구토를 보호자가 보고했는가"
    )

    # --- 목표 ---
    target_weight_kg: Optional[float] = Field(default=None, description="비우면 현재 체중 유지")

    birth_date: Optional[date] = None

    # -- 파생값 --------------------------------------------------------------

    @computed_field
    @property
    def is_puppy(self) -> bool:
        """성장기 여부. 사료량 계수가 크게 달라진다."""
        return self.age_months < 12

    @computed_field
    @property
    def is_senior(self) -> bool:
        """노령 기준은 크기별로 다르다. 대형견이 더 빨리 늙는다."""
        threshold = {DogSize.SMALL: 132, DogSize.MEDIUM: 108, DogSize.LARGE: 84}
        return self.age_months >= threshold[self.size]

    @computed_field
    @property
    def rer(self) -> float:
        """휴식기 에너지 요구량(kcal/day) = 70 x 체중^0.75"""
        return 70.0 * (self.weight_kg ** 0.75)

    @computed_field
    @property
    def is_mixed(self) -> bool:
        """견종 미상/잡종. True면 견종 가중치를 중립(1.0)으로 둔다."""
        return len(self.breeds) == 0


# ---------------------------------------------------------------------------
# 센서 데이터 (동적 데이터)
# ---------------------------------------------------------------------------

class BehaviorEvent(BaseModel):
    """
    목줄에서 올라오는 단일 행동 이벤트.

    원시 IMU 파형이 아니라 '분류 결과'만 올린다(A안).
    confidence는 서버가 신뢰도 게이팅에 쓴다.
    """
    ts: datetime
    type: BehaviorType
    confidence: float = Field(ge=0.0, le=1.0)
    duration_s: float = Field(gt=0)


class HourlyBins(BaseModel):
    """
    시간대별 24칸 집계.

    앱 대시보드가 시간대별 막대그래프를 그려야 하므로
    일별 합계가 아니라 1시간 단위로 모아둔다.
    """
    activity_sec: list[int] = Field(min_length=24, max_length=24, description="걷기+뛰기 활동 초")
    scratch: list[int] = Field(min_length=24, max_length=24)
    shake: list[int] = Field(min_length=24, max_length=24)
    posture_change: list[int] = Field(min_length=24, max_length=24)


class SleepSummary(BaseModel):
    total_min: int = Field(ge=0, description="총 수면 시간(분)")
    restless_count: int = Field(ge=0, description="수면 중 뒤척임 횟수")
    night_wake_count: int = Field(ge=0, description="야간 각성 횟수")


class DaySummary(BaseModel):
    """앱 상단 요약 카드에 바로 꽂히는 값들."""
    steps: int

    # 알고리즘은 초 단위를 쓴다.
    # 하루 2~5분만 뛰는 개를 정수 '분'으로 자르면 신호가 뭉개져
    # 완만한 활동량 감소를 놓치게 된다.
    walk_sec: int
    run_sec: int

    # 앱 표시용 (분)
    walk_min: int
    run_min: int
    scratch_total: int
    scratch_night: int = Field(description="22시~04시 긁기. 피부축의 핵심 지표")
    shake_total: int


class DailySummary(BaseModel):
    """
    하루치 정제된 센서 요약. 알고리즘의 입력 단위.

    valid=False인 날은 baseline 계산에서 제외된다.
    """
    date: date
    hourly: HourlyBins
    sleep: SleepSummary
    summary: DaySummary

    wear_ratio: float = Field(ge=0.0, le=1.0, description="목줄 착용 시간 비율")
    low_confidence_ratio: float = Field(ge=0.0, le=1.0, description="confidence 미달로 버린 비율")

    # 급식기 앞 체중계. 개가 올라간 날만 값이 있다.
    # DER 캐스케이드 제어의 '외부 루프' 입력.
    weight_kg: Optional[float] = None

    # 로드셀 잔반 측정. 처방과 실제 섭취는 다르다.
    # 긴급 정지 규칙(3일 연속 섭취 30% 감소)의 입력이기도 하다.
    food_offered_g: int = 0
    food_eaten_g: int = 0

    @computed_field
    @property
    def valid(self) -> bool:
        """착용률 60% 미만인 날은 통계에 넣지 않는다."""
        return self.wear_ratio >= 0.60

    @computed_field
    @property
    def intake_ratio(self) -> float:
        """준 것 대비 실제로 먹은 비율. 1.0이면 완식."""
        if self.food_offered_g <= 0:
            return 1.0
        return round(self.food_eaten_g / self.food_offered_g, 3)


class SensorDataset(BaseModel):
    """생성기가 통째로 내보내는 한 마리 분량의 데이터."""
    profile: DogProfile
    scenario: str
    days: list[DailySummary]
    events: list[BehaviorEvent] = Field(default_factory=list, description="원본 타임라인(디버깅용)")


# ---------------------------------------------------------------------------
# 제품 사양
# ---------------------------------------------------------------------------

class CartridgeSpec(BaseModel):
    """카트리지 1종. 1슬롯 = 1성분."""
    slot: int = Field(ge=1, le=8)
    id: str
    name: str
    color: str = Field(description="앱 UI 색상")

    nutrients_per_pellet: dict[str, float] = Field(
        description="알갱이 1개당 영양소 함량. 키는 constants.Nutrient 값"
    )
    max_pellets_per_day: int = Field(description="기계적/안전 상 하루 최대 알 수")
    meal_slot: MealSlot = Field(description="어느 끼니에 사출할지 (길항 회피)")
    onset_weeks: int = Field(description="효과 발현까지 걸리는 주")
    axes: list[HealthAxis] = Field(description="담당하는 추론 축")


class FoodSpec(BaseModel):
    """사료 1종. 상한 검증 시 '이미 먹고 있는 양'으로 차감된다."""
    id: str
    name: str
    kcal_per_g: float
    nutrients_per_g: dict[str, float]
    excludes: list[Allergen] = Field(default_factory=list, description="이 사료가 빼고 만든 단백질")


# ---------------------------------------------------------------------------
# 출력 (Step 4에서 채워짐)
# ---------------------------------------------------------------------------

class AxisScore(BaseModel):
    """추론 축 하나의 판정 결과."""
    axis: HealthAxis
    z_score: float
    active: bool = Field(description="히스테리시스 통과 여부")
    contributors: dict[str, float] = Field(
        default_factory=dict, description="어떤 지표가 얼마나 기여했는지"
    )
    message: str = Field(default="", description="사용자에게 보여줄 관찰 문장(진단 아님)")


class DispenseItem(BaseModel):
    cartridge_id: str
    name: str
    color: str
    pellets: int
    meal_slot: MealSlot
    reason: str


class TraceStep(BaseModel):
    """PrescriptionTrace의 한 줄. 앱에서 '왜 이렇게 나왔나요?'를 펼치면 이게 보인다."""
    step: str
    detail: str
    changed: bool = Field(default=False, description="이 단계에서 값이 실제로 바뀌었는지")


class Prescription(BaseModel):
    dog_id: str
    date: date

    der_kcal: float
    food_grams: int

    axes: list[AxisScore] = Field(default_factory=list)
    items: list[DispenseItem] = Field(default_factory=list)

    escalated: bool = Field(default=False, description="급성 이상 -> 처방 동결 + 수의사 권고")
    escalation_reason: str = ""

    trace: list[TraceStep] = Field(default_factory=list)
