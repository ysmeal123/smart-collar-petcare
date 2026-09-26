"""
배치 잡.

    수집(이벤트)  ->  [rollup]  ->  하루 요약  ->  [prescribe]  ->  처방  ->  [enqueue]  ->  사출 명령

모든 잡은 몇 번을 다시 돌려도 같은 결과가 나와야 한다.
장애를 복구하면서 어제치를 다시 돌려야 하는 상황이 반드시 온다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from core.aggregate import build_daily_summary, wear_ratio_from_seconds
from agent.wellness_agent import Context
from core.intake import MealResult, daily_intake
from core.plan import build as build_plan
from core.weight import ScaleSession, daily_weight
from core.inference import PrescriptionState
from core.models import BehaviorEvent, BehaviorType, DailySummary, DogProfile
from core.prescribe import prescribe
from store import db

# 상수를 튜닝하면 이 값을 올린다.
# 과거 처방을 그때 기준으로 설명하려면 무엇으로 계산했는지 남아 있어야 한다.
ALGO_VERSION = "1.0.0"

# 사출 명령은 급여 시각 30분 전에 내리고, 2시간이 지나면 버린다.
COMMAND_LEAD = timedelta(minutes=30)
COMMAND_TTL = timedelta(hours=2)


# ---------------------------------------------------------------------------
# 1. 집계
# ---------------------------------------------------------------------------

def rollup(conn, collar: str, day: date) -> DailySummary | None:
    """
    하루치 원시 이벤트를 요약으로 접는다.

    같은 날짜를 다시 돌리면 덮어쓴다. 뒤늦게 도착한 과거 데이터가 반영되려면
    그 날을 다시 접어야 하기 때문에 이 성질이 필요하다.
    """
    dog_id = db.dog_of_collar(conn, collar)
    if dog_id is None:
        return None

    rows = db.events_of_day(conn, collar, day)
    events = [
        BehaviorEvent(
            ts=datetime.fromisoformat(r["ts"]),
            type=BehaviorType(r["type"]),
            confidence=r["conf"],
            duration_s=r["dur_s"],
        )
        for r in rows
    ]

    # 착용률의 분모는 '하루 86400초'가 아니라 '표본이 실제로 설명한 초'다.
    # 기기가 꺼져 있던 시간까지 미착용으로 치면 멀쩡한 날이 통계에서 빠진다.
    wear_rows = db.status_of_day(conn, collar, day)
    worn_sec = sum(r["worn_sec"] for r in wear_rows)
    covered_sec = sum(r["covered_sec"] for r in wear_rows)
    steps = sum(r["steps"] for r in wear_rows)

    wear = wear_ratio_from_seconds(worn_sec, covered_sec)

    # Model A 요약을 시(0~23) -> 활동 초 로 편다
    activity: dict[int, dict[str, int]] = {}
    for r in wear_rows:
        activity[r["hour"]] = {
            "walk_sec": r["walk_sec"],
            "run_sec": r["run_sec"],
            "vigorous_sec": r["vigorous_sec"],
        }

    # 체중 - 캐스케이드 외부 루프의 입력.
    # 그날 채택된 값이 없으면 None으로 둔다. 추측해서 채우면
    # 활동계수가 잘못 교정되고 그 오차가 몇 달에 걸쳐 누적된다.
    weight = _accepted_weight(conn, dog_id, day)

    # 실제 섭취량. 로드셀이 없으면 0으로 남고, 그러면 intake_ratio 는 1.0이 된다
    # (준 게 없으면 다 먹은 것으로 친다 — models.py 참조).
    offered, eaten = _daily_intake(conn, dog_id, day)

    summary = build_daily_summary(
        day, events, wear_ratio=wear,
        activity=activity,
        steps=steps if steps > 0 else None,
        weight_kg=weight,
        food_offered_g=offered,
        food_eaten_g=eaten,
    )
    db.save_daily(conn, dog_id, day, summary.model_dump_json())
    return summary


def _daily_intake(conn, dog_id: str, day: date) -> tuple[int, int]:
    """그날 실제로 준 양과 먹은 양."""
    rows = db.meals_of_day(conn, dog_id, day)
    meals = [
        MealResult(
            eaten_g=r["eaten_g"], offered_g=int(round(r["dispensed_g"])),
            ratio=r["ratio"], duration_min=r["duration_min"],
            skipped=r["ratio"] < 0.10, note=r["note"],
        )
        for r in rows
    ]
    return daily_intake(meals)


def _accepted_weight(conn, dog_id: str, day: date) -> float | None:
    """그날 채택된 체중. 보호자 입력이 체중계보다 우선한다."""
    rows = db.weights_of_day(conn, dog_id, day)
    ok = [r for r in rows if r["accepted"]]
    if not ok:
        return None
    manual = [r["kg"] for r in ok if r["source"] == "manual"]
    return manual[-1] if manual else ok[-1]["kg"]


def record_scale_sessions(
    conn, dog_id: str, day: date, sessions: list[ScaleSession]
) -> dict:
    """
    급식판 체중계가 올린 세션들을 정제해 저장한다.

    버린 것도 이유와 함께 남긴다 - "왜 그날 체중이 반영 안 됐나"에
    답할 수 있어야 한다.
    """
    last = db.last_accepted_weight(conn, dog_id, day)
    result = daily_weight(sessions, manual_kg=None, last_known_kg=last)

    at = datetime.combine(day, datetime.min.time()) + timedelta(hours=12)
    if result.kg is not None:
        db.save_weight(conn, dog_id, at, result.kg, "scale", True, result.reason)
    elif sessions:
        # 원본 중앙값이라도 남겨야 나중에 필터가 빡빡했는지 확인할 수 있다
        raw = [v for s in sessions for v in s.samples_kg]
        if raw:
            db.save_weight(
                conn, dog_id, at, round(sum(raw) / len(raw), 2),
                "scale", False, result.reason,
            )

    return {"kg": result.kg, "accepted": result.kg is not None, "reason": result.reason}


def record_manual_weight(conn, dog_id: str, at: datetime, kg: float) -> dict:
    """보호자가 앱에서 직접 입력한 체중."""
    last = db.last_accepted_weight(conn, dog_id, at.date())
    result = daily_weight([], manual_kg=kg, last_known_kg=last)

    db.save_weight(
        conn, dog_id, at, kg, "manual",
        result.kg is not None, result.reason,
    )
    return {"kg": result.kg, "accepted": result.kg is not None, "reason": result.reason}


# ---------------------------------------------------------------------------
# 2. 처방
# ---------------------------------------------------------------------------

def recompute(conn, dog_id: str, today: date | None = None) -> dict | None:
    """
    저장된 하루 요약들로 **급여 계획**을 만든다.

    처방만 내는 게 아니라 트윈 조립 · 14일 시뮬레이션 · 보호자 질문까지
    한 번에 돈다. 이 결과가 시스템의 최종 출력이다.

    직전 처방이 남긴 상태를 이어받는다. 이게 없으면 히스테리시스와 슬루율
    제한이 매일 초기화되어 처방이 켜졌다 꺼졌다 발진한다.
    """
    raw = db.get_dog(conn, dog_id)
    if raw is None:
        return None

    profile = DogProfile.model_validate(raw)
    days = [DailySummary.model_validate(d) for d in db.load_daily(conn, dog_id)]
    if not days:
        return None

    prev = db.latest_state(conn, dog_id)
    state = PrescriptionState.model_validate(prev) if prev else PrescriptionState()

    answers, answered_at = db.load_context(conn, dog_id)
    context = Context(
        answers=answers,
        updated_at=date.fromisoformat(answered_at[:10]) if answered_at else None,
    )

    plan, rx, next_state = build_plan(
        profile, days, state=state, context=context, today=today
    )

    # 계획과 처방은 용도가 다르다. 섞어 넣으면 대시보드 스키마가 깨진다.
    #   plans          무엇을 줄 것인가 (밥통이 읽는다)
    #   prescriptions  왜 그렇게 정했나 (감사 추적)
    payload = plan.model_dump_json()
    db.save_plan_record(conn, dog_id, plan.date, payload)
    db.save_prescription(
        conn, dog_id, plan.date, ALGO_VERSION,
        rx.model_dump_json(), next_state.model_dump_json(),
    )
    return json.loads(payload)


# ---------------------------------------------------------------------------
# 3. 사출 명령
# ---------------------------------------------------------------------------

def enqueue_dispense(conn, dog_id: str, day: date, meal_hours=None) -> int:
    """
    급여 계획의 끼니를 그대로 사출 명령으로 적재한다.

    끼니 배분은 core/plan.py 가 이미 했다. 여기서 다시 쪼개면 로직이 두 벌이 된다.

    멱등키는 (개체, 날짜, 끼니 번호)다.
    잡이 두 번 돌아도 같은 끼니가 두 번 나가지 않는다 - 이중 급여는 사고다.
    """
    plan = db.latest_plan(conn, dog_id)
    if plan is None:
        return 0

    queued = 0
    for meal in plan.get("meals", []):
        at = datetime.combine(day, datetime.min.time()) + timedelta(
            hours=int(meal["hour"])
        )
        payload = {
            "food_g": meal["food_g"],
            "pellets": [
                {"slot": p["slot"], "cartridge_id": p["cartridge_id"],
                 "name": p["name"], "count": p["count"]}
                for p in meal.get("pellets", [])
            ],
        }
        if db.queue_command(
            conn, f"{dog_id}:{day}:{meal['index']}", dog_id,
            scheduled=at - COMMAND_LEAD,
            expires=at + COMMAND_TTL,
            payload=json.dumps(payload, ensure_ascii=False),
        ):
            queued += 1
    return queued


# ---------------------------------------------------------------------------
# 전체 실행
# ---------------------------------------------------------------------------

def run_daily(conn, collar: str, day: date, meal_hours: list[int] | None = None) -> dict:
    """매일 새벽에 도는 잡. 집계 -> 처방 -> 명령 적재 순서다."""
    dog_id = db.dog_of_collar(conn, collar)
    if dog_id is None:
        return {"error": f"목줄 {collar} 에 연결된 개체가 없습니다"}

    summary = rollup(conn, collar, day)
    plan = recompute(conn, dog_id, today=day)
    queued = enqueue_dispense(conn, dog_id, day + timedelta(days=1))
    expired = db.expire_stale(conn, datetime.now())

    return {
        "dog_id": dog_id,
        "date": str(day),
        "wear_ratio": summary.wear_ratio if summary else None,
        "valid": summary.valid if summary else None,
        "food_grams": plan.get("total_food_g") if plan else None,
        "pellets": sum(
            len(m.get("pellets", [])) for m in plan.get("meals", [])
        ) if plan else 0,
        "escalated": plan.get("escalated") if plan else None,
        "simulation": (plan.get("simulation") or {}).get("energy") if plan else None,
        "questions": len(plan.get("questions", [])) if plan else 0,
        "commands_queued": queued,
        "commands_expired": expired,
    }
