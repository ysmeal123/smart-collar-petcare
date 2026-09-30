"""
Wellness Agent — 보호자에게 물어본다.

강아지는 "3일 전에 샴푸 바꿨어요"라고 말할 수 없고, 센서도 그걸 모른다.
**센서로 알 수 없는 맥락만** 묻는다.

    묻는다        최근 샴푸를 바꿨나요?  사료를 바꿨나요?  붉은 반점이 보이나요?
    묻지 않는다   밤에 긁었나요?  산책 얼마나 했나요?   ← 이미 안다

LLM을 쓰지 않는다. 질문 목록이 축마다 서너 개로 정해져 있고,
LLM을 끼우면 느려지고 비싸지고 같은 상황에 다른 질문이 나온다.
무엇보다 **답변이 배식 로직에 직접 닿는 경로를 만들면 안 된다.**

    Agent  →  구조화된 맥락  →  Nutrition  →  Safety  →  Simulation  →  배식
              ^^^^^^^^^^^^
              자연어는 여기서 끊긴다
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, Field

from core.models import HealthAxis
from core.twin import DogTwin


class AnswerType(str, Enum):
    YES_NO = "yes_no"
    CHOICE = "choice"
    TEXT = "text"


class Question(BaseModel):
    """
    보호자에게 보낼 질문 하나.

    key 가 곧 저장될 맥락 필드다. 자연어 답변을 그대로 들고 다니지 않고
    여기서 구조화된 값으로 바꾼다.
    """

    key: str
    axis: HealthAxis
    text: str
    type: AnswerType = AnswerType.YES_NO
    choices: list[str] = Field(default_factory=list)
    why: str = Field(description="왜 묻는지. 앱에 함께 보여준다")


class Answer(BaseModel):
    key: str
    value: str
    answered_at: date


class Context(BaseModel):
    """
    보호자가 알려준 것들. 구조화된 값만 담는다.

    Nutrition Engine 은 이 객체만 본다. 자연어는 여기까지 오지 않는다.
    """

    answers: dict[str, str] = Field(default_factory=dict)
    updated_at: date | None = None

    #: 긍정으로 받아들이는 값.
    #:
    #: 앱은 "네"를 "yes"로 정규화해서 보낸다. 그런데 정규화가 클라이언트에만
    #: 있으면, 다른 클라이언트(게이트웨이·curl·팀원 테스트)가 "예"를 보낼 때
    #: **아무 에러 없이 조용히 무시된다.** 처방이 안 바뀌는데 이유를 알 수 없다.
    #: 서버에서 한 번 더 받아준다.
    YES: ClassVar[frozenset[str]] = frozenset({"yes", "y", "true", "1", "네", "예", "있어요", "보여요"})

    def said_yes(self, key: str) -> bool:
        v = self.answers.get(key)
        return v is not None and v.strip().lower() in self.YES

    def known(self, key: str) -> bool:
        return key in self.answers


# ---------------------------------------------------------------------------
# 질문 목록
#
# 축마다 서너 개. 전부 '센서가 모르는 것'이다.
# ---------------------------------------------------------------------------

QUESTIONS: dict[HealthAxis, list[Question]] = {
    HealthAxis.SKIN: [
        Question(
            key="shampoo_changed", axis=HealthAxis.SKIN,
            text="최근 2주 안에 샴푸나 목욕 제품을 바꾸셨나요?",
            why="접촉성 피부 자극은 제품 교체 직후에 가장 흔합니다",
        ),
        Question(
            key="food_changed", axis=HealthAxis.SKIN,
            text="최근 2주 안에 사료나 간식을 바꾸셨나요?",
            why="식이 알러지는 보통 2~6주에 걸쳐 나타납니다",
        ),
        Question(
            key="env_exposure", axis=HealthAxis.SKIN,
            text="최근에 풀밭이나 흙에서 놀았나요?",
            why="야외 접촉은 접촉성 자극과 벼룩·진드기의 가장 흔한 경로입니다",
        ),
        Question(
            key="skin_visible", axis=HealthAxis.SKIN,
            text="피부가 붉거나 털이 빠진 부분이 보이나요?",
            type=AnswerType.CHOICE,
            choices=["보여요", "잘 모르겠어요", "없어요"],
            why="눈에 보이는 병변이 있으면 영양이 아니라 진료가 먼저입니다",
        ),
    ],
    HealthAxis.EAR: [
        Question(
            key="ear_smell", axis=HealthAxis.EAR,
            text="귀에서 평소와 다른 냄새가 나나요?",
            why="외이염의 가장 이른 신호입니다",
        ),
        Question(
            key="ear_discharge", axis=HealthAxis.EAR,
            text="귀 안에 갈색이나 검은 분비물이 보이나요?",
            why="분비물이 있으면 영양이 아니라 진료가 필요합니다",
        ),
        Question(
            key="recent_bath", axis=HealthAxis.EAR,
            text="최근에 목욕이나 수영을 했나요?",
            why="귀에 물이 들어가면 며칠간 흔들기가 늘 수 있습니다",
        ),
    ],
    HealthAxis.MOBILITY: [
        Question(
            key="limping", axis=HealthAxis.MOBILITY,
            text="걸을 때 다리를 저는 모습이 보이나요?",
            why="파행이 보이면 영양 지원이 아니라 진료 대상입니다",
        ),
        Question(
            key="stairs_avoid", axis=HealthAxis.MOBILITY,
            text="계단이나 소파 오르기를 피하나요?",
            why="활동량 감소가 통증 때문인지 날씨 때문인지 가릅니다",
        ),
        Question(
            key="weather_cold", axis=HealthAxis.MOBILITY,
            text="요즘 산책을 줄이셨나요? (날씨, 일정 등)",
            why="보호자 사정으로 줄어든 것을 관절 문제로 읽으면 안 됩니다",
        ),
    ],
    HealthAxis.SLEEP: [
        Question(
            key="env_changed", axis=HealthAxis.SLEEP,
            text="잠자리나 집 환경이 최근에 바뀌었나요?",
            why="이사·가구 이동·새 반려동물은 수면을 크게 흔듭니다",
        ),
        Question(
            key="noise", axis=HealthAxis.SLEEP,
            text="밤에 평소보다 시끄러운 일이 있었나요?",
            why="공사나 손님은 며칠짜리 변화라 처방 대상이 아닙니다",
        ),
    ],
    HealthAxis.APPETITE: [
        Question(
            key="treats", axis=HealthAxis.APPETITE,
            text="최근에 간식을 평소보다 많이 주셨나요?",
            why="간식으로 배가 차면 사료를 남깁니다. 식욕 저하가 아닙니다",
        ),
        Question(
            key="vomit", axis=HealthAxis.APPETITE,
            text="구토나 설사가 있었나요?",
            why="소화기 증상이 있으면 영양제를 멈추고 진료를 권합니다",
        ),
        Question(
            key="food_changed", axis=HealthAxis.APPETITE,
            text="사료를 바꾸셨나요?",
            why="새 사료에 적응하는 동안 덜 먹는 건 흔한 일입니다",
        ),
    ],
}

#: 같은 질문을 이 기간 안에 다시 묻지 않는다.
#: 매일 같은 걸 물으면 사용자가 앱을 닫는다.
ASK_COOLDOWN_DAYS = 14


def ask(twin: DogTwin, context: Context, today: date | None = None) -> list[Question]:
    """
    지금 물어볼 질문들.

    **발화한 축에 대해서만 묻는다.** 아무 일도 없는데 매일 질문을 던지면
    알림 피로가 쌓이고, 정작 중요할 때 답을 안 하게 된다.

    이미 답한 질문은 쿨다운이 지나기 전엔 다시 묻지 않는다.
    """
    today = today or twin.as_of
    stale = (
        context.updated_at is None
        or (today - context.updated_at) >= timedelta(days=ASK_COOLDOWN_DAYS)
    )

    out: list[Question] = []
    for axis_score in twin.wellness:
        if not axis_score.active:
            continue
        for q in QUESTIONS.get(axis_score.axis, []):
            if context.known(q.key) and not stale:
                continue
            out.append(q)

    return out


def brief(twin: DogTwin) -> str:
    """
    지금 상태를 한 문단으로. 질문과 함께 보여준다.

    진단 언어를 쓰지 않는다. 관찰된 사실만 서술한다.
    """
    fired = [a for a in twin.wellness if a.active]
    if not fired:
        return f"{twin.profile.name}는 모든 지표가 평소 범위입니다."

    lines = [a.message for a in fired if a.message]
    body = " ".join(lines) if lines else "평소와 다른 지표가 있습니다."
    return f"{twin.profile.name}의 최근 2주 관찰입니다. {body}"


# ---------------------------------------------------------------------------
# 답변 -> 처방 조정
# ---------------------------------------------------------------------------

class ContextEffect(BaseModel):
    """
    맥락이 처방에 미치는 영향.

    **영양제를 늘리는 방향으로는 작동하지 않는다.**
    보호자 답변으로 용량을 올리면, 답변을 유도해 매출을 늘릴 수 있는
    경로가 열린다. 맥락은 '멈추거나 미루는' 쪽으로만 쓴다.
    """

    defer_axes: list[HealthAxis] = Field(default_factory=list)
    block_all: bool = False
    vet_referral: bool = False
    notes: list[str] = Field(default_factory=list)


def interpret(context: Context) -> ContextEffect:
    """
    구조화된 답변을 처방 조정으로 바꾼다.

    자연어는 여기 오지 않는다. key/value 만 본다.
    """
    eff = ContextEffect()

    # 눈에 보이는 병변·분비물은 영양이 아니라 진료 문제다
    if context.answers.get("skin_visible") == "보여요":
        eff.defer_axes.append(HealthAxis.SKIN)
        eff.vet_referral = True
        eff.notes.append("피부 병변이 보인다고 하셨습니다 — 진료를 먼저 권합니다")

    if context.said_yes("ear_discharge"):
        eff.vet_referral = True
        eff.notes.append("귀 분비물이 있다고 하셨습니다 — 진료를 먼저 권합니다")

    if context.said_yes("limping"):
        eff.defer_axes.append(HealthAxis.MOBILITY)
        eff.vet_referral = True
        eff.notes.append("파행이 보인다고 하셨습니다 — 진료를 먼저 권합니다")

    if context.said_yes("vomit"):
        eff.block_all = True
        eff.vet_referral = True
        eff.notes.append("구토·설사가 있다고 하셨습니다 — 영양제를 멈춥니다")

    # 원인이 설명되는 변화는 처방하지 않고 지켜본다
    if context.said_yes("shampoo_changed"):
        eff.defer_axes.append(HealthAxis.SKIN)
        eff.notes.append("샴푸를 바꾸셨습니다 — 2주 더 지켜봅니다")

    if context.said_yes("env_exposure"):
        eff.defer_axes.append(HealthAxis.SKIN)
        eff.notes.append("풀밭·흙에서 놀았다고 하셨습니다 — 접촉성 자극일 수 있어 2주 지켜봅니다")

    if context.said_yes("recent_bath"):
        eff.notes.append("최근 목욕·수영이 있었습니다 — 며칠 더 지켜봅니다")

    if context.said_yes("weather_cold"):
        eff.defer_axes.append(HealthAxis.MOBILITY)
        eff.notes.append("산책을 줄이셨습니다 — 활동량 감소의 원인이 설명됩니다")

    if context.said_yes("env_changed") or context.said_yes("noise"):
        eff.defer_axes.append(HealthAxis.SLEEP)
        eff.notes.append("환경 변화가 있었습니다 — 수면 변화의 원인이 설명됩니다")

    if context.said_yes("treats"):
        eff.notes.append("간식을 늘리셨습니다 — 사료를 남기는 이유가 설명됩니다")

    eff.defer_axes = list(dict.fromkeys(eff.defer_axes))
    return eff
