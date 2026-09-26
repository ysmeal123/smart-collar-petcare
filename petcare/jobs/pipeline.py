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
from core.weight import ScaleSession, daily_weight
from core.inference import PrescriptionState
from core.constants import CARTRIDGE_BY_ID
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

    summary = build_daily_summary(
        day, events, wear_ratio=wear,
        activity=activity,
        steps=steps if steps > 0 else None,
        weight_kg=weight,
    )
    db.save_daily(conn, dog_id, day, summary.model_dump_json())
    return summary


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
    저장된 하루 요약들로 처방을 다시 계산한다.

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

    rx, next_state = prescribe(profile, days, state=state, today=today)

    db.save_prescription(
        conn, dog_id, today or days[-1].date,
        ALGO_VERSION, rx.model_dump_json(), next_state.model_dump_json(),
    )
    return json.loads(rx.model_dump_json())


# ---------------------------------------------------------------------------
# 3. 사출 명령
# ---------------------------------------------------------------------------

def split_meals(rx: dict, meal_hours: list[int]) -> list[tuple[int, dict]]:
    """
    처방을 끼니별로 쪼갠다.

    영양제의 끼니 배치는 이미 알고리즘이 정해놨다(길항 성분 분리).
    여기서는 그 결정을 따라 담기만 한다.
    사료는 균등 분할하고 나머지 그램은 첫 끼니에 붙인다.
    """
    n = max(1, len(meal_hours))
    grams = int(rx.get("food_grams", 0))
    base = grams // n
    per = [base] * n
    per[0] += grams - base * n

    items = rx.get("items", [])
    out: list[tuple[int, dict]] = []

    for i, hour in enumerate(meal_hours):
        if n == 1:
            picked = items                      # 한 끼면 전부 같이 나간다
        elif n == 3 and i == 1:
            picked = []                         # 점심은 사료만
        else:
            slot = "morning" if i == 0 else "evening"
            picked = [it for it in items if it.get("meal_slot") == slot]

        out.append((hour, {
            "food_g": per[i],
            "pellets": [
                {
                    "cartridge_id": it["cartridge_id"],
                    "slot": CARTRIDGE_BY_ID[it["cartridge_id"]].slot,
                    "name": it["name"],
                    "count": it["pellets"],
                }
                for it in picked
            ],
        }))
    return out


def enqueue_dispense(conn, dog_id: str, day: date, meal_hours: list[int]) -> int:
    """
    끼니별 사출 명령을 적재한다.

    멱등키를 (개체, 날짜, 끼니 번호)로 만든다.
    잡이 두 번 돌아도 같은 끼니가 두 번 나가지 않는다 - 이중 급여는 사고다.
    """
    rx = db.latest_prescription(conn, dog_id)
    if rx is None:
        return 0

    queued = 0
    for i, (hour, payload) in enumerate(split_meals(rx, meal_hours)):
        at = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour)
        if db.queue_command(
            conn, f"{dog_id}:{day}:{i}", dog_id,
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
    rx = recompute(conn, dog_id, today=day)

    hours = meal_hours or [8, 19]
    queued = enqueue_dispense(conn, dog_id, day + timedelta(days=1), hours)
    expired = db.expire_stale(conn, datetime.now())

    return {
        "dog_id": dog_id,
        "date": str(day),
        "wear_ratio": summary.wear_ratio if summary else None,
        "valid": summary.valid if summary else None,
        "food_grams": rx.get("food_grams") if rx else None,
        "items": len(rx.get("items", [])) if rx else 0,
        "escalated": rx.get("escalated") if rx else None,
        "commands_queued": queued,
        "commands_expired": expired,
    }
