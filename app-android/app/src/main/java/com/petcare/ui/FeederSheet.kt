package com.petcare.ui

import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.petcare.data.Feeder
import com.petcare.data.FeederStore
import com.petcare.data.FeedingPlan
import com.petcare.data.MyDog
import com.petcare.data.Notify
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 밥통 제어. **경로가 둘이다.**
 *
 *     서버 경유   명령을 서버에 쌓고 밥통이 60초 안에 가져간다 (기본)
 *     직접        같은 핫스팟의 ESP32 에 바로 쳐서 1초 안에 돈다 (시연용)
 *
 * 슬롯마다 수량을 정하고 [배출]을 누르면 그 모터만 돈다.
 * [오늘 계획대로 급여] 는 서버가 짠 하루치(영양제 + 사료)를 명령 하나로 보낸다.
 *
 * 화면이 떠 있는 동안 상태를 읽어서 버튼 활성 여부와 진행 표시를 전부
 * 그 값에서 낸다. **앱이 "돌고 있을 것" 이라고 추측하지 않는다.**
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FeederSheet(
    store: FeederStore,
    dog: MyDog,
    plan: FeedingPlan? = null,
    onDismiss: () -> Unit,
) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()

    var base by remember { mutableStateOf(store.base) }
    var editing by remember { mutableStateOf(base) }
    var route by remember { mutableStateOf(store.route) }
    var status by remember { mutableStateOf<Feeder.Status?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }

    val link = Feeder.Link(
        route = route,
        direct = base,
        serverBase = dog.serverBase,
        serverDogId = dog.serverDogId,
        serverToken = dog.serverToken,
    )

    // 상태를 아직 못 받았을 때 보여줄 기본값. 펌웨어 SLOTS[] 와 같은 순서.
    //
    // 4번은 **그램**이다. 서버가 사료를 food_g 로 다루므로 앱도 같은 단위를
    // 쓴다 - 경로마다 단위가 다르면 보호자가 틀린 양을 넣는 날이 온다.
    val fallback = remember {
        listOf(
            Feeder.Slot(1, "omega3", "오메가3", "알", 10),
            Feeder.Slot(2, "skin_barrier", "피부장벽", "알", 10),
            Feeder.Slot(3, "joint", "관절", "알", 10),
            Feeder.Slot(4, "food", "사료", "g", 300),
        )
    }
    val counts = remember { mutableStateListOf(1, 1, 1, 40) }

    // 남은 사료 알림. 기준을 넘는 순간 한 번만 울린다 (권한은 메인 화면이 묻는다).
    val ctx = LocalContext.current
    var notified by remember { mutableStateOf(false) }
    val leftAlert = status?.leftoverAlert == true
    LaunchedEffect(leftAlert) {
        val st = status
        if (leftAlert && !notified && st != null) {
            Notify.leftover(ctx, dog.name.ifBlank { "우리 아이" }, st.weightG, st.leftoverMin.toInt())
            notified = true
        } else if (!leftAlert) {
            notified = false
        }
    }

    // 밥통이 2초마다 보고한다. 앱이 그보다 느리게 읽으면 그만큼 더 늦는다.
    val pollMs = if (link.viaServer) 1_000L else 700L

    LaunchedEffect(base, route) {
        status = null
        error = null
        while (isActive) {
            try {
                status = Feeder.status(link)
                error = null
            } catch (e: Feeder.Failed) {
                status = null
                error = e.message
            }
            delay(pollMs)
        }
    }

    val slots = status?.slots?.takeIf { it.isNotEmpty() } ?: fallback
    val connected = status != null
    val busy = status?.busy == true

    // 서버 경유는 밥통이 꺼져 있어도 명령을 쌓을 수 있다. 버튼을 막으면
    // 보호자가 할 수 있는 일이 없어진다 — 10분 안에 깨면 실행된다.
    val canSend = (link.viaServer || connected) && !busy

    fun send(block: suspend () -> Unit, okText: String) {
        scope.launch {
            message = try {
                block()
                okText
            } catch (e: Feeder.Failed) {
                e.message
            }
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = state,
        containerColor = T.canvas,
        contentColor = T.ink,
    ) {
        Column(modifier = Modifier.fillMaxWidth().padding(horizontal = T.gutter)) {
            Text("밥통", style = T.displayMd)
            Spacer(Modifier.height(T.xxs))
            ConnectionLine(connected, busy, status, error, link.viaServer)

            Spacer(Modifier.height(T.md))
            RouteToggle(
                route = route,
                canServer = dog.serverBase.isNotBlank() && dog.serverDogId.isNotBlank(),
                onPick = { r ->
                    route = r
                    store.route = r
                    message = null
                },
            )

            if (connected) {
                Spacer(Modifier.height(T.md))
                CollarCard(status?.collar)
                ScaleCard(
                    status = status,
                    onTare = { send({ Feeder.tare(link) }, "영점을 맞췄습니다. 그릇이 0g 이 됩니다") },
                )
            }

            // --- 오늘 계획 --------------------------------------------------
            //
            // 끼니별 알 수를 슬롯마다 더하고, 사료는 하루 합계를 쓴다.
            // 판정은 서버가 했다. 앱은 숫자를 옮기기만 한다.
            val dayPellets = plan?.meals.orEmpty()
                .flatMap { it.pellets }
                .groupBy { it.slot }
                .mapValues { (_, ps) -> ps.sumOf { it.count } }
                .filterValues { it > 0 }
            val dayFood = plan?.totalFoodG ?: 0
            if (plan != null && plan.ready && !plan.escalated && (dayPellets.isNotEmpty() || dayFood > 0)) {
                Spacer(Modifier.height(T.lg))
                Column(modifier = Modifier.fillMaxWidth().utilityCard(padding = T.md)) {
                    Text("오늘 급여 계획", style = T.captionStrong)
                    Spacer(Modifier.height(T.xxs))
                    val bySlot = plan.meals.flatMap { it.pellets }.groupBy { it.slot }
                    val parts = dayPellets.toSortedMap().map { (slot, n) ->
                        val ps = bySlot[slot].orEmpty()
                        val mg = ps.sumOf { it.doseMg }
                        val amt = if (mg > 0) ps.first().copy(doseMg = mg).amount else "${n}알"
                        "${ps.firstOrNull()?.name ?: "${slot}번"} $amt"
                    } + listOfNotNull(if (dayFood > 0) "사료 ${dayFood}g" else null)
                    Text(parts.joinToString(" · "), style = T.caption)
                    Spacer(Modifier.height(T.sm))
                    PrimaryPill(
                        label = "오늘 계획대로 급여",
                        modifier = Modifier.fillMaxWidth(),
                        enabled = canSend,
                        onClick = {
                            val req = "plan-${System.currentTimeMillis()}"
                            send(
                                { Feeder.dispensePlan(link, dayPellets, dayFood, req) },
                                if (link.viaServer) "오늘 계획 전송 — 밥통이 곧 가져갑니다"
                                else "오늘 계획 배출 시작",
                            )
                        },
                    )
                }
            }

            Spacer(Modifier.height(T.lg))

            // --- 슬롯 ---------------------------------------------------------
            slots.forEachIndexed { i, s ->
                // 사료는 그램이라 1씩 올리면 300까지 300번을 눌러야 한다.
                val step = if (s.slot == Feeder.FOOD_SLOT) 10 else 1
                SlotRow(
                    slot = s,
                    count = counts.getOrElse(i) { 1 },
                    step = step,
                    onCount = { c -> if (i < counts.size) counts[i] = c.coerceIn(1, s.max) },
                    enabled = canSend,
                    progress = if (busy && s.requested > 0) "${s.done}/${s.requested}" else null,
                    onDispense = {
                        val c = counts.getOrElse(i) { 1 }
                        // 버튼 한 번에 키 하나. 같은 눌림이 두 번 나가지 않게
                        // 서버가 이 값으로 걸러낸다.
                        val req = "btn-${s.slot}-${System.currentTimeMillis()}"
                        send(
                            { Feeder.dispense(link, s.slot, c, req) },
                            if (link.viaServer) "${s.name} ${c}${s.unit} — 밥통이 몇 초 안에 가져갑니다"
                            else "${s.name} ${c}${s.unit} 배출 시작",
                        )
                    },
                )
                Spacer(Modifier.height(T.xs))
            }

            Spacer(Modifier.height(T.xs))
            GhostPill(
                label = if (link.viaServer) "대기 중인 명령 취소" else "정지",
                modifier = Modifier.fillMaxWidth(),
                onClick = {
                    scope.launch {
                        message = try {
                            Feeder.stop(link)
                        } catch (e: Feeder.Failed) {
                            e.message
                        }
                    }
                },
            )

            if (link.viaServer && (status?.pending ?: 0) > 0) {
                Spacer(Modifier.height(T.xs))
                Text(
                    "아직 밥통이 안 가져간 명령 ${status?.pending}건. " +
                        "10분이 지나면 저절로 사라집니다.",
                    style = T.finePrint,
                )
            }

            message?.let {
                Spacer(Modifier.height(T.sm))
                Text(it, style = T.caption, modifier = Modifier.fillMaxWidth(), textAlign = TextAlign.Center)
            }

            // --- 주소 ---------------------------------------------------------
            //
            // 직접 경로에서만 쓴다. 서버 경유일 때는 서버가 밥통을 찾으므로
            // 주소를 알 필요가 없다.
            Spacer(Modifier.height(T.lg))
            Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
                Text("밥통 주소 (직접 경로)", style = T.captionStrong)
                Spacer(Modifier.height(T.xxs))
                Text(
                    "ESP32 시리얼 로그에 찍힌 주소입니다. 폰이 같은 핫스팟에 연결돼 있어야 합니다.",
                    style = T.caption.copy(color = T.inkMuted48),
                )
                Spacer(Modifier.height(T.md))
                PillInput(
                    value = editing,
                    onValueChange = { editing = it },
                    placeholder = Feeder.DEFAULT_BASE,
                )
                Spacer(Modifier.height(T.sm))
                PrimaryPill(
                    label = "저장하고 연결",
                    modifier = Modifier.fillMaxWidth(),
                    onClick = {
                        val v = editing.trim().ifEmpty { Feeder.DEFAULT_BASE }
                        store.base = v
                        editing = v
                        base = v
                        message = null
                    },
                )
            }
            Spacer(Modifier.height(T.xxl))
        }
    }
}

@Composable
private fun ConnectionLine(
    connected: Boolean,
    busy: Boolean,
    status: Feeder.Status?,
    error: String?,
    viaServer: Boolean,
) {
    // 서버 경유에서는 '연결' 이 두 겹이다 - 앱↔서버, 그리고 밥통↔서버.
    // 서버는 붙었는데 밥통이 자고 있는 상태를 뭉개면 안 된다.
    val online = status?.ok == true
    val (dot, text) = when {
        busy -> T.primary to "배출 중"
        viaServer && connected && online ->
            T.primary to "서버 경유 · 밥통이 ${status?.ageS ?: 0}초 전에 보고"
        viaServer && connected ->
            T.attention to "서버는 붙었지만 밥통이 보고를 끊었습니다"
        connected -> T.primary to "직접 연결 · 신호 ${status?.rssi ?: 0}dBm"
        error != null -> T.critical to error
        else -> T.inkMuted48 to "연결 확인 중…"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).background(dot, T.pill))
        Spacer(Modifier.width(T.xs))
        Text(text, style = T.caption.copy(color = T.inkMuted48))
    }
}

/**
 * 경로 전환.
 *
 * 서버 설정이 없으면 서버 쪽을 고를 수 없다. 고르게 해두면 아무것도
 * 안 되는 화면을 보게 되는데, **왜 안 되는지가 화면에 없다.**
 */
@Composable
private fun RouteToggle(route: String, canServer: Boolean, onPick: (String) -> Unit) {
    Column(modifier = Modifier.fillMaxWidth()) {
        Row(horizontalArrangement = Arrangement.spacedBy(T.xs)) {
            OptionChip(
                label = "서버 경유",
                selected = route == Feeder.VIA_SERVER && canServer,
                // 서버 설정이 없으면 눌러도 안 바꾼다. 바꿔두면 아무것도 안
                // 되는 화면이 되고, 왜 안 되는지는 아래 문장에만 있다.
                onClick = { if (canServer) onPick(Feeder.VIA_SERVER) },
            )
            OptionChip(
                label = "직접",
                selected = route == Feeder.VIA_DIRECT || !canServer,
                onClick = { onPick(Feeder.VIA_DIRECT) },
            )
        }
        Spacer(Modifier.height(T.xxs))
        Text(
            when {
                !canServer -> "설정에서 서버 주소를 넣으면 서버 경유를 쓸 수 있습니다."
                route == Feeder.VIA_SERVER ->
                    "명령을 서버에 맡깁니다. 집 밖에서도 되고, 밥통이 몇 초 안에 가져갑니다."
                else ->
                    "같은 핫스팟의 밥통에 바로 보냅니다. 1초 안에 돌지만 밖에서는 안 됩니다."
            },
            style = T.finePrint,
        )
    }
}

/**
 * 목줄 → (BLE) → 밥통 → (Wi-Fi) → 앱.
 * 밥통이 목줄과 붙어 있는지, 지금 무슨 행동으로 판정됐는지, 최근 1분 요약.
 */
@Composable
private fun CollarCard(collar: Feeder.Collar?) {
    Column(modifier = Modifier.fillMaxWidth().utilityCard(padding = T.md)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            val on = collar?.connected == true
            Box(Modifier.size(8.dp).background(if (on) T.primary else T.inkMuted48, T.pill))
            Spacer(Modifier.width(T.xs))
            Text(
                when {
                    collar == null -> "목줄 정보 없음 (밥통 펌웨어 확인)"
                    on -> "목줄 연결됨"
                    else -> "목줄 찾는 중…"
                },
                style = T.captionStrong,
            )
        }

        if (collar != null && collar.fresh) {
            Spacer(Modifier.height(T.xs))
            Row(verticalAlignment = Alignment.Bottom) {
                // 서버 경유면 최대 1분 지난 값이다. "지금" 이라고 적으면
                // 거짓이 된다 - 몇 초 전인지 그대로 쓴다.
                Text(
                    if (collar.age > 5) "${collar.age}초 전 " else "지금 ",
                    style = T.caption.copy(color = T.inkMuted48),
                )
                Text(collar.behavior ?: "-", style = T.displayMd)
                Spacer(Modifier.width(T.xs))
                Text("${collar.conf}%", style = T.caption.copy(color = T.inkMuted48))
            }
        }

        // 털 때마다 바로 오른다. 시연에서 "센서가 진짜로 잡는다" 를 보여주는 숫자.
        if (collar != null && collar.shakes >= 0) {
            Spacer(Modifier.height(T.xs))
            Row(verticalAlignment = Alignment.Bottom) {
                Text("털기 ", style = T.caption.copy(color = T.inkMuted48))
                Text("${collar.shakes}", style = T.displayMd)
                Text("회 (밥통 켜진 뒤)", style = T.caption.copy(color = T.inkMuted48))
            }
        }

        val m = collar?.min
        // 밥통이 1초 판정으로 직접 세므로 min_age 가 없어도(-1) 값이 차 있으면 보인다.
        if (m != null && m.size >= 4 && ((collar?.minAge ?: -1) >= 0 || m.sum() > 0)) {
            Spacer(Modifier.height(T.xs))
            Text(
                "최근 1분 · 쉬기 ${m[0]}초 · 걷기 ${m[1]}초 · 뛰기 ${m[2]}초 · 털기 ${m[3]}회",
                style = T.finePrint,
            )
        }
    }
}

/**
 * 밥그릇 저울. 사료가 나오는 동안 숫자가 오르는 걸 보여준다.
 *
 * **g 숫자는 항상 보인다.** 저울이 없거나 아직 못 읽었으면 '--' 로 두고
 * 이유를 적는다 - 카드가 사라지면 저울이 있는지조차 알 수 없다.
 */
@Composable
private fun ScaleCard(status: Feeder.Status?, onTare: () -> Unit) {
    val has = status?.hasScale == true
    val w = status?.weightG ?: -1.0
    Spacer(Modifier.height(T.xs))
    Column(modifier = Modifier.fillMaxWidth().utilityCard(padding = T.md)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text("밥그릇 무게", style = T.captionStrong)
                Spacer(Modifier.height(T.xs))
                Row(verticalAlignment = Alignment.Bottom) {
                    // 영점 근처 흔들림(-0.3g 등)은 0 으로 보여준다.
                    val text = when {
                        !has -> "--"
                        w < 0.5 -> "0.0"
                        else -> String.format(java.util.Locale.US, "%.1f", w)
                    }
                    Text(text, style = T.displayMd)
                    Text(" g", style = T.caption.copy(color = T.inkMuted48))
                }
            }
            PrimaryPill(label = "영점", enabled = has, onClick = onTare)
        }
        Spacer(Modifier.height(T.xxs))
        Text(
            when {
                status == null -> "밥통에 연결되면 무게가 보입니다"
                !has -> "저울이 연결되지 않았습니다 (밥통 펌웨어·배선 확인)"
                else -> "그릇을 올리고 [영점]을 누르면 그릇 무게가 빠집니다"
            },
            style = T.finePrint,
        )
        if (status != null && status.leftoverAlert) {
            Spacer(Modifier.height(T.xs))
            Text(
                "사료 ${w.toInt()}g이 ${status.leftoverMin.toInt()}분째 남아 있어요. 식욕을 확인해 주세요.",
                style = T.captionStrong.copy(color = T.critical),
            )
        } else if (has && w >= Feeder.LEFTOVER_G && status!!.leftoverMin > 0) {
            Spacer(Modifier.height(T.xs))
            Text(
                "남은 사료 ${w.toInt()}g · ${status.leftoverMin.toInt()}분째 " +
                    "(${Feeder.LEFTOVER_MIN.toInt()}분 넘으면 알림)",
                style = T.finePrint,
            )
        }
    }
}

@Composable
private fun SlotRow(
    slot: Feeder.Slot,
    count: Int,
    step: Int,
    onCount: (Int) -> Unit,
    enabled: Boolean,
    progress: String?,
    onDispense: () -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth().utilityCard(padding = T.sm),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text("${slot.slot}. ${slot.name}", style = T.bodyStrong)
            Text(
                progress?.let { "진행 $it" } ?: "최대 ${slot.max}${slot.unit}",
                style = T.finePrint,
            )
        }
        CircularIconButton(glyph = "−", onClick = { onCount(count - step) })
        Text(
            "$count${slot.unit}",
            style = T.bodyStrong,
            textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(min = 52.dp),
        )
        CircularIconButton(glyph = "+", onClick = { onCount(count + step) })
        Spacer(Modifier.width(T.xs))
        PrimaryPill(label = "배출", enabled = enabled, onClick = onDispense)
    }
}
