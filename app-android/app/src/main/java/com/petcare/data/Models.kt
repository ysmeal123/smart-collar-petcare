package com.petcare.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * 백엔드 응답 스키마.
 *
 * Python 쪽 Pydantic 모델이 snake_case로 직렬화하므로 @SerialName으로 맞춘다.
 * 백엔드가 보내는 필드 중 앱이 안 쓰는 것이 많아 ignoreUnknownKeys를 켠다.
 */

@Serializable
data class DogProfile(
    val name: String,
    @SerialName("age_months") val ageMonths: Int,
    @SerialName("weight_kg") val weightKg: Double,
    val breeds: List<String> = emptyList(),
    @SerialName("meals_per_day") val mealsPerDay: Int,
) {
    val ageLabel: String
        get() = if (ageMonths < 12) "${ageMonths}개월" else "${ageMonths / 12}세"

    /** 견종은 선택 항목이다. 잡종·유기견 입양 케이스를 지원해야 한다. */
    val breedLabel: String
        get() = if (breeds.isEmpty()) "믹스" else breeds.joinToString(" · ")

    val subtitle: String
        get() = "$ageLabel · ${weightKg}kg · $breedLabel"
}

@Serializable
data class HourlyBins(
    @SerialName("activity_sec") val activitySec: List<Int>,
    val scratch: List<Int>,
    @SerialName("posture_change") val postureChange: List<Int>,
)

@Serializable
data class SleepSummary(
    @SerialName("total_min") val totalMin: Int,
    @SerialName("restless_count") val restless: Int,
)

@Serializable
data class DaySummaryFields(
    val steps: Int,
    @SerialName("walk_min") val walkMin: Int,
    @SerialName("run_min") val runMin: Int,
    @SerialName("scratch_night") val scratchNight: Int,
)

@Serializable
data class DaySummary(
    val date: String,
    val hourly: HourlyBins,
    val sleep: SleepSummary,
    val summary: DaySummaryFields,
    @SerialName("wear_ratio") val wearRatio: Double,
    @SerialName("intake_ratio") val intakeRatio: Double = 1.0,
) {
    val activeSec: Int get() = hourly.activitySec.sum()

    val sleepLabel: String
        get() {
            val h = sleep.totalMin / 60
            val m = sleep.totalMin % 60
            return if (m == 0) "${h}시간" else "${h}시간 ${m}분"
        }
}

@Serializable
data class AxisScore(
    val axis: String,
    @SerialName("z_score") val z: Double,
    val active: Boolean,
    val message: String = "",
) {
    val label: String
        get() = when (axis) {
            "skin" -> "피부"
            "mobility" -> "이동성"
            "ear" -> "귀"
            "sleep" -> "수면·회복"
            "appetite" -> "식욕"
            else -> axis
        }
}

@Serializable
data class DispenseItem(
    @SerialName("cartridge_id") val cartridgeId: String,
    val name: String,
    val color: String,
    val pellets: Int,
    @SerialName("meal_slot") val mealSlot: String,
    val reason: String = "",
) {
    val isMorning: Boolean get() = mealSlot == "morning"
}

/** 처방 근거 한 줄. 앱에서 '왜 이렇게 나왔나요?'를 펼치면 이게 보인다. */
@Serializable
data class TraceStep(
    val step: String,
    val detail: String,
    val changed: Boolean = false,
)

@Serializable
data class Prescription(
    @SerialName("der_kcal") val derKcal: Double,
    @SerialName("food_grams") val foodGrams: Int,
    val axes: List<AxisScore>,
    val items: List<DispenseItem>,
    val escalated: Boolean,
    @SerialName("escalation_reason") val escalationReason: String = "",
    val trace: List<TraceStep>,
) {
    /** 실제로 처방을 발생시킨 축 (참고 지표나 억제된 축은 제외된다) */
    val firedAxes: List<AxisScore> get() = axes.filter { it.active }

    val morning: List<DispenseItem> get() = items.filter { it.isMorning }
    val evening: List<DispenseItem> get() = items.filter { !it.isMorning }
}

/** 차트의 '평소 수준' 점선에 쓸 baseline */
@Serializable
data class Trend(
    val values: List<Double>,
    val baseline: Double,
)

@Serializable
data class Dashboard(
    val scenario: String,
    val profile: DogProfile,
    @SerialName("recent_days") val days: List<DaySummary>,
    val prescription: Prescription,
    val trend: Map<String, Trend>,
    /** 최종 출력. 서버와 데모 asset 모두 같은 모양으로 담는다. */
    val plan: FeedingPlan? = null,
    /** 개체의 현재 상태. 웰니스 5축 화면이 읽는다. */
    val twin: DogTwin? = null,
) {
    val today: DaySummary get() = days.last()
}
