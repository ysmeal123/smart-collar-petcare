package com.petcare

import com.petcare.data.Allergen
import com.petcare.data.Condition
import com.petcare.data.Dashboard
import com.petcare.data.DogSize
import com.petcare.data.Medication
import com.petcare.data.MyDog
import com.petcare.data.SafetyPreview
import com.petcare.data.Surgery
import com.petcare.data.SurgeryType
import com.petcare.data.withSafetyFilter
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 온보딩이 만들어낸 프로필이 실제로 처방을 바꾸는지 검증한다.
 *
 * 여기서 지키려는 것은 "입력을 받았다"가 아니라
 * "받은 입력이 사출량에 반영된다"는 사실이다.
 */
class ProfileTest {

    private val json = Json { ignoreUnknownKeys = true }

    private fun dog(
        weight: Double = 5.2,
        allergies: List<Allergen> = emptyList(),
        conditions: List<Condition> = emptyList(),
        medications: List<Medication> = emptyList(),
        surgeries: List<Surgery> = emptyList(),
    ) = MyDog(
        name = "초코",
        ageMonths = 24,
        weightKg = weight,
        allergies = allergies,
        conditions = conditions,
        medications = medications,
        surgeries = surgeries,
    )

    // -- 기본 계산 -----------------------------------------------------------

    @Test
    fun `RER은 체중의 0_75제곱에 비례한다`() {
        assertEquals(241.05, dog(weight = 5.2).rer, 0.05)
        // 체중이 4배가 되어도 요구량은 4배가 아니라 2.83배다
        assertEquals(681.78, dog(weight = 20.8).rer, 0.05)
        assertEquals(2.83, dog(weight = 20.8).rer / dog(weight = 5.2).rer, 0.01)
    }

    @Test
    fun `나이를 사람이 읽는 형태로 바꾼다`() {
        assertEquals("8개월", dog().copy(ageMonths = 8).ageLabel)
        assertEquals("2세", dog().copy(ageMonths = 24).ageLabel)
        assertEquals("2세 3개월", dog().copy(ageMonths = 27).ageLabel)
    }

    @Test
    fun `노령 기준은 크기마다 다르다`() {
        val nine = 108
        assertFalse(dog().copy(ageMonths = nine, size = DogSize.SMALL).isSenior)
        assertTrue(dog().copy(ageMonths = nine, size = DogSize.MEDIUM).isSenior)
        assertTrue(dog().copy(ageMonths = nine, size = DogSize.LARGE).isSenior)
    }

    @Test
    fun `견종을 입력하지 않으면 믹스로 표시한다`() {
        assertEquals("믹스", dog().breedLabel)
        assertEquals("말티즈", dog().copy(breeds = listOf("말티즈")).breedLabel)
    }

    // -- 사료 선택 -----------------------------------------------------------

    @Test
    fun `닭고기 알러지가 있으면 닭 사료를 추천하지 않는다`() {
        val (id, _) = dog(allergies = listOf(Allergen.CHICKEN)).recommendedFood
        assertEquals("core_salmon", id)
    }

    @Test
    fun `닭과 생선에 모두 알러지가 있으면 제한식으로 간다`() {
        val (id, _) = dog(
            allergies = listOf(Allergen.CHICKEN, Allergen.FISH),
        ).recommendedFood
        assertEquals("core_lamb_ld", id)
    }

    // -- 안전 필터 -----------------------------------------------------------

    @Test
    fun `생선 알러지는 어유만 막고 조류 오메가3는 남긴다`() {
        val blocked = SafetyPreview.blockedCartridges(dog(allergies = listOf(Allergen.FISH)))
        assertTrue(SafetyPreview.OMEGA3 in blocked)
        assertFalse(SafetyPreview.ALGAE_OMEGA3 in blocked)
    }

    @Test
    fun `항응고제는 오메가3를 종류 상관없이 전부 막는다`() {
        val blocked = SafetyPreview.blockedCartridges(
            dog(medications = listOf(Medication.ANTICOAGULANT)),
        )
        assertTrue(SafetyPreview.OMEGA3 in blocked)
        assertTrue(SafetyPreview.ALGAE_OMEGA3 in blocked)
    }

    @Test
    fun `췌장염 병력은 고지방 카트리지를 전부 막는다`() {
        val blocked = SafetyPreview.blockedCartridges(
            dog(conditions = listOf(Condition.PANCREATITIS)),
        )
        assertTrue(blocked.containsAll(listOf("omega3", "algae_omega3", "cognition")))
    }

    @Test
    fun `관절 수술 이력은 기준선 규칙으로 안내된다`() {
        val rules = SafetyPreview.rules(
            dog(surgeries = listOf(Surgery(SurgeryType.CRUCIATE, 2.0))),
        )
        val rule = rules.firstOrNull { it.layer == "기준선" }
        assertNotNull("십자인대 이력이 기준선 보정으로 이어져야 한다", rule)
        // 처방을 막는 규칙이 아니라 해석을 바꾸는 규칙이다
        assertFalse(rule!!.blocking)
    }

    @Test
    fun `막을 것이 없으면 규칙도 비어 있다`() {
        assertTrue(SafetyPreview.rules(dog()).isEmpty())
        assertTrue(SafetyPreview.blockedCartridges(dog()).isEmpty())
    }

    // -- 처방에 실제로 반영되는지 --------------------------------------------

    private val board: Dashboard get() = json.decodeFromString(FIXTURE)

    @Test
    fun `프로필이 깨끗하면 처방을 건드리지 않는다`() {
        val out = board.withSafetyFilter(dog())
        assertEquals(2, out.prescription.items.size)
        assertEquals(board.prescription.trace.size, out.prescription.trace.size)
    }

    @Test
    fun `생선 알러지가 있으면 어유가 조류 오메가3로 바뀐다`() {
        val out = board.withSafetyFilter(dog(allergies = listOf(Allergen.FISH)))

        val omega = out.prescription.items.first { it.pellets == 4 }
        assertEquals("algae_omega3", omega.cartridgeId)
        assertEquals("조류 오메가3", omega.name)
        // 대체지 삭제가 아니다. 알 개수가 유지돼야 한다.
        assertEquals(4, omega.pellets)
        assertEquals(2, out.prescription.items.size)

        // 왜 바뀌었는지 근거에 남아야 한다
        assertTrue(out.prescription.trace.any { it.step.contains("Layer 0") })
    }

    @Test
    fun `항응고제를 먹으면 오메가3가 처방에서 사라진다`() {
        val out = board.withSafetyFilter(
            dog(medications = listOf(Medication.ANTICOAGULANT)),
        )
        assertTrue(out.prescription.items.none { it.cartridgeId.contains("omega3") })
        assertEquals(1, out.prescription.items.size)
        assertTrue(
            out.prescription.trace.any { it.detail.contains("항응고제") },
        )
    }

    @Test
    fun `선택 문항을 건너뛰면 그 사실을 근거에 남긴다`() {
        val out = board.withSafetyFilter(
            dog(allergies = listOf(Allergen.FISH)).copy(skippedOptional = true),
        )
        assertTrue(out.prescription.trace.any { it.step == "입력 누락" })
    }
}

private const val FIXTURE = """
{
  "scenario": "skin",
  "profile": {
    "name": "초코", "age_months": 24, "weight_kg": 5.2,
    "breeds": ["말티즈"], "meals_per_day": 2
  },
  "recent_days": [{
    "date": "2026-08-11",
    "hourly": {
      "activity_sec": [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
      "scratch":      [1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,3,2],
      "posture_change":[2,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,2]
    },
    "sleep": {"total_min": 400, "restless_count": 18},
    "summary": {"steps": 4210, "walk_min": 50, "run_min": 12, "scratch_night": 11},
    "wear_ratio": 0.94
  }],
  "prescription": {
    "der_kcal": 356.0, "food_grams": 99,
    "axes": [
      {"axis": "skin", "z_score": 8.46, "active": true,
       "message": "밤에 긁는 횟수가 평소보다 2.5배 늘었어요"}
    ],
    "items": [
      {"cartridge_id": "omega3", "name": "오메가3", "color": "#F2994A",
       "pellets": 4, "meal_slot": "morning", "reason": "skin"},
      {"cartridge_id": "skin_barrier", "name": "피부장벽", "color": "#F2C94C",
       "pellets": 1, "meal_slot": "evening", "reason": "skin"}
    ],
    "escalated": false, "escalation_reason": "",
    "trace": [{"step": "상태 추론", "detail": "skin z=+8.46", "changed": true}]
  },
  "trend": {
    "scratch_night": {"values": [7,8,14,10,13,14,6], "baseline": 4.0}
  }
}
"""
