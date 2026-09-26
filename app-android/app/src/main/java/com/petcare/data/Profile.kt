package com.petcare.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlin.math.pow
import kotlin.math.roundToInt

/**
 * 온보딩으로 받는 정적 데이터.
 *
 * 필드와 값은 백엔드 `petcare/core/models.py` 의 DogProfile 과 1:1로 맞췄다.
 * 그대로 직렬화해서 POST 하면 서버가 받는다.
 */

@Serializable
enum class Sex(val label: String) {
    @SerialName("male") MALE("남아"),
    @SerialName("female") FEMALE("여아"),
}

@Serializable
enum class DogSize(val label: String, val detail: String) {
    @SerialName("small") SMALL("소형견", "10kg 미만"),
    @SerialName("medium") MEDIUM("중형견", "10~25kg"),
    @SerialName("large") LARGE("대형견", "25kg 이상"),
}

/** 숫자(BCS 1~9)로 물으면 보호자가 답하지 못한다. 실루엣 3장으로 받는다. */
@Serializable
enum class BodyCondition(val label: String, val detail: String, val waist: Float) {
    @SerialName("thin") THIN("마른 편", "갈비뼈가 만져지고 보여요", 0.42f),
    @SerialName("ideal") IDEAL("적당해요", "갈비뼈가 만져지지만 보이진 않아요", 0.62f),
    @SerialName("overweight") OVERWEIGHT("통통한 편", "갈비뼈가 잘 안 만져져요", 0.88f),
}

@Serializable
enum class ActivityLevel(val label: String, val detail: String) {
    @SerialName("low") LOW("차분해요", "산책은 짧게, 대부분 쉬어요"),
    @SerialName("normal") NORMAL("보통이에요", "하루 30분~1시간 산책"),
    @SerialName("high") HIGH("활발해요", "매일 오래 뛰어놀아요"),
}

@Serializable
enum class Allergen(val label: String) {
    @SerialName("fish") FISH("생선·연어"),
    @SerialName("chicken") CHICKEN("닭고기"),
    @SerialName("beef") BEEF("소고기"),
    @SerialName("lamb") LAMB("양고기"),
    @SerialName("egg") EGG("달걀"),
    @SerialName("dairy") DAIRY("유제품"),
    @SerialName("wheat") WHEAT("밀"),
    @SerialName("corn") CORN("옥수수"),
    @SerialName("soy") SOY("콩"),
    @SerialName("environmental") ENVIRONMENTAL("꽃가루·집먼지"),
    @SerialName("other") OTHER("기타"),
}

@Serializable
enum class SurgeryType(val label: String) {
    @SerialName("neuter") NEUTER("중성화"),
    @SerialName("patella") PATELLA("슬개골 탈구"),
    @SerialName("cruciate") CRUCIATE("십자인대"),
    @SerialName("disc") DISC("디스크"),
    @SerialName("stone") STONE("결석"),
    @SerialName("tumor") TUMOR("종양 제거"),
    @SerialName("gi") GI("소화기"),
    @SerialName("other") OTHER("기타"),
}

@Serializable
enum class Condition(val label: String) {
    @SerialName("kidney") KIDNEY("신장질환"),
    @SerialName("pancreatitis") PANCREATITIS("췌장염 병력"),
    @SerialName("liver") LIVER("간질환"),
    @SerialName("heart") HEART("심장질환"),
    @SerialName("diabetes") DIABETES("당뇨"),
    @SerialName("epilepsy") EPILEPSY("뇌전증"),
    @SerialName("arthritis") ARTHRITIS("관절염"),
    @SerialName("atopy") ATOPY("아토피"),
    @SerialName("obesity") OBESITY("비만"),
}

@Serializable
enum class Medication(val label: String) {
    @SerialName("nsaid") NSAID("소염진통제"),
    @SerialName("anticoagulant") ANTICOAGULANT("항응고제"),
    @SerialName("steroid") STEROID("스테로이드"),
    @SerialName("anticonvulsant") ANTICONVULSANT("항경련제"),
    @SerialName("antibiotic") ANTIBIOTIC("항생제"),
}

@Serializable
data class Surgery(
    val type: SurgeryType,
    @SerialName("years_ago") val yearsAgo: Double,
)

/** 온보딩 결과. 이 객체 하나가 알고리즘의 정적 입력 전부다. */
@Serializable
data class MyDog(
    @SerialName("dog_id") val dogId: String = "dog-local",
    val name: String = "",
    @SerialName("age_months") val ageMonths: Int = 24,
    val sex: Sex = Sex.MALE,
    val neutered: Boolean = false,
    @SerialName("weight_kg") val weightKg: Double = 5.0,
    @SerialName("body_condition") val bodyCondition: BodyCondition = BodyCondition.IDEAL,
    val size: DogSize = DogSize.SMALL,
    @SerialName("meals_per_day") val mealsPerDay: Int = 2,
    @SerialName("meal_hours") val mealHours: List<Int> = listOf(8, 19),
    @SerialName("activity_level") val activityLevel: ActivityLevel = ActivityLevel.NORMAL,
    val breeds: List<String> = emptyList(),
    val allergies: List<Allergen> = emptyList(),
    val surgeries: List<Surgery> = emptyList(),
    val conditions: List<Condition> = emptyList(),
    val medications: List<Medication> = emptyList(),
    /** 9~12번을 건너뛰었는지. 처방 근거에 이 사실을 표시해야 한다. */
    @SerialName("skipped_optional") val skippedOptional: Boolean = false,

    // --- 서버 연결 ---
    //
    // 비어 있으면 asset 데모 데이터로 돈다. 발표 중 네트워크가 끊겨도
    // 화면이 비지 않아야 하기 때문에 서버는 선택 사항으로 둔다.
    @SerialName("server_base") val serverBase: String = "",
    @SerialName("server_dog_id") val serverDogId: String = "",
) {
    /** 휴식기 에너지 요구량. 모든 급여량 계산의 출발점이다. */
    val rer: Double get() = 70.0 * weightKg.pow(0.75)

    val isPuppy: Boolean get() = ageMonths < 12

    val isSenior: Boolean
        get() = ageMonths >= when (size) {
            DogSize.SMALL -> 132
            DogSize.MEDIUM -> 108
            DogSize.LARGE -> 84
        }

    val ageLabel: String
        get() = when {
            ageMonths < 12 -> "${ageMonths}개월"
            ageMonths % 12 == 0 -> "${ageMonths / 12}세"
            else -> "${ageMonths / 12}세 ${ageMonths % 12}개월"
        }

    val breedLabel: String get() = if (breeds.isEmpty()) "믹스" else breeds.joinToString(" · ")

    val subtitle: String get() = "$ageLabel · ${weightKg}kg · $breedLabel"

    /**
     * 알러지에 맞는 사료를 고른다.
     * 백엔드 FOODS 의 excludes 를 그대로 뒤집은 것이다.
     */
    val recommendedFood: Pair<String, String>
        get() = when {
            Allergen.CHICKEN in allergies &&
                (Allergen.BEEF in allergies || Allergen.FISH in allergies) ->
                "core_lamb_ld" to "코어 저알러지 (양, 제한식)"

            Allergen.CHICKEN in allergies ->
                "core_salmon" to "코어 밸런스 (연어)"

            else ->
                "core_chicken" to "코어 밸런스 (닭)"
        }

    /** 프로필만으로 채워지는 값이 몇 %인지. 온보딩 완료 화면에서 쓴다. */
    val completeness: Int
        get() {
            var filled = 0
            if (name.isNotBlank()) filled++
            if (weightKg > 0) filled++
            if (breeds.isNotEmpty()) filled++
            if (allergies.isNotEmpty() || !skippedOptional) filled++
            if (surgeries.isNotEmpty() || !skippedOptional) filled++
            return ((filled / 5.0) * 100).roundToInt()
        }
}

// ---------------------------------------------------------------------------
// 안전 필터 미리보기
// ---------------------------------------------------------------------------

/**
 * 프로필이 처방을 어떻게 바꾸는지 보여준다.
 *
 * 백엔드 `constants.py` 의 BLOCKED_BY_* / SURGERY_EFFECT / CARTRIDGE_SUBSTITUTE 를
 * 그대로 옮긴 것이다. 판정 권한은 서버에 있고 이건 표시용 사본이다.
 * 규칙을 바꾸려면 서버를 먼저 고치고 여기를 맞춰야 한다.
 */
data class SafetyRule(
    val layer: String,
    val title: String,
    val detail: String,
    /** 처방을 실제로 막는 규칙인지, 민감도만 조정하는 규칙인지. */
    val blocking: Boolean,
)

object SafetyPreview {

    /** 생선 알러지 -> 어유 오메가3를 조류 유래로 교체. */
    const val OMEGA3 = "omega3"
    const val ALGAE_OMEGA3 = "algae_omega3"

    fun rules(dog: MyDog): List<SafetyRule> {
        val out = mutableListOf<SafetyRule>()

        if (Allergen.FISH in dog.allergies) {
            out += SafetyRule(
                layer = "Layer 0",
                title = "오메가3를 조류 유래로 바꿉니다",
                detail = "생선 알러지가 있어 어유 오메가3를 제외하고, " +
                    "같은 EPA·DHA를 가진 조류 오메가3로 대체합니다.",
                blocking = true,
            )
        }

        if (Allergen.ENVIRONMENTAL in dog.allergies) {
            out += SafetyRule(
                layer = "감도",
                title = "피부 지표를 더 민감하게 봅니다",
                detail = "환경 알러지가 있는 경우 같은 변화라도 더 이르게 잡습니다. (민감도 1.25배)",
                blocking = false,
            )
        }

        if (Condition.PANCREATITIS in dog.conditions) {
            out += SafetyRule(
                layer = "Layer 1",
                title = "고지방 영양제를 전부 막습니다",
                detail = "췌장염 병력이 있어 오메가3와 인지·항산화(MCT)를 처방하지 않습니다.",
                blocking = true,
            )
        }

        if (Condition.KIDNEY in dog.conditions) {
            out += SafetyRule(
                layer = "Layer 1",
                title = "인지·항산화를 막습니다",
                detail = "신장질환이 있어 해당 카트리지를 처방하지 않습니다.",
                blocking = true,
            )
        }

        if (Medication.ANTICOAGULANT in dog.medications) {
            out += SafetyRule(
                layer = "Layer 1",
                title = "오메가3를 전부 막습니다",
                detail = "항응고제와 겹치면 출혈 경향이 커집니다. 어유·조류 모두 제외합니다.",
                blocking = true,
            )
        }

        if (Medication.NSAID in dog.medications) {
            out += SafetyRule(
                layer = "용량",
                title = "오메가3 용량을 절반으로 낮춥니다",
                detail = "소염진통제를 먹는 동안에는 상한을 50%로 조입니다.",
                blocking = false,
            )
        }

        val jointSurgeries = dog.surgeries.filter {
            it.type in setOf(SurgeryType.PATELLA, SurgeryType.CRUCIATE, SurgeryType.DISC)
        }
        if (jointSurgeries.isNotEmpty()) {
            val names = jointSurgeries.joinToString(", ") { it.type.label }
            out += SafetyRule(
                layer = "기준선",
                title = "관절 기준선을 낮춰 잡습니다",
                detail = "$names 이력이 있는 아이는 원래 덜 뜁니다. " +
                    "이걸 모르면 평소 모습을 악화로 잘못 읽습니다.",
                blocking = false,
            )
        }

        if (dog.surgeries.any { it.type == SurgeryType.GI }) {
            out += SafetyRule(
                layer = "기준선",
                title = "장 건강을 우선 후보로 둡니다",
                detail = "소화기 수술 이력이 있어 관련 지표를 더 주의 깊게 봅니다.",
                blocking = false,
            )
        }

        return out
    }

    /** 이 프로필에서 아예 처방될 수 없는 카트리지 목록. */
    fun blockedCartridges(dog: MyDog): Set<String> {
        val blocked = mutableSetOf<String>()
        if (Allergen.FISH in dog.allergies) blocked += OMEGA3
        if (Condition.PANCREATITIS in dog.conditions) {
            blocked += setOf(OMEGA3, ALGAE_OMEGA3, "cognition")
        }
        if (Condition.KIDNEY in dog.conditions) blocked += "cognition"
        if (Medication.ANTICOAGULANT in dog.medications) {
            blocked += setOf(OMEGA3, ALGAE_OMEGA3)
        }
        return blocked
    }
}

// ---------------------------------------------------------------------------
// 견종
// ---------------------------------------------------------------------------

/**
 * 견종은 선택 항목이다. 유기견 입양이나 잡종이면 아예 모를 수 있고,
 * 알고리즘은 견종 없이도 전부 동작한다.
 */
object Breeds {
    val ALL = listOf(
        "말티즈", "푸들", "포메라니안", "시츄", "치와와", "요크셔테리어",
        "비숑프리제", "닥스훈트", "웰시코기", "슈나우저", "스피츠", "페키니즈",
        "파피용", "미니핀", "잭러셀테리어", "보스턴테리어", "프렌치불독",
        "퍼그", "시바견", "진돗개", "비글", "코커스패니얼", "보더콜리",
        "셔틀랜드시프도그", "삽살개", "풍산개", "래브라도리트리버",
        "골든리트리버", "저먼셰퍼드", "시베리안허스키", "사모예드",
        "도베르만", "로트와일러", "그레이트피레니즈", "버니즈마운틴독",
        "달마시안", "아키타", "말라뮤트", "차우차우", "불테리어",
    )

    fun search(q: String): List<String> =
        if (q.isBlank()) ALL else ALL.filter { it.contains(q.trim()) }
}
