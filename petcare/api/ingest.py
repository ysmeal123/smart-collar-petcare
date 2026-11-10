"""
수집 · 사출 명령 엔드포인트.

인증 주체가 사용자가 아니라 **기기**다.
밥통이 사용자 토큰을 들고 있으면 안 되므로 기기별 자격증명을 따로 쓴다.
(지금은 헤더에 시리얼만 넣는다. 실제로는 기기별 발급 토큰이 필요하다.)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from api.auth import require_app, require_device
from core.intake import MealReading, evaluate
from core.weight import ScaleSession
from core.telemetry import (
    IngestBatch,
    resolved_status,
    to_behavior_events,
)
from jobs import pipeline
from store import db

router = APIRouter(prefix="/v1", tags=["device"])


@router.post("/ingest", dependencies=[Depends(require_device)])
def ingest(batch: IngestBatch) -> dict:
    """
    게이트웨이(밥통)가 목줄 데이터를 올린다.

    BLE가 끊긴 동안 쌓인 게 몰려 오거나, 재시도로 같은 묶음이 두 번 오는 게
    정상 동작이다. 중복은 (목줄, 부팅ID, 일련번호) 기본키로 DB가 막는다.
    """
    with db.connect() as c:
        dog_id = db.dog_of_collar(c, batch.collar_serial)
        if dog_id is None:
            raise HTTPException(404, f"등록되지 않은 목줄입니다: {batch.collar_serial}")

        # 이벤트 — 부팅 후 경과 ms를 실시각으로 환산해서 넣는다
        resolved = to_behavior_events(batch)
        pairs = list(zip(batch.events, [e.ts for e in resolved]))
        inserted = db.insert_events(c, batch.collar_serial, batch.boot_id, pairs)

        # 상태 표본 — 착용·걸음·활동. 이벤트로는 역산이 안 되는 값들이다.
        # 접지 않고 표본 그대로 넣는다. 접으면 재전송 시 두 배가 된다.
        samples = db.insert_status(
            c, batch.collar_serial, batch.boot_id, resolved_status(batch)
        )

        battery = batch.status[-1].battery if batch.status else None
        db.touch_collar(c, batch.collar_serial, batch.fw_version, battery)

        return {
            "accepted": inserted,
            "duplicates": len(batch.events) - inserted,
            "status_samples": samples,
        }


@router.get("/feeder/{dog_id}/commands", dependencies=[Depends(require_device)])
def commands(dog_id: str) -> dict:
    """
    밥통이 폴링한다.

    만료된 명령은 주지 않는다 - 아침 급여가 저녁에 실행되면 안 된다.
    밥통은 이 응답을 로컬에 캐시해 두고, 서버가 죽어도 캐시로 급여를 계속한다.
    """
    with db.connect() as c:
        return {"commands": db.pending_commands(c, dog_id, datetime.now())}


@router.post("/feeder/commands/{cmd_id}/ack", dependencies=[Depends(require_device)])
def ack(cmd_id: str, state: str = "done", result: str = "") -> dict:
    """
    실행 결과 보고.

    명령과 실행 결과를 대조하지 않으면 '준 것'과 '먹은 것'의 비교가 틀어져
    intake_ratio 가 오염되고 긴급 정지 규칙이 오작동한다.
    """
    if state not in {"done", "failed", "skipped"}:
        raise HTTPException(400, "state는 done/failed/skipped 중 하나여야 합니다")
    with db.connect() as c:
        db.ack_command(c, cmd_id, state, result)
    return {"ok": True}


@router.post("/jobs/daily", dependencies=[Depends(require_device)])
def run_daily_job(
    collar: str, day: str | None = None, meals: str = "8,19"
) -> dict:
    """
    일일 배치를 수동으로 돌린다.

    상용에서는 스케줄러가 새벽에 부른다. 지금은 시연·디버깅용으로 열어둔다.
    몇 번을 다시 돌려도 같은 결과가 나온다.
    """
    target = date.fromisoformat(day) if day else date.today()
    hours = [int(h) for h in meals.split(",") if h.strip()]
    with db.connect() as c:
        return pipeline.run_daily(c, collar, target, hours)


# ---------------------------------------------------------------------------
# 섭취량 — 밥그릇 로드셀
# ---------------------------------------------------------------------------

class MealIn(BaseModel):
    """
    식사 한 번. 밥통이 배식 직후와 식사 종료 후를 재서 올린다.

    식사가 끝나기 전에 먼저 올려도 된다(ended_at/leftover_g 없이).
    끝난 뒤 같은 started_at 으로 다시 올리면 덮어쓴다.
    """

    started_at: datetime
    ended_at: datetime | None = None
    dispensed_g: float = Field(gt=0)
    leftover_g: float | None = Field(default=None, ge=0)


@router.post("/feeder/{dog_id}/intake", dependencies=[Depends(require_device)])
def feeder_intake(dog_id: str, meal: MealIn) -> dict:
    """
    처방한 양과 실제 먹은 양은 다르다.

    이 차이를 모르면 밥을 안 먹는 개에게 영양제를 늘리게 되고,
    긴급 정지 규칙(3일 연속 섭취 70% 미만)이 영영 발동하지 않는다.
    """
    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        reading = MealReading(
            started_at=meal.started_at, ended_at=meal.ended_at,
            dispensed_g=meal.dispensed_g, leftover_g=meal.leftover_g,
        )
        r = evaluate(reading)
        db.save_meal(c, dog_id, meal.started_at, meal.ended_at,
                     meal.dispensed_g, meal.leftover_g, r)

        return {
            "offered_g": r.offered_g, "eaten_g": r.eaten_g,
            "ratio": r.ratio, "skipped": r.skipped, "note": r.note,
        }


@router.get("/dogs/{dog_id}/intake", dependencies=[Depends(require_app)])
def intake_history(dog_id: str, days: int = 14) -> dict:
    """식사 이력. 앱의 식욕 화면과 긴급정지 근거 표시에 쓴다."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT started_at, dispensed_g, eaten_g, ratio, duration_min, note "
            "FROM meal_intake WHERE dog_id=? ORDER BY started_at DESC LIMIT ?",
            (dog_id, days * 3),
        ).fetchall()
        return {"meals": [dict(r) for r in reversed(rows)]}


# ---------------------------------------------------------------------------
# 체중 — 캐스케이드 외부 루프의 입력
# ---------------------------------------------------------------------------

class ScaleSessionIn(BaseModel):
    """개가 급식판 체중계에 한 번 올라간 동안의 표본들."""

    started_at: datetime
    samples_kg: list[float] = Field(min_length=1)


class ScaleBatch(BaseModel):
    sessions: list[ScaleSessionIn] = Field(default_factory=list)


class ManualWeight(BaseModel):
    kg: float = Field(gt=0, le=120)
    measured_at: datetime | None = None


@router.post("/feeder/{dog_id}/weight", dependencies=[Depends(require_device)])
def feeder_weight(dog_id: str, batch: ScaleBatch) -> dict:
    """
    급식판 체중계가 올린다.

    잡음이 많은 소스다. 개가 부분적으로 올라가거나, 계속 움직이거나,
    다른 개가 올라갈 수 있다. 서버가 세션 단위로 정제하고
    의심스러우면 채택하지 않는다 - 추측해서 채우면 급여량이 틀어진다.
    """
    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        sessions = [
            ScaleSession(started_at=s.started_at, samples_kg=s.samples_kg)
            for s in batch.sessions
        ]
        day = sessions[0].started_at.date() if sessions else date.today()
        return pipeline.record_scale_sessions(c, dog_id, day, sessions)


@router.post("/dogs/{dog_id}/weight", dependencies=[Depends(require_app)])
def manual_weight(dog_id: str, body: ManualWeight) -> dict:
    """
    보호자가 앱에서 직접 입력한다.

    체중계 추정보다 정확하므로 같은 날이면 이쪽을 채택한다.
    다만 오타(5.8 -> 58)는 걸러야 하므로 직전 값 대비 타당성은 본다.
    """
    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")
        at = body.measured_at or datetime.now()
        return pipeline.record_manual_weight(c, dog_id, at, body.kg)


@router.get("/dogs/{dog_id}/weights", dependencies=[Depends(require_app)])
def weight_history(dog_id: str, days: int = 60) -> dict:
    """체중 이력. 버린 측정도 이유와 함께 돌려준다."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT measured_at, kg, source, accepted, reason FROM weights "
            "WHERE dog_id=? ORDER BY measured_at DESC LIMIT ?",
            (dog_id, days),
        ).fetchall()
        return {"weights": [dict(r) for r in reversed(rows)]}


# ---------------------------------------------------------------------------
# 최종 출력 — 급여 계획
# ---------------------------------------------------------------------------

@router.get("/dogs/{dog_id}/plan", dependencies=[Depends(require_app)])
def feeding_plan(dog_id: str) -> dict:
    """
    **이 시스템의 최종 출력.**

    오늘 이 개에게 언제 무엇을 얼마나 줄 것인가.
    하드웨어 앞에서 멈춘다 - 모터를 돌리는 건 밥통 담당이다.

    안에 들어 있는 것:
        끼니별 사료 그램 · 슬롯별 알 수 · 시각
        왜 그렇게 나왔는지 (trace)
        14일 시뮬레이션
        보호자에게 물을 것
    """
    with db.connect() as c:
        plan = db.latest_plan(c, dog_id)
        if plan is None:
            raise HTTPException(409, "아직 계획이 계산되지 않았습니다")
        return plan


@router.get("/dogs/{dog_id}/plan/text", response_class=PlainTextResponse, dependencies=[Depends(require_app)])
def feeding_plan_text(dog_id: str) -> str:
    """같은 계획을 사람이 읽는 형태로. 시연·디버깅용."""
    from core.plan import FeedingPlan

    with db.connect() as c:
        raw = db.latest_plan(c, dog_id)
        if raw is None:
            raise HTTPException(409, "아직 계획이 계산되지 않았습니다")
        return FeedingPlan.model_validate(raw).render()


@router.get("/dogs/{dog_id}/twin", dependencies=[Depends(require_app)])
def dog_twin(dog_id: str) -> dict:
    """개체의 현재 상태 전부. 앱의 웰니스 화면이 읽는다."""
    from core.inference import evaluate_axes
    from core.models import DailySummary, DogProfile
    from core.twin import build as build_twin

    with db.connect() as c:
        raw = db.get_dog(c, dog_id)
        if raw is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        profile = DogProfile.model_validate(raw)
        days = [DailySummary.model_validate(d) for d in db.load_daily(c, dog_id)]
        if not days:
            raise HTTPException(409, "아직 수집된 데이터가 없습니다")

        axes = evaluate_axes(profile, days)
        return json.loads(build_twin(profile, days, axes).model_dump_json())


# ---------------------------------------------------------------------------
# 보호자 맥락
# ---------------------------------------------------------------------------

class AnswersIn(BaseModel):
    answers: dict[str, str]


@router.post("/dogs/{dog_id}/context", dependencies=[Depends(require_app)])
def save_context(dog_id: str, body: AnswersIn) -> dict:
    """
    보호자 답변을 저장한다.

    **자연어는 여기서 끊긴다.** key/value 로 구조화된 값만 받고,
    이후 Nutrition Engine 은 이 값만 본다.
    """
    from agent.wellness_agent import Context, interpret

    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")
        db.save_answers(c, dog_id, body.answers)
        c.commit()

        # 저장만 하고 끝내면 안 된다.
        #
        # 대시보드는 저장된 계획을 읽는다. 여기서 다시 계산하지 않으면
        # 답을 했는데 화면의 계획은 그대로다. 답변이 저장되는데 아무 일도
        # 일어나지 않는, 에러 없는 실패가 된다.
        pipeline.recompute(c, dog_id)
        c.commit()

        eff = interpret(Context(answers=body.answers))
        return {
            "saved": len(body.answers),
            "defer_axes": [a.value for a in eff.defer_axes],
            "block_all": eff.block_all,
            "vet_referral": eff.vet_referral,
            "notes": eff.notes,
            "chat": _chat_payload(c, dog_id),
        }


class ProfileIn(BaseModel):
    """
    앱 온보딩에서 보호자가 고른 값. **보낸 것만 바꾼다.**

    행동 기록(목줄)은 그대로 두고 개의 정보만 바꾼다. 시연에서는 기록이
    가상 44일치 한 벌이고, 이름·견종·체중은 보는 사람이 앱에서 고른다.
    """

    name: str | None = Field(default=None, max_length=40)
    age_months: int | None = None
    sex: str | None = None
    neutered: bool | None = None
    weight_kg: float | None = None
    body_condition: str | None = None
    size: str | None = None
    meals_per_day: int | None = None
    breeds: list[str] | None = None
    allergies: list[str] | None = None
    surgeries: list[dict] | None = None
    conditions: list[str] | None = None
    medications: list[str] | None = None


@router.post("/dogs/{dog_id}/profile", dependencies=[Depends(require_app)])
def save_profile(dog_id: str, body: ProfileIn) -> dict:
    """
    개 정보를 바꾸고 급여 계획을 다시 만든다.

    **바뀐 게 없으면 다시 계산하지 않는다.** 앱은 화면을 열 때마다 보낸다.
    같은 날 계획을 여러 번 새로 쌓으면 '전날 대비 ±10%' 제한이 계산마다
    한 번씩 걸려, 아무것도 안 바꿨는데 숫자가 조금씩 움직인다.
    """
    from pydantic import ValidationError

    from core.models import DogProfile

    with db.connect() as c:
        raw = db.get_dog(c, dog_id)
        if raw is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        before = DogProfile.model_validate(raw)
        merged = {**raw, **body.model_dump(exclude_none=True)}
        merged["dog_id"] = dog_id
        try:
            after = DogProfile.model_validate(merged)
        except ValidationError as e:
            raise HTTPException(422, f"입력값을 확인하세요: {e.errors()[0]['msg']}")

        if after == before:
            return {"changed": False}

        db.upsert_dog(c, dog_id, after.model_dump_json())
        plan = pipeline.recompute(c, dog_id, reset_food=True)
        c.commit()
        return {
            "changed": True,
            "name": after.name,
            "total_food_g": plan.get("total_food_g") if plan else None,
        }


class NoteIn(BaseModel):
    text: str


def _chat_payload(c, dog_id: str) -> dict:
    """
    대화록을 만든다.

    **저장된 것으로부터 매번 다시 만든다.** 대화 상태를 따로 들고 있지 않다.
    그래야 서버가 재시작해도, 앱을 새로 깔아도 같은 화면이 나온다.
    """
    from agent import chat as chat_mod
    from agent.wellness_agent import Context
    from core.inference import evaluate_axes
    from core.models import DailySummary, DogProfile
    from core.plan import FeedingPlan
    from core.twin import build as build_twin

    raw = db.get_dog(c, dog_id)
    if raw is None:
        raise HTTPException(404, "등록되지 않은 개체입니다")

    profile = DogProfile.model_validate(raw)
    days = [DailySummary.model_validate(d) for d in db.load_daily(c, dog_id)]
    if not days:
        raise HTTPException(409, "아직 수집된 데이터가 없습니다")

    answers, answered_at = db.load_context(c, dog_id)
    context = Context(
        answers=answers,
        updated_at=date.fromisoformat(answered_at[:10]) if answered_at else None,
    )

    axes = evaluate_axes(profile, days)
    twin = build_twin(profile, days, axes)

    notes = [chat_mod.Note.model_validate(n) for n in db.load_notes(c, dog_id)]

    saved = db.latest_plan(c, dog_id)
    plan = FeedingPlan.model_validate(saved) if saved else None

    turns = chat_mod.build(twin, context, notes, plan)
    return {
        "dog_name": profile.name,
        "turns": [json.loads(t.model_dump_json()) for t in turns],
    }


@router.get("/dogs/{dog_id}/chat", dependencies=[Depends(require_app)])
def get_chat(dog_id: str) -> dict:
    """대화 화면. 문진을 말풍선으로 보여주기 위한 형태."""
    with db.connect() as c:
        return _chat_payload(c, dog_id)


@router.post("/dogs/{dog_id}/note", dependencies=[Depends(require_app)])
def post_note(dog_id: str, body: NoteIn) -> dict:
    """
    보호자가 자유롭게 적은 특이사항.

    자연어는 **여기서 끊긴다.** 원문은 기록만 하고, 아래로 내려가는 것은
    extract() 가 뽑은 구조화된 key/value 뿐이다.

    못 알아들어도 원문은 저장한다. 조용히 버리면 보호자는 전달했다고
    믿는데 아무 일도 일어나지 않는다.
    """
    from agent.extract import extract

    text = body.text.strip()
    if not text:
        raise HTTPException(422, "내용이 비어 있습니다")

    ex = extract(text)

    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        db.save_note(c, dog_id, text, ex.answers, ex.engine)
        if ex.answers:
            db.save_answers(c, dog_id, ex.answers)
        c.commit()

        # 추출된 것이 있으면 계획을 다시 만든다. 없으면 대화록만 갱신한다.
        if ex.answers:
            pipeline.recompute(c, dog_id)
            c.commit()

        return {
            "understood": ex.answers,
            "matched": ex.matched,
            "unmatched": ex.unmatched,
            # 규칙 사전이 해석했는지 LLM 이 해석했는지. 키를 넣기 전/후를
            # 같은 문장으로 비교할 수 있어야 한다.
            "engine": ex.engine,
            "chat": _chat_payload(c, dog_id),
        }


@router.get("/dogs/{dog_id}/questions", dependencies=[Depends(require_app)])
def questions(dog_id: str) -> dict:
    """지금 보호자에게 물어볼 것. 계획에 이미 들어 있지만 따로도 뺀다."""
    with db.connect() as c:
        plan = db.latest_plan(c, dog_id)
        if plan is None:
            return {"questions": []}
        return {"questions": plan.get("questions", [])}


@router.get("/dogs/{dog_id}/dashboard", dependencies=[Depends(require_app)])
def dashboard(dog_id: str, days: int = 7) -> dict:
    """
    앱이 읽는 대시보드.

    스키마는 asset의 demo_*.json 과 같다.
    앱의 Repository.load() 를 이 주소로 바꾸기만 하면 된다.
    """
    with db.connect() as c:
        profile = db.get_dog(c, dog_id)
        if profile is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        recent = db.load_daily(c, dog_id)
        rx = db.latest_prescription(c, dog_id)
        if rx is None:
            raise HTTPException(409, "아직 급여 계획이 계산되지 않았습니다")

        # 앱은 한 번만 부른다. 차트·계획·상태·질문을 한 응답에 담는다.
        # 왕복이 늘수록 화면이 조각조각 뜨고 오프라인 처리가 복잡해진다.
        plan = db.latest_plan(c, dog_id)

        return {
            "scenario": "live",
            "profile": profile,
            "recent_days": recent[-days:],
            "prescription": rx,
            "trend": _trend(recent),
            "plan": plan,
            "twin": _twin_payload(c, dog_id),
        }


def _twin_payload(c, dog_id: str) -> dict | None:
    """대시보드에 같이 실어 보낼 개체 상태."""
    from core.inference import evaluate_axes
    from core.models import DailySummary, DogProfile
    from core.twin import build as build_twin

    raw = db.get_dog(c, dog_id)
    rows = db.load_daily(c, dog_id)
    if raw is None or not rows:
        return None

    profile = DogProfile.model_validate(raw)
    days = [DailySummary.model_validate(d) for d in rows]
    axes = evaluate_axes(profile, days)
    return json.loads(build_twin(profile, days, axes).model_dump_json())


def _trend(recent: list[dict], window: int = 7) -> dict:
    """차트의 '평소 수준' 점선에 쓸 값."""
    if not recent:
        return {}

    def series(pick) -> list[float]:
        return [float(pick(d)) for d in recent]

    out = {}
    for key, pick in (
        ("scratch_night", lambda d: d["summary"]["scratch_night"]),
        ("activity_sec", lambda d: d["summary"]["walk_sec"] + d["summary"]["run_sec"]),
    ):
        vals = series(pick)
        # baseline 은 전체 기간의 중앙값. 최근 창은 여기서 제외한다.
        base = sorted(vals[:-window]) if len(vals) > window else sorted(vals)
        mid = base[len(base) // 2] if base else 0.0
        out[key] = {"values": vals[-window:], "baseline": mid}
    return out


@router.get("/status", dependencies=[Depends(require_app)])
def device_status() -> dict:
    """등록된 목줄들의 마지막 통신 시각. 앱 기기 화면용."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT serial, dog_id, fw_version, battery, last_seen FROM collars"
        ).fetchall()
        return {"collars": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# 밥통 — 앱이 서버를 거쳐 조작한다
#
# 앱은 밥통에 직접 붙지 않는다. 둘 다 서버하고만 말한다.
#
#     앱  --POST /dogs/{id}/dispense-->  서버  <--GET /feeder/{id}/commands--  밥통
#     앱  <--GET  /dogs/{id}/feeder---   서버  <--POST /feeder/{id}/state----  밥통
#
# 직접 붙이면 같은 Wi-Fi 에 있을 때만 되고, 집 밖에서는 못 쓴다.
# 서버를 거치면 밥통이 잠깬 뒤에 받아가므로 **앱이 켜져 있지 않아도 된다.**
# ---------------------------------------------------------------------------

#: 수동 급여 명령의 수명. 예약 급여(2시간)보다 짧다.
#:
#: 밥통이 꺼져 있는 동안 누른 버튼이 **세 시간 뒤에 실행되면 안 된다.**
#: 그때 보호자는 이미 손으로 줬다. 지나간 요청은 조용히 사라지는 게 맞다.
MANUAL_TTL = timedelta(minutes=10)

#: 밥통이 이 시간 안에 보고했으면 '연결됨' 으로 본다.
#: 폴링 주기(60초)의 세 배. 한 번 놓쳐도 끊겼다고 하지 않는다.
FEEDER_ONLINE = timedelta(minutes=3)

#: 남은 사료 알림. 그릇에 LEFTOVER_G 이상이 LEFTOVER_MIN 분 넘게 남아 있으면
#: 앱이 보호자에게 알린다. 안 먹는 것 자체가 건강 신호다 (식욕 저하).
#:
#: 분은 환경변수로 바꿀 수 있다. 시연에서는 1~2분으로 줄여 보여준다.
LEFTOVER_G = float(os.environ.get("PEBBLE_LEFTOVER_G", "10"))
LEFTOVER_MIN = float(os.environ.get("PEBBLE_LEFTOVER_MIN", "30"))


def _leftover(state: dict, now: datetime) -> dict:
    """그릇에 남은 양과 그 상태로 지난 분. 저울이 없으면 grams 가 None."""
    w = state.get("weight_g", -1.0)
    if w is None or w <= -1.0:
        return {"grams": None, "minutes": 0, "alert": False,
                "threshold_g": LEFTOVER_G, "after_min": LEFTOVER_MIN}
    since = state.get("leftover_since")
    minutes = 0.0
    if since:
        minutes = (now - datetime.fromisoformat(since)).total_seconds() / 60
    return {
        "grams": round(w, 1),
        "minutes": round(minutes, 1),
        "alert": bool(since) and minutes >= LEFTOVER_MIN,
        "threshold_g": LEFTOVER_G,
        "after_min": LEFTOVER_MIN,
    }


class ManualDispense(BaseModel):
    """
    앱에서 누른 수동 급여.

    **처방을 바꾸지 않는다.** 이건 "지금 한 번 주기" 이고, 계획·용량은
    그대로다. 수동으로 더 준 것이 다음 처방을 늘리는 경로가 되면
    '영양제를 많이 팔수록 매출이 는다' 는 이해상충이 그대로 들어온다.
    """

    #: 슬롯 -> 알 수. 장착되지 않은 슬롯은 거부한다.
    pellets: dict[int, int] = Field(default_factory=dict)
    #: 사료 그램. 0 이면 사료는 안 나간다.
    food_g: float = Field(default=0.0, ge=0.0, le=500.0)
    #: 앱이 버튼 한 번에 하나씩 만드는 멱등키.
    #:
    #: 응답을 못 받으면 앱은 재시도한다. 그때 **같은 키를 다시 보내야**
    #: 두 번 나가지 않는다. 비워 보내면 서버가 매번 새 키를 만들고,
    #: 재시도 보호는 아래의 '대기 중 명령' 검사에만 의존한다.
    request_id: str = Field(default="", max_length=64)


class FeederStateIn(BaseModel):
    """밥통이 올리는 현재 상태. 이력이 아니라 지금 값이다."""

    busy: bool = False
    last_job: str = ""
    collar_connected: bool = False
    #: rest / walk / run / body_shake. 빈 문자열이면 아직 판정이 없다.
    #:
    #: **보고 사이에 지나간 것 중 가장 눈에 띄는 것**이다. 털기는 1~2초라
    #: '지금 무엇' 만 보내면 흔든 것이 통째로 빠진다. 밥통이 보고 주기 동안
    #: 본 것 중 털기 > 뛰기 > 걷기 > 쉬기 순으로 골라 보낸다.
    collar_class: str = ""
    collar_conf: float = Field(default=0.0, ge=0.0, le=1.0)
    #: 그 판정이 몇 초 전이었나. 앱이 "3초 전 털기" 로 보여준다.
    collar_age_s: int = Field(default=0, ge=0)
    #: 직전 1분 요약 {rest_s, walk_s, run_s, shake_n}
    minute: dict[str, int] = Field(default_factory=dict)
    #: 밥통이 켜진 뒤 받은 털기 수. 화면에 바로 오르는 숫자 (기록은 /ingest 가 한다).
    shake_live: int = Field(default=0, ge=0)
    #: 밥그릇 저울의 지금 무게(g). 그릇 무게는 영점으로 뺀 값이다.
    #: -1 = 저울 없음. 영점 근처에서는 -0.3 처럼 살짝 음수가 나올 수 있다.
    weight_g: float = Field(default=-1.0, ge=-2000, le=5000)


@router.post("/dogs/{dog_id}/dispense", dependencies=[Depends(require_app)])
def manual_dispense(dog_id: str, body: ManualDispense) -> dict:
    """
    앱 버튼 -> 서버에 명령을 쌓는다. 밥통이 폴링해서 가져간다.

    예약 급여와 **같은 모양의 명령**을 넣는다. 밥통에 코드가 두 벌
    생기면 한쪽만 고치는 사고가 난다.
    """
    from core.constants import CARTRIDGE_BY_ID, CARTRIDGES, LOADED_SLOTS

    by_slot = {c.slot: c for c in CARTRIDGES}

    pellets = []
    for slot, count in body.pellets.items():
        if count == 0:
            continue
        if slot not in LOADED_SLOTS:
            raise HTTPException(422, f"{slot}번 슬롯에는 로더가 없습니다")
        spec = by_slot.get(slot)
        if spec is None:
            raise HTTPException(422, f"{slot}번 슬롯에 해당하는 카트리지가 없습니다")
        # 하루 상한을 한 번에 넘기지 못하게 막는다. 오타로 통이 비는 걸 막고,
        # 수동 급여가 상한을 우회하는 구멍이 되지 않게 한다.
        if not 0 < count <= spec.max_pellets_per_day:
            raise HTTPException(
                422,
                f"{spec.name} 은 1~{spec.max_pellets_per_day}알까지입니다",
            )
        pellets.append({
            "slot": slot, "cartridge_id": spec.id,
            "name": spec.name, "count": count,
        })

    if not pellets and body.food_g <= 0:
        raise HTTPException(422, "내보낼 것이 없습니다")

    now = datetime.now()
    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        # **이미 대기 중인 명령이 있으면 받지 않는다.**
        #
        # 버튼을 두 번 누르거나, 밥통이 꺼진 동안 여러 번 누르면 깬 순간
        # 전부 실행된다. 이중 급여는 사고다. 멱등키만으로는 초가 달라지면
        # 통과하므로 여기서 한 번 더 막는다.
        if db.count_pending(c, dog_id, now) > 0:
            raise HTTPException(409, "아직 실행되지 않은 급여 명령이 있습니다")

        payload = {
            "food_g": round(body.food_g, 1),
            "pellets": pellets,
            # 밥통이 로그에 구분해 남긴다. 예약과 수동을 섞으면
            # "왜 이 시각에 나갔나" 를 나중에 설명할 수 없다.
            "source": "manual",
        }
        key = body.request_id.strip() or now.isoformat(timespec="microseconds")
        cmd_id = f"{dog_id}:manual:{key}"
        queued = db.queue_command(
            c, cmd_id, dog_id,
            scheduled=now, expires=now + MANUAL_TTL,
            payload=json.dumps(payload, ensure_ascii=False),
        )
        c.commit()

    if not queued:
        raise HTTPException(409, "같은 요청이 이미 들어왔습니다")
    return {"ok": True, "command_id": cmd_id, "expires_in_s": int(MANUAL_TTL.total_seconds())}


@router.post("/dogs/{dog_id}/dispense/cancel", dependencies=[Depends(require_app)])
def cancel_dispense(dog_id: str) -> dict:
    """
    아직 실행되지 않은 급여 명령을 취소한다.

    **진행 중인 배출은 멈출 수 없다.** 밥통은 60초마다 물어보므로 이미
    가져간 명령에는 손이 닿지 않는다. 그건 밥통에 직접 /stop 을 쳐야 한다.
    여기서 되는 것은 '아직 안 가져간 것' 뿐이고, 화면에도 그렇게 적는다.
    """
    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")
        n = db.cancel_pending(c, dog_id, datetime.now())
        c.commit()
    return {"ok": True, "cancelled": n}


@router.post("/feeder/{dog_id}/state", dependencies=[Depends(require_device)])
def put_feeder_state(dog_id: str, body: FeederStateIn) -> dict:
    """
    밥통이 자기 상태를 올리고, **그 답으로 명령을 받아 간다.**

    처음에는 상태 보고와 명령 조회가 따로였다. 상태는 10초, 명령은 60초
    주기라 버튼을 누르고 모터가 돌기까지 최악 60초가 걸렸다. 둘을 한 번에
    주고받으면 왕복이 절반이 되고, 주기를 줄여도 요청 수가 안 늘어난다.

    **행동 기록은 여기 쌓이지 않는다.** 그건 /ingest 로 가서 events 와
    status_samples 에 들어간다. 여기 있는 건 화면에 띄울 현재값뿐이고,
    사라져도 복구할 필요가 없다.
    """
    now = datetime.now()
    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        prev = db.feeder_state(c, dog_id)
        old = prev[1] if prev else {}
        state = body.model_dump()

        # 앱이 [영점] 을 눌렀으면 이번 답에 실어 보내고 지운다.
        tare = bool(old.get("tare_req"))

        # 남은 사료: 기준 이상이 된 첫 시각을 이어서 들고 간다.
        # 영점을 잡으면 그릇이 0 이 되므로 처음부터 다시 센다.
        if body.weight_g >= LEFTOVER_G and not tare:
            state["leftover_since"] = old.get("leftover_since") or now.isoformat(
                timespec="seconds")
        else:
            state["leftover_since"] = None

        db.save_feeder_state(c, dog_id, json.dumps(state, ensure_ascii=False), now)
        c.commit()
        out = {"ok": True, "commands": db.pending_commands(c, dog_id, now)}
        if tare:
            out["tare"] = True
        return out


@router.post("/dogs/{dog_id}/feeder/tare", dependencies=[Depends(require_app)])
def request_tare(dog_id: str) -> dict:
    """
    앱 [영점] → 밥통이 다음 상태 보고(2초 안)의 답으로 받아 저울을 0 으로 맞춘다.

    명령 큐에 넣지 않는다. 급여 명령과 섞이면 '대기 중 명령이 있어 409' 에
    걸리고, 영점은 몇 번 눌러도 결과가 같아서 멱등키도 필요 없다.
    """
    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")
        got = db.feeder_state(c, dog_id)
        if got is None:
            raise HTTPException(409, "밥통이 아직 한 번도 보고하지 않았습니다")
        seen, state = got
        state["tare_req"] = datetime.now().isoformat(timespec="seconds")
        state["leftover_since"] = None
        db.save_feeder_state(c, dog_id, json.dumps(state, ensure_ascii=False), seen)
        c.commit()
    return {"ok": True}


@router.get("/dogs/{dog_id}/feeder", dependencies=[Depends(require_app)])
def feeder_view(dog_id: str) -> dict:
    """
    앱의 [밥통] 화면이 읽는다.

    `online` 이 거짓이면 밥통이 보고를 끊은 것이다. **그때 버튼을 눌러도
    명령은 쌓인다** - 10분 안에 깨면 실행되고, 아니면 만료된다.
    화면에는 그 사실을 그대로 보여주는 게 맞다.
    """
    now = datetime.now()
    with db.connect() as c:
        if db.get_dog(c, dog_id) is None:
            raise HTTPException(404, "등록되지 않은 개체입니다")

        got = db.feeder_state(c, dog_id)
        state: dict = {}
        age_s = None
        if got is not None:
            seen, state = got
            age_s = int((now - seen).total_seconds())

        return {
            "online": age_s is not None and age_s <= FEEDER_ONLINE.total_seconds(),
            "age_s": age_s,
            "state": state,
            "pending": db.count_pending(c, dog_id, now),
            "last_ack": db.last_acked_command(c, dog_id),
            "leftover": _leftover(state, now),
        }
