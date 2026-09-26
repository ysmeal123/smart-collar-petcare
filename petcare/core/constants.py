"""
제품 사양과 수의학적 기준값.

여기 있는 숫자를 바꾸면 시스템 전체 동작이 바뀐다.
알고리즘 코드에는 매직 넘버를 두지 않고 전부 여기서 가져다 쓴다.

주의: 함량/상한값은 캡스톤 시연용 근사치다.
      실제 제품화 시 수의영양학 검증이 필요하다.
"""

from __future__ import annotations

from core.models import (
    Allergen,
    CartridgeSpec,
    Condition,
    DogSize,
    FoodSpec,
    HealthAxis,
    MealSlot,
    Medication,
    SurgeryType,
)


# ---------------------------------------------------------------------------
# 영양소 키
# ---------------------------------------------------------------------------

class Nutrient:
    EPA_DHA = "epa_dha_mg"
    ZINC = "zinc_mg"
    BIOTIN = "biotin_mg"
    COPPER = "copper_mg"
    VIT_E = "vit_e_iu"
    MCT = "mct_mg"
    GLUCOSAMINE = "glucosamine_mg"
    MSM = "msm_mg"
    GLM = "glm_mg"              # 녹색입홍합
    PROBIOTIC = "probiotic_cfu"
    INULIN = "inulin_mg"
    THEANINE = "theanine_mg"
    TRYPTOPHAN = "tryptophan_mg"


# ---------------------------------------------------------------------------
# 카트리지 8슬롯 (1슬롯 = 1성분)
#
# 알갱이 크기 원칙: "3kg 소형견이 하루 1~2알"이 되도록 함량을 정한다.
#                  대형견은 같은 알갱이를 여러 개 받는다.
#                  이렇게 하면 반올림 오차가 허용 범위에 들어와
#                  잔차 이월(carry-over) 로직이 불필요해진다.
#
# 종합비타민과 칼슘은 의도적으로 제외했다.
#   - 사료가 AAFCO 완전균형식이므로 효과는 0이고 상한 초과 위험만 생긴다
#   - 칼슘 부족은 IMU로 감지할 수 없다. 추론 못 하는 것은 처방하지 않는다
# ---------------------------------------------------------------------------

CARTRIDGES: list[CartridgeSpec] = [
    CartridgeSpec(
        slot=1, id="omega3", name="오메가3", color="#F2994A",
        nutrients_per_pellet={Nutrient.EPA_DHA: 80.0},
        max_pellets_per_day=20,
        meal_slot=MealSlot.MORNING,
        onset_weeks=6,
        axes=[HealthAxis.SKIN, HealthAxis.MOBILITY],
    ),
    CartridgeSpec(
        slot=2, id="skin_barrier", name="피부장벽", color="#F2C94C",
        nutrients_per_pellet={Nutrient.BIOTIN: 0.5, Nutrient.ZINC: 2.0},
        max_pellets_per_day=12,
        # 4번(식이섬유)과 다른 끼니에 배치 -> 아연 흡수 경쟁 회피
        meal_slot=MealSlot.EVENING,
        onset_weeks=9,
        axes=[HealthAxis.SKIN],
    ),
    CartridgeSpec(
        slot=3, id="joint", name="관절", color="#2D9CDB",
        nutrients_per_pellet={
            Nutrient.GLUCOSAMINE: 50.0, Nutrient.MSM: 25.0, Nutrient.GLM: 20.0
        },
        max_pellets_per_day=20,
        meal_slot=MealSlot.EVENING,
        onset_weeks=5,
        axes=[HealthAxis.MOBILITY],
    ),
    CartridgeSpec(
        slot=4, id="gut", name="장건강", color="#27AE60",
        nutrients_per_pellet={Nutrient.PROBIOTIC: 5e8, Nutrient.INULIN: 50.0},
        max_pellets_per_day=10,
        meal_slot=MealSlot.MORNING,
        onset_weeks=2,
        # 축이 없다. 소화축은 근거가 취약해 제거했고,
        # 이 카트리지는 문진(GUT_TRIGGERS)으로만 처방된다.
        axes=[],
    ),
    CartridgeSpec(
        slot=5, id="calm", name="진정·수면", color="#9B51E0",
        nutrients_per_pellet={Nutrient.THEANINE: 12.5, Nutrient.TRYPTOPHAN: 25.0},
        max_pellets_per_day=12,
        # 저녁 배치: 밤에 자야 하므로
        meal_slot=MealSlot.EVENING,
        onset_weeks=1,
        axes=[HealthAxis.SLEEP],
    ),
    CartridgeSpec(
        slot=6, id="cognition", name="인지·항산화", color="#EB5757",
        nutrients_per_pellet={Nutrient.MCT: 100.0, Nutrient.VIT_E: 5.0},
        max_pellets_per_day=15,
        # 아침 배치: 케톤을 주간 인지활동에 공급
        meal_slot=MealSlot.MORNING,
        onset_weeks=6,
        axes=[HealthAxis.SLEEP],
    ),
    CartridgeSpec(
        slot=7, id="algae_omega3", name="조류 오메가3", color="#BDBDBD",
        nutrients_per_pellet={Nutrient.EPA_DHA: 80.0},
        max_pellets_per_day=20,
        meal_slot=MealSlot.MORNING,
        onset_weeks=6,
        # 1번(어유)이 생선 알러지로 차단됐을 때 자동 대체된다
        axes=[HealthAxis.SKIN, HealthAxis.MOBILITY],
    ),
]

CARTRIDGE_BY_ID = {c.id: c for c in CARTRIDGES}

# 생선 알러지 -> 어유를 조류 유래로 교체
CARTRIDGE_SUBSTITUTE = {"omega3": "algae_omega3"}


# ---------------------------------------------------------------------------
# 사료
# ---------------------------------------------------------------------------

FOODS: list[FoodSpec] = [
    FoodSpec(
        id="core_chicken", name="코어 밸런스 (닭)",
        kcal_per_g=3.6,
        nutrients_per_g={
            Nutrient.ZINC: 0.15,
            Nutrient.VIT_E: 0.10,
            Nutrient.EPA_DHA: 0.50,
            Nutrient.COPPER: 0.015,
        },
    ),
    FoodSpec(
        id="core_salmon", name="코어 밸런스 (연어)",
        kcal_per_g=3.7,
        nutrients_per_g={
            Nutrient.ZINC: 0.15,
            Nutrient.VIT_E: 0.11,
            Nutrient.EPA_DHA: 1.20,
            Nutrient.COPPER: 0.015,
        },
        excludes=[Allergen.CHICKEN],
    ),
    FoodSpec(
        id="core_lamb_ld", name="코어 저알러지 (양, 제한식)",
        kcal_per_g=3.5,
        nutrients_per_g={
            Nutrient.ZINC: 0.16,
            Nutrient.VIT_E: 0.12,
            Nutrient.EPA_DHA: 0.30,
            Nutrient.COPPER: 0.014,
        },
        excludes=[Allergen.CHICKEN, Allergen.BEEF, Allergen.FISH],
    ),
]

FOOD_BY_ID = {f.id: f for f in FOODS}


# ---------------------------------------------------------------------------
# 안전 상한 (Layer 2)
#
# 단위: 체중 1kg당 하루 섭취 상한.
# 여기에 SAFETY_MARGIN을 곱해 보수적으로 쓴다.
#
# 30일 누적 상한 검사는 생략하고, 대신 상한 자체를 80%로 조여
# 안전계수 하나로 대체했다. (서브시스템 하나 -> 상수 하나)
# ---------------------------------------------------------------------------

SAFETY_MARGIN = 0.80

UPPER_LIMIT_PER_KG: dict[str, float] = {
    Nutrient.EPA_DHA: 100.0,   # mg/kg/day  출혈 경향 고려
    Nutrient.ZINC: 8.0,        # mg/kg/day  과다 시 구리 결핍 유발
    Nutrient.VIT_E: 10.0,      # IU/kg/day  지용성, 축적성
    Nutrient.MCT: 500.0,       # mg/kg/day  지방 부하
    Nutrient.COPPER: 0.5,      # mg/kg/day
}

# 사료 외 간식으로 들어오는 미측정분. DER의 이 비율만큼 헤드룸을 미리 비워둔다.
TREAT_RESERVE_RATIO = 0.10


# ---------------------------------------------------------------------------
# 절대 금기 (Layer 1)
# ---------------------------------------------------------------------------

BLOCKED_BY_CONDITION: dict[Condition, list[str]] = {
    Condition.PANCREATITIS: ["omega3", "algae_omega3", "cognition"],  # 고지방 부하
    Condition.KIDNEY: ["cognition"],                                   # 비타민D/인 계열 주의
}

BLOCKED_BY_MEDICATION: dict[Medication, list[str]] = {
    Medication.ANTICOAGULANT: ["omega3", "algae_omega3"],  # 혈소판 응집 억제 중첩
    Medication.NSAID: [],  # 차단 대신 용량 상한을 절반으로 (SOFT_LIMIT 참조)
}

# 차단까지는 아니고 용량만 조이는 경우
SOFT_LIMIT_BY_MEDICATION: dict[Medication, dict[str, float]] = {
    Medication.NSAID: {"omega3": 0.5, "algae_omega3": 0.5},
}

BLOCKED_BY_ALLERGY: dict[Allergen, list[str]] = {
    Allergen.FISH: ["omega3"],  # 어유. 조류 오메가3로 자동 대체된다
}

# 수술 이력이 baseline과 기본 처방에 미치는 영향
#   십자인대 수술한 개는 원래 덜 뛴다.
#   이걸 모르면 "관절 악화 중"으로 오판해 영양제를 계속 늘리게 된다.
SURGERY_EFFECT: dict[SurgeryType, dict] = {
    SurgeryType.PATELLA:  {"axis": HealthAxis.MOBILITY, "baseline_shift": -0.8, "prime": "joint"},
    SurgeryType.CRUCIATE: {"axis": HealthAxis.MOBILITY, "baseline_shift": -1.0, "prime": "joint"},
    SurgeryType.DISC:     {"axis": HealthAxis.MOBILITY, "baseline_shift": -0.7, "prime": "joint"},
    # 소화기 수술은 축을 바꾸지 않는다. 소화축이 없어졌기 때문이다.
    # 장건강 카트리지를 후보로 올리는 역할만 한다.
    SurgeryType.GI:       {"axis": None, "baseline_shift": 0.0, "prime": "gut"},
}

# 알러지가 축 민감도에 미치는 영향 (이미 알러지 체질이면 더 빨리 잡는다)
ALLERGY_AXIS_SENSITIVITY: dict[Allergen, dict[HealthAxis, float]] = {
    Allergen.ENVIRONMENTAL: {HealthAxis.SKIN: 1.25},
}


# ---------------------------------------------------------------------------
# 길항 (끼니 분리로 해결)
#
# 칼슘 카트리지를 제거하면서 칼슘 관련 길항 규칙 3개가 삭제되었다.
# 남은 것은 아래 2개뿐이다.
# ---------------------------------------------------------------------------

ANTAGONIST_PAIRS: list[tuple[str, str, str]] = [
    ("gut", "skin_barrier", "식이섬유가 아연 흡수를 방해 -> 다른 끼니로 분리"),
]

# 아연을 장기 고용량으로 주면 구리가 결핍된다. 이 비율을 넘지 않게 감시.
ZINC_COPPER_MAX_RATIO = 15.0

# 1끼만 먹는 개는 길항 성분을 분리할 수 없다.
# 흡수율이 떨어지는 만큼 용량을 보정한다.
SINGLE_MEAL_ABSORPTION_PENALTY = 0.25


# ---------------------------------------------------------------------------
# 사료량 (DER) 계수
# ---------------------------------------------------------------------------

K_BASE_NEUTERED = 1.6
K_BASE_INTACT = 1.8
K_BASE_SENIOR = 1.4
K_BASE_WEIGHT_LOSS = 1.0
K_BASE_PUPPY_EARLY = 3.0    # 4개월 미만
K_BASE_PUPPY_LATE = 2.0     # 4~12개월

# 활동량 z-score가 활동계수를 얼마나 움직이는가
K_ACTIVITY_GAIN = 0.15
# 활동계수가 기준값에서 벗어날 수 있는 범위
K_CLAMP_RATIO = 0.15

# 활동계수의 절대 하한.
#
# RER은 '가만히 있어도 필요한 최소 에너지'다. 활동량이 아무리 낮아도
# DER이 RER 아래로 내려가면 안 된다. 체중 감량 프로토콜에서도
# (목표 체중 기준) RER x 1.0이 하한이다.
K_ABSOLUTE_MIN = 1.0
# 급여량 하루 변동 상한. 위장 부담을 피하기 위한 슬루율 제한.
DAILY_GRAM_SLEW_RATIO = 0.10

# 외부 루프: 체중이 주당 이 이상 변하면 활동계수 기준값을 보정한다
WEIGHT_DRIFT_THRESHOLD = 0.01   # ±1%/주
WEIGHT_CORRECTION_STEP = 0.05   # k_base ∓5%

# 체형 -> 목표 체중 배율
TARGET_WEIGHT_RATIO = {
    "thin": 1.08,
    "ideal": 1.00,
    "overweight": 0.88,
}


# ---------------------------------------------------------------------------
# 신호 정제 / 추론 임계값
# ---------------------------------------------------------------------------

CONFIDENCE_GATE = 0.60        # 이 미만 이벤트는 버린다
STEPS_PER_WALK_SEC = 1.2      # 걷기 1초당 걸음 수. 보수계가 없을 때만 쓴다
WEAR_RATIO_GATE = 0.60        # 이 미만인 날은 통계에서 제외
BASELINE_DAYS = 30            # baseline 산출 구간
RECENT_DAYS = 7               # 최근값 구간 (중앙값 -> 임펄스 자동 제거)
MAD_TO_SIGMA = 1.4826         # MAD를 표준편차 스케일로 변환

# 중앙값의 표준오차 계수.
#
# z의 분자는 '7일 중앙값'인데 분모를 '일별 MAD'로 두면 분모가 과대평가되어
# 신호를 절반 가까이 깎아먹는다. 7일 중앙값은 하루치보다 훨씬 안정적이므로
# 표본 수로 보정해야 한다.
#
#     se = MAD_TO_SIGMA * MAD * MEDIAN_SE_FACTOR / sqrt(유효일수)
#
# 유효일수 7일이면 보정 계수는 0.47배가 되어, 같은 변화량에 z가 약 2배로 커진다.
MEDIAN_SE_FACTOR = 1.253

Z_ENTER = 2.0                 # 히스테리시스 진입
Z_EXIT = 0.5                  # 히스테리시스 해제
MIN_MAD = 0.5                 # MAD가 0이면 z가 발산한다. 하한을 둔다.

# 축 점수가 이 주(週) 수만큼 연속으로 임계를 넘어야 실제로 처방한다.
#
# 지표 4개 x 축 4개를 매주 검정하면, 아무 이상 없는 개도
# 우연히 z가 2를 넘는 주가 생긴다(다중비교 문제).
# 7일 중앙값은 하루 튄 값은 지우지만, 7일 창 전체가 우연히 낮은 경우는 못 막는다.
#
# 어차피 영양제는 주 단위 갱신이므로 2주 연속을 요구해도 실기하지 않는다.
PERSISTENCE_WEEKS = 2

# 축마다 관찰 창 길이가 다르다.
#
# 피부 가려움은 며칠 만에 확 심해지므로 7일 창이면 잡힌다.
# 반면 관절 악화는 몇 달에 걸쳐 진행하고 활동량 자체의 일별 변동이 커서(CV 40%+),
# 7일 창으로는 신호가 노이즈에 묻힌다. 창을 14일로 늘리면
# 표준오차가 sqrt(2)배 줄어 민감도가 약 40% 오른다.
#
# 창 길이 x 연속 횟수 >= 14일이 되도록 맞춰, 어느 축이든
# 최소 2주는 관찰한 뒤에 처방하도록 통일한다.
AXIS_WINDOW_DAYS: dict[HealthAxis, int] = {
    HealthAxis.SKIN: 7,
    HealthAxis.MOBILITY: 14,   # 만성·저신호 -> 창을 두 배로
    HealthAxis.EAR: 7,
    HealthAxis.SLEEP: 7,
    HealthAxis.APPETITE: 3,    # 섭취 급락은 빨리 잡아야 한다
}

AXIS_PERSISTENCE: dict[HealthAxis, int] = {
    HealthAxis.SKIN: 2,
    HealthAxis.MOBILITY: 1,    # 이미 14일을 봤으므로 1회로 충분
    HealthAxis.EAR: 2,
    HealthAxis.SLEEP: 2,
    HealthAxis.APPETITE: 1,    # 차단 전용이라 빠르게 반응해도 안전하다
}

# 급여 시각. 식후 2시간이 소화축의 관찰 구간이 된다.
MEAL_HOURS = [8, 19]

NIGHT_HOURS = list(range(22, 24)) + list(range(0, 4))   # 22시~04시 (깊은 밤)
SLEEP_HOURS = [22, 23, 0, 1, 2, 3, 4, 5, 6]              # 수면 창 (22시~07시)
POST_MEAL_HOURS = 2                                      # 식후 몇 시간을 볼지

# ---------------------------------------------------------------------------
# 축마다 권한이 다르다
#
# 다섯 축 중 영양제를 실제로 움직일 수 있는 건 두 개뿐이다.
# 센서로 추론 가능한 것만 처방한다는 원칙을 축 단위로 강제한 결과다.
# ---------------------------------------------------------------------------

# 처방 권한이 있는 축.
# SLEEP은 여기 있지만 AXIS_SUPPRESSED_BY로 조건이 걸린다.
PRESCRIBING_AXES: list[HealthAxis] = [
    HealthAxis.SKIN,
    HealthAxis.MOBILITY,
    HealthAxis.SLEEP,
]

# 알림만 한다. 영양제를 움직이지 않는다.
#
# 머리 흔들기는 외이염의 신호일 수 있지만, 그건 영양으로 다룰 문제가 아니라
# 병원에 가야 할 문제다. 영양제로 덮으면 진료가 늦어진다.
ALERT_ONLY_AXES: list[HealthAxis] = [HealthAxis.EAR]

# 차단만 한다. 발화하면 영양제 변경 자체를 막는다.
#
# 밥을 안 먹는 개에게 영양제를 늘리는 건 위험하다.
# 원인을 모르는 채 조성을 바꾸면 상태를 더 흐린다.
BLOCKING_AXES: list[HealthAxis] = [HealthAxis.APPETITE]

# 앱에 표시는 하되 처방하지 않는 축 전부
OBSERVATION_ONLY_AXES: list[HealthAxis] = ALERT_ONLY_AXES + BLOCKING_AXES

# 종속 축 억제.
#
# 피부가 가려우면 잠을 못 자고, 관절이 아파도 잠을 못 잔다.
# 이때 수면축이 발화한다고 진정제를 주는 것은 '결과'를 덮는 것이다.
# 원인 축이 이미 처방 중이면 수면축 처방은 보류하고, 원인이 해결된 뒤에
# 그래도 수면이 나쁘면 그때 처방한다.
AXIS_SUPPRESSED_BY: dict[HealthAxis, list[HealthAxis]] = {
    HealthAxis.SLEEP: [HealthAxis.SKIN, HealthAxis.MOBILITY, HealthAxis.EAR],
}

# 축별 MAD 하한.
#
# 희소 이벤트는 baseline MAD가 0이 되어 z가 발산한다.
# 특히 귀축이 그렇다 - 정상 개는 머리를 하루 0~2회 흔든다.
# 공통 하한(0.5)을 쓰면 하루 두 번 흔든 게 z=+8로 나오고,
# 분류기 오탐 몇 건이 그대로 축 발화가 된다.
#
# 섭취율은 0~1 비율이라 스케일 자체가 다르다. 하한을 훨씬 작게 잡아야
# 신호가 살아남는다.
AXIS_MIN_MAD: dict[HealthAxis, float] = {
    HealthAxis.SKIN: 0.5,
    HealthAxis.MOBILITY: 0.5,
    HealthAxis.EAR: 2.0,       # 희소 -> 하한을 높여 오탐을 막는다
    HealthAxis.SLEEP: 0.5,
    HealthAxis.APPETITE: 0.05,  # 비율 스케일
}

# 장건강 카트리지의 처방 조건 (IMU가 아니라 문진 기반)
GUT_TRIGGERS = {
    "antibiotic": True,        # 항생제 복용 중 -> 장내 세균총 회복
    "gi_symptom_reported": True,  # 보호자가 설사/구토 보고
    "gi_surgery_recent_years": 1.0,
}


# 축별 지표 가중치. 합이 1.0이 되도록 맞춘다.
# 음수 부호는 "줄어드는 것이 나쁜 신호"라는 뜻이다(활동량 등).
AXIS_WEIGHTS: dict[HealthAxis, dict[str, float]] = {
    HealthAxis.SKIN: {
        "scratch_night": 0.5,
        "body_shake": 0.3,      # 가려우면 몸을 턴다
        "restless_night": 0.2,
    },
    HealthAxis.MOBILITY: {
        # 분 단위로 자르면 하루 2~5분만 뛰는 개의 신호가 뭉개진다. 초 단위를 쓴다.
        "run_sec": -0.35,
        "walk_sec": -0.25,
        "vigorous_sec": -0.25,
        "restless_night": 0.15,  # 아프면 밤에 뒤척인다
    },
    HealthAxis.EAR: {
        # 지표가 하나뿐이다. 그래서 AXIS_MIN_MAD를 높게 잡았다.
        "head_shake": 1.0,
    },
    HealthAxis.SLEEP: {
        "restless": 0.5,
        "sleep_min": -0.3,
        "night_activity": 0.2,
    },
    HealthAxis.APPETITE: {
        # 로드셀 직접 측정. IMU 추론이 아니라서 근거가 가장 강하다.
        "intake_ratio": -1.0,
    },
}

# 크기별 관절축 민감도 (소형=슬개골, 대형=고관절 호발)
SIZE_MOBILITY_SENSITIVITY = {
    DogSize.SMALL: 1.15,
    DogSize.MEDIUM: 1.0,
    DogSize.LARGE: 1.20,
}


# ---------------------------------------------------------------------------
# 축 -> 카트리지 용량 매핑
#
# 값은 "z가 최대 심각도일 때 체중 1kg당 목표 용량".
# 실제 처방은 여기에 심각도(0~1)를 곱해서 정한다.
# ---------------------------------------------------------------------------

AXIS_DOSE: dict[HealthAxis, dict[str, float]] = {
    HealthAxis.SKIN: {
        "omega3": 60.0,        # mg EPA+DHA / kg
        "skin_barrier": 0.4,   # mg zinc / kg
    },
    HealthAxis.MOBILITY: {
        "omega3": 50.0,
        "joint": 15.0,         # mg glucosamine / kg
    },
    # 소화축은 처방 권한이 없다. 장건강은 GUT_TRIGGERS로만 나간다.
    HealthAxis.SLEEP: {
        "calm": 3.0,           # mg theanine / kg
        "cognition": 25.0,     # mg MCT / kg
    },
}

# 이 용량이 어느 영양소 기준인지
AXIS_DOSE_NUTRIENT: dict[str, str] = {
    "omega3": Nutrient.EPA_DHA,
    "algae_omega3": Nutrient.EPA_DHA,
    "skin_barrier": Nutrient.ZINC,
    "joint": Nutrient.GLUCOSAMINE,
    "gut": Nutrient.PROBIOTIC,        # GUT_TRIGGERS로만 처방된다
    "calm": Nutrient.THEANINE,
    "cognition": Nutrient.MCT,
}

# 장건강은 축 점수가 아니라 문진 트리거로 정액 처방한다
GUT_DOSE_PER_KG = 1.5e8   # CFU / kg


# ---------------------------------------------------------------------------
# 종료 규칙
# ---------------------------------------------------------------------------

SUNSET_WEEKS = 8              # 이 기간 개선 없으면 중단 + 수의사 권고
RELAPSE_WATCH_WEEKS = 4       # 종료 후 재발 감시 기간(baseline 동결 유지)


# ---------------------------------------------------------------------------
# 긴급 정지 (Layer 0) — 다른 모든 로직에 우선한다
# ---------------------------------------------------------------------------

# 급성 판정은 '직전 상태 대비 급변'을 본다.
#
# 30일 baseline과 비교하면 두 가지가 다 깨진다:
#   - 만성 질환이 충분히 진행되면 절대 z가 커져 급성으로 오판한다
#     (관절염 시나리오에서 실제로 오발동했다)
#   - 급성 발생 직후엔 긴 창에 정상일이 섞여 신호가 희석된다
# 따라서 참조 구간은 baseline이 아니라 '최근 3일을 제외한 직전 14일'이다.
ESCALATION_RULES = {
    # 활동량이 급락하고 야간 각성이 치솟으면 급성 통증을 의심한다
    "acute_pain": {
        "activity_z": -3.0,
        "night_wake_z": 2.0,
        "window_days": 3,     # 최근 3일
        "ref_days": 14,       # 그 직전 14일과 비교
    },
    # 3일 연속 섭취량이 30% 이상 줄면 이상 신호
    "appetite_drop": {"days": 3, "ratio": 0.70},
}
