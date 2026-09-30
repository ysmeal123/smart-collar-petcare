package com.petcare.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.petcare.data.ChatLog
import com.petcare.data.ChatTurn
import com.petcare.data.MyDog
import com.petcare.data.Repository
import kotlinx.coroutines.launch

/**
 * 대화 화면.
 *
 * 문진을 말풍선으로 보여준다. **판단은 전부 서버가 한다** — 여기서 답을
 * 해석하거나 계획을 바꾸지 않는다. 화면이 로직을 갖게 되면 같은 판단이
 * 두 군데 생기고 반드시 갈라진다.
 *
 * 대화록도 앱이 들고 있지 않다. 서버가 저장된 답변·메모로부터 매번 다시
 * 만들어 준다. 앱을 새로 깔아도 같은 대화가 나온다.
 *
 * 서버가 없으면 열지 않는다. 데모 대화를 흉내낼 수는 있지만 그러면
 * 답을 넣어도 계획이 안 바뀌는 화면이 된다 — 그건 문진이 장식이던
 * 예전 상태와 같다.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatSheet(
    dog: MyDog,
    repo: Repository,
    onDismiss: () -> Unit,
    onChanged: () -> Unit,
) {
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()

    var log by remember { mutableStateOf<ChatLog?>(null) }
    var draft by remember { mutableStateOf("") }
    var thinking by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }

    suspend fun refresh() {
        val next = repo.chat(dog.serverBase, dog.serverDogId, dog.serverToken)
        if (next == null) failed = true else log = next
    }

    LaunchedEffect(Unit) {
        thinking = true
        refresh()
        thinking = false
    }

    // 새 말풍선이 붙으면 아래로 따라간다. 사용자가 직접 스크롤하게 하면
    // 방금 온 답을 놓친다.
    LaunchedEffect(log?.turns?.size) {
        val n = log?.turns?.size ?: 0
        if (n > 0) listState.animateScrollToItem(n - 1)
    }

    /**
     * 자유 텍스트 특이사항.
     *
     * 서버가 문장을 구조화된 값으로 바꾸고 계획을 다시 만든다.
     */
    fun sendNote(text: String) {
        if (text.isBlank() || thinking) return
        scope.launch {
            thinking = true
            val r = repo.sendNote(dog.serverBase, dog.serverDogId, text, dog.serverToken)
            if (r == null) failed = true else log = r.chat
            thinking = false
            onChanged()          // 대시보드의 계획 카드도 갱신한다
        }
    }

    /**
     * 선택지 답변.
     *
     * 자유 텍스트와 경로가 다르다. 칩은 이미 자기 key 를 알고 있으므로
     * 그대로 보낸다. 문장으로 바꿔 보내면 서버가 다시 추출해야 하고,
     * "보여요" 같은 짧은 답은 추출에 실패한다.
     */
    fun answer(turn: ChatTurn, option: String) {
        if (thinking) return
        scope.launch {
            thinking = true
            val r = repo.answerQuestion(
                dog.serverBase, dog.serverDogId,
                turn.key, turn.valueOf(option), dog.serverToken,
            )
            if (r == null) failed = true else log = r.chat
            thinking = false
            onChanged()
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = state,
        containerColor = T.parchment,
        contentColor = T.ink,
    ) {
        // 높이를 확정해 둔다.
        //
        // 내용 높이에 맡기면 목록이 스스로를 감싸려 하면서 시트가 화면
        // 절반쯤에서 멈추고, 입력창이 화면 밖으로 밀려난다.
        // 시트 높이를 먼저 정하고 목록에 남는 공간을 주는 편이 예측 가능하다.
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.92f)
                .padding(horizontal = T.gutter),
        ) {

            Text(
                if (log?.dogName?.isNotBlank() == true) "${log!!.dogName} 상담"
                else "상담",
                style = T.displayMd,
            )
            Spacer(Modifier.height(T.xxs))
            Text(
                "센서로 알 수 없는 것만 여쭤봅니다. 답변은 영양 계획에 바로 반영됩니다.",
                style = T.caption.copy(color = T.inkMuted48),
            )
            Spacer(Modifier.height(T.md))

            when {
                failed && log == null -> Column(
                    modifier = Modifier.fillMaxWidth().weight(1f)
                        .padding(vertical = T.xxl),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("서버에 연결해야 상담을 할 수 있어요", style = T.bodyStrong)
                    Spacer(Modifier.height(T.xs))
                    Text(
                        "설정에서 서버 주소를 입력해 주세요. 해석과 재계산은 서버에서 합니다.",
                        style = T.caption.copy(color = T.inkMuted48),
                    )
                }

                log == null -> Column(
                    modifier = Modifier.fillMaxWidth().weight(1f)
                        .padding(vertical = T.xxl),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    ThinkingOrb(size = 64.dp)
                    Spacer(Modifier.height(T.md))
                    Text("기록을 살펴보고 있어요", style = T.caption)
                }

                else -> LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxWidth().weight(1f),
                    verticalArrangement = Arrangement.spacedBy(T.xs),
                ) {
                    itemsIndexed(log!!.turns) { i, turn ->
                        // 선택지는 **마지막 질문에만** 붙인다. 위쪽 질문까지
                        // 버튼이 살아 있으면 어디에 답하는 중인지 알 수 없다.
                        val last = log!!.turns.drop(i + 1).none { it.kind == "question" }
                        Bubble(
                            turn = turn,
                            answerable = turn.kind == "question" && last && !thinking,
                            onAnswer = { answer(turn, it) },
                        )
                    }
                    if (thinking) {
                        item {
                            ThinkingRow("계획을 다시 세우고 있어요")
                        }
                    }
                }
            }

            if (log != null) {
                Spacer(Modifier.height(T.md))
                NoteField(
                    value = draft,
                    onValueChange = { draft = it },
                    enabled = !thinking,
                    onSend = { sendNote(draft); draft = "" },
                )
                Spacer(Modifier.height(T.xs))
                Text(
                    "이 앱은 목줄이 관찰한 행동 변화를 알려드립니다. " +
                        "질병을 진단하거나 치료하지 않습니다.",
                    style = T.microLegal,
                )
            }

            Spacer(Modifier.height(T.md))
        }
    }
}

// ---------------------------------------------------------------------------
// 말풍선
// ---------------------------------------------------------------------------

@Composable
private fun Bubble(
    turn: ChatTurn,
    answerable: Boolean,
    onAnswer: (String) -> Unit,
) {
    val mine = !turn.fromAgent

    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
    ) {
        Box(
            modifier = Modifier
                .widthIn(max = 300.dp)
                .background(
                    color = when {
                        mine -> T.primary
                        turn.kind == "plan" -> T.canvas
                        else -> T.canvas
                    },
                    // 보내는 쪽 꼬리를 안쪽으로 눕힌다. 방향이 한눈에 읽힌다.
                    shape = RoundedCornerShape(
                        topStart = T.rLg, topEnd = T.rLg,
                        bottomStart = if (mine) T.rLg else T.rXs,
                        bottomEnd = if (mine) T.rXs else T.rLg,
                    ),
                )
                .then(
                    if (turn.kind == "plan")
                        Modifier.border(
                            1.dp, T.primary,
                            RoundedCornerShape(
                                topStart = T.rLg, topEnd = T.rLg,
                                bottomStart = T.rXs, bottomEnd = T.rLg,
                            ),
                        )
                    else Modifier
                )
                .padding(horizontal = T.md, vertical = T.sm),
        ) {
            Column {
                if (turn.kind == "plan") {
                    Text("오늘의 계획", style = T.captionStrong.copy(color = T.primary))
                    Spacer(Modifier.height(T.xxs))
                }
                Text(
                    turn.text,
                    style = T.body.copy(color = if (mine) T.onDark else T.ink),
                )
                // 왜 묻는지 함께 보여준다. 이유 없는 질문에는 아무도 답하지 않는다.
                if (turn.why.isNotBlank() && answerable) {
                    Spacer(Modifier.height(T.xxs))
                    Text(turn.why, style = T.finePrint)
                }
            }
        }

        if (answerable) {
            Spacer(Modifier.height(T.xs))
            Row(horizontalArrangement = Arrangement.spacedBy(T.xs)) {
                turn.choices.forEach { option ->
                    OptionChip(
                        label = option,
                        selected = false,
                        // 선택지를 그대로 보낸다. 서버의 extract() 가 해석한다 —
                        // 정규화를 앱에만 두면 다른 클라이언트에서 조용히 깨진다.
                        onClick = { onAnswer(option) },
                    )
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// 입력
// ---------------------------------------------------------------------------

/**
 * 특이사항 입력.
 *
 * `PillInput` 은 한 줄짜리라 여기 맞지 않는다. 보호자가 쓰는 문장은
 * "3일 전에 풀밭에서 뒹굴었는데 그 뒤부터 긁어요" 처럼 길다.
 */
@Composable
private fun NoteField(
    value: String,
    onValueChange: (String) -> Unit,
    enabled: Boolean,
    onSend: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(T.canvas, RoundedCornerShape(T.rLg))
            .border(1.dp, T.hairline, RoundedCornerShape(T.rLg))
            .padding(start = T.md, end = T.xs, top = T.xs, bottom = T.xs),
        verticalAlignment = Alignment.Bottom,
    ) {
        Box(
            modifier = Modifier.weight(1f).heightIn(min = 40.dp).padding(vertical = T.xs),
            contentAlignment = Alignment.CenterStart,
        ) {
            if (value.isEmpty()) {
                Text(
                    "특이사항을 자유롭게 적어주세요",
                    style = T.body.copy(color = T.inkMuted48),
                )
            }
            BasicTextField(
                value = value,
                onValueChange = onValueChange,
                textStyle = T.body,
                enabled = enabled,
                maxLines = 4,
                cursorBrush = SolidColor(T.primary),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Text),
                modifier = Modifier.fillMaxWidth(),
            )
        }
        UtilityButton(
            label = "전달",
            enabled = enabled && value.isNotBlank(),
            onClick = onSend,
        )
    }
}
