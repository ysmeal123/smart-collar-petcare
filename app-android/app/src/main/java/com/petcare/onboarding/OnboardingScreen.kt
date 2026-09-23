package com.petcare.onboarding

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.petcare.data.ActivityLevel
import com.petcare.data.Allergen
import com.petcare.data.BodyCondition
import com.petcare.data.Breeds
import com.petcare.data.Condition
import com.petcare.data.DogSize
import com.petcare.data.Medication
import com.petcare.data.MyDog
import com.petcare.data.SafetyPreview
import com.petcare.data.Sex
import com.petcare.data.Surgery
import com.petcare.data.SurgeryType
import com.petcare.ui.GhostPill
import com.petcare.ui.Hairline
import com.petcare.ui.HairlineProgress
import com.petcare.ui.OptionCard
import com.petcare.ui.OptionChip
import com.petcare.ui.PillInput
import com.petcare.ui.PrimaryPill
import com.petcare.ui.StickyBar
import com.petcare.ui.T
import com.petcare.ui.TextLink
import com.petcare.ui.utilityCard
import java.util.Calendar
import kotlin.math.roundToInt

/**
 * 온보딩 12문항.
 *
 * 형식은 하이브리드다. 말은 대화체로 걸고, 입력은 버튼과 슬라이더로 받는다.
 * 자유 입력은 이름과 견종 검색뿐이다 — 나머지를 텍스트로 받으면 정규화가 지옥이 되고
 * 알고리즘에 그대로 못 꽂는다.
 *
 * 1~8번은 필수다. 알고리즘이 이 값 없이는 급여량 자체를 못 낸다.
 * 9~12번은 건너뛸 수 있다. 대신 건너뛴 사실을 프로필에 남겨서
 * 나중에 처방 근거에 "이 정보 없이 계산했습니다"라고 밝힌다.
 */

private enum class Step {
    WELCOME,
    NAME, AGE, SEX, WEIGHT, SIZE, BODY, ACTIVITY, MEALS,
    BREED, ALLERGY, SURGERY, HEALTH,
    SUMMARY,
}

private val QUESTIONS = listOf(
    Step.NAME, Step.AGE, Step.SEX, Step.WEIGHT, Step.SIZE, Step.BODY,
    Step.ACTIVITY, Step.MEALS, Step.BREED, Step.ALLERGY, Step.SURGERY, Step.HEALTH,
)

private val OPTIONAL = setOf(Step.BREED, Step.ALLERGY, Step.SURGERY, Step.HEALTH)

/** 끼니 수에 따른 기본 급여 시각. */
private fun defaultMealHours(count: Int): List<Int> = when (count) {
    1 -> listOf(8)
    3 -> listOf(7, 13, 19)
    else -> listOf(8, 19)
}

private fun hourChoices(slot: Int, total: Int): List<Int> = when {
    total == 1 -> listOf(7, 8, 9, 10, 11, 12)
    total == 2 && slot == 0 -> listOf(6, 7, 8, 9, 10)
    total == 2 -> listOf(17, 18, 19, 20, 21)
    slot == 0 -> listOf(6, 7, 8)
    slot == 1 -> listOf(12, 13, 14)
    else -> listOf(18, 19, 20)
}

/**
 * @param initial 이미 등록된 프로필. 설정에서 수정할 때 넘어온다.
 *   이 값이 있으면 환영 화면을 건너뛰고 바로 1번 문항부터 시작한다.
 */
@Composable
fun OnboardingScreen(initial: MyDog? = null, onDone: (MyDog) -> Unit) {
    val steps = remember { Step.entries.toList() }
    var index by remember { mutableIntStateOf(if (initial == null) 0 else 1) }
    var dog by remember { mutableStateOf(initial ?: MyDog()) }

    // 문항별 UI 상태. 프로필에 남길 값이 아니라서 따로 둔다.
    var birthYear by remember { mutableIntStateOf(Calendar.getInstance().get(Calendar.YEAR) - 2) }
    var birthMonth by remember { mutableIntStateOf(1) }
    var breedQuery by remember { mutableStateOf("") }
    var allergyAsked by remember { mutableStateOf<Boolean?>(null) }
    var surgeryAsked by remember { mutableStateOf<Boolean?>(null) }
    var surgeryWhen by remember { mutableStateOf(2.0) }

    val step = steps[index]
    val questionNo = QUESTIONS.indexOf(step)

    fun syncAge() {
        val now = Calendar.getInstance()
        val months = (now.get(Calendar.YEAR) - birthYear) * 12 +
            (now.get(Calendar.MONTH) + 1 - birthMonth)
        dog = dog.copy(ageMonths = months.coerceIn(0, 300))
    }

    fun advance() {
        if (step == Step.SUMMARY) onDone(dog) else index++
    }

    when (step) {
        Step.WELCOME -> WelcomeTile(onStart = { index++ })

        Step.SUMMARY -> SummaryScreen(dog = dog, onStart = { onDone(dog) })

        else -> QuestionScaffold(
            progress = (questionNo + 1) / QUESTIONS.size.toFloat(),
            questionNo = questionNo + 1,
            canGoBack = index > 1,
            optional = step in OPTIONAL,
            canAdvance = when (step) {
                Step.NAME -> dog.name.isNotBlank()
                Step.ALLERGY -> allergyAsked != null &&
                    (allergyAsked == false || dog.allergies.isNotEmpty())
                Step.SURGERY -> surgeryAsked != null &&
                    (surgeryAsked == false || dog.surgeries.isNotEmpty())
                else -> true
            },
            hint = stepHint(step, dog),
            onBack = { index-- },
            onSkip = {
                dog = dog.copy(skippedOptional = true)
                index++
            },
            onNext = { advance() },
        ) {
            when (step) {
                Step.NAME -> QuestionBody(
                    title = "반가워요.\n아이 이름이 어떻게 되나요?",
                    lead = "앱 곳곳에서 이 이름으로 부를게요.",
                ) {
                    PillInput(
                        value = dog.name,
                        onValueChange = { dog = dog.copy(name = it) },
                        placeholder = "예: 초코",
                    )
                }

                Step.AGE -> QuestionBody(
                    title = "${dog.name}는 언제 태어났나요?",
                    lead = "정확하지 않아도 괜찮아요. 대략이면 충분합니다.",
                ) {
                    val thisYear = Calendar.getInstance().get(Calendar.YEAR)
                    ChipGroup(
                        label = "태어난 해",
                        options = (0..19).map { thisYear - it },
                        selected = { it == birthYear },
                        labelOf = { "${it}년" },
                        onSelect = { birthYear = it; syncAge() },
                    )
                    Spacer(Modifier.height(T.lg))
                    ChipGroup(
                        label = "태어난 달",
                        options = (1..12).toList(),
                        selected = { it == birthMonth },
                        labelOf = { "${it}월" },
                        onSelect = { birthMonth = it; syncAge() },
                    )
                    Spacer(Modifier.height(T.lg))
                    ReadoutCard(
                        value = dog.ageLabel,
                        note = if (dog.isPuppy) {
                            "성장기예요. 성견보다 더 많은 열량이 필요합니다."
                        } else if (dog.isSenior) {
                            "노령기에 들어섰어요. 관절과 인지 지표를 더 자주 봅니다."
                        } else {
                            "성견 기준으로 급여량을 계산합니다."
                        },
                    )
                }

                Step.SEX -> QuestionBody(
                    title = "성별을 알려주세요.",
                    lead = "중성화 여부에 따라 필요한 열량이 달라집니다.",
                ) {
                    Row(horizontalArrangement = Arrangement.spacedBy(T.sm)) {
                        Sex.entries.forEach { s ->
                            OptionCard(
                                title = s.label,
                                selected = dog.sex == s,
                                modifier = Modifier.weight(1f),
                                onClick = { dog = dog.copy(sex = s) },
                            )
                        }
                    }
                    Spacer(Modifier.height(T.lg))
                    Text("중성화 수술을 했나요?", style = T.captionStrong)
                    Spacer(Modifier.height(T.sm))
                    Row(horizontalArrangement = Arrangement.spacedBy(T.xs)) {
                        listOf(true to "했어요", false to "안 했어요").forEach { (v, l) ->
                            OptionChip(
                                label = l,
                                selected = dog.neutered == v,
                                onClick = {
                                    dog = dog.copy(
                                        neutered = v,
                                        surgeries = if (v) {
                                            (dog.surgeries + Surgery(SurgeryType.NEUTER, 2.0))
                                                .distinctBy { it.type }
                                        } else {
                                            dog.surgeries.filterNot {
                                                it.type == SurgeryType.NEUTER
                                            }
                                        },
                                    )
                                },
                            )
                        }
                    }
                }

                Step.WEIGHT -> QuestionBody(
                    title = "지금 몸무게가 얼마인가요?",
                    lead = "하루 급여량은 체중의 0.75제곱에 비례합니다. 가장 중요한 숫자예요.",
                ) {
                    ReadoutCard(
                        value = "${dog.weightKg} kg",
                        note = "휴식기 에너지 요구량 ${dog.rer.roundToInt()} kcal",
                    )
                    Spacer(Modifier.height(T.lg))
                    Slider(
                        value = dog.weightKg.toFloat(),
                        onValueChange = { v ->
                            val kg = (v * 10).roundToInt() / 10.0
                            dog = dog.copy(
                                weightKg = kg,
                                // 크기는 다음 문항에서 고치면 되고, 여기서 미리 맞춰둔다
                                size = when {
                                    kg < 10 -> DogSize.SMALL
                                    kg < 25 -> DogSize.MEDIUM
                                    else -> DogSize.LARGE
                                },
                            )
                        },
                        valueRange = 1f..60f,
                        colors = SliderDefaults.colors(
                            thumbColor = T.primary,
                            activeTrackColor = T.primary,
                            inactiveTrackColor = T.hairline,
                        ),
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text("1kg", style = T.finePrint)
                        Text("60kg", style = T.finePrint)
                    }
                }

                Step.SIZE -> QuestionBody(
                    title = "체구는 어느 쪽에 가깝나요?",
                    lead = "견종을 몰라도 이건 답할 수 있어요. 노령 판정 기준이 크기마다 다릅니다.",
                ) {
                    DogSize.entries.forEach { s ->
                        OptionCard(
                            title = s.label,
                            subtitle = s.detail,
                            selected = dog.size == s,
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { dog = dog.copy(size = s) },
                        )
                        Spacer(Modifier.height(T.xs))
                    }
                }

                Step.BODY -> QuestionBody(
                    title = "위에서 봤을 때\n어떤 모습에 가깝나요?",
                    lead = "허리가 잘록한 정도로 고르면 됩니다.",
                ) {
                    Row(horizontalArrangement = Arrangement.spacedBy(T.xs)) {
                        BodyCondition.entries.forEach { b ->
                            OptionCard(
                                title = b.label,
                                selected = dog.bodyCondition == b,
                                modifier = Modifier.weight(1f),
                                visual = {
                                    DogSilhouette(
                                        waist = b.waist,
                                        active = dog.bodyCondition == b,
                                    )
                                },
                                onClick = { dog = dog.copy(bodyCondition = b) },
                            )
                        }
                    }
                    Spacer(Modifier.height(T.sm))
                    Text(
                        dog.bodyCondition.detail,
                        style = T.caption.copy(color = T.inkMuted48),
                        modifier = Modifier.fillMaxWidth(),
                        textAlign = TextAlign.Center,
                    )
                }

                Step.ACTIVITY -> QuestionBody(
                    title = "평소 얼마나 움직이나요?",
                    lead = "첫 2주 동안 쓸 기준값이에요. 이후에는 목줄이 잰 실제 활동량으로 대체됩니다.",
                ) {
                    ActivityLevel.entries.forEach { a ->
                        OptionCard(
                            title = a.label,
                            subtitle = a.detail,
                            selected = dog.activityLevel == a,
                            modifier = Modifier.fillMaxWidth(),
                            onClick = { dog = dog.copy(activityLevel = a) },
                        )
                        Spacer(Modifier.height(T.xs))
                    }
                }

                Step.MEALS -> QuestionBody(
                    title = "하루에 몇 번 먹나요?",
                    lead = "영양제는 사료와 같이 나옵니다. 성분이 서로 방해하지 않도록 끼니를 나눠 배치해요.",
                ) {
                    Row(horizontalArrangement = Arrangement.spacedBy(T.xs)) {
                        listOf(1, 2, 3).forEach { n ->
                            OptionCard(
                                title = "${n}번",
                                selected = dog.mealsPerDay == n,
                                modifier = Modifier.weight(1f),
                                onClick = {
                                    dog = dog.copy(
                                        mealsPerDay = n,
                                        mealHours = defaultMealHours(n),
                                    )
                                },
                            )
                        }
                    }
                    Spacer(Modifier.height(T.lg))

                    dog.mealHours.forEachIndexed { i, hour ->
                        ChipGroup(
                            label = mealName(i, dog.mealsPerDay),
                            options = hourChoices(i, dog.mealsPerDay),
                            selected = { it == hour },
                            labelOf = { "${it}시" },
                            onSelect = { h ->
                                dog = dog.copy(
                                    mealHours = dog.mealHours.toMutableList()
                                        .also { it[i] = h },
                                )
                            },
                        )
                        Spacer(Modifier.height(T.sm))
                    }

                    if (dog.mealsPerDay == 1) {
                        Spacer(Modifier.height(T.xs))
                        NoteCard(
                            "한 끼만 먹으면 서로 방해하는 성분을 떼어놓을 수 없어요. " +
                                "흡수율이 떨어지는 만큼 용량을 보정합니다.",
                        )
                    }
                }

                Step.BREED -> QuestionBody(
                    title = "견종을 알고 있나요?",
                    lead = "몰라도 괜찮아요. 견종 없이도 모든 계산이 됩니다.",
                ) {
                    PillInput(
                        value = breedQuery,
                        onValueChange = { breedQuery = it },
                        placeholder = "견종 검색",
                    )
                    Spacer(Modifier.height(T.md))

                    ChipFlow {
                        Breeds.search(breedQuery).take(14).forEach { b ->
                            OptionChip(
                                label = b,
                                selected = b in dog.breeds,
                                onClick = {
                                    dog = dog.copy(
                                        breeds = if (b in dog.breeds) {
                                            dog.breeds - b
                                        } else {
                                            // 믹스는 두 견종까지만 받는다
                                            (dog.breeds + b).takeLast(2)
                                        },
                                    )
                                },
                            )
                        }
                    }

                    Spacer(Modifier.height(T.lg))
                    GhostPill(
                        label = "잘 모르겠어요",
                        modifier = Modifier.fillMaxWidth(),
                        onClick = { dog = dog.copy(breeds = emptyList()) },
                    )
                }

                Step.ALLERGY -> QuestionBody(
                    title = "알러지가 있나요?",
                    lead = "이 답이 처방을 직접 바꿉니다. 해당 성분은 아예 나오지 않아요.",
                ) {
                    YesNo(
                        asked = allergyAsked,
                        onYes = { allergyAsked = true },
                        onNo = {
                            allergyAsked = false
                            dog = dog.copy(allergies = emptyList())
                        },
                    )
                    if (allergyAsked == true) {
                        Spacer(Modifier.height(T.lg))
                        Text("해당하는 것을 모두 골라주세요", style = T.captionStrong)
                        Spacer(Modifier.height(T.sm))
                        ChipFlow {
                            Allergen.entries.forEach { a ->
                                OptionChip(
                                    label = a.label,
                                    selected = a in dog.allergies,
                                    onClick = {
                                        dog = dog.copy(
                                            allergies = if (a in dog.allergies) {
                                                dog.allergies - a
                                            } else {
                                                dog.allergies + a
                                            },
                                        )
                                    },
                                )
                            }
                        }
                        SafetyNotes(dog)
                    }
                }

                Step.SURGERY -> QuestionBody(
                    title = "수술을 받은 적 있나요?",
                    lead = "관절 수술을 한 아이는 원래 덜 뜁니다. 이걸 모르면 평소 모습을 " +
                        "이상 신호로 잘못 읽어요.",
                ) {
                    YesNo(
                        asked = surgeryAsked,
                        onYes = { surgeryAsked = true },
                        onNo = {
                            surgeryAsked = false
                            dog = dog.copy(
                                surgeries = dog.surgeries.filter {
                                    it.type == SurgeryType.NEUTER
                                },
                            )
                        },
                    )
                    if (surgeryAsked == true) {
                        Spacer(Modifier.height(T.lg))
                        Text("어떤 수술인가요?", style = T.captionStrong)
                        Spacer(Modifier.height(T.sm))
                        ChipFlow {
                            SurgeryType.entries.forEach { s ->
                                val on = dog.surgeries.any { it.type == s }
                                OptionChip(
                                    label = s.label,
                                    selected = on,
                                    onClick = {
                                        dog = dog.copy(
                                            surgeries = if (on) {
                                                dog.surgeries.filterNot { it.type == s }
                                            } else {
                                                dog.surgeries + Surgery(s, surgeryWhen)
                                            },
                                        )
                                    },
                                )
                            }
                        }
                        Spacer(Modifier.height(T.lg))
                        ChipGroup(
                            label = "언제쯤이었나요",
                            options = listOf(0.5, 2.0, 5.0),
                            selected = { it == surgeryWhen },
                            labelOf = {
                                when (it) {
                                    0.5 -> "6개월 이내"
                                    2.0 -> "1~3년 전"
                                    else -> "3년 이상"
                                }
                            },
                            onSelect = { y ->
                                surgeryWhen = y
                                dog = dog.copy(
                                    surgeries = dog.surgeries.map { it.copy(yearsAgo = y) },
                                )
                            },
                        )
                        SafetyNotes(dog)
                    }
                }

                Step.HEALTH -> QuestionBody(
                    title = "마지막이에요.\n건강 상태를 알려주세요.",
                    lead = "해당 없으면 그냥 넘어가도 됩니다.",
                ) {
                    Text("진단받은 질환", style = T.captionStrong)
                    Spacer(Modifier.height(T.sm))
                    ChipFlow {
                        Condition.entries.forEach { c ->
                            OptionChip(
                                label = c.label,
                                selected = c in dog.conditions,
                                onClick = {
                                    dog = dog.copy(
                                        conditions = if (c in dog.conditions) {
                                            dog.conditions - c
                                        } else {
                                            dog.conditions + c
                                        },
                                    )
                                },
                            )
                        }
                    }

                    Spacer(Modifier.height(T.lg))
                    Text("복용 중인 약", style = T.captionStrong)
                    Spacer(Modifier.height(T.sm))
                    ChipFlow {
                        Medication.entries.forEach { m ->
                            OptionChip(
                                label = m.label,
                                selected = m in dog.medications,
                                onClick = {
                                    dog = dog.copy(
                                        medications = if (m in dog.medications) {
                                            dog.medications - m
                                        } else {
                                            dog.medications + m
                                        },
                                    )
                                },
                            )
                        }
                    }
                    SafetyNotes(dog)
                }

                else -> Unit
            }
        }
    }
}

// ---------------------------------------------------------------------------
// 화면 뼈대
// ---------------------------------------------------------------------------

@Composable
private fun QuestionScaffold(
    progress: Float,
    questionNo: Int,
    canGoBack: Boolean,
    optional: Boolean,
    canAdvance: Boolean,
    hint: String?,
    onBack: () -> Unit,
    onSkip: () -> Unit,
    onNext: () -> Unit,
    content: @Composable () -> Unit,
) {
    Column(modifier = Modifier.fillMaxSize().background(T.canvas)) {
        HairlineProgress(progress)

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = T.gutter, vertical = T.sm),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            if (canGoBack) {
                TextLink(label = "뒤로", onClick = onBack)
            }
            Spacer(Modifier.weight(1f))
            Text("$questionNo / ${QUESTIONS.size}", style = T.finePrint)
            if (optional) {
                Spacer(Modifier.width(T.md))
                TextLink(label = "건너뛰기", onClick = onSkip)
            }
        }

        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = T.gutter)
                .padding(top = T.md, bottom = T.xxl),
        ) {
            content()
        }

        StickyBar {
            if (hint != null) {
                Text(
                    hint,
                    style = T.caption.copy(color = T.inkMuted48),
                    modifier = Modifier.weight(1f).padding(end = T.sm),
                )
            } else {
                Spacer(Modifier.weight(1f))
            }
            PrimaryPill(label = "다음", enabled = canAdvance, onClick = onNext)
        }
    }
}

@Composable
private fun QuestionBody(
    title: String,
    lead: String,
    content: @Composable () -> Unit,
) {
    Text(title, style = T.hero)
    Spacer(Modifier.height(T.sm))
    Text(lead, style = T.lead)
    Spacer(Modifier.height(T.xl))
    content()
}

// ---------------------------------------------------------------------------
// 조각들
// ---------------------------------------------------------------------------

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChipFlow(content: @Composable () -> Unit) {
    FlowRow(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.spacedBy(T.xs),
        verticalArrangement = Arrangement.spacedBy(T.xs),
    ) { content() }
}

@Composable
private fun <O> ChipGroup(
    label: String,
    options: List<O>,
    selected: (O) -> Boolean,
    labelOf: (O) -> String,
    onSelect: (O) -> Unit,
) {
    Column {
        Text(label, style = T.captionStrong)
        Spacer(Modifier.height(T.xs))
        ChipFlow {
            options.forEach { o ->
                OptionChip(
                    label = labelOf(o),
                    selected = selected(o),
                    onClick = { onSelect(o) },
                )
            }
        }
    }
}

@Composable
private fun YesNo(asked: Boolean?, onYes: () -> Unit, onNo: () -> Unit) {
    Row(horizontalArrangement = Arrangement.spacedBy(T.sm)) {
        OptionCard(
            title = "있어요",
            selected = asked == true,
            modifier = Modifier.weight(1f),
            onClick = onYes,
        )
        OptionCard(
            title = "없어요",
            selected = asked == false,
            modifier = Modifier.weight(1f),
            onClick = onNo,
        )
    }
}

/** 지금까지 입력한 값이 처방을 어떻게 바꾸는지 즉시 보여준다. */
@Composable
private fun SafetyNotes(dog: MyDog) {
    val rules = SafetyPreview.rules(dog)
    if (rules.isEmpty()) return

    Spacer(Modifier.height(T.lg))
    Column(modifier = Modifier.fillMaxWidth().utilityCard(fill = T.parchment)) {
        Text("입력하신 내용이 반영됩니다", style = T.captionStrong)
        Spacer(Modifier.height(T.sm))
        rules.forEachIndexed { i, r ->
            if (i > 0) {
                Spacer(Modifier.height(T.sm))
                Hairline(color = T.dividerSoft)
                Spacer(Modifier.height(T.sm))
            }
            Text(r.title, style = T.body)
            Spacer(Modifier.height(T.xxs))
            Text(r.detail, style = T.caption.copy(color = T.inkMuted48))
        }
    }
}

@Composable
private fun ReadoutCard(value: String, note: String) {
    Column(modifier = Modifier.fillMaxWidth().utilityCard(fill = T.parchment)) {
        Text(value, style = T.displayLg)
        Spacer(Modifier.height(T.xxs))
        Text(note, style = T.caption.copy(color = T.inkMuted48))
    }
}

@Composable
private fun NoteCard(text: String) {
    Box(modifier = Modifier.fillMaxWidth().utilityCard(fill = T.parchment)) {
        Text(text, style = T.caption)
    }
}

private fun mealName(index: Int, total: Int): String = when {
    total == 1 -> "급여 시각"
    total == 2 -> if (index == 0) "아침" else "저녁"
    else -> listOf("아침", "점심", "저녁")[index]
}

private fun stepHint(step: Step, dog: MyDog): String? = when (step) {
    Step.NAME -> if (dog.name.isBlank()) "이름을 입력해 주세요" else null
    Step.WEIGHT -> "${dog.weightKg}kg · ${dog.size.label}"
    Step.AGE -> dog.ageLabel
    Step.MEALS -> dog.mealHours.joinToString(" · ") { "${it}시" }
    else -> null
}
