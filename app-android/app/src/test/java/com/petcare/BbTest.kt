package com.petcare

import com.petcare.data.Bb
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BbTest {

    @Test
    fun `가장 많은 슬롯이 3알, 나머지는 비율로 최소 1알`() {
        assertEquals(mapOf(1 to 3, 2 to 1), Bb.pellets(mapOf(1 to 4, 2 to 1)))
    }

    @Test
    fun `1알은 3바퀴라 g 도 3바퀴 몫이다`() {
        assertEquals(3 * 3 * 0.96, Bb.pelletGrams(2, 3)!!, 1e-9)
        assertNull(Bb.pelletGrams(3, 1))   // 3번은 측정값 없음
    }

    @Test
    fun `사료 3g 요청은 2점5바퀴라 실측 기준 약 2점3g`() {
        assertEquals("2.3g", Bb.g(Bb.foodGrams))
    }
}
