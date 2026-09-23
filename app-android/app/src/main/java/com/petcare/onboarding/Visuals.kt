package com.petcare.onboarding

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.petcare.ui.T

/**
 * 체형 선택용 실루엣.
 *
 * BCS를 숫자로 물으면 보호자가 답하지 못한다. 위에서 내려다본 몸통 실루엣을
 * 보여주고 고르게 한다. 실제 BCS 판정 기준도 허리 잘록함이므로 그림이 곧 기준이다.
 *
 * 사진 대신 도형으로 그린다. 이미지 에셋이 없어도 되고, 세 장의 차이가
 * 허리 폭 하나로만 나므로 비교가 정확하다.
 */
@Composable
fun DogSilhouette(
    waist: Float,
    modifier: Modifier = Modifier,
    active: Boolean = true,
) {
    val tint: Color = if (active) T.ink else T.ink.copy(alpha = 0.28f)

    Canvas(modifier = modifier.size(width = 76.dp, height = 92.dp)) {
        val w = size.width
        val h = size.height

        fun ellipse(cx: Float, cy: Float, rw: Float, rh: Float) {
            drawOval(
                color = tint,
                topLeft = Offset(cx - rw, cy - rh),
                size = Size(rw * 2, rh * 2),
            )
        }

        // 머리와 귀
        ellipse(w * 0.5f, h * 0.12f, w * 0.15f, h * 0.10f)
        ellipse(w * 0.33f, h * 0.07f, w * 0.06f, h * 0.05f)
        ellipse(w * 0.67f, h * 0.07f, w * 0.06f, h * 0.05f)

        // 앞다리
        ellipse(w * 0.20f, h * 0.42f, w * 0.06f, h * 0.09f)
        ellipse(w * 0.80f, h * 0.42f, w * 0.06f, h * 0.09f)

        // 가슴 — 체형과 무관하게 고정폭. 기준점 역할을 한다.
        ellipse(w * 0.5f, h * 0.42f, w * 0.34f, h * 0.16f)

        // 허리 — 여기만 변한다
        val waistHalf = w * (0.16f + 0.20f * waist.coerceIn(0f, 1f))
        drawRect(
            color = tint,
            topLeft = Offset(w * 0.5f - waistHalf, h * 0.42f),
            size = Size(waistHalf * 2, h * 0.28f),
        )

        // 엉덩이
        ellipse(w * 0.5f, h * 0.72f, w * 0.30f, h * 0.14f)

        // 뒷다리
        ellipse(w * 0.22f, h * 0.76f, w * 0.06f, h * 0.09f)
        ellipse(w * 0.78f, h * 0.76f, w * 0.06f, h * 0.09f)

        // 꼬리
        drawRect(
            color = tint,
            topLeft = Offset(w * 0.5f - w * 0.025f, h * 0.84f),
            size = Size(w * 0.05f, h * 0.12f),
        )
    }
}

/** 급여 슬롯 표시용 작은 알갱이 점. */
@Composable
fun PelletDots(count: Int, color: Color, modifier: Modifier = Modifier) {
    Canvas(modifier = modifier.height(10.dp)) {
        val r = 3.5.dp.toPx()
        val gap = 4.dp.toPx()
        repeat(count.coerceAtMost(10)) { i ->
            drawCircle(
                color = color,
                radius = r,
                center = Offset(r + i * (r * 2 + gap), size.height / 2f),
            )
        }
    }
}
