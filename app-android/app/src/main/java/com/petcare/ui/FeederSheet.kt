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
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 밥통 직접 제어.
 *
 * 슬롯마다 수량을 정하고 [배출]을 누르면 ESP32 가 그 모터만 돌린다.
 * 서버를 거치지 않는다 — 같은 핫스팟 안에서 바로 명령한다.
 *
 * 화면이 떠 있는 동안 1초마다 /status 를 읽는다. 배출 버튼의 활성 여부와
 * 진행 표시가 전부 그 값에서 나온다. 앱이 "돌고 있을 것" 이라고 추측하지 않는다.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FeederSheet(store: FeederStore, onDismiss: () -> Unit) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()

    var base by remember { mutableStateOf(store.base) }
    var editing by remember { mutableStateOf(base) }
    var status by remember { mutableStateOf<Feeder.Status?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf<String?>(null) }

    // 상태를 아직 못 받았을 때 보여줄 기본값. 펌웨어 SLOTS[] 와 같은 순서.
    val fallback = remember {
        listOf(
            Feeder.Slot(1, "omega3", "오메가3", "알", 10),
            Feeder.Slot(2, "skin_barrier", "피부장벽", "알", 10),
            Feeder.Slot(3, "joint", "관절", "알", 10),
            Feeder.Slot(4, "food", "사료", "회", 30),
        )
    }
    val counts = remember { mutableStateListOf(1, 1, 1, 1) }

    LaunchedEffect(base) {
        status = null
        error = null
        while (isActive) {
            try {
                status = Feeder.status(base)
                error = null
            } catch (e: Feeder.Failed) {
                status = null
                error = e.message
            }
            delay(1_000)
        }
    }

    val slots = status?.slots?.takeIf { it.isNotEmpty() } ?: fallback
    val connected = status != null
    val busy = status?.busy == true

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
            ConnectionLine(connected, busy, status, error)

            if (connected) {
                Spacer(Modifier.height(T.md))
                CollarCard(status?.collar)
            }

            Spacer(Modifier.height(T.lg))

            // --- 슬롯 ---------------------------------------------------------
            slots.forEachIndexed { i, s ->
                SlotRow(
                    slot = s,
                    count = counts.getOrElse(i) { 1 },
                    onCount = { c -> if (i < counts.size) counts[i] = c.coerceIn(1, s.max) },
                    enabled = connected && !busy,
                    progress = if (busy && s.requested > 0) "${s.done}/${s.requested}" else null,
                    onDispense = {
                        val c = counts.getOrElse(i) { 1 }
                        send({ Feeder.dispense(base, s.slot, c) }, "${s.name} ${c}${s.unit} 배출 시작")
                    },
                )
                Spacer(Modifier.height(T.xs))
            }

            Spacer(Modifier.height(T.xs))
            GhostPill(
                label = "정지",
                modifier = Modifier.fillMaxWidth(),
                onClick = { send({ Feeder.stop(base) }, "정지 요청을 보냈습니다") },
            )

            message?.let {
                Spacer(Modifier.height(T.sm))
                Text(it, style = T.caption, modifier = Modifier.fillMaxWidth(), textAlign = TextAlign.Center)
            }

            // --- 주소 ---------------------------------------------------------
            Spacer(Modifier.height(T.lg))
            Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
                Text("밥통 주소", style = T.captionStrong)
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
) {
    val (dot, text) = when {
        busy -> T.primary to "배출 중"
        connected -> T.primary to "연결됨 · 신호 ${status?.rssi ?: 0}dBm"
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
                Text("지금 ", style = T.caption.copy(color = T.inkMuted48))
                Text(collar.behavior ?: "-", style = T.displayMd)
                Spacer(Modifier.width(T.xs))
                Text("${collar.conf}%", style = T.caption.copy(color = T.inkMuted48))
            }
        }

        val m = collar?.min
        if (m != null && m.size >= 4 && (collar?.minAge ?: -1) >= 0) {
            Spacer(Modifier.height(T.xs))
            Text(
                "최근 1분 · 쉬기 ${m[0]}초 · 걷기 ${m[1]}초 · 뛰기 ${m[2]}초 · 털기 ${m[3]}회",
                style = T.finePrint,
            )
        }
    }
}

@Composable
private fun SlotRow(
    slot: Feeder.Slot,
    count: Int,
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
        CircularIconButton(glyph = "−", onClick = { onCount(count - 1) })
        Text(
            "$count${slot.unit}",
            style = T.bodyStrong,
            textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(min = 44.dp),
        )
        CircularIconButton(glyph = "+", onClick = { onCount(count + 1) })
        Spacer(Modifier.width(T.xs))
        PrimaryPill(label = "배출", enabled = enabled, onClick = onDispense)
    }
}
