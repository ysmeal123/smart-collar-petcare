package com.petcare.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin

/**
 * 계산 중 표시.
 *
 * 점으로 이루어진 구(球) 위를 입자들이 기울어진 궤도로 돈다.
 * Jakub Antalik 의 thinking-orbs(MIT)에서 가져온 시각적 아이디어를
 * Compose Canvas 로 다시 구현했다.
 *
 *   https://github.com/Jakubantalik/thinking-orbs
 *
 * 원본은 웹 2D 캔버스라 코드를 그대로 쓸 수 없다. 대신 원본이 **순수 arc 와 점**
 * 으로만 그려진다는 점이 중요하다 — WebGL 도 필터도 없으니 삼각함수만으로
 * 옮겨진다. 스피너 하나 때문에 WebView 를 띄우면 느려지고 테마도 어긋난다.
 *
 * **이걸 쓰는 자리를 고르는 규칙.**
 * 실제로 계산이 일어나는 곳에만 쓴다. 40ms 짜리 캐시 읽기에 애니메이션을
 * 물리면 앱이 똑똑해 보이는 게 아니라 느려진다.
 * 지금 쓰는 곳은 두 군데다 — 서버 콜드스타트(30~60초)와 문진 재계산.
 *
 * 단계를 여러 개 순서대로 연출하지 않는다. 재계산은 HTTP 요청 한 번이고
 * 앱은 서버 내부 단계를 모른다. 없는 파이프라인을 애니메이션으로 꾸미면
 * 그건 거짓말이다.
 */

/** 구 표면의 점 개수. 늘려도 무거워지진 않지만 촘촘하면 구가 탁해진다. */
private const val DOTS = 132

/** 궤도를 도는 입자 수. */
private const val PARTICLES = 3

/** 궤도 기울기(라디안). 정면이 아니라 비스듬해야 입체로 읽힌다. */
private const val TILT = 0.42f

@Composable
fun ThinkingOrb(
    modifier: Modifier = Modifier,
    size: Dp = 64.dp,
    color: Color = T.primary,
) {
    val spin by rememberInfiniteTransition(label = "orb").animateFloat(
        initialValue = 0f,
        targetValue = (2 * PI).toFloat(),
        animationSpec = infiniteRepeatable(
            // 4초 한 바퀴. 더 빠르면 초조해 보이고 더 느리면 멈춘 것처럼 보인다.
            animation = tween(4000, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "spin",
    )

    Canvas(modifier = modifier.size(size)) {
        val r = kotlin.math.min(this.size.width, this.size.height) / 2f * 0.82f
        val c = Offset(this.size.width / 2f, this.size.height / 2f)

        // --- 구: 점을 표면에 고르게 뿌린다 -------------------------------
        //
        // 위도를 균등하게 나누면 극지방에 점이 몰린다. 높이(z)를 균등하게
        // 나누면 표면적 기준으로 고르게 퍼진다. 황금각으로 경도를 돌려
        // 줄무늬가 생기지 않게 한다.
        val golden = PI * (3.0 - kotlin.math.sqrt(5.0))
        for (i in 0 until DOTS) {
            val z = 1f - 2f * i / (DOTS - 1f)
            val ring = kotlin.math.sqrt((1f - z * z).coerceAtLeast(0f))
            val theta = (golden * i).toFloat() + spin * 0.35f

            val x = ring * cos(theta)
            val y = ring * sin(theta) * cos(TILT) - z * sin(TILT)
            val depth = ring * sin(theta) * sin(TILT) + z * cos(TILT)

            // 뒤쪽 점은 흐리고 작게. 이것만으로 깊이가 생긴다.
            val front = (depth + 1f) / 2f
            drawCircle(
                color = color.copy(alpha = 0.10f + 0.28f * front.pow(2)),
                radius = r * (0.012f + 0.020f * front),
                center = Offset(c.x + x * r, c.y + y * r),
            )
        }

        // --- 입자: 기울어진 궤도를 돈다 ----------------------------------
        for (p in 0 until PARTICLES) {
            val phase = spin * 1.6f + p * (2 * PI / PARTICLES).toFloat()

            // 궤도마다 기울기를 살짝 달리 준다. 같으면 한 줄로 겹쳐 보인다.
            val lean = TILT + p * 0.55f
            val x = cos(phase)
            val y = sin(phase) * cos(lean)
            val depth = sin(phase) * sin(lean)

            val front = (depth + 1f) / 2f
            drawCircle(
                color = color.copy(alpha = 0.35f + 0.65f * front),
                radius = r * (0.055f + 0.045f * front),
                center = Offset(c.x + x * r * 1.04f, c.y + y * r * 1.04f),
            )
        }
    }
}

/**
 * 궤도 + 문구.
 *
 * 문구가 반드시 있어야 한다. 애니메이션만 돌면 사용자는 앱이 멈췄는지
 * 일하는 중인지 구분하지 못한다.
 */
@Composable
fun ThinkingRow(
    label: String,
    modifier: Modifier = Modifier,
    size: Dp = 20.dp,
) {
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(T.xs),
    ) {
        ThinkingOrb(size = size)
        Text(label, style = T.caption.copy(color = T.inkMuted48))
    }
}
