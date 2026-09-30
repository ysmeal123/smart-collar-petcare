"""
대화 화면을 만든다.

같은 문진을 **대화로** 보여준다. 데이터는 그대로다 — `QUESTIONS`, `ask()`,
`interpret()` 가 하던 일을 말풍선으로 렌더링할 형태로 바꿔 놓는 것이 전부다.

**대화가 판단을 하지 않는다.** 여기서 만드는 것은 표시용 대화록이고,
처방을 바꾸는 것은 여전히 `interpret()` 뿐이다. 말풍선이 로직을 갖게 되면
같은 판단이 두 군데 생기고 반드시 갈라진다.

    센서 → 트윈 → [여기: 무엇을 물을지 정하고 말로 바꾼다] → 보호자
    보호자 → extract() → interpret() → 처방 조정

대화록은 저장된 답변과 메모로부터 **매번 다시 만든다.** 대화 상태를 따로
들고 있지 않다. 그래야 서버가 재시작해도, 앱을 새로 깔아도 같은 화면이 나온다.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from agent.extract import TOPICS
from agent.wellness_agent import QUESTIONS, Context, ask, brief
from core.models import HealthAxis
from core.twin import DogTwin

#: 말풍선 종류. 앱이 이걸 보고 모양을 정한다.
#:
#:   text        평범한 말풍선
#:   question    선택지가 붙은 질문
#:   understood  "이렇게 알아들었습니다" — 보호자 메모에 대한 확인
#:   plan        계산 결과 요약
KINDS = ("text", "question", "understood", "plan")


class ChatTurn(BaseModel):
    role: str = Field(description="agent | guardian")
    text: str
    kind: str = "text"
    key: str = ""
    choices: list[str] = Field(default_factory=list)
    why: str = ""
    at: datetime | None = None


class Note(BaseModel):
    """보호자가 자유롭게 적은 특이사항 하나."""

    text: str
    at: datetime
    understood: dict[str, str] = Field(default_factory=dict)
    #: 무엇이 해석했는가. rules | llm
    engine: str = "rules"


#: 축 이름을 사람 말로.
AXIS_WORD = {
    HealthAxis.SKIN: "피부",
    HealthAxis.MOBILITY: "관절·활동",
    HealthAxis.EAR: "귀",
    HealthAxis.SLEEP: "수면",
    HealthAxis.APPETITE: "식욕",
}

#: 질문 key 를 사람 말로. "이렇게 알아들었습니다" 에 쓴다.
UNDERSTOOD_WORD = {
    "shampoo_changed": "샴푸를 바꾸셨다",
    "food_changed": "사료나 간식을 바꾸셨다",
    "env_exposure": "풀밭이나 흙에서 놀았다",
    "skin_visible": "피부에 보이는 변화가 있다",
    "ear_smell": "귀에서 냄새가 난다",
    "ear_discharge": "귀에 분비물이 있다",
    "recent_bath": "최근 목욕이나 수영을 했다",
    "limping": "다리를 전다",
    "stairs_avoid": "계단이나 소파를 피한다",
    "weather_cold": "산책을 줄이셨다",
    "env_changed": "집 환경이 바뀌었다",
    "noise": "밤에 시끄러운 일이 있었다",
    "treats": "간식을 늘리셨다",
    "eating_less": "밥을 잘 안 먹는다",
    "vomit": "구토나 설사가 있었다",
}


def greeting(twin: DogTwin) -> list[ChatTurn]:
    """
    첫 인사.

    기준선이 안 여물었으면 그걸 먼저 말한다. 아직 평소를 모르는 상태에서
    "이상이 없습니다"라고 하면 거짓말이다 — 비교할 대상이 없을 뿐이다.
    """
    name = twin.profile.name
    turns = [ChatTurn(
        role="agent",
        text=f"안녕하세요. {name}의 지난 2주를 보고 있었어요.",
    )]

    if not twin.baseline.mature:
        turns.append(ChatTurn(
            role="agent",
            text=f"{twin.baseline.stage_note} "
                 f"({twin.baseline.days_observed}일 관찰). "
                 f"평소를 알기 전까지는 사료만 정상 급여합니다.",
        ))
        return turns

    turns.append(ChatTurn(role="agent", text=brief(twin)))
    return turns


def pending(twin: DogTwin, context: Context, today: date | None = None) -> list[ChatTurn]:
    """
    지금 물어볼 것.

    `ask()` 가 정한다. 여기서 다시 고르지 않는다 —
    질문을 고르는 규칙이 두 군데 있으면 갈라진다.
    """
    out = []
    for q in ask(twin, context, today):
        out.append(ChatTurn(
            role="agent",
            text=q.text,
            kind="question",
            key=q.key,
            choices=q.choices or ["네", "아니요"],
            why=q.why,
        ))
    return out


def answered(context: Context, skip: set[str] | None = None) -> list[ChatTurn]:
    """
    이미 답한 것들을 대화록에 되살린다.

    `skip` 은 **메모에서 추출된 key** 다. 그걸 문답으로 또 보여주면,
    보호자가 쓴 문장보다 위에 "풀밭에서 놀았나요? → 네" 가 먼저 나온다.
    묻지도 않은 질문에 답한 것처럼 보이고 순서가 거꾸로 읽힌다.
    메모 쪽에서 이미 "이렇게 이해했어요" 로 보여주니 여기서는 뺀다.
    """
    skip = skip or set()
    out = []
    by_key = {q.key: q for qs in QUESTIONS.values() for q in qs}
    for key, value in context.answers.items():
        if key in skip:
            continue
        q = by_key.get(key)
        if q is None:
            continue
        out.append(ChatTurn(role="agent", text=q.text, kind="text"))
        out.append(ChatTurn(role="guardian", text=_pretty(value)))
    return out


def _pretty(value: str) -> str:
    """저장된 값을 화면에 쓸 말로. 'yes' 를 그대로 보여주면 안 된다."""
    return {"yes": "네", "no": "아니요"}.get(value, value)


def understood(note: Note, has_pending: bool = True) -> ChatTurn:
    """
    "이렇게 알아들었습니다."

    **못 알아들은 것도 말한다.** 조용히 넘기면 보호자는 전달했다고 믿는데
    아무 일도 일어나지 않는다. 침묵보다 나쁘다.

    못 알아들었는데 이어서 물을 질문도 없으면 막다른 길이 된다.
    그때는 **무엇을 말할 수 있는지** 알려준다. 사전에 없는 말을 못 알아듣는
    것은 규칙 기반의 본질이라, 범위를 드러내는 편이 정직하고 쓸모 있다.
    """
    if not note.understood:
        if has_pending:
            text = ("말씀은 기록했지만 제가 이해하지 못했어요. "
                    "아래 질문으로 다시 여쭤볼게요.")
        else:
            topics = " · ".join(TOPICS)
            text = ("말씀은 기록했어요. 다만 제가 영양 계획에 반영할 수 있는 "
                    f"내용은 아직 이런 것들입니다.\n\n{topics}\n\n"
                    "그 밖의 변화는 수의사 선생님과 상의해 주세요.")
        return ChatTurn(role="agent", kind="understood", text=text)

    words = [UNDERSTOOD_WORD.get(k, k) for k in note.understood]
    body = ", ".join(words)
    return ChatTurn(
        role="agent",
        kind="understood",
        text=f"{body} — 이렇게 이해했어요. 영양 계획에 반영합니다.",
    )


def plan_summary(plan) -> ChatTurn:
    """계산 결과를 한 줄로. 숫자는 계획 카드가 보여주니 여기선 방향만."""
    pellets = sum(p.count for m in plan.meals for p in m.pellets)
    if plan.escalated:
        text = ("급성 변화가 보여서 영양제를 멈췄어요. "
                "병원 진료를 먼저 권합니다.")
    elif pellets == 0:
        text = f"오늘은 사료 {plan.total_food_g}g만 드립니다. 영양제는 없어요."
    else:
        text = (f"오늘은 사료 {plan.total_food_g}g과 영양제 {pellets}알을 "
                f"나눠 드립니다.")
    return ChatTurn(role="agent", kind="plan", text=text)


def build(
    twin: DogTwin,
    context: Context,
    notes: list[Note],
    plan=None,
    today: date | None = None,
) -> list[ChatTurn]:
    """
    대화록 전체.

    순서가 곧 이야기다.

        인사 · 관찰  →  지난 문답  →  보호자 메모와 이해  →  지금 물을 것  →  결과
    """
    from_notes = {k for n in notes for k in n.understood}

    ask_turns = pending(twin, context, today)

    turns = greeting(twin)
    turns += answered(context, skip=from_notes)

    for n in sorted(notes, key=lambda x: x.at):
        turns.append(ChatTurn(role="guardian", text=n.text, at=n.at))
        # 뒤에 물을 질문이 없으면 "못 알아들었다"로 끝나 막다른 길이 된다.
        turns.append(understood(n, has_pending=bool(ask_turns)))

    turns += ask_turns

    if plan is not None:
        turns.append(plan_summary(plan))

    return turns
