package com.petcare.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json

/**
 * 데이터 소스.
 *
 * **서버가 있으면 서버, 없으면 asset.** 자동으로 내려간다.
 *
 * 발표 중에 네트워크가 끊겨도 화면이 비지 않아야 한다. 그렇다고 데모 데이터를
 * 실데이터처럼 보여주면 안 되므로, 어느 쪽인지 화면에 표시한다.
 *
 * 두 경로가 **같은 스키마**를 쓴다. 서버의 `/v1/dogs/{id}/dashboard` 응답과
 * asset의 `demo_*.json` 이 같은 모양이라, 화면 코드는 출처를 몰라도 된다.
 */

enum class Origin { LIVE, DEMO }

/** 서버를 깨우는 중인지. 무료 호스팅은 첫 요청이 30~60초 걸린다. */
enum class Phase { WAKING, DONE }

data class Loaded(
    val dashboard: Dashboard,
    val origin: Origin,
    val note: String = "",
)

class Repository(private val context: Context) {

    private val json = Json {
        // 백엔드가 보내는 필드 중 앱이 안 쓰는 것이 많다
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    /**
     * 서버 우선, 실패하면 데모.
     *
     * @param scenario 데모로 내려갔을 때 보여줄 시나리오
     */
    suspend fun load(
        scenario: String,
        dog: MyDog? = null,
        base: String? = null,
        dogId: String? = null,
        token: String = "",
    ): Loaded {
        val server = base?.takeIf { it.isNotBlank() }
        val id = dogId?.takeIf { it.isNotBlank() }

        if (server != null && id != null) {
            runCatching { Net.dashboard(server, id, token) }
                .onSuccess { board ->
                    return Loaded(
                        dashboard = if (dog == null) board else board.withSafetyFilter(dog),
                        origin = Origin.LIVE,
                    )
                }
                .onFailure { e ->
                    return Loaded(
                        dashboard = demo(scenario, dog),
                        origin = Origin.DEMO,
                        note = "서버에 연결하지 못했습니다 (${e.message?.take(60)})",
                    )
                }
        }

        return Loaded(demo(scenario, dog), Origin.DEMO)
    }

    private suspend fun demo(scenario: String, dog: MyDog?): Dashboard =
        withContext(Dispatchers.IO) {
            val raw = context.assets
                .open("demo_$scenario.json")
                .bufferedReader()
                .use { it.readText() }

            val board = json.decodeFromString<Dashboard>(raw)
            if (dog == null) board else board.withSafetyFilter(dog)
        }

    /** 보호자 답변 전송. 서버가 없으면 조용히 실패한다 (데모에서는 화면만 갱신). */
    suspend fun answer(
        base: String?, dogId: String?, answers: Map<String, String>, token: String = "",
    ): Boolean {
        val server = base?.takeIf { it.isNotBlank() } ?: return false
        val id = dogId?.takeIf { it.isNotBlank() } ?: return false
        return runCatching { Net.answer(server, id, answers, token) }.isSuccess
    }

    /**
     * 대화록.
     *
     * 서버가 없으면 대화 자체가 성립하지 않는다 — 해석과 재계산이 서버에서
     * 일어나기 때문이다. 데모 대화를 만들어 흉내낼 수도 있지만, 그러면
     * 답을 넣어도 계획이 안 바뀌는 화면이 된다. 그건 문진이 장식이던
     * 예전 상태와 같다. 서버가 없으면 없다고 말한다.
     */
    suspend fun chat(base: String?, dogId: String?, token: String = ""): ChatLog? {
        val server = base?.takeIf { it.isNotBlank() } ?: return null
        val id = dogId?.takeIf { it.isNotBlank() } ?: return null
        return runCatching { Net.chat(server, id, token) }.getOrNull()
    }

    /** 선택지 답변. 질문 key 를 그대로 보낸다 — 서버가 추출할 필요가 없다. */
    suspend fun answerQuestion(
        base: String?, dogId: String?, key: String, value: String, token: String = "",
    ): NoteResult? {
        val server = base?.takeIf { it.isNotBlank() } ?: return null
        val id = dogId?.takeIf { it.isNotBlank() } ?: return null
        return runCatching {
            Net.answerQuestion(server, id, key, value, token)
        }.getOrNull()
    }

    /** 특이사항 전달. 해석 결과와 갱신된 대화록이 함께 온다. */
    suspend fun sendNote(
        base: String?, dogId: String?, text: String, token: String = "",
    ): NoteResult? {
        val server = base?.takeIf { it.isNotBlank() } ?: return null
        val id = dogId?.takeIf { it.isNotBlank() } ?: return null
        return runCatching { Net.note(server, id, text, token) }.getOrNull()
    }

    suspend fun sendWeight(base: String?, dogId: String?, kg: Double): Boolean {
        val server = base?.takeIf { it.isNotBlank() } ?: return false
        val id = dogId?.takeIf { it.isNotBlank() } ?: return false
        return runCatching { Net.putWeight(server, id, kg) }.isSuccess
    }

    companion object {
        /** 시연용 시나리오. 실제 서비스에는 없는 화면이다. */
        val scenarios = linkedMapOf(
            "skin" to "피부 이상",
            "joint" to "관절 이상",
            "normal" to "건강함",
            "acute" to "급성 이상",
        )
    }
}

// ---------------------------------------------------------------------------
// 온보딩 입력을 처방에 반영
// ---------------------------------------------------------------------------

/**
 * 온보딩에서 받은 알러지·질환·복용약을 처방에 적용한다.
 *
 * 실제 서비스에서는 서버가 처방을 만들 때 이미 반영한다. 여기서 한 번 더 하는
 * 이유는 asset 데모 데이터가 특정 개체(초코)로 고정돼 있어서다.
 * 규칙은 `petcare/core/constants.py` 의 BLOCKED_BY_* 와 CARTRIDGE_SUBSTITUTE 를
 * 그대로 옮겼고, 판정 권한은 어디까지나 서버에 있다.
 */
fun Dashboard.withSafetyFilter(dog: MyDog): Dashboard {
    val blocked = SafetyPreview.blockedCartridges(dog)
    if (blocked.isEmpty()) return this

    val rx = prescription
    val notes = mutableListOf<TraceStep>()
    val kept = mutableListOf<DispenseItem>()

    rx.items.forEach { item ->
        when {
            item.cartridgeId !in blocked -> kept += item

            // 어유가 막혔고 조류 오메가3는 살아 있으면 그쪽으로 대체한다.
            // 같은 EPA·DHA 를 주므로 처방 의도가 유지된다.
            item.cartridgeId == SafetyPreview.OMEGA3 &&
                SafetyPreview.ALGAE_OMEGA3 !in blocked -> {
                kept += item.copy(
                    cartridgeId = SafetyPreview.ALGAE_OMEGA3,
                    name = "조류 오메가3",
                    color = "#BDBDBD",
                    reason = "생선 알러지로 어유 대신 조류 유래 사용",
                )
                notes += TraceStep(
                    step = "🛡 Layer 0 · 알러지 차단",
                    detail = "생선 알러지 → 오메가3(어유) 제외, " +
                        "조류 오메가3 ${item.pellets}알로 대체",
                    changed = true,
                )
            }

            else -> notes += TraceStep(
                step = "🛡 Layer 0/1 · 처방 차단",
                detail = "${item.name} ${item.pellets}알을 제외했습니다 " +
                    "(${blockReason(dog, item.cartridgeId)})",
                changed = true,
            )
        }
    }

    if (dog.skippedOptional) {
        notes += TraceStep(
            step = "입력 누락",
            detail = "알러지·수술 이력을 건너뛰셨습니다. " +
                "해당 정보 없이 계산했습니다.",
            changed = false,
        )
    }

    // 계획의 끼니에서도 같은 카트리지를 갈아끼운다.
    // 처방만 고치고 계획을 안 고치면 화면 두 곳이 다른 말을 한다.
    val fixedPlan = plan?.let { p ->
        p.copy(meals = p.meals.map { meal ->
            meal.copy(pellets = meal.pellets.mapNotNull { pel ->
                when {
                    pel.cartridgeId !in blocked -> pel
                    pel.cartridgeId == SafetyPreview.OMEGA3 &&
                        SafetyPreview.ALGAE_OMEGA3 !in blocked ->
                        pel.copy(
                            cartridgeId = SafetyPreview.ALGAE_OMEGA3,
                            name = "조류 오메가3",
                            color = "#BDBDBD",
                        )
                    else -> null
                }
            })
        })
    }

    return copy(
        prescription = rx.copy(
            items = kept,
            // 근거는 순서가 곧 안전 설계다. 차단은 계산 뒤에 일어나므로 뒤에 붙인다.
            trace = rx.trace + notes,
        ),
        plan = fixedPlan,
    )
}

private fun blockReason(dog: MyDog, cartridgeId: String): String = when {
    Medication.ANTICOAGULANT in dog.medications &&
        cartridgeId in setOf(SafetyPreview.OMEGA3, SafetyPreview.ALGAE_OMEGA3) ->
        "항응고제 복용 중"

    Condition.PANCREATITIS in dog.conditions -> "췌장염 병력"
    Condition.KIDNEY in dog.conditions -> "신장질환"
    Allergen.FISH in dog.allergies -> "생선 알러지"
    else -> "안전 필터"
}
