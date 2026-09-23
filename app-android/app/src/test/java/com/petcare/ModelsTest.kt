package com.petcare

import com.petcare.data.Dashboard
import com.petcare.data.DogProfile
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 파싱과 표시 규칙 검증.
 *
 * 실행 환경(asset 로딩, 에뮬레이터)에 의존하지 않도록 인라인 JSON을 쓴다.
 * ./gradlew test 로 바로 돌아간다.
 */
class ModelsTest {

    private val json = Json { ignoreUnknownKeys = true }
    private val data: Dashboard get() = json.decodeFromString(FIXTURE)

    @Test
    fun `프로필이 사람이 읽는 형태로 표시된다`() {
        assertEquals("초코", data.profile.name)
        assertEquals("2세 · 5.2kg · 말티즈", data.profile.subtitle)
    }

    @Test
    fun `견종이 비어 있으면 믹스로 표시한다`() {
        val mixed = DogProfile(
            name = "뭉치", ageMonths = 132, weightKg = 28.5,
            breeds = emptyList(), mealsPerDay = 2,
        )
        assertEquals("믹스", mixed.breedLabel)
    }

    @Test
    fun `처방을 끼니별로 나눈다`() {
        assertEquals(listOf("오메가3"), data.prescription.morning.map { it.name })
        assertEquals(listOf("피부장벽"), data.prescription.evening.map { it.name })
    }

    @Test
    fun `처방을 발생시킨 축만 골라낸다`() {
        val fired = data.prescription.firedAxes
        assertEquals(1, fired.size)
        assertEquals("skin", fired.first().axis)
        assertEquals("피부", fired.first().label)
    }

    @Test
    fun `관찰 메시지에 진단 용어를 쓰지 않는다`() {
        data.prescription.firedAxes.forEach {
            assertFalse("진단 언어(\"피부염\" 등)를 노출하면 안 된다", it.message.contains("염"))
            assertTrue(it.message.contains("평소"))
        }
    }

    @Test
    fun `수면 시간을 사람이 읽는 형태로 바꾼다`() {
        assertEquals("6시간 40분", data.today.sleepLabel)
    }

    @Test
    fun `시간대별 배열은 24칸이다`() {
        val h = data.today.hourly
        assertEquals(24, h.activitySec.size)
        assertEquals(24, h.scratch.size)
        assertEquals(24, h.postureChange.size)
    }

    @Test
    fun `추이에 평소 수준 기준선이 들어 있다`() {
        val t = data.trend.getValue("scratch_night")
        assertEquals(7, t.values.size)
        assertEquals(4.0, t.baseline, 1e-9)
    }

    @Test
    fun `백엔드가 보내는 모르는 필드는 무시한다`() {
        // Pydantic이 is_senior, rer 같은 계산 필드를 함께 보낸다
        assertEquals(24, data.profile.ageMonths)
    }
}

private const val FIXTURE = """
{
  "scenario": "skin",
  "profile": {
    "name": "초코", "age_months": 24, "weight_kg": 5.2,
    "breeds": ["말티즈"], "meals_per_day": 2,
    "is_senior": false, "rer": 241.3
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
    "wear_ratio": 0.94, "intake_ratio": 0.95
  }],
  "prescription": {
    "der_kcal": 356.0, "food_grams": 99,
    "axes": [
      {"axis": "skin", "z_score": 8.46, "active": true,
       "message": "밤에 긁는 횟수가 평소보다 2.5배 늘었어요"},
      {"axis": "sleep", "z_score": 2.50, "active": false, "message": ""}
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
