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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.petcare.data.AxisScore
import com.petcare.data.Dashboard
import com.petcare.data.Feeder
import com.petcare.data.FeederStore
import com.petcare.data.Notify
import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import com.petcare.data.Loaded
import com.petcare.data.Origin
import com.petcare.data.MyDog
import com.petcare.data.Prescription
import com.petcare.data.Repository
import kotlin.math.abs
import kotlin.math.roundToInt
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
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
    var waking by remember { mutableStateOf(false) }
    // 상담창은 재로드보다 오래 살아야 한다. 답변 -> reload++ -> Content 재생성
    // 이므로, Content 안에 두면 답을 할 때마다 창이 닫힌다.
    var showChat by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    // 남은 사료 알림이 메인 화면에서 울리므로 권한도 여기서 한 번 묻는다 (Android 13+).
    val ctx = LocalContext.current
    val askNotify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    LaunchedEffect(Unit) {
        if (Build.VERSION.SDK_INT >= 33 && !Notify.allowed(ctx)) {
            askNotify.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    LaunchedEffect(scenario, dog, reload) {
        loaded = null
        error = null
        // 무료 호스팅은 잠들어 있다가 첫 요청에 30~60초가 걸린다.
        // 기다리는 동안 무슨 일이 일어나는지 알려줘야 한다.
        waking = dog.serverBase.isNotBlank()
        runCatching {
            repo.load(
                scenario, dog, dog.serverBase, dog.serverDogId, dog.serverToken
            )
        }
            .onSuccess { loaded = it }
            .onFailure { error = it.message ?: it.toString() }
        waking = false
    }

    val data = loaded?.dashboard

    Box(modifier = Modifier.fillMaxSize().background(T.canvas)) {
        when {
            error != null -> Text(
                "데이터를 불러오지 못했습니다.\n$error",
                style = T.caption,
                modifier = Modifier.align(Alignment.Center).padding(T.xl),
            )

            data == null -> Column(
                modifier = Modifier.align(Alignment.Center),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                // 여기가 앱에서 가장 긴 기다림이다(콜드스타트 30~60초).
                // 기본 스피너 대신 궤도를 돌린다 — 기다리는 동안 무슨 일이
                // 일어나는지 문구로 함께 알려준다.
                ThinkingOrb(size = 72.dp)
                if (waking) {
                    Spacer(Modifier.height(T.md))
                    Text("서버를 깨우는 중입니다", style = T.caption)
                    Spacer(Modifier.height(T.xxs))
                    Text("무료 호스팅은 처음 한 번이 오래 걸립니다", style = T.finePrint)
                }
            }

            else -> Content(
                repo = repo,
                onReload = { reload++ },
                onOpenChat = { showChat = true },
                data = data!!,
                loaded = loaded!!,
                dog = dog,
                scenario = scenario,
                onSwitch = { scenario = it },
                onEditProfile = onEditProfile,
                onSaveProfile = onSaveProfile,
                onAnswer = { answers ->
                    scope.launch {
                        repo.answer(
                            dog.serverBase, dog.serverDogId, answers, dog.serverToken
                        )
                        reload++
                    }
                },
            )
        }

        if (showChat) {
            ChatSheet(
                dog = dog,
                repo = repo,
                onDismiss = { showChat = false },
                onChanged = { reload++ },
            )
        }
    }
}

@Composable
private fun Content(
    repo: Repository,
    onReload: () -> Unit,
    onOpenChat: () -> Unit,
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
    var showFeeder by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val feederStore = remember { FeederStore(context.applicationContext) }

    // --- 실시간: 목줄이 지금 하는 행동을 오늘 숫자에 더한다 ---------------------
    // 밥통 화면을 안 열어도 걸음·걷기·뛰기가 오르고, 남은 사료 알림도 여기서 울린다.
    // shortcut: 앱이 켜져 있는 동안 본 것만 더한다. 목줄 기록이 서버에 쌓이기 시작하면 서버 값으로 바꾼다.
    var liveWalkS by remember { mutableIntStateOf(0) }
    var liveRunS by remember { mutableIntStateOf(0) }
    var live by remember { mutableStateOf<Feeder.Status?>(null) }
    var leftNotified by remember { mutableStateOf(false) }
    LaunchedEffect(showFeeder, feederStore.route, feederStore.base) {
        if (showFeeder) return@LaunchedEffect          // 밥통 화면이 따로 읽는다
        val link = Feeder.Link(
            route = feederStore.route, direct = feederStore.base,
            serverBase = dog.serverBase, serverDogId = dog.serverDogId,
            serverToken = dog.serverToken,
        )
        var last = System.currentTimeMillis()
        while (isActive) {
            val st = runCatching { Feeder.status(link) }.getOrNull()
            val now = System.currentTimeMillis()
            val dt = ((now - last) / 1000).toInt().coerceIn(0, 5)   // 끊겼다 붙으면 크게 더하지 않는다
            last = now
            if (st != null) live = st
            val c = st?.collar
            if (c != null && c.fresh) when (c.cls) {
                1 -> liveWalkS += dt
                2 -> liveRunS += dt
            }
            if (st?.leftoverAlert == true && !leftNotified) {
                Notify.leftover(context, dog.name.ifBlank { "우리 아이" }, st.weightG, st.leftoverMin.toInt())
                leftNotified = true
            } else if (st?.leftoverAlert != true) {
                leftNotified = false
            }
            delay(2_000)
        }
    }

    // 활동 목표는 baseline(평소 수준)으로 잡는다.
    // 5kg 말티즈와 28kg 대형견에게 같은 걸음 수 목표를 들이대는 건 의미가 없다.
    val actTrend = data.trend["activity_sec"]
    val progress = if (actTrend != null && actTrend.baseline > 0) {
        ((today.activeSec + liveWalkS + liveRunS) / actTrend.baseline).toFloat()
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
            Header(dog, onFeeder = { showFeeder = true }, onSettings = { showSettings = true })
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
                    centerLabel = "평소 활동량",
                    color = if (rx.escalated) T.critical else T.primary,
                )
                Spacer(Modifier.height(T.md))
                // 걸음은 목줄이 세지 않아 시간으로 어림한다 (개 보행 약 2보/초, 뜀 약 3보/초).
                val steps = today.summary.steps + liveWalkS * 2 + liveRunS * 3
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Stat("$steps", "걸음")
                    StatDivider()
                    Stat(minSec(today.summary.runMin * 60 + liveRunS), "뛰기")
                    StatDivider()
                    Stat(minSec(today.summary.walkMin * 60 + liveWalkS), "걷기")
                }
                LiveLine(live)
            }

            Spacer(Modifier.height(T.xl))
            InsightCard(
                rx,
                added = data.plan?.meals.orEmpty()
                    .flatMap { m -> m.pellets.map { it.name } }.distinct(),
            )

            // 최종 출력. 계획이 있으면 그걸 쓰고, 없으면 기존 처방 카드로 내려간다.
            val plan = data.plan
            Spacer(Modifier.height(T.sm))
            if (plan != null) {
                PlanCard(plan, onFeed = { showFeeder = true }) { showTrace = true }
            } else {
                FeedingCard(rx, dog.mealsPerDay) { showTrace = true }
            }

            plan?.simulation?.let {
                Spacer(Modifier.height(T.sm))
                SimulationCard(it)
            }

            if (loaded.origin == Origin.LIVE) {
                Spacer(Modifier.height(T.sm))
                ChatEntryCard(plan?.questions?.size ?: 0, onOpenChat)
            } else if (plan != null && plan.questions.isNotEmpty()) {
                // 데모에는 서버가 없어 상담을 할 수 없다. 질문만 보여준다.
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

    if (showFeeder) {
        FeederSheet(store = feederStore, dog = dog, plan = data.plan, onDismiss = { showFeeder = false })
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
private fun Header(dog: MyDog, onFeeder: () -> Unit, onSettings: () -> Unit) {
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
        UtilityButton(label = "밥통", onClick = onFeeder)
        Spacer(Modifier.width(T.xs))
        UtilityButton(label = "설정", onClick = onSettings)
    }
}

/** 1분이 안 돼도 숫자가 움직이게 초까지 보여준다. */
private fun minSec(sec: Int): String =
    if (sec % 60 == 0) "${sec / 60}분" else "${sec / 60}분 ${sec % 60}초"

/** 목줄 지금 상태 한 줄. 밥통이 없으면 아무것도 안 보인다. */
@Composable
private fun LiveLine(st: Feeder.Status?) {
    val c = st?.collar ?: return
    Spacer(Modifier.height(T.xs))
    val now = if (c.fresh) "지금 ${c.behavior}" else if (c.connected) "목줄 연결됨" else "목줄 찾는 중"
    val shakes = if (c.shakes >= 0) " · 털기 ${c.shakes}회" else ""
    val bowl = if (st.hasScale) " · 밥그릇 ${if (st.weightG < 0.5) 0 else st.weightG.roundToInt()}g" else ""
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier.size(6.dp).background(
                if (c.fresh) T.primary else T.inkMuted48, CircleShape,
            ),
        )
        Spacer(Modifier.width(T.xxs))
        Text(now + shakes + bowl, style = T.finePrint)
    }
    if (st.leftoverAlert) {
        Spacer(Modifier.height(T.xxs))
        Text(
            "밥이 ${st.leftoverMin.toInt()}분째 남아 있어요",
            style = T.finePrint.copy(color = T.critical),
        )
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
