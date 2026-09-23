package com.petcare.data

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json

/**
 * 데이터 소스.
 *
 * asset에 동봉된 처방 결과를 읽는다. 백엔드를 안 띄워도 앱이 돈다 -
 * 발표 시연에서 서버 의존성을 없애기 위해서다.
 *
 * 서버를 붙이려면 load()에 HTTP 호출을 넣고 AndroidManifest에서
 * INTERNET 권한 주석을 풀면 된다. JSON 스키마는 동일하다.
 */
class Repository(private val context: Context) {

    private val json = Json {
        // 백엔드가 보내는 필드 중 앱이 안 쓰는 것이 많다
        ignoreUnknownKeys = true
    }

    suspend fun load(scenario: String, dog: MyDog? = null): Dashboard =
        withContext(Dispatchers.IO) {
            val raw = context.assets
                .open("demo_$scenario.json")
                .bufferedReader()
                .use { it.readText() }

            val board = json.decodeFromString<Dashboard>(raw)
            if (dog == null) board else board.withSafetyFilter(dog)
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

    return copy(
        prescription = rx.copy(
            items = kept,
            // 근거는 순서가 곧 안전 설계다. 차단은 계산 뒤에 일어나므로 뒤에 붙인다.
            trace = rx.trace + notes,
        ),
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
