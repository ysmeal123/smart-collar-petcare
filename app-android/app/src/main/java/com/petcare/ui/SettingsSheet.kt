package com.petcare.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import com.petcare.data.MyDog
import com.petcare.data.Net

/**
 * 설정.
 *
 * 서버 주소를 비워두면 asset 데모 데이터로 돈다.
 * 발표 중 네트워크가 끊겨도 화면이 비지 않아야 하므로 서버는 선택 사항이다.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsSheet(
    dog: MyDog,
    onDismiss: () -> Unit,
    onEditProfile: () -> Unit,
    onSave: (MyDog) -> Unit,
) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var base by remember { mutableStateOf(dog.serverBase) }
    var dogId by remember { mutableStateOf(dog.serverDogId) }
    var token by remember { mutableStateOf(dog.serverToken) }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = state,
        containerColor = T.canvas,
        contentColor = T.ink,
    ) {
        Column(modifier = Modifier.fillMaxWidth().padding(horizontal = T.gutter)) {
            Text("설정", style = T.displayMd)
            Spacer(Modifier.height(T.lg))

            // --- 프로필 -----------------------------------------------------
            Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
                Text(dog.name, style = T.bodyStrong)
                Spacer(Modifier.height(T.xxs))
                Text(dog.subtitle, style = T.caption.copy(color = T.inkMuted48))
                Spacer(Modifier.height(T.md))
                GhostPill(
                    label = "프로필 수정",
                    modifier = Modifier.fillMaxWidth(),
                    onClick = onEditProfile,
                )
            }

            Spacer(Modifier.height(T.sm))

            // --- 서버 -------------------------------------------------------
            Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
                Text("서버 연결", style = T.captionStrong)
                Spacer(Modifier.height(T.xxs))
                Text(
                    "비워두면 기기에 들어 있는 데모 데이터로 동작합니다.",
                    style = T.caption.copy(color = T.inkMuted48),
                )

                Spacer(Modifier.height(T.md))
                PillInput(
                    value = base,
                    onValueChange = { base = it },
                    placeholder = Net.DEFAULT_BASE,
                )
                Spacer(Modifier.height(T.xs))
                PillInput(
                    value = dogId,
                    onValueChange = { dogId = it },
                    placeholder = "개체 ID (예: dog_choco)",
                )
                Spacer(Modifier.height(T.xs))
                PillInput(
                    value = token,
                    onValueChange = { token = it },
                    placeholder = "앱 토큰 (배포 서버에 필요)",
                )

                Spacer(Modifier.height(T.sm))
                Text(
                    "에뮬레이터에서 PC 서버는 10.0.2.2 입니다. " +
                        "localhost 는 에뮬레이터 자신을 가리킵니다.",
                    style = T.microLegal,
                )

                Spacer(Modifier.height(T.md))
                Row(horizontalArrangement = Arrangement.spacedBy(T.xs)) {
                    GhostPill(
                        label = "데모로 되돌리기",
                        modifier = Modifier.weight(1f),
                        onClick = { base = ""; dogId = ""; token = "" },
                    )
                    PrimaryPill(
                        label = "저장",
                        modifier = Modifier.weight(1f),
                        onClick = {
                            onSave(
                                dog.copy(
                                    serverBase = base.trim(),
                                    serverDogId = dogId.trim(),
                                    serverToken = token.trim(),
                                )
                            )
                        },
                    )
                }
            }

            Spacer(Modifier.height(T.lg))
            Text(
                "이 앱은 목줄이 관찰한 행동 변화를 알려드립니다. " +
                    "질병을 진단하거나 치료하지 않습니다.",
                style = T.microLegal,
            )
            Spacer(Modifier.height(T.xxl))
        }
    }
}
