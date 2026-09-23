package com.petcare.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 디자인 토큰 — DESIGN-apple.md 를 그대로 옮긴 것.
 *
 * 핵심 규칙 네 가지:
 *   1. 인터랙티브 색은 Action Blue(#0066cc) 하나뿐이다. 두 번째 강조색은 없다.
 *   2. 크롬(카드/버튼/텍스트)에는 그림자를 쓰지 않는다. 그림자는 제품 이미지 전용.
 *   3. 장식용 그라디언트를 쓰지 않는다.
 *   4. 강조가 필요하면 색을 더하기 전에 면(light tile <-> dark tile)을 바꾼다.
 *
 * 폰트: SF Pro는 애플 전용이라 안드로이드에 없다. 문서 권고대로 system-ui(Roboto)를
 * 쓰되 크기/두께/자간 같은 '수치'는 전부 문서값을 지킨다. 애플 특유의 조인 자간
 * (17sp에서 -0.374)이 여기서 나온다. Inter로 바꾸려면 res/font에 넣고
 * APPLE_FONT 만 교체하면 된다.
 */
object T {

    // -- 색 ------------------------------------------------------------------

    /** Action Blue. 모든 "누르세요" 신호는 이 색 하나다. */
    val primary = Color(0xFF0066CC)
    val primaryFocus = Color(0xFF0071E3)

    /** 어두운 타일 위에서만 쓴다. 밝은 면에서는 절대 쓰지 않는다. */
    val primaryOnDark = Color(0xFF2997FF)

    val ink = Color(0xFF1D1D1F)
    val inkMuted80 = Color(0xFF333333)
    val inkMuted48 = Color(0xFF7A7A7A)

    val onDark = Color(0xFFFFFFFF)
    val bodyMuted = Color(0xFFCCCCCC)

    val canvas = Color(0xFFFFFFFF)
    val parchment = Color(0xFFF5F5F7)
    val pearl = Color(0xFFFAFAFC)

    val tile1 = Color(0xFF272729)
    val tile2 = Color(0xFF2A2A2C)
    val tile3 = Color(0xFF252527)
    val black = Color(0xFF000000)

    val dividerSoft = Color(0xFFF0F0F0)
    val hairline = Color(0xFFE0E0E0)
    val chipTranslucent = Color(0xFFD2D2D7)

    // -- 색 (확장) -----------------------------------------------------------
    //
    // 원본 문서는 마케팅 사이트를 분석한 것이라 '건강 상태'라는 개념이 없다.
    // 우리 앱은 이상 징후를 알려야 하므로 딱 두 색만 추가한다.
    //
    // 지켜야 할 선: 이 두 색은 데이터 표현에만 쓴다.
    // 버튼/링크/칩 같은 인터랙티브 요소에는 쓰지 않는다 -> 규칙 1 유지.
    // 값은 애플 시스템 컬러의 접근성 변형(밝은 배경 대비 확보)을 가져왔다.

    /** 평소 범위를 벗어난 지표. */
    val attention = Color(0xFFC93400)

    /** 급성 이상. */
    val critical = Color(0xFFD70015)

    /** 차트에서 22~04시 구간을 덮는 음영 (밝은 면 / 어두운 면). */
    val nightOnLight = Color(0xFFF0F0F2)
    val nightOnDark = Color(0xFF303033)

    // -- 모서리 --------------------------------------------------------------

    val rNone = 0.dp
    val rXs = 5.dp
    val rSm = 8.dp
    val rMd = 11.dp
    val rLg = 18.dp
    val pill = RoundedCornerShape(percent = 50)

    // -- 여백 ----------------------------------------------------------------

    val xxs = 4.dp
    val xs = 8.dp
    val sm = 12.dp

    /** 문서의 17px. 8의 배수가 아닌 게 맞다. */
    val md = 17.dp
    val lg = 24.dp
    val xl = 32.dp
    val xxl = 48.dp

    /**
     * 문서상 섹션 패딩은 80px이지만 그건 데스크톱 값이다.
     * 반응형 규칙("small-phone에서 80 -> 48")을 따라 모바일은 48을 쓴다.
     */
    val section = 48.dp

    /** 화면 좌우 기본 여백. */
    val gutter = 24.dp

    // -- 타이포 --------------------------------------------------------------
    //
    // 문서의 크기/두께/행간/자간을 그대로 옮겼다.
    // 두께 사다리는 300 / 400 / 600 / 700 이고 500은 의도적으로 비어 있다.

    private fun style(
        size: Int,
        weight: FontWeight,
        lineHeightRatio: Double,
        tracking: Double,
        color: Color,
    ) = TextStyle(
        color = color,
        fontSize = size.sp,
        fontWeight = weight,
        lineHeight = (size * lineHeightRatio).sp,
        letterSpacing = tracking.sp,
        lineHeightStyle = LineHeightStyle(
            alignment = LineHeightStyle.Alignment.Center,
            trim = LineHeightStyle.Trim.None,
        ),
    )

    /**
     * hero-display는 문서상 56px이지만 반응형 규칙이
     * "640px 이하에서 34px, 419px 이하에서 28px"로 내리라고 한다.
     * 폰은 항상 그 구간이므로 34로 고정한다.
     */
    val hero = style(34, FontWeight.W600, 1.07, -0.6, ink)

    val displayLg = style(28, FontWeight.W600, 1.10, -0.4, ink)
    val displayMd = style(24, FontWeight.W600, 1.25, -0.374, ink)
    val lead = style(21, FontWeight.W400, 1.30, 0.196, inkMuted80)
    val leadAiry = style(20, FontWeight.W300, 1.45, 0.0, inkMuted80)
    val tagline = style(21, FontWeight.W600, 1.19, 0.231, ink)

    /** 본문은 17sp. 16이 아니다 — 이 1px가 브랜드의 읽는 속도를 만든다. */
    val body = style(17, FontWeight.W400, 1.47, -0.374, ink)
    val bodyStrong = style(17, FontWeight.W600, 1.24, -0.374, ink)

    val caption = style(14, FontWeight.W400, 1.43, -0.224, inkMuted80)
    val captionStrong = style(14, FontWeight.W600, 1.29, -0.224, ink)
    val buttonLarge = style(18, FontWeight.W300, 1.15, 0.0, onDark)
    val buttonUtility = style(14, FontWeight.W400, 1.29, -0.224, ink)
    val finePrint = style(12, FontWeight.W400, 1.35, -0.12, inkMuted48)
    val microLegal = style(10, FontWeight.W400, 1.3, -0.08, inkMuted48)

    /** 큰 숫자. 자간을 더 조여야 애플처럼 보인다. */
    val numeric = style(40, FontWeight.W600, 1.05, -1.2, ink)

    /** 카트리지 색상 문자열("#F2994A")을 Color로. */
    fun hex(s: String): Color = Color(("FF" + s.removePrefix("#")).toLong(16))
}

// ---------------------------------------------------------------------------
// 공통 modifier
// ---------------------------------------------------------------------------

/**
 * store-utility-card. 흰 면 + 1px 헤어라인 + 18dp 모서리.
 * 그림자는 넣지 않는다.
 */
fun Modifier.utilityCard(
    fill: Color = T.canvas,
    stroke: Color = T.hairline,
    strokeWidth: androidx.compose.ui.unit.Dp = 1.dp,
    radius: androidx.compose.ui.unit.Dp = T.rLg,
    padding: androidx.compose.ui.unit.Dp = T.lg,
): Modifier = this
    .background(fill, RoundedCornerShape(radius))
    .border(strokeWidth, stroke, RoundedCornerShape(radius))
    .padding(padding)

/**
 * 눌렀을 때 0.95배로 줄어드는 시스템 공통 마이크로 인터랙션.
 * 문서에서 "every button"이라고 못박은 동작이라 버튼마다 붙인다.
 */
fun Modifier.pressScale(source: MutableInteractionSource): Modifier = composed {
    val pressed by source.collectIsPressedAsState()
    scale(if (pressed) 0.95f else 1f)
}

@Composable
fun PetCareTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = T.primary,
            onPrimary = T.onDark,
            background = T.canvas,
            onBackground = T.ink,
            surface = T.canvas,
            onSurface = T.ink,
            error = T.critical,
        ),
        content = content,
    )
}
