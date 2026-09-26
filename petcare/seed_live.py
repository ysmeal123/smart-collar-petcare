"""
가짜 목줄로 실제 수집 경로를 채운다.

mock 생성기가 만든 44일치 이벤트를 게이트웨이가 올리듯 /v1/ingest 형식으로
밀어넣고, 일일 배치를 돌려 처방과 사출 명령까지 만든다.

    python seed_live.py

끝나면 서버를 띄워 실데이터 엔드포인트를 볼 수 있다.

    uvicorn api.main:app --reload
    http://localhost:8000/v1/dogs/dog-skin/dashboard
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

from core.telemetry import (
    CollarEvent,
    CollarStatus,
    IngestBatch,
    steps_by_hour,
    to_behavior_events,
    wear_seconds_by_hour,
)
from core.weight import ScaleSession
from jobs import pipeline
from mock.generator import generate
from store import db

HOUR_MS = 3_600_000

# 시나리오별 목줄 시리얼
COLLARS = {
    "skin": "PBL-0001",
    "joint": "PBL-0002",
    "normal": "PBL-0003",
    "acute": "PBL-0004",
}


def push_day(conn, collar: str, day: date, events, wear_ratio: float) -> int:
    """
    하루치를 게이트웨이가 올리듯 밀어넣는다.

    boot_id를 날짜로 둔다. 실제 목줄도 하루 한 번쯤은 재부팅하고,
    seq가 부팅마다 0으로 돌아가므로 날짜별로 구분해야 중복으로 버려지지 않는다.
    """
    boot = datetime.combine(day, datetime.min.time())

    collar_events = [
        CollarEvent(
            seq=i,
            t_ms=int((e.ts - boot).total_seconds() * 1000),
            type=e.type,
            conf=e.confidence,
            dur_s=e.duration_s,
        )
        for i, e in enumerate(events)
    ]

    # 1분마다 상태를 찍는다. 착용률은 생성기가 준 값을 그대로 재현한다.
    worn_minutes = int(1440 * wear_ratio)
    status = [
        CollarStatus(
            t_ms=m * 60_000,
            worn_sec=60 if m < worn_minutes else 0,
            steps=2,
            battery=max(20, 100 - (day.toordinal() % 80)),
        )
        for m in range(1440)
    ]

    batch = IngestBatch(
        collar_serial=collar,
        boot_id=day.toordinal(),
        uptime_ms=24 * HOUR_MS,
        received_at=boot + timedelta(hours=24),
        fw_version="0.1.0",
        events=collar_events,
        status=status,
    )

    resolved = to_behavior_events(batch)
    n = db.insert_events(
        conn, collar, batch.boot_id,
        list(zip(batch.events, [e.ts for e in resolved])),
    )

    wear = wear_seconds_by_hour(batch)
    steps = steps_by_hour(batch)
    for hour, (worn, covered) in wear.items():
        db.upsert_wear(conn, collar, hour.isoformat(), worn, covered, steps.get(hour, 0))

    db.touch_collar(conn, collar, batch.fw_version, status[-1].battery)
    return n


def seed(scenario: str, conn) -> dict:
    ds = generate(scenario)
    collar = COLLARS[scenario]

    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, collar, ds.profile.dog_id)

    by_day: dict[date, list] = {}
    for e in ds.events:
        by_day.setdefault(e.ts.date(), []).append(e)

    wear_by_day = {d.date: d.wear_ratio for d in ds.days}

    # 생성기가 만든 체중을 급식판 체중계가 잰 것처럼 밀어넣는다.
    # 개가 가만히 서 있지 않으므로 표본에 흔들림을 섞는다.
    weight_by_day = {d.date: d.weight_kg for d in ds.days}

    total = 0
    for day in sorted(by_day):
        total += push_day(conn, collar, day, by_day[day], wear_by_day.get(day, 1.0))

        w = weight_by_day.get(day)
        if w is not None:
            jitter = [round(w + (i - 3) * 0.01, 2) for i in range(8)]
            pipeline.record_scale_sessions(
                conn, ds.profile.dog_id, day,
                [ScaleSession(
                    started_at=datetime.combine(day, datetime.min.time())
                    + timedelta(hours=8),
                    samples_kg=jitter,
                )],
            )

        pipeline.rollup(conn, collar, day)

    last = ds.days[-1].date
    rx = pipeline.recompute(conn, ds.profile.dog_id, today=last)

    # 사출 명령은 '실제 내일' 급여분으로 적재한다.
    # 생성기 데이터의 마지막 날짜를 쓰면 이미 만료된 명령이 되어
    # 밥통이 폴링해도 아무것도 받지 못한다 (만료 필터가 정상 동작하는 것이다).
    tomorrow = date.today() + timedelta(days=1)
    queued = pipeline.enqueue_dispense(
        conn, ds.profile.dog_id, tomorrow,
        [8, 19] if ds.profile.meals_per_day == 2 else [8],
    )

    return {
        "scenario": scenario,
        "dog_id": ds.profile.dog_id,
        "collar": collar,
        "events": total,
        "days": len(by_day),
        "food_grams": rx["food_grams"] if rx else None,
        "items": [f"{i['name']} {i['pellets']}알" for i in rx["items"]] if rx else [],
        "escalated": rx["escalated"] if rx else None,
        "fired": [a["axis"] for a in rx["axes"] if a["active"]] if rx else [],
        "commands": queued,
        "weights": sum(1 for d in ds.days if d.weight_kg is not None),
        "k_final": rx["trace"] if False else None,
    }


def main() -> None:
    db.init()
    print(f"DB: {db.DB_PATH}\n")

    with db.connect() as conn:
        for scenario in COLLARS:
            r = seed(scenario, conn)
            print(f"[{r['scenario']}]  {r['dog_id']}  ({r['collar']})")
            print(f"  이벤트 {r['events']:,}건 / {r['days']}일 수집")
            print(f"  발화 축 {r['fired'] or '없음'}"
                  f"{'  🚨 긴급정지' if r['escalated'] else ''}")
            print(f"  사료 {r['food_grams']}g   영양제 {r['items'] or '없음'}")
            print(f"  사출 명령 {r['commands']}건 적재\n")

    print("서버를 띄워 확인하세요:")
    print("  uvicorn api.main:app --reload")
    print("  http://localhost:8000/v1/dogs/dog-skin/dashboard")


if __name__ == "__main__":
    sys.exit(main())
