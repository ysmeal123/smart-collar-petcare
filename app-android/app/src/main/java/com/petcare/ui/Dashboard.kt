package com.petcare.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.petcare.data.AxisScore
import com.petcare.data.Dashboard
import com.petcare.data.Loaded
import com.petcare.data.Origin
import com.petcare.data.MyDog
import com.petcare.data.Prescription
import com.petcare.data.Repository
import kotlin.math.abs
import kotlin.math.roundToInt
import kotlinx.coroutines.launch

/**
 * 대시보드.
 *
 * 화면 구성은 DESIGN-apple.md 의 타일 리듬을 따른다.
 *   흰 면 → 어두운 타일 → 파치먼트
 * 타일끼리 간격을 두지 않고 배경색이 바뀌는 것 자체가 구분선이다.
 * 카드를 띄우는 그림자는 쓰지 않는다.
 */
@Composable
fun DashboardScreen(
    repo: Repository,
    dog: MyDog,
    onEditProfile: () -> Unit,
    onSaveProfile: (MyDog) -> Unit = {},
) {
    var scenario by remember { mutableStateOf("skin") }
    var loaded by remember { mutableStateOf<Loaded?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(scenario, dog, reload) {
        loaded = null
        error = null
        runCatching {
            repo.load(scenario, dog, dog.serverBase, dog.serverDogId)
        }
            .onSuccess { loaded = it }
            .onFailure { error = it.message ?: it.toString() }
    }

    val data = loaded?.dashboard

    Box(modifier = Modifier.fillMaxSize().background(T.canvas)) {
        when {
            error != null -> Text(
                "데이터를 불러오지 못했습니다.\n$error",
                style = T.caption,
                modifier = Modifier.align(Alignment.Center).padding(T.xl),
            )

            data == null -> CircularProgressIndicator(
                color = T.primary,
                modifier = Modifier.align(Alignment.Center),
            )

            else -> Content(
                data = data!!,
                loaded = loaded!!,
                dog = dog,
                scenario = scenario,
                onSwitch = { scenario = it },
                onEditProfile = onEditProfile,
                onSaveProfile = onSaveProfile,
                onAnswer = { answers ->
                    scope.launch {
                        repo.answer(dog.serverBase, dog.serverDogId, answers)
                        reload++
                    }
                },
            )
        }
    }
}

@Composable
private fun Content(
    data: Dashboard,
    loaded: Loaded,
    dog: MyDog,
    scenario: String,
    onSwitch: (String) -> Unit,
    onEditProfile: () -> Unit,
    onSaveProfile: (MyDog) -> Unit,
    onAnswer: (Map<String, String>) -> Unit,
) {
    val today = data.today
    val rx = data.prescription
    var showTrace by remember { mutableStateOf(false) }
    var showSettings by remember { mutableStateOf(false) }

    // 활동 목표는 baseline(평소 수준)으로 잡는다.
    // 5kg 말티즈와 28kg 대형견에게 같은 걸음 수 목표를 들이대는 건 의미가 없다.
    val actTrend = data.trend["activity_sec"]
    val progress = if (actTrend != null && actTrend.baseline > 0) {
        (today.activeSec / actTrend.baseline).toFloat()
    } else {
        0f
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState()),
    ) {

        // --- 흰 면 ---------------------------------------------------------
        Tile(fill = T.canvas, verticalPadding = T.lg) {
            Header(dog) { showSettings = true }
            Spacer(Modifier.height(T.sm))
            OriginBadge(loaded.origin == Origin.LIVE, loaded.note)

            // 시연용 시나리오 전환. 서버에 붙으면 의미가 없으므로 감춘다.
            if (loaded.origin == Origin.DEMO) {
                Spacer(Modifier.height(T.md))
                ScenarioPicker(scenario, onSwitch)
            }
            Spacer(Modifier.height(T.xl))

            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                ActivityRing(
                    progress = progress,
                    centerValue = "${(progress * 100).roundToInt()}%",
                    centerLabel = "평소 대비",
                    color = if (rx.escalated) T.critical else T.primary,
                )
                Spacer(Modifier.height(T.md))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Stat("${today.summary.steps}", "걸음")
                    StatDivider()
                    Stat("${today.summary.runMin}분", "뛰기")
                    StatDivider()
                    Stat("${today.summary.walkMin}분", "걷기")
                }
            }

            Spacer(Modifier.height(T.xl))
            InsightCard(rx)

            // 최종 출력. 계획이 있으면 그걸 쓰고, 없으면 기존 처방 카드로 내려간다.
            val plan = data.plan
            Spacer(Modifier.height(T.sm))
            if (plan != null) {
                PlanCard(plan) { showTrace = true }
            } else {
                FeedingCard(rx, dog.mealsPerDay) { showTrace = true }
            }

            plan?.simulation?.let {
                Spacer(Modifier.height(T.sm))
                SimulationCard(it)
            }

            if (plan != null && plan.questions.isNotEmpty()) {
                Spacer(Modifier.height(T.sm))
                QuestionCard(plan.questions, onAnswer)
            }
        }

        // --- 어두운 타일 ----------------------------------------------------
        //
        // 차트를 어두운 면에 몰아넣는다. 색을 더하지 않고 면을 바꿔서
        // 섹션을 나누는 것이 이 디자인 시스템의 방식이다.
        Tile(fill = T.tile1, verticalPadding = T.section) {
            SectionHead("활동량", "${today.activeSec / 60}분", onDark = true)
            Spacer(Modifier.height(T.md))
            HourlyChart(values = today.hourly.activitySec, onDark = true)

            Spacer(Modifier.height(T.xxl))
            SectionHead("수면", today.sleepLabel, onDark = true)
            Spacer(Modifier.height(T.md))
            SleepBand(today.hourly.postureChange, onDark = true)
            Spacer(Modifier.height(T.sm))
            Text(
                "뒤척임 ${today.sleep.restless}회 · 진할수록 자주 뒤척인 시간",
                style = T.finePrint.copy(color = T.bodyMuted),
            )

            data.trend["scratch_night"]?.let { t ->
                Spacer(Modifier.height(T.xxl))
                SectionHead("밤에 긁은 횟수", "최근 7일", onDark = true)
                Spacer(Modifier.height(T.md))
                TrendChart(values = t.values, baseline = t.baseline, onDark = true)
                Spacer(Modifier.height(T.sm))
                Text(
                    "점선이 평소 수준 (${t.baseline.roundToInt()}회)",
                    style = T.finePrint.copy(color = T.bodyMuted),
                )
            }

            Spacer(Modifier.height(T.xxl))
            SectionHead("긁은 시간대", "오늘", onDark = true)
            Spacer(Modifier.height(T.md))
            HourlyChart(
                values = today.hourly.scratch,
                onDark = true,
                highlightNight = true,
            )
            Spacer(Modifier.height(T.sm))
            Text(
                "밝은 구간이 밤 시간대 (22시~04시)",
                style = T.finePrint.copy(color = T.bodyMuted),
            )
        }

        // --- 파치먼트 -------------------------------------------------------
        Tile(fill = T.parchment, verticalPadding = T.section) {
            val twin = data.twin
            if (twin != null) {
                WellnessCard(twin)
            } else {
                SectionHead("상태 지표", "평소 대비")
                Spacer(Modifier.height(T.lg))
                rx.axes.forEachIndexed { i, a ->
                    AxisRow(a)
                    if (i != rx.axes.lastIndex) Spacer(Modifier.height(T.md))
                }
            }

            Spacer(Modifier.height(T.xxl))
            Hairline()
            Spacer(Modifier.height(T.md))
            Text(
                "이 앱은 목줄이 관찰한 행동 변화를 알려드립니다. " +
                    "질병을 진단하거나 치료하지 않습니다. " +
                    "아이에게 이상이 느껴지면 수의사에게 보여주세요.",
                style = T.microLegal,
            )
        }
    }

    if (showTrace) {
        TraceSheet(rx = rx, onDismiss = { showTrace = false })
    }

    if (showSettings) {
        SettingsSheet(
            dog = dog,
            onDismiss = { showSettings = false },
            onEditProfile = { showSettings = false; onEditProfile() },
            onSave = { showSettings = false; onSaveProfile(it) },
        )
    }
}

// ---------------------------------------------------------------------------

@Composable
private fun Header(dog: MyDog, onSettings: () -> Unit) {
    Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Box(
            modifier = Modifier
                .size(48.dp)
                .background(T.parchment, CircleShape)
                .border(1.dp, T.hairline, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            Text("🐕", fontSize = 22.sp)
        }
        Spacer(Modifier.width(T.sm))
        Column(modifier = Modifier.weight(1f)) {
            Text(dog.name, style = T.displayMd)
            Text(dog.subtitle, style = T.caption.copy(color = T.inkMuted48))
        }
        UtilityButton(label = "설정", onClick = onSettings)
    }
}

@Composable
private fun Stat(value: String, label: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, style = T.bodyStrong)
        Spacer(Modifier.height(2.dp))
        Text(label, style = T.finePrint)
    }
}

@Composable
private fun StatDivider() {
    Box(
        Modifier
            .padding(horizontal = T.lg)
            .width(1.dp)
            .height(24.dp)
            .background(T.hairline),
    )
}

/** 축 하나의 현재 점수. 처방 권한이 없는 축도 참고용으로 보여준다. */
@Composable
private fun AxisRow(axis: AxisScore) {
    // z를 -4 ~ +8 범위로 눌러서 게이지로 표현한다
    val t = (((axis.z + 4) / 12).coerceIn(0.0, 1.0)).toFloat()
    val color: Color = when {
        axis.active -> T.attention
        abs(axis.z) >= 2 -> T.primary
        else -> T.primary.copy(alpha = 0.45f)
    }

    Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            axis.label,
            style = T.caption.copy(color = T.ink),
            modifier = Modifier.width(66.dp),
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
            style = T.caption.copy(color = if (axis.active) T.attention else T.inkMuted48),
            modifier = Modifier.width(42.dp),
        )
    }
}

/** 시연용 시나리오 전환 칩. 실제 서비스에는 없는 화면이다. */
@Composable
private fun ScenarioPicker(current: String, onSelect: (String) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(T.xs),
    ) {
        Repository.scenarios.forEach { (key, label) ->
            OptionChip(
                label = label,
                selected = key == current,
                onClick = { onSelect(key) },
            )
        }
    }
}
