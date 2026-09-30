package com.petcare.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * 서버의 최종 출력 — 급여 계획.
 *
 * `petcare/core/plan.py` 의 FeedingPlan 과 1:1로 맞췄다.
 * 데모(asset)와 실서버가 **같은 스키마**를 쓴다. 두 모드에서 다른 코드를
 * 타면 한쪽만 깨져도 모른다.
 */

@Serializable
data class Pellet(
    val slot: Int,
    @SerialName("cartridge_id") val cartridgeId: String,
    val name: String,
    val count: Int,
    val color: String,
    val reason: String = "",
)

@Serializable
data class Meal(
    val index: Int,
    val hour: Int,
    @SerialName("food_g") val foodG: Int,
    val pellets: List<Pellet> = emptyList(),
    val label: String = "",
    val line: String = "",
) {
    val timeLabel: String get() = "%02d:00".format(hour)
}

/**
 * 14일 전향 시뮬레이션.
 *
 * 방향만 말한다. "14일 뒤 정확히 4.213kg" 같은 예측은 하지 않는다.
 */
@Serializable
data class Simulation(
    @SerialName("horizon_days") val horizonDays: Int = 14,
    val energy: String,
    val weight: String,
    val exposure: String,
    val stability: String,
    @SerialName("projected_kg") val projectedKg: Double? = null,
    @SerialName("target_kg") val targetKg: Double? = null,
    @SerialName("daily_balance_kcal") val dailyBalanceKcal: Double = 0.0,
    val passed: Boolean = true,
    val reasons: List<String> = emptyList(),
) {
    val energyLabel: String
        get() = when (energy) {
            "BALANCED" -> "균형"
            "MILD_DEFICIT" -> "약간 부족"
            "STRONG_DEFICIT" -> "많이 부족"
            "MILD_SURPLUS" -> "약간 과잉"
            "STRONG_SURPLUS" -> "많이 과잉"
            else -> energy
        }

    val weightLabel: String
        get() = when (weight) {
            "TOWARD_TARGET" -> "목표로 향함"
            "AWAY_FROM_TARGET" -> "목표에서 멀어짐"
            "STABLE" -> "유지"
            else -> "알 수 없음"
        }

    /** 주의가 필요한 결과인가. UI가 면을 바꾸는 기준이 된다. */
    val needsAttention: Boolean
        get() = !passed || weight == "AWAY_FROM_TARGET" || exposure != "ACCEPTABLE"
}

/** 보호자에게 물을 것. 센서가 알 수 없는 맥락만 묻는다. */
@Serializable
data class Question(
    val key: String,
    val axis: String,
    val text: String,
    val type: String = "yes_no",
    val choices: List<String> = emptyList(),
    val why: String = "",
) {
    /** 답변 버튼 목록. 예/아니오 질문은 기본 두 개. */
    val options: List<String>
        get() = if (choices.isNotEmpty()) choices else listOf("네", "아니요")

    /** 서버로 보낼 값. 예/아니오는 yes/no 로 정규화한다. */
    fun valueOf(option: String): String = when {
        choices.isNotEmpty() -> option
        option == "네" -> "yes"
        else -> "no"
    }
}

@Serializable
data class FeedingPlan(
    @SerialName("dog_name") val dogName: String = "",
    val date: String = "",
    val meals: List<Meal> = emptyList(),
    @SerialName("total_food_g") val totalFoodG: Int = 0,
    @SerialName("der_kcal") val derKcal: Double = 0.0,
    val attention: List<String> = emptyList(),
    val observations: List<String> = emptyList(),
    val trace: List<TraceStep> = emptyList(),
    val escalated: Boolean = false,
    @SerialName("escalation_reason") val escalationReason: String = "",
    val simulation: Simulation? = null,
    val questions: List<Question> = emptyList(),
    @SerialName("baseline_stage") val baselineStage: String = "mature",
    val ready: Boolean = true,
    @SerialName("blocked_reason") val blockedReason: String = "",
) {
    val hasSupplements: Boolean get() = meals.any { it.pellets.isNotEmpty() }
}

// ---------------------------------------------------------------------------
// DogTwin — 개체의 현재 상태
// ---------------------------------------------------------------------------

@Serializable
data class BaselineState(
    @SerialName("days_observed") val daysObserved: Int = 0,
    val stage: String = "provisional",
    @SerialName("stage_note") val stageNote: String = "",
    val mature: Boolean = false,
)

@Serializable
data class WeightState(
    @SerialName("current_kg") val currentKg: Double? = null,
    @SerialName("target_kg") val targetKg: Double? = null,
    @SerialName("trend_pct_per_week") val trendPctPerWeek: Double? = null,
    @SerialName("measurements_30d") val measurements30d: Int = 0,
    val direction: String = "unknown",
) {
    val directionLabel: String
        get() = when (direction) {
            "gaining" -> "느는 중"
            "losing" -> "빠지는 중"
            "stable" -> "유지"
            else -> "측정 부족"
        }
}

@Serializable
data class IntakeState(
    @SerialName("recent_ratio") val recentRatio: Double? = null,
    @SerialName("skipped_meals_7d") val skippedMeals7d: Int = 0,
    val measured: Boolean = false,
)

@Serializable
data class DogTwin(
    @SerialName("dog_id") val dogId: String = "",
    val baseline: BaselineState = BaselineState(),
    val wellness: List<AxisScore> = emptyList(),
    val weight: WeightState = WeightState(),
    val intake: IntakeState = IntakeState(),
    @SerialName("wear_ratio") val wearRatio: Double = 0.0,
    @SerialName("data_days") val dataDays: Int = 0,
    @SerialName("ready_to_prescribe") val readyToPrescribe: Boolean = false,
    val attention: List<String> = emptyList(),
)

// ---------------------------------------------------------------------------
// 대화
//
// `petcare/agent/chat.py` 의 ChatTurn 과 1:1.
// 대화록은 서버가 저장된 답변·메모로부터 매번 다시 만든다. 앱은 상태를
// 들고 있지 않다 — 앱을 새로 깔아도 같은 대화가 나온다.
// ---------------------------------------------------------------------------

@Serializable
data class ChatTurn(
    /** "agent" | "guardian" */
    val role: String,
    val text: String,
    /** "text" | "question" | "understood" | "plan" */
    val kind: String = "text",
    val key: String = "",
    val choices: List<String> = emptyList(),
    val why: String = "",
) {
    val fromAgent: Boolean get() = role == "agent"

    /** 서버로 보낼 값. 예/아니오는 yes/no 로 정규화한다. */
    fun valueOf(option: String): String = when {
        choices.size != 2 -> option
        option == "네" -> "yes"
        option == "아니요" -> "no"
        else -> option
    }
}

@Serializable
data class ChatLog(
    @SerialName("dog_name") val dogName: String = "",
    val turns: List<ChatTurn> = emptyList(),
)

@Serializable
data class NoteResult(
    val understood: Map<String, String> = emptyMap(),
    val matched: List<String> = emptyList(),
    val unmatched: Boolean = false,
    val chat: ChatLog = ChatLog(),
)
