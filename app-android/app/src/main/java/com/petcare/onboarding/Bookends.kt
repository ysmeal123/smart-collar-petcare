package com.petcare.onboarding

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.petcare.data.MyDog
import com.petcare.data.SafetyPreview
import com.petcare.ui.Hairline
import com.petcare.ui.PrimaryPill
import com.petcare.ui.StickyBar
import com.petcare.ui.T
import com.petcare.ui.utilityCard
import kotlin.math.roundToInt

/**
 * 온보딩의 처음과 끝.
 *
 * 문항 화면은 전부 흰 면이고, 이 두 화면만 면을 바꾼다.
 * 시작은 어두운 타일, 끝은 파치먼트 — 흐름 전체에 리듬이 생긴다.
 */

/** product-tile-dark. 시작 화면. */
@Composable
fun WelcomeTile(onStart: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(T.tile1)
            .padding(horizontal = T.gutter),
        verticalArrangement = Arrangement.Center,
    ) {
        Text("pebble", style = T.tagline.copy(color = T.primaryOnDark))
        Spacer(Modifier.height(T.md))
        Text(
            "아이를 먼저\n알아야 합니다.",
            style = T.hero.copy(color = T.onDark),
        )
        Spacer(Modifier.height(T.md))
        Text(
            "목줄이 재는 것은 움직임뿐입니다. 그 움직임이 평소인지 아닌지 " +
                "판단하려면 아이가 어떤 아이인지 알아야 해요.\n\n" +
                "12가지만 여쭤볼게요. 2분이면 됩니다.",
            style = T.body.copy(color = T.bodyMuted),
        )
        Spacer(Modifier.height(T.xxl))
        PrimaryPill(label = "시작하기", large = true, onClick = onStart)
        Spacer(Modifier.height(T.lg))
        Text(
            "1번부터 8번까지는 급여량 계산에 반드시 필요합니다. " +
                "나머지는 건너뛸 수 있어요.",
            style = T.finePrint.copy(color = T.bodyMuted),
        )
    }
}

/** 온보딩 결과 확인. 입력값이 무엇을 바꿨는지 여기서 전부 보여준다. */
@Composable
fun SummaryScreen(dog: MyDog, onStart: () -> Unit) {
    val rules = SafetyPreview.rules(dog)
    val (_, foodName) = dog.recommendedFood

    Column(modifier = Modifier.fillMaxSize().background(T.parchment)) {
        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = T.gutter)
                .padding(top = T.section, bottom = T.xxl),
        ) {
            Text("다 됐어요.", style = T.hero)
            Spacer(Modifier.height(T.sm))
            Text(
                "${dog.name}에 대해 알려주신 내용입니다.",
                style = T.lead,
            )
            Spacer(Modifier.height(T.xl))

            // --- 프로필 -----------------------------------------------------
            Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
                Text(dog.name, style = T.displayLg)
                Spacer(Modifier.height(T.xxs))
                Text(dog.subtitle, style = T.caption.copy(color = T.inkMuted48))
                Spacer(Modifier.height(T.md))
                Hairline(color = T.dividerSoft)
                Spacer(Modifier.height(T.md))

                Fact("성별", "${dog.sex.label}${if (dog.neutered) " · 중성화" else ""}")
                Fact("체구", dog.size.label)
                Fact("체형", dog.bodyCondition.label)
                Fact("활동", dog.activityLevel.label)
                Fact(
                    "급여",
                    "하루 ${dog.mealsPerDay}번 · " +
                        dog.mealHours.joinToString(", ") { "${it}시" },
                )
                if (dog.allergies.isNotEmpty()) {
                    Fact("알러지", dog.allergies.joinToString(", ") { it.label })
                }
                if (dog.surgeries.isNotEmpty()) {
                    Fact("수술", dog.surgeries.joinToString(", ") { it.type.label })
                }
                if (dog.conditions.isNotEmpty()) {
                    Fact("질환", dog.conditions.joinToString(", ") { it.label })
                }
                if (dog.medications.isNotEmpty()) {
                    Fact("복용약", dog.medications.joinToString(", ") { it.label })
                }
            }

            Spacer(Modifier.height(T.sm))

            // --- 계산 결과 ---------------------------------------------------
            Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
                Text("여기서 시작합니다", style = T.captionStrong)
                Spacer(Modifier.height(T.sm))
                Text("${dog.rer.roundToInt()} kcal", style = T.numeric)
                Spacer(Modifier.height(T.xxs))
                Text(
                    "휴식기 에너지 요구량 (RER = 70 × 체중^0.75). " +
                        "실제 급여량은 여기에 활동량을 곱해 매일 다시 계산합니다. " +
                        "어떤 경우에도 이 값 아래로는 주지 않아요.",
                    style = T.caption.copy(color = T.inkMuted48),
                )
                Spacer(Modifier.height(T.md))
                Hairline(color = T.dividerSoft)
                Spacer(Modifier.height(T.md))
                Fact("추천 사료", foodName)
            }

            Spacer(Modifier.height(T.sm))

            // --- 안전 필터 ---------------------------------------------------
            Column(modifier = Modifier.fillMaxWidth().utilityCard()) {
                Text("알려주신 내용이 처방을 바꿉니다", style = T.captionStrong)
                Spacer(Modifier.height(T.sm))

                if (rules.isEmpty()) {
                    Text(
                        "지금은 막아야 할 성분이 없습니다. " +
                            "나중에 알러지나 수술 이력이 생기면 설정에서 추가해 주세요.",
                        style = T.caption.copy(color = T.inkMuted48),
                    )
                } else {
                    rules.forEachIndexed { i, r ->
                        if (i > 0) {
                            Spacer(Modifier.height(T.sm))
                            Hairline(color = T.dividerSoft)
                            Spacer(Modifier.height(T.sm))
                        }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(r.layer, style = T.finePrint)
                            Spacer(Modifier.width(T.xs))
                            Box(
                                Modifier
                                    .height(3.dp)
                                    .width(3.dp)
                                    .background(
                                        if (r.blocking) T.attention else T.hairline,
                                        T.pill,
                                    ),
                            )
                        }
                        Spacer(Modifier.height(T.xxs))
                        Text(r.title, style = T.body)
                        Spacer(Modifier.height(T.xxs))
                        Text(r.detail, style = T.caption.copy(color = T.inkMuted48))
                    }
                }
            }

            Spacer(Modifier.height(T.sm))

            // --- 앞으로 2주 ---------------------------------------------------
            //
            // 이 화면이 없으면 가입 첫날 사용자는 텅 빈 대시보드를 본다.
            // 기준선을 쌓는 중이라는 사실을 먼저 알려야 한다.
            Column(modifier = Modifier.fillMaxWidth().utilityCard(fill = T.tile1)) {
                Text(
                    "앞으로 2주",
                    style = T.captionStrong.copy(color = T.primaryOnDark),
                )
                Spacer(Modifier.height(T.sm))
                Text(
                    "${dog.name}의 평소를 배웁니다.",
                    style = T.displayMd.copy(color = T.onDark),
                )
                Spacer(Modifier.height(T.sm))
                Text(
                    "이상 신호는 남과 비교해서 찾는 게 아니라 " +
                        "${dog.name}의 평소와 비교해서 찾습니다. " +
                        "그 평소를 만드는 데 14일이 걸려요.\n\n" +
                        "그동안에도 사료는 정상적으로 나갑니다. " +
                        "영양제 처방만 기준선이 잡힌 뒤에 시작합니다.",
                    style = T.body.copy(color = T.bodyMuted),
                )
            }

            Spacer(Modifier.height(T.lg))
            Text(
                "이 앱은 목줄이 관찰한 행동 변화를 알려드립니다. " +
                    "질병을 진단하거나 치료하지 않습니다. " +
                    "아이에게 이상이 느껴지면 수의사에게 보여주세요.",
                style = T.microLegal,
            )
        }

        StickyBar {
            Text(
                "${dog.name} · ${dog.rer.roundToInt()} kcal",
                style = T.caption.copy(color = T.inkMuted48),
                modifier = Modifier.weight(1f),
            )
            PrimaryPill(label = "시작하기", onClick = onStart)
        }
    }
}

@Composable
private fun Fact(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 5.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Text(
            label,
            style = T.caption.copy(color = T.inkMuted48),
            modifier = Modifier.width(72.dp),
        )
        Text(value, style = T.caption.copy(color = T.ink))
    }
}
