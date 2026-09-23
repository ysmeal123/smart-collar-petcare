package com.petcare.ui

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import kotlin.math.max
import kotlin.math.min

/**
 * 차트 라이브러리를 쓰지 않고 Compose Canvas로 직접 그린다.
 *
 * 그리는 규칙은 DESIGN-apple.md 를 따른다.
 *   - 장식용 그라디언트를 쓰지 않는다. 막대는 단색이다.
 *   - 인터랙티브 색(Action Blue)은 하나뿐이다. 밝은 면에서는 #0066cc,
 *     어두운 타일에서는 #2997ff 를 쓴다.
 *   - 그림자를 넣지 않는다.
 *
 * 예외는 '평소보다 높음'을 나타내는 attention 색 하나다.
 * 데이터 표현에만 쓰고 버튼이나 링크에는 절대 쓰지 않는다.
 */

val NIGHT_HOURS = setOf(22, 23, 0, 1, 2, 3)
val SLEEP_HOURS = listOf(22, 23, 0, 1, 2, 3, 4, 5, 6)

/** 밝은 면 / 어두운 타일에 따라 달라지는 색 묶음. */
class ChartColors(onDark: Boolean) {
    val mark: Color = if (onDark) T.primaryOnDark else T.primary
    val track: Color = if (onDark) T.tile2 else T.dividerSoft
    val night: Color = if (onDark) T.nightOnDark else T.nightOnLight
    val label: TextStyle =
        if (onDark) T.finePrint.copy(color = T.bodyMuted) else T.finePrint
    val muted: Color = if (onDark) Color(0xFF5A5A5E) else T.hairline
}

/** 진입 애니메이션용 0 -> 1 진행값. */
@Composable
private fun enterProgress(durationMs: Int = 700): Float {
    var started by remember { mutableStateOf(false) }
    val t by animateFloatAsState(
        targetValue = if (started) 1f else 0f,
        animationSpec = tween(durationMs, easing = FastOutSlowInEasing),
        label = "enter",
    )
    LaunchedEffect(Unit) { started = true }
    return t
}

// ---------------------------------------------------------------------------
// 활동 링
// ---------------------------------------------------------------------------

@Composable
fun ActivityRing(
    progress: Float,
    centerValue: String,
    centerLabel: String,
    onDark: Boolean = false,
    color: Color? = null,
) {
    val c = ChartColors(onDark)
    val mark = color ?: c.mark
    val t = enterProgress(900)

    Box(modifier = Modifier.size(184.dp), contentAlignment = Alignment.Center) {
        Canvas(modifier = Modifier.fillMaxWidth().height(184.dp)) {
            val stroke = 10.dp.toPx()
            val radius = (min(size.width, size.height) - stroke) / 2f
            val topLeft = Offset(
                (size.width - radius * 2) / 2f,
                (size.height - radius * 2) / 2f,
            )
            val arcSize = Size(radius * 2, radius * 2)

            // 12시 방향에서 시작하고 위쪽에 틈을 둬 게이지처럼 보이게 한다
            val start = -90f + 10f
            val sweep = 360f - 20f

            drawArc(
                color = c.track,
                startAngle = start,
                sweepAngle = sweep,
                useCenter = false,
                topLeft = topLeft,
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round),
            )

            val p = progress.coerceIn(0f, 1.5f) * t
            if (p > 0f) {
                drawArc(
                    color = mark,
                    startAngle = start,
                    sweepAngle = sweep * min(p, 1f),
                    useCenter = false,
                    topLeft = topLeft,
                    size = arcSize,
                    style = Stroke(width = stroke, cap = StrokeCap.Round),
                )
            }
        }

        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                centerValue,
                style = T.numeric.copy(color = if (onDark) T.onDark else T.ink),
            )
            Text(
                centerLabel,
                style = if (onDark) {
                    T.caption.copy(color = T.bodyMuted)
                } else {
                    T.caption.copy(color = T.inkMuted48)
                },
                modifier = Modifier.padding(top = T.xxs),
            )
        }
    }
}

// ---------------------------------------------------------------------------
// 시간대별 막대 (24시간)
// ---------------------------------------------------------------------------

@Composable
fun HourlyChart(
    values: List<Int>,
    onDark: Boolean = false,
    color: Color? = null,
    /** 22~04시에 음영을 깐다. "밤에 긁는다"가 눈에 보여야 하므로. */
    highlightNight: Boolean = false,
) {
    val c = ChartColors(onDark)
    val mark = color ?: c.mark
    val t = enterProgress()

    Column {
        Canvas(modifier = Modifier.fillMaxWidth().height(96.dp)) {
            drawHourlyBars(values, mark, c, highlightNight, t)
        }
        AxisLabels(listOf("0시", "6시", "12시", "18시", "23시"), c)
    }
}

private fun DrawScope.drawHourlyBars(
    values: List<Int>,
    mark: Color,
    c: ChartColors,
    highlightNight: Boolean,
    t: Float,
) {
    if (values.isEmpty()) return

    val maxV = (values.maxOrNull() ?: 0).toFloat()
    val slot = size.width / values.size
    val barW = max(3.dp.toPx(), slot * 0.56f)
    val floor = 2.dp.toPx()

    values.forEachIndexed { i, v ->
        val cx = slot * i + slot / 2f

        if (highlightNight && i in NIGHT_HOURS) {
            drawRect(
                color = c.night,
                topLeft = Offset(slot * i, 0f),
                size = Size(slot, size.height),
            )
        }

        // 바닥선: 값이 0인 시간대도 흐리게 보여야 하루 리듬이 읽힌다
        drawRoundRect(
            color = c.track,
            topLeft = Offset(cx - barW / 2f, size.height - floor),
            size = Size(barW, floor),
            cornerRadius = CornerRadius(1.dp.toPx()),
        )

        if (maxV <= 0f) return@forEachIndexed
        val h = (v / maxV) * (size.height - floor * 2) * t
        if (h < 0.5f) return@forEachIndexed

        val top = size.height - h - floor
        drawRoundRect(
            color = mark,
            topLeft = Offset(cx - barW / 2f, top),
            size = Size(barW, h + floor),
            cornerRadius = CornerRadius(barW / 2f),
        )
    }
}

// ---------------------------------------------------------------------------
// 수면 밴드 (22시~07시)
// ---------------------------------------------------------------------------

/**
 * 뒤척임이 많을수록 진해진다.
 *
 * 두 색을 섞지 않고 한 색의 투명도만 바꾼다 — 강조색은 하나라는 규칙을 지키면서도
 * 밀도 차이는 그대로 읽힌다.
 */
@Composable
fun SleepBand(restlessByHour: List<Int>, onDark: Boolean = false) {
    val c = ChartColors(onDark)

    Column {
        Canvas(modifier = Modifier.fillMaxWidth().height(36.dp)) {
            val vals = SLEEP_HOURS.map { restlessByHour.getOrElse(it) { 0 }.toFloat() }
            val maxV = max(1f, vals.maxOrNull() ?: 1f)
            val slot = size.width / vals.size
            val gap = 3.dp.toPx()

            vals.forEachIndexed { i, v ->
                val ratio = (v / maxV).coerceIn(0f, 1f)
                drawRoundRect(
                    color = c.mark.copy(alpha = 0.14f + 0.76f * ratio),
                    topLeft = Offset(slot * i + gap / 2f, 0f),
                    size = Size(slot - gap, size.height),
                    cornerRadius = CornerRadius(T.rSm.toPx()),
                )
            }
        }
        AxisLabels(listOf("22시", "01시", "04시", "07시"), c)
    }
}

// ---------------------------------------------------------------------------
// 7일 추이 (평소 수준 점선 포함)
// ---------------------------------------------------------------------------

@Composable
fun TrendChart(
    values: List<Double>,
    baseline: Double,
    onDark: Boolean = false,
) {
    val c = ChartColors(onDark)
    val t = enterProgress()

    Column {
        Canvas(modifier = Modifier.fillMaxWidth().height(100.dp)) {
            if (values.isEmpty()) return@Canvas

            val maxV = max(
                (values.maxOrNull() ?: 0.0).toFloat(),
                (baseline * 1.25).toFloat(),
            )
            if (maxV <= 0f) return@Canvas

            val slot = size.width / values.size
            val barW = max(10.dp.toPx(), slot * 0.40f)
            val headroom = 18.dp.toPx()

            fun y(v: Float) = size.height - (v / maxV) * (size.height - headroom)

            // 평소 수준 점선. 이게 있어야 "늘었다"가 한눈에 보인다.
            val by = y(baseline.toFloat())
            drawLine(
                color = c.muted,
                start = Offset(0f, by),
                end = Offset(size.width, by),
                strokeWidth = 1.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(
                    floatArrayOf(4.dp.toPx(), 3.dp.toPx()),
                ),
            )

            values.forEachIndexed { i, v ->
                val cx = slot * i + slot / 2f
                val barTop = y(v.toFloat() * t)
                val over = v > baseline

                drawRoundRect(
                    // 평소보다 높은 날만 attention. 나머지는 기본 강조색.
                    color = if (over) T.attention else c.mark.copy(alpha = 0.45f),
                    topLeft = Offset(cx - barW / 2f, barTop),
                    size = Size(barW, size.height - barTop),
                    cornerRadius = CornerRadius(barW / 2f),
                )
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth().padding(top = T.xs),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            // values[0]이 가장 오래된 날, 마지막이 오늘
            values.indices.forEach { i ->
                val label = if (i == values.lastIndex) "오늘" else "${values.lastIndex - i}일 전"
                Text(label, style = c.label)
            }
        }
    }
}

// ---------------------------------------------------------------------------

@Composable
private fun AxisLabels(labels: List<String>, c: ChartColors) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(top = T.xs),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        labels.forEach { Text(it, style = c.label) }
    }
}
