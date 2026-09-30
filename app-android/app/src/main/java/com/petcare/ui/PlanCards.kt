package com.petcare.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.petcare.data.AxisScore
import com.petcare.data.DogTwin
import com.petcare.data.FeedingPlan
import com.petcare.data.Meal
import com.petcare.data.Question
import com.petcare.data.Simulation
import kotlin.math.abs
import kotlin.math.roundToInt

// ---------------------------------------------------------------------------
// 급여 계획 — 이 시스템의 최종 출력
// ---------------------------------------------------------------------------

/**
 * 오늘 언제 무엇을 얼마나 줄 것인가.
 *
 * 서버가 내는 마지막 결과물이고, 밥통이 그대로 받아 사출한다.
 */
@Composable
fun PlanCard(plan: FeedingPlan, onWhy: () -> Unit) {
    Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("오늘의 급여 계획", style = T.captionStrong)
            Text(
                "${plan.derKcal.roundToInt()} kcal",
                style = T.caption.copy(color = T.inkMuted48),
            )
        }

        Spacer(Modifier.height(T.sm))
        Row(verticalAlignment = Alignment.Bottom) {
            Text("${plan.totalFoodG}", style = T.numeric)
            Spacer(Modifier.width(T.xxs))
            Text(
                "g",
                style = T.lead.copy(color = T.inkMuted48),
                modifier = Modifier.padding(bottom = 5.dp),
            )
        }

        if (!plan.ready && plan.blockedReason.isNotBlank()) {
            Spacer(Modifier.height(T.sm))
            Note(plan.blockedReason)
        }

        Spacer(Modifier.height(T.lg))
        plan.meals.forEachIndexed { i, meal ->
            if (i > 0) Spacer(Modifier.height(T.xs))
            MealRow(meal)
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
private fun MealRow(meal: Meal) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(T.pearl, RoundedCornerShape(T.rMd))
            .padding(horizontal = T.md, vertical = T.sm),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(meal.timeLabel, style = T.captionStrong)
            Spacer(Modifier.width(T.xs))
            Text(meal.label, style = T.finePrint)
            Spacer(Modifier.weight(1f))
            Text("사료 ${meal.foodG}g", style = T.caption.copy(color = T.ink))
        }

        meal.pellets.forEach { p ->
            Spacer(Modifier.height(T.xs))
            Row(verticalAlignment = Alignment.CenterVertically) {
                // 카트리지 색은 제품 식별용이다. 인터랙티브 색은 Action Blue 하나뿐.
                Box(Modifier.size(8.dp).background(T.hex(p.color), CircleShape))
                Spacer(Modifier.width(T.xs))
                Text("슬롯 ${p.slot}", style = T.finePrint)
                Spacer(Modifier.width(T.xs))
                Text(p.name, style = T.caption.copy(color = T.ink))
                Spacer(Modifier.weight(1f))
                Text("${p.count}알", style = T.captionStrong)
            }
        }
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

// ---------------------------------------------------------------------------
// 14일 전향 시뮬레이션
// ---------------------------------------------------------------------------

/**
 * 이 계획을 2주 유지하면 어디로 가는가.
 *
 * Safety 는 "오늘 안전한가"를 보고, 이건 "2주 뒤 방향이 맞는가"를 본다.
 * 숫자가 아니라 방향만 말한다 — 정밀 예측은 근거가 없다.
 */
@Composable
fun SimulationCard(sim: Simulation) {
    val dark = sim.needsAttention

    Column(
        modifier = Modifier.fillMaxWidth().utilityCard(
            fill = if (dark) T.tile1 else T.canvas,
            stroke = if (dark) T.tile1 else T.hairline,
        ),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(6.dp).background(
                    if (dark) T.attention else T.primary, CircleShape
                )
            )
            Spacer(Modifier.width(T.xs))
            Text(
                "${sim.horizonDays}일 뒤 전망",
                style = T.captionStrong.copy(
                    color = if (dark) T.bodyMuted else T.inkMuted48
                ),
            )
        }

        Spacer(Modifier.height(T.sm))
        Text(
            sim.weightLabel,
            style = T.displayMd.copy(color = if (dark) T.onDark else T.ink),
        )

        Spacer(Modifier.height(T.md))
        Row(modifier = Modifier.fillMaxWidth()) {
            SimStat("에너지", sim.energyLabel, dark, Modifier.weight(1f))
            SimStat(
                "체중",
                sim.projectedKg?.let { "%.1fkg".format(it) } ?: "—",
                dark, Modifier.weight(1f),
            )
            SimStat(
                "영양소",
                when (sim.exposure) {
                    "ACCEPTABLE" -> "여유 있음"
                    "NEAR_LIMIT" -> "상한 근처"
                    else -> "상한 초과"
                },
                dark, Modifier.weight(1f),
            )
        }

        if (sim.reasons.isNotEmpty()) {
            Spacer(Modifier.height(T.md))
            Hairline(color = if (dark) T.tile2 else T.dividerSoft)
            Spacer(Modifier.height(T.md))
            sim.reasons.forEach {
                Text(
                    "· $it",
                    style = T.caption.copy(
                        color = if (dark) T.bodyMuted else T.inkMuted48
                    ),
                )
                Spacer(Modifier.height(T.xxs))
            }
        }
    }
}

@Composable
private fun SimStat(label: String, value: String, dark: Boolean, modifier: Modifier) {
    Column(modifier = modifier) {
        Text(
            label,
            style = T.finePrint.copy(color = if (dark) T.bodyMuted else T.inkMuted48),
        )
        Spacer(Modifier.height(2.dp))
        Text(
            value,
            style = T.caption.copy(color = if (dark) T.onDark else T.ink),
        )
    }
}

// ---------------------------------------------------------------------------
// 보호자 질문 — 센서가 알 수 없는 맥락만
// ---------------------------------------------------------------------------

/**
 * 강아지는 "3일 전에 샴푸 바꿨어요"라고 말할 수 없다.
 *
 * 센서가 이미 아는 건 묻지 않는다. 답변은 처방을 **멈추거나 미루는** 쪽으로만
 * 작동한다 — 답변으로 용량이 올라가면 매출을 유도하는 경로가 열린다.
 */
@Composable
fun QuestionCard(
    questions: List<Question>,
    onAnswer: (Map<String, String>) -> Unit,
) {
    if (questions.isEmpty()) return

    val answers = remember(questions) { mutableStateMapOf<String, String>() }

    Column(modifier = Modifier.fillMaxWidth().utilityCard(fill = T.parchment)) {
        Text("몇 가지만 여쭤볼게요", style = T.captionStrong)
        Spacer(Modifier.height(T.xxs))
        Text(
            "목줄이 알 수 없는 것만 묻습니다.",
            style = T.caption.copy(color = T.inkMuted48),
        )

        questions.forEachIndexed { i, q ->
            if (i > 0) {
                Spacer(Modifier.height(T.md))
                Hairline(color = T.hairline)
            }
            Spacer(Modifier.height(T.md))

            Text(q.text, style = T.body)
            Spacer(Modifier.height(T.xxs))
            Text(q.why, style = T.finePrint)
            Spacer(Modifier.height(T.sm))

            Row(horizontalArrangement = Arrangement.spacedBy(T.xs)) {
                q.options.forEach { opt ->
                    OptionChip(
                        label = opt,
                        selected = answers[q.key] == q.valueOf(opt),
                        onClick = { answers[q.key] = q.valueOf(opt) },
                    )
                }
            }
        }

        Spacer(Modifier.height(T.lg))
        PrimaryPill(
            label = "답변 보내기",
            modifier = Modifier.fillMaxWidth(),
            enabled = answers.size == questions.size,
            onClick = { onAnswer(answers.toMap()) },
        )
    }
}

// ---------------------------------------------------------------------------
// 웰니스 5축
// ---------------------------------------------------------------------------

/**
 * 축마다 권한이 다르다. 그 차이를 화면에서도 보여준다.
 *
 *   피부 · 이동성   영양제를 움직일 수 있다
 *   수면·회복       원인 축이 조용할 때만
 *   귀              알림만 — 병원에 갈 문제다
 *   식욕            차단만 — 안 먹으면 영양제를 멈춘다
 */
private val AXIS_ROLE = mapOf(
    "skin" to "처방",
    "mobility" to "처방",
    "sleep" to "종속",
    "ear" to "알림만",
    "appetite" to "차단만",
)

@Composable
fun WellnessCard(twin: DogTwin) {
    Column(modifier = Modifier.fillMaxWidth()) {
        SectionHead("웰니스", "평소 대비")
        Spacer(Modifier.height(T.lg))

        twin.wellness.forEachIndexed { i, a ->
            if (i > 0) Spacer(Modifier.height(T.md))
            WellnessRow(a)
        }

        if (!twin.baseline.mature) {
            Spacer(Modifier.height(T.lg))
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(T.canvas, RoundedCornerShape(T.rMd))
                    .padding(T.md),
            ) {
                Text(
                    "${twin.baseline.stageNote} " +
                        "(관찰 ${twin.baseline.daysObserved}일). " +
                        "평소를 알기 전에는 영양제를 처방하지 않습니다.",
                    style = T.caption,
                )
            }
        }
    }
}

@Composable
private fun WellnessRow(axis: AxisScore) {
    // z를 -4 ~ +8 범위로 눌러서 게이지로 표현한다
    val t = (((axis.z + 4) / 12).coerceIn(0.0, 1.0)).toFloat()
    val color: Color = when {
        axis.active -> T.attention
        abs(axis.z) >= 2 -> T.primary
        else -> T.primary.copy(alpha = 0.45f)
    }

    Column(modifier = Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                axis.label,
                style = T.caption.copy(color = T.ink),
                modifier = Modifier.width(72.dp),
            )
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(4.dp)
                    .background(T.hairline, RoundedCornerShape(2.dp)),
            ) {
                Box(
                    Modifier
                        .fillMaxWidth(t)
                        .height(4.dp)
                        .background(color, RoundedCornerShape(2.dp)),
                )
            }
            Spacer(Modifier.width(T.sm))
            Text(
                String.format("%.1f", axis.z),
                style = T.caption.copy(
                    color = if (axis.active) T.attention else T.inkMuted48
                ),
                modifier = Modifier.width(42.dp),
            )
        }

        AXIS_ROLE[axis.axis]?.let { role ->
            if (role != "처방") {
                Spacer(Modifier.height(2.dp))
                Text(
                    "        $role",
                    style = T.microLegal,
                )
            }
        }

        if (axis.active && axis.message.isNotBlank()) {
            Spacer(Modifier.height(T.xxs))
            Text(
                "        ${axis.message}",
                style = T.finePrint.copy(color = T.attention),
            )
        }
    }
}

// ---------------------------------------------------------------------------
// 데이터 출처 표시
// ---------------------------------------------------------------------------

/**
 * 지금 보는 게 실데이터인지 데모인지.
 *
 * 발표 중 네트워크가 끊겨도 화면이 비지 않아야 하지만,
 * 데모 데이터를 실데이터처럼 보여주면 안 된다.
 */
@Composable
fun OriginBadge(live: Boolean, note: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier.size(6.dp).background(
                if (live) T.primary else T.inkMuted48, CircleShape
            )
        )
        Spacer(Modifier.width(T.xs))
        Text(
            if (live) "실시간 데이터" else "데모 데이터",
            style = T.finePrint,
        )
        if (note.isNotBlank()) {
            Spacer(Modifier.width(T.xs))
            Text(note, style = T.microLegal)
        }
    }
}

// ---------------------------------------------------------------------------
// 상담 입구
// ---------------------------------------------------------------------------

/**
 * 대화 화면으로 들어가는 입구.
 *
 * 물을 것이 있으면 개수를 보여준다. 없으면 "특이사항을 알려주세요"로 바꾼다.
 * 항상 같은 문구를 쓰면 물을 게 있을 때와 없을 때가 구분되지 않는다.
 *
 * 서버가 붙어 있을 때만 쓴다 — 해석과 재계산이 서버에서 일어나므로
 * 데모에서 열면 답을 넣어도 계획이 안 바뀐다.
 */
@Composable
fun ChatEntryCard(pending: Int, onClick: () -> Unit) {
    val src = remember { MutableInteractionSource() }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .pressScale(src)
            .background(T.canvas, RoundedCornerShape(T.rLg))
            .border(1.dp, if (pending > 0) T.primary else T.hairline, RoundedCornerShape(T.rLg))
            .clickable(interactionSource = src, indication = null, onClick = onClick)
            .padding(T.md),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        ThinkingOrb(size = 36.dp)
        Spacer(Modifier.width(T.md))
        Column(modifier = Modifier.weight(1f)) {
            Text(
                if (pending > 0) "여쭤볼 것이 ${pending}개 있어요" else "특이사항을 알려주세요",
                style = T.bodyStrong,
            )
            Spacer(Modifier.height(T.xxs))
            Text(
                if (pending > 0) "답변은 영양 계획에 바로 반영됩니다"
                else "센서가 알 수 없는 것을 알려주시면 계획에 반영합니다",
                style = T.caption.copy(color = T.inkMuted48),
            )
        }
        Text("›", style = T.displayMd.copy(color = T.inkMuted48))
    }
}
