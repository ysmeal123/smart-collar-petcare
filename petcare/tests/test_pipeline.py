"""
목줄 -> 수집 -> 집계 -> 처방 -> 사출 명령 전 구간 검증.

mock 생성기가 만든 44일치 이벤트를 실제 수집 경로로 밀어넣는다.
생성기가 내부에서 요약을 만들어 건네주는 게 아니라,
이벤트만 넘기고 서버가 스스로 접게 한다 - 실기기와 같은 경로다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from core.models import BehaviorType
from core.telemetry import CollarEvent, CollarStatus, IngestBatch, to_behavior_events
from jobs import pipeline
from mock.generator import generate
from store import db

COLLAR = "PBL-TEST-01"
HOUR_MS = 3_600_000


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "test.db"
    db.init(path)
    with db.connect(path) as c:
        yield c


def spread_activity(hour_totals: dict[str, int]) -> list[dict[str, int]]:
    """
    한 시간의 활동 초를 60개 분 표본으로 쪼갠다.

    한 분은 60초를 넘을 수 없으므로 앞쪽 분부터 채운다.
    총합이 보존되는 것이 핵심이다 - 여기서 잃으면 집계 검증이 무의미해진다.
    """
    out = [{"walk_sec": 0, "run_sec": 0, "vigorous_sec": 0} for _ in range(60)]
    i = 0
    for kind in ("walk_sec", "run_sec", "vigorous_sec"):
        left = hour_totals.get(kind, 0)
        while left > 0 and i < 60:
            used = sum(out[i].values())
            take = min(60 - used, left)
            if take <= 0:
                i += 1
                continue
            out[i][kind] += take
            left -= take
    return out


def push(conn, day: date, events, worn_hours=range(24), steps_per_hour=40,
         activity: dict | None = None):
    """
    하루치를 게이트웨이가 올리듯 밀어넣는다.

    boot_id를 날짜로 둔다. seq는 부팅마다 0으로 돌아가므로
    boot_id가 같은데 날짜만 바뀌면 둘째 날부터 전부 중복으로 버려진다.
    """
    boot = datetime.combine(day, datetime.min.time())
    boot_id = day.toordinal()
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
    # 목줄은 1분마다 상태를 찍는다. 시간당 1개만 보내면 그 시간의 1분만
    # 판단 근거가 생긴다.
    # 활동(Model A)도 이 표본에 실려 온다 - 이벤트가 아니다.
    act = activity or {}
    status = []
    for h in worn_hours:
        minutes = spread_activity(act.get(h, {}))
        for m, a in enumerate(minutes):
            status.append(CollarStatus(
                t_ms=h * HOUR_MS + m * 60_000,
                worn_sec=60,
                steps=steps_per_hour // 60,
                battery=80,
                rest_sec=60 - sum(a.values()),
                **a,
            ))

    batch = IngestBatch(
        collar_serial=COLLAR, boot_id=boot_id,
        uptime_ms=24 * HOUR_MS, received_at=boot + timedelta(hours=24),
        events=collar_events, status=status,
    )

    resolved = to_behavior_events(batch)
    db.insert_events(conn, COLLAR, batch.boot_id,
                     list(zip(batch.events, [e.ts for e in resolved])))
    from core.telemetry import resolved_status
    db.insert_status(conn, COLLAR, batch.boot_id, resolved_status(batch))
    return batch


# ---------------------------------------------------------------------------

def test_이벤트만_보내도_하루_요약이_나온다(conn):
    """게이트웨이는 요약을 만들지 않는다. 이벤트만 올리고 서버가 접는다."""
    ds = generate("skin")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    day = ds.days[-1].date
    todays = [e for e in ds.events if e.ts.date() == day]
    push(conn, day, todays, activity=ds.activity.get(day.isoformat()))

    summary = pipeline.rollup(conn, COLLAR, day)

    assert summary is not None
    assert summary.wear_ratio == 1.0
    assert summary.valid is True
    # 생성기가 직접 집계한 값과 서버가 접은 값이 같아야 한다
    assert summary.summary.scratch_night == ds.days[-1].summary.scratch_night
    assert summary.summary.run_sec == ds.days[-1].summary.run_sec


def test_같은_묶음이_두_번_와도_긁은_횟수가_늘지_않는다(conn):
    """
    재전송으로 중복이 쌓이면 없는 이상을 만들어낸다.
    이게 이 시스템에서 가장 조용하고 위험한 고장이다.
    """
    ds = generate("skin")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    day = ds.days[-1].date
    todays = [e for e in ds.events if e.ts.date() == day]

    act = ds.activity.get(day.isoformat())
    push(conn, day, todays, activity=act)
    once = pipeline.rollup(conn, COLLAR, day)

    push(conn, day, todays, activity=act)   # 똑같은 묶음을 다시
    twice = pipeline.rollup(conn, COLLAR, day)

    assert twice.summary.scratch_total == once.summary.scratch_total
    assert twice.summary.run_sec == once.summary.run_sec


def test_재전송해도_활동량이_부풀려지지_않는다(conn):
    """
    이벤트는 기본키가 중복을 막는데 활동만 두 배가 되면
    이동성 축이 '더 건강해졌다'고 읽는다. 조용하고 위험한 고장이다.

    그래서 상태 표본도 접지 않고 표본 단위로 저장한다.
    """
    ds = generate("skin")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    day = ds.days[-1].date
    todays = [e for e in ds.events if e.ts.date() == day]
    act = ds.activity.get(day.isoformat())

    push(conn, day, todays, activity=act)
    once = pipeline.rollup(conn, COLLAR, day)

    push(conn, day, todays, activity=act)       # 게이트웨이가 재시도
    twice = pipeline.rollup(conn, COLLAR, day)

    assert twice.summary.walk_sec == once.summary.walk_sec
    assert twice.summary.run_sec == once.summary.run_sec
    assert twice.summary.vigorous_sec == once.summary.vigorous_sec
    assert twice.summary.steps == once.summary.steps
    assert twice.wear_ratio == once.wear_ratio


def test_생성기와_수집경로가_같은_활동값을_만든다(conn):
    """
    목줄이 1분 요약으로 올린 활동이 생성기가 직접 집계한 값과 같아야 한다.
    여기서 갈라지면 mock 검증이 실제 파이프라인을 보장하지 못한다.
    """
    ds = generate("joint")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    day = ds.days[-1].date
    push(conn, day, [e for e in ds.events if e.ts.date() == day],
         activity=ds.activity.get(day.isoformat()))
    got = pipeline.rollup(conn, COLLAR, day)

    want = ds.days[-1].summary
    assert got.summary.walk_sec == want.walk_sec
    assert got.summary.run_sec == want.run_sec
    assert got.summary.vigorous_sec == want.vigorous_sec


def test_목줄을_반만_찬_날은_통계에서_빠진다(conn):
    ds = generate("normal")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    day = ds.days[-1].date
    todays = [e for e in ds.events if e.ts.date() == day]
    push(conn, day, todays, worn_hours=range(24), steps_per_hour=0)

    # 앞 12시간은 목줄을 벗겨 뒀다고 본다
    conn.execute(
        "UPDATE status_samples SET worn_sec=0 "
        "WHERE collar=? AND CAST(strftime('%H', ts) AS INTEGER) < 12",
        (COLLAR,),
    )

    summary = pipeline.rollup(conn, COLLAR, day)
    assert summary.wear_ratio == pytest.approx(0.5, abs=0.01)
    assert summary.valid is False       # 60% 게이트 미달


def test_44일을_밀어넣으면_처방이_나온다(conn):
    """수집부터 처방까지 실제 경로로 한 번 통과시킨다."""
    ds = generate("skin")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    by_day: dict[date, list] = {}
    for e in ds.events:
        by_day.setdefault(e.ts.date(), []).append(e)

    for day, events in sorted(by_day.items()):
        push(conn, day, events, activity=ds.activity.get(day.isoformat()))
        pipeline.rollup(conn, COLLAR, day)

    plan = pipeline.recompute(conn, ds.profile.dog_id)

    assert plan is not None
    assert plan["total_food_g"] > 0
    # 피부 시나리오이므로 피부 축이 발화해야 한다
    assert "skin" in plan["attention"]

    pellets = [p for m in plan["meals"] for p in m["pellets"]]
    assert any(p["cartridge_id"] == "omega3" for p in pellets)

    # 최종 출력에는 시뮬레이션과 근거가 함께 들어 있다
    assert plan["simulation"] is not None
    assert plan["trace"]


def test_사출_명령이_끼니별로_쪼개진다(conn):
    ds = generate("skin")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    by_day: dict[date, list] = {}
    for e in ds.events:
        by_day.setdefault(e.ts.date(), []).append(e)
    for day, events in sorted(by_day.items()):
        push(conn, day, events, activity=ds.activity.get(day.isoformat()))
        pipeline.rollup(conn, COLLAR, day)

    plan = pipeline.recompute(conn, ds.profile.dog_id)
    tomorrow = ds.days[-1].date + timedelta(days=1)

    n = pipeline.enqueue_dispense(conn, ds.profile.dog_id, tomorrow)
    assert n == 2

    cmds = db.pending_commands(
        conn, ds.profile.dog_id,
        datetime.combine(tomorrow, datetime.min.time()) + timedelta(hours=7),
    )
    total_food = sum(c["food_g"] for c in cmds)
    assert total_food == plan["total_food_g"]      # 나눠도 총량은 보존된다


def test_같은_끼니를_두_번_적재하지_않는다(conn):
    """잡이 두 번 돌아도 이중 급여가 되면 안 된다. 이건 사고다."""
    ds = generate("normal")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    day = ds.days[-1].date
    push(conn, day, [e for e in ds.events if e.ts.date() == day],
         activity=ds.activity.get(day.isoformat()))
    pipeline.rollup(conn, COLLAR, day)
    pipeline.recompute(conn, ds.profile.dog_id)

    first = pipeline.enqueue_dispense(conn, ds.profile.dog_id, day)
    again = pipeline.enqueue_dispense(conn, ds.profile.dog_id, day)

    assert first == 2
    assert again == 0


def test_만료된_명령은_내려가지_않는다(conn):
    """아침 급여 명령이 저녁에 실행되면 안 된다."""
    ds = generate("normal")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    day = ds.days[-1].date
    push(conn, day, [e for e in ds.events if e.ts.date() == day],
         activity=ds.activity.get(day.isoformat()))
    pipeline.rollup(conn, COLLAR, day)
    pipeline.recompute(conn, ds.profile.dog_id)
    pipeline.enqueue_dispense(conn, ds.profile.dog_id, day)

    base = datetime.combine(day, datetime.min.time())
    # 08시 급여는 10시에 만료된다. 저녁 급여(19시)는 아직 살아 있다.
    assert len(db.pending_commands(conn, ds.profile.dog_id, base + timedelta(hours=8))) == 2
    assert len(db.pending_commands(conn, ds.profile.dog_id, base + timedelta(hours=11))) == 1
    assert len(db.pending_commands(conn, ds.profile.dog_id, base + timedelta(hours=22))) == 0


def test_뒤늦게_온_데이터가_그_날짜에_반영된다(conn):
    """
    BLE가 끊겼다 복구되면 과거 데이터가 몰려 온다.
    그 날을 다시 접어야 반영되므로 rollup은 몇 번이든 다시 돌 수 있어야 한다.
    """
    ds = generate("skin")
    db.upsert_dog(conn, ds.profile.dog_id, ds.profile.model_dump_json())
    db.bind_collar(conn, COLLAR, ds.profile.dog_id)

    day = ds.days[-1].date
    todays = [e for e in ds.events if e.ts.date() == day]
    half = len(todays) // 2

    push(conn, day, todays[:half], activity=ds.activity.get(day.isoformat()))
    partial = pipeline.rollup(conn, COLLAR, day)

    # 나머지가 뒤늦게 도착 (seq가 이어지도록 오프셋을 준다)
    boot = datetime.combine(day, datetime.min.time())
    late = IngestBatch(
        collar_serial=COLLAR, boot_id=day.toordinal(),
        uptime_ms=30 * HOUR_MS, received_at=boot + timedelta(hours=30),
        events=[
            CollarEvent(
                seq=half + i,
                t_ms=int((e.ts - boot).total_seconds() * 1000),
                type=e.type, conf=e.confidence, dur_s=e.duration_s,
            )
            for i, e in enumerate(todays[half:])
        ],
    )
    resolved = to_behavior_events(late)
    db.insert_events(conn, COLLAR, day.toordinal(),
                     list(zip(late.events, [e.ts for e in resolved])))

    full = pipeline.rollup(conn, COLLAR, day)
    assert full.summary.scratch_total > partial.summary.scratch_total
    assert full.summary.scratch_total == ds.days[-1].summary.scratch_total
