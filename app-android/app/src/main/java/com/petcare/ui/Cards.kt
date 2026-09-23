package com.petcare.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.petcare.data.DispenseItem
import com.petcare.data.Prescription
import com.petcare.data.TraceStep

// ---------------------------------------------------------------------------
// 관찰 카드
// ---------------------------------------------------------------------------

/**
 * 진단 언어를 쓰지 않는다. 관찰된 사실만 서술한다.
 *   X "피부염입니다"
 *   O "밤에 긁는 횟수가 평소보다 2.5배 늘었어요"
 *
 * 심각도는 색이 아니라 면으로 표현한다.
 *   평소     흰 카드
 *   관찰됨   파치먼트 카드
 *   급성     어두운 타일
 * 색을 더하기 전에 면을 바꾸라는 게 이 디자인 시스템의 규칙이고,
 * 색맹 사용자에게도 대비가 그대로 남는다는 실용적 이점도 있다.
 */
@Composable
fun InsightCard(rx: Prescription) {
    when {
        rx.escalated -> Column(
            modifier = Modifier.fillMaxWidth().utilityCard(
                fill = T.tile1,
                stroke = T.tile1,
            ),
        ) {
            Marker("지금 확인이 필요합니다", T.critical, onDark = true)
            Spacer(Modifier.height(T.sm))
            Text("병원 진료를 권합니다", style = T.displayMd.copy(color = T.onDark))
            Spacer(Modifier.height(T.sm))
            Text(rx.escalationReason, style = T.body.copy(color = T.bodyMuted))
            Spacer(Modifier.height(T.md))
            Text(
                "영양제 처방은 자동으로 멈췄습니다. 사료는 평소대로 나갑니다.",
                style = T.caption.copy(color = T.bodyMuted),
            )
        }

        rx.firedAxes.isEmpty() -> Column(
            modifier = Modifier.fillMaxWidth().utilityCard(),
        ) {
            Marker("이번 주 관찰", T.primary)
            Spacer(Modifier.height(T.sm))
            Text("평소와 같아요", style = T.displayMd)
            Spacer(Modifier.height(T.sm))
            Text(
                "모든 지표가 평소 범위입니다. 오늘은 영양제를 추가하지 않았어요.",
                style = T.body.copy(color = T.inkMuted80),
            )
        }

        else -> Column(
            modifier = Modifier.fillMaxWidth().utilityCard(fill = T.parchment),
        ) {
            Marker("이번 주 관찰", T.attention)
            Spacer(Modifier.height(T.sm))
            rx.firedAxes.forEachIndexed { i, a ->
                if (i > 0) {
                    Spacer(Modifier.height(T.md))
                    Hairline(color = T.hairline)
                    Spacer(Modifier.height(T.md))
                }
                Text(a.message, style = T.displayMd)
                Spacer(Modifier.height(T.xs))
                Text("${a.label} 지표", style = T.caption.copy(color = T.inkMuted48))
            }
        }
    }
}

/** 작은 점 + 라벨. 아이콘 라이브러리 없이 상태를 표시한다. */
@Composable
private fun Marker(label: String, dot: Color, onDark: Boolean = false) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(6.dp).background(dot, CircleShape))
        Spacer(Modifier.width(T.xs))
        Text(
            label,
            style = T.captionStrong.copy(
                color = if (onDark) T.bodyMuted else T.inkMuted48,
            ),
        )
    }
}

// ---------------------------------------------------------------------------
// 오늘의 식사
// ---------------------------------------------------------------------------

@Composable
fun FeedingCard(rx: Prescription, mealsPerDay: Int, onWhy: () -> Unit) {
    Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("오늘의 식사", style = T.captionStrong)
            Text(
                "${rx.derKcal.toInt()} kcal",
                style = T.caption.copy(color = T.inkMuted48),
            )
        }

        Spacer(Modifier.height(T.sm))

        Row(verticalAlignment = Alignment.Bottom) {
            Text("${rx.foodGrams}", style = T.numeric)
            Spacer(Modifier.width(T.xxs))
            Text(
                "g",
                style = T.lead.copy(color = T.inkMuted48),
                modifier = Modifier.padding(bottom = 5.dp),
            )
            Spacer(Modifier.width(T.sm))
            Text(
                if (mealsPerDay > 1) "${mealsPerDay}회 나눠서" else "한 번에",
                style = T.caption.copy(color = T.inkMuted48),
                modifier = Modifier.padding(bottom = T.xs),
            )
        }

        Spacer(Modifier.height(T.lg))

        when {
            rx.escalated -> Note("영양제를 모두 중단했습니다. 사료만 정상 급여합니다.")
            rx.items.isEmpty() -> Note("오늘 추가할 영양제가 없습니다.")
            else -> {
                if (rx.morning.isNotEmpty()) MealGroup("아침", rx.morning)
                if (rx.evening.isNotEmpty()) {
                    Spacer(Modifier.height(T.md))
                    MealGroup("저녁", rx.evening)
                }
            }
        }

        Spacer(Modifier.height(T.lg))
        GhostPill(
            label = "왜 이렇게 나왔나요?",
            modifier = Modifier.fillMaxWidth(),
            onClick = onWhy,
        )
    }
}

@Composable
private fun Note(text: String) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .background(T.parchment, RoundedCornerShape(T.rMd))
            .padding(horizontal = T.md, vertical = T.sm),
    ) {
        Text(text, style = T.caption)
    }
}

@Composable
private fun MealGroup(label: String, items: List<DispenseItem>) {
    Column {
        Text(label, style = T.finePrint)
        Spacer(Modifier.height(T.xs))
        items.forEachIndexed { i, item ->
            PelletRow(item)
            if (i != items.lastIndex) Spacer(Modifier.height(T.xs))
        }
    }
}

@Composable
private fun PelletRow(item: DispenseItem) {
    // 카트리지 색은 제품 식별용이다. 데이터 표시에만 쓰고
    // 버튼이나 링크에는 쓰지 않는다 - 인터랙티브 색은 Action Blue 하나다.
    val c = T.hex(item.color)

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(T.pearl, RoundedCornerShape(T.rMd))
            .padding(horizontal = T.md, vertical = T.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(8.dp).background(c, CircleShape))
        Spacer(Modifier.width(T.sm))

        Column(modifier = Modifier.weight(1f)) {
            Text(item.name, style = T.caption.copy(color = T.ink))
            if (item.reason.isNotEmpty()) {
                Spacer(Modifier.height(2.dp))
                Text(item.reason, style = T.finePrint)
            }
        }

        // 알갱이를 실제 개수만큼 점으로 보여준다
        Row(verticalAlignment = Alignment.CenterVertically) {
            repeat(item.pellets.coerceAtMost(8)) {
                Box(
                    Modifier
                        .padding(start = 3.dp)
                        .size(6.dp)
                        .background(c, CircleShape),
                )
            }
            Spacer(Modifier.width(T.sm))
            Text("${item.pellets}알", style = T.captionStrong)
        }
    }
}

// ---------------------------------------------------------------------------
// 처방 근거 시트
// ---------------------------------------------------------------------------

/**
 * 모든 계산 단계를 그대로 펼쳐 보여준다.
 *
 * 영양제 사출량을 정하는 알고리즘이 곧 매출을 정한다는 이해상충이 있으므로,
 * 근거를 숨기지 않는 것이 최선의 방어다.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TraceSheet(rx: Prescription, onDismiss: () -> Unit) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = state,
        containerColor = T.canvas,
        contentColor = T.ink,
    ) {
        Column(modifier = Modifier.fillMaxWidth().padding(horizontal = T.gutter)) {
            Text("오늘의 처방 근거", style = T.displayMd)
            Spacer(Modifier.height(T.xs))
            Text(
                "계산 과정을 그대로 보여드립니다.",
                style = T.caption.copy(color = T.inkMuted48),
            )
            Spacer(Modifier.height(T.lg))

            // ModalBottomSheet 안에서 LazyColumn을 쓰면 높이 제약이 무한대로 넘어와
            // 크래시할 수 있다. 항목이 20개 안팎이라 일반 스크롤로 충분하다.
            Column(
                modifier = Modifier
                    .heightIn(max = 480.dp)
                    .verticalScroll(rememberScrollState())
                    .padding(bottom = T.xl),
            ) {
                rx.trace.forEachIndexed { i, step ->
                    TraceRow(step, isLast = i == rx.trace.lastIndex)
                }
            }
        }
    }
}

@Composable
private fun TraceRow(step: TraceStep, isLast: Boolean) {
    // 안전 필터가 개입한 단계는 눈에 띄게 표시한다
    val accent = when {
        step.step.contains("🚨") -> T.critical
        step.step.contains("🛡") -> T.primary
        step.changed -> T.ink
        else -> T.inkMuted48
    }

    Row(modifier = Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
        Column(
            modifier = Modifier.width(8.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(6.dp))
            Box(
                Modifier
                    .size(8.dp)
                    .background(if (step.changed) accent else T.hairline, CircleShape),
            )
            if (!isLast) {
                Box(Modifier.width(1.dp).weight(1f).background(T.dividerSoft))
            }
        }

        Spacer(Modifier.width(T.sm))

        Column(modifier = Modifier.padding(bottom = if (isLast) 0.dp else T.md)) {
            Text(step.step, style = T.captionStrong.copy(color = accent))
            Spacer(Modifier.height(2.dp))
            Text(step.detail, style = T.caption.copy(color = T.inkMuted48))
        }
    }
}
