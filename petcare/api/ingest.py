"""
수집 · 사출 명령 엔드포인트.

인증 주체가 사용자가 아니라 **기기**다.
밥통이 사용자 토큰을 들고 있으면 안 되므로 기기별 자격증명을 따로 쓴다.
(지금은 헤더에 시리얼만 넣는다. 실제로는 기기별 발급 토큰이 필요하다.)
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.weight import ScaleSession
from core.telemetry import (
    IngestBatch,
    resolved_status,
    to_behavior_events,
)
from jobs import pipeline
from store import db

router = APIRouter(prefix="/v1", tags=["device"])


@router.post("/ingest")
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


@router.get("/feeder/{dog_id}/commands")
def commands(dog_id: str) -> dict:
    """
    밥통이 폴링한다.

    만료된 명령은 주지 않는다 - 아침 급여가 저녁에 실행되면 안 된다.
    밥통은 이 응답을 로컬에 캐시해 두고, 서버가 죽어도 캐시로 급여를 계속한다.
    """
    with db.connect() as c:
        return {"commands": db.pending_commands(c, dog_id, datetime.now())}


@router.post("/feeder/commands/{cmd_id}/ack")
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


@router.post("/jobs/daily")
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


@router.post("/feeder/{dog_id}/weight")
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


@router.post("/dogs/{dog_id}/weight")
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


@router.get("/dogs/{dog_id}/weights")
def weight_history(dog_id: str, days: int = 60) -> dict:
    """체중 이력. 버린 측정도 이유와 함께 돌려준다."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT measured_at, kg, source, accepted, reason FROM weights "
            "WHERE dog_id=? ORDER BY measured_at DESC LIMIT ?",
            (dog_id, days),
        ).fetchall()
        return {"weights": [dict(r) for r in reversed(rows)]}


@router.get("/dogs/{dog_id}/dashboard")
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
            raise HTTPException(409, "아직 처방이 계산되지 않았습니다")

        return {
            "scenario": "live",
            "profile": profile,
            "recent_days": recent[-days:],
            "prescription": rx,
            "trend": _trend(recent),
        }


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


@router.get("/status")
def device_status() -> dict:
    """등록된 목줄들의 마지막 통신 시각. 앱 기기 화면용."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT serial, dog_id, fw_version, battery, last_seen FROM collars"
        ).fetchall()
        return {"collars": [dict(r) for r in rows]}
