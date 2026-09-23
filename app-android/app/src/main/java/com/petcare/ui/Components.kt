package com.petcare.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * DESIGN-apple.md 의 컴포넌트를 Compose로 옮긴 것.
 *
 * 버튼 문법은 두 가지뿐이다.
 *   파란 pill  = 액션      (button-primary / button-store-hero)
 *   8dp 사각   = 유틸리티  (button-dark-utility)
 * 그 사이의 어중간한 모서리는 만들지 않는다.
 */

// ---------------------------------------------------------------------------
// 버튼
// ---------------------------------------------------------------------------

/** button-primary. 파란 pill. 이 앱의 유일한 액션 신호다. */
@Composable
fun PrimaryPill(
    label: String,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    large: Boolean = false,
    onClick: () -> Unit,
) {
    val src = remember { MutableInteractionSource() }
    val fill = if (enabled) T.primary else T.hairline
    val textColor = if (enabled) T.onDark else T.inkMuted48

    Box(
        modifier = modifier
            .pressScale(src)
            .background(fill, T.pill)
            .clickable(
                interactionSource = src,
                indication = null,
                enabled = enabled,
                onClick = onClick,
            )
            .padding(
                horizontal = if (large) 28.dp else 22.dp,
                vertical = if (large) 14.dp else 11.dp,
            ),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            style = if (large) {
                T.buttonLarge.copy(color = textColor)
            } else {
                T.body.copy(color = textColor)
            },
        )
    }
}

/** button-secondary-pill. 파란 테두리 고스트 pill. */
@Composable
fun GhostPill(
    label: String,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val src = remember { MutableInteractionSource() }

    Box(
        modifier = modifier
            .pressScale(src)
            .border(1.dp, T.primary, T.pill)
            .clickable(interactionSource = src, indication = null, onClick = onClick)
            .padding(horizontal = 22.dp, vertical = 11.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, style = T.body.copy(color = T.primary))
    }
}

/** text-link. 밝은 면이면 Action Blue, 어두운 타일이면 Sky Link Blue. */
@Composable
fun TextLink(
    label: String,
    modifier: Modifier = Modifier,
    onDark: Boolean = false,
    onClick: () -> Unit,
) {
    val src = remember { MutableInteractionSource() }

    Box(
        modifier = modifier
            .pressScale(src)
            .clickable(interactionSource = src, indication = null, onClick = onClick)
            .padding(vertical = T.xs),
    ) {
        Text(
            label,
            style = T.body.copy(color = if (onDark) T.primaryOnDark else T.primary),
        )
    }
}

/** button-dark-utility. 8dp 모서리 잉크색 사각. 상단 유틸리티 전용. */
@Composable
fun UtilityButton(
    label: String,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val src = remember { MutableInteractionSource() }

    Box(
        modifier = modifier
            .pressScale(src)
            .background(T.ink, RoundedCornerShape(T.rSm))
            .clickable(interactionSource = src, indication = null, onClick = onClick)
            .padding(horizontal = 15.dp, vertical = T.xs),
    ) {
        Text(label, style = T.buttonUtility.copy(color = T.onDark))
    }
}

// ---------------------------------------------------------------------------
// 선택 요소
// ---------------------------------------------------------------------------

/**
 * configurator-option-chip. pill 모양 선택 칩.
 *
 * 선택되면 테두리만 2dp Focus Blue로 굵어진다. 배경은 흰색 그대로다.
 * 선택 상태를 배경색으로 칠하지 않는 것이 이 시스템의 규칙이다.
 */
@Composable
fun OptionChip(
    label: String,
    selected: Boolean,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val src = remember { MutableInteractionSource() }

    Box(
        modifier = modifier
            .pressScale(src)
            .background(T.canvas, T.pill)
            .border(
                width = if (selected) 2.dp else 1.dp,
                color = if (selected) T.primaryFocus else T.hairline,
                shape = T.pill,
            )
            .clickable(interactionSource = src, indication = null, onClick = onClick)
            .padding(horizontal = 16.dp, vertical = T.sm),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            style = T.caption.copy(color = if (selected) T.ink else T.inkMuted80),
            textAlign = TextAlign.Center,
        )
    }
}

/** store-utility-card 를 선택 가능하게 만든 것. 큰 선택지에 쓴다. */
@Composable
fun OptionCard(
    title: String,
    selected: Boolean,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    visual: (@Composable () -> Unit)? = null,
    onClick: () -> Unit,
) {
    val src = remember { MutableInteractionSource() }

    Column(
        modifier = modifier
            .pressScale(src)
            .background(T.canvas, RoundedCornerShape(T.rLg))
            .border(
                width = if (selected) 2.dp else 1.dp,
                color = if (selected) T.primaryFocus else T.hairline,
                shape = RoundedCornerShape(T.rLg),
            )
            .clickable(interactionSource = src, indication = null, onClick = onClick)
            .padding(T.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        if (visual != null) {
            visual()
            Spacer(Modifier.height(T.sm))
        }
        Text(title, style = T.bodyStrong, textAlign = TextAlign.Center)
        if (subtitle != null) {
            Spacer(Modifier.height(T.xxs))
            Text(
                subtitle,
                style = T.caption.copy(color = T.inkMuted48),
                textAlign = TextAlign.Center,
            )
        }
    }
}

// ---------------------------------------------------------------------------
// 입력
// ---------------------------------------------------------------------------

/**
 * search-input. pill 모양 입력칸.
 * 입력창도 CTA와 같은 pill 문법을 쓰는 게 이 시스템의 특징이다.
 */
@Composable
fun PillInput(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    leading: String? = null,
    numeric: Boolean = false,
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .background(T.canvas, T.pill)
            .border(1.dp, T.hairline, T.pill)
            .heightIn(min = 52.dp)
            .padding(horizontal = 20.dp, vertical = T.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (leading != null) {
            Text(leading, style = T.body.copy(color = T.inkMuted48))
            Spacer(Modifier.width(T.sm))
        }
        Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.CenterStart) {
            if (value.isEmpty()) {
                Text(placeholder, style = T.body.copy(color = T.inkMuted48))
            }
            BasicTextField(
                value = value,
                onValueChange = onValueChange,
                textStyle = T.body,
                singleLine = true,
                cursorBrush = SolidColor(T.primary),
                keyboardOptions = KeyboardOptions(
                    keyboardType = if (numeric) KeyboardType.Number else KeyboardType.Text,
                ),
                modifier = Modifier.fillMaxWidth(),
            )
        }
    }
}

// ---------------------------------------------------------------------------
// 면
// ---------------------------------------------------------------------------

/**
 * product-tile. 화면 폭을 가득 채우는 띠.
 *
 * 타일끼리는 간격 없이 맞닿고, 배경색이 바뀌는 것 자체가 구분선 역할을 한다.
 * 그래서 모서리를 굴리지 않고 테두리도 넣지 않는다.
 */
@Composable
fun Tile(
    fill: Color,
    modifier: Modifier = Modifier,
    verticalPadding: Dp = T.section,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .background(fill)
            .padding(horizontal = T.gutter, vertical = verticalPadding),
        content = content,
    )
}

/** 섹션 제목 + 오른쪽 보조 텍스트. 어두운 타일 위에서도 쓸 수 있다. */
@Composable
fun SectionHead(
    title: String,
    trailing: String? = null,
    onDark: Boolean = false,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.Bottom,
    ) {
        Text(title, style = T.displayMd.copy(color = if (onDark) T.onDark else T.ink))
        if (trailing != null) {
            Text(
                trailing,
                style = T.caption.copy(color = if (onDark) T.bodyMuted else T.inkMuted48),
            )
        }
    }
}

/** floating-sticky-bar. 화면 하단에 떠 있는 바. */
@Composable
fun StickyBar(content: @Composable RowScope.() -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Hairline()
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(T.parchment)
                .padding(horizontal = T.gutter, vertical = T.sm)
                .heightIn(min = 56.dp),
            verticalAlignment = Alignment.CenterVertically,
            content = content,
        )
    }
}

/** 1dp 헤어라인 구분선. */
@Composable
fun Hairline(color: Color = T.hairline, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(1.dp).background(color))
}

/** 진행 표시. Material의 두꺼운 바 대신 헤어라인 위에 파란 선을 얹는다. */
@Composable
fun HairlineProgress(fraction: Float, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(2.dp).background(T.dividerSoft)) {
        Box(
            Modifier
                .fillMaxWidth(fraction.coerceIn(0f, 1f))
                .height(2.dp)
                .background(T.primary),
        )
    }
}

/** button-icon-circular. 44dp 원형 컨트롤. */
@Composable
fun CircularIconButton(
    glyph: String,
    modifier: Modifier = Modifier,
    onClick: () -> Unit,
) {
    val src = remember { MutableInteractionSource() }

    Box(
        modifier = modifier
            .pressScale(src)
            .size(44.dp)
            .background(T.chipTranslucent.copy(alpha = 0.64f), T.pill)
            .clickable(interactionSource = src, indication = null, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(glyph, style = T.bodyStrong)
    }
}
