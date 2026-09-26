"""
저장소.

SQLite를 쓴다. 팀원이 클론해서 바로 돌릴 수 있는 게 지금 단계에서 제일 중요하다.
상용에서는 Postgres로 가야 한다 - 스키마는 그대로 옮겨간다.

설계상 지켜야 할 세 가지:

1. daily_metrics 의 기본키가 (dog_id, date) 다.
   같은 날짜를 다시 올리면 덮어쓴다. 게이트웨이가 재시도해도 중복이 쌓이지 않는다.

2. prescriptions 는 고쳐 쓰지 않고 계속 쌓는다.
   algo_version 을 함께 저장해서, 나중에 상수를 튜닝해도 과거 처방을
   그때 기준으로 설명할 수 있어야 한다. 사고가 났을 때 "왜 그렇게 줬는가"에
   답하지 못하면 끝이다.

3. seen_events 로 (collar, boot_id, seq) 중복을 막는다.
   재전송으로 긁은 횟수가 부풀려지면 없는 이상을 만들어낸다.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "petcare.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS dogs (
    dog_id     TEXT PRIMARY KEY,
    profile    TEXT NOT NULL,          -- DogProfile JSON
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collars (
    serial      TEXT PRIMARY KEY,
    dog_id      TEXT NOT NULL REFERENCES dogs(dog_id),
    fw_version  TEXT DEFAULT '',
    battery     INTEGER,
    last_seen   TEXT
);

-- 원시 행동 이벤트. 집계 뒤에도 남겨둔다.
-- 분류기를 개선하면 과거 데이터를 다시 돌려봐야 하기 때문이다.
CREATE TABLE IF NOT EXISTS events (
    collar   TEXT NOT NULL,
    boot_id  INTEGER NOT NULL,
    seq      INTEGER NOT NULL,
    ts       TEXT NOT NULL,
    type     TEXT NOT NULL,
    conf     REAL NOT NULL,
    dur_s    REAL NOT NULL,
    PRIMARY KEY (collar, boot_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(collar, ts);

-- 시간대별 착용 초와 걸음 수. 이벤트로는 역산이 안 되는 값들이다.
CREATE TABLE IF NOT EXISTS wear (
    collar      TEXT NOT NULL,
    hour        TEXT NOT NULL,         -- ISO, 시 단위로 자른 값
    worn_sec    INTEGER NOT NULL,
    covered_sec INTEGER NOT NULL,      -- 표본이 실제로 설명한 초. 착용률의 분모다
    steps       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collar, hour)
);

-- 하루치 요약. 같은 날짜를 다시 올리면 덮어쓴다.
CREATE TABLE IF NOT EXISTS daily_metrics (
    dog_id  TEXT NOT NULL,
    date    TEXT NOT NULL,
    payload TEXT NOT NULL,             -- DailySummary JSON
    PRIMARY KEY (dog_id, date)
);

-- 처방은 고쳐 쓰지 않고 쌓는다.
CREATE TABLE IF NOT EXISTS prescriptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dog_id       TEXT NOT NULL,
    date         TEXT NOT NULL,
    algo_version TEXT NOT NULL,
    payload      TEXT NOT NULL,        -- Prescription JSON (trace 포함)
    state        TEXT NOT NULL DEFAULT '{}',   -- PrescriptionState. 다음 날이 이어받는다
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rx ON prescriptions(dog_id, date);

-- 밥통에 내릴 사출 명령.
CREATE TABLE IF NOT EXISTS dispense_commands (
    id         TEXT PRIMARY KEY,       -- 멱등키
    dog_id     TEXT NOT NULL,
    scheduled  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL,
    result     TEXT
);
CREATE INDEX IF NOT EXISTS idx_cmd ON dispense_commands(dog_id, state);
"""


@contextmanager
def connect(path: Path | str = DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # 게이트웨이 여러 대가 동시에 올릴 수 있다
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(path: Path | str = DB_PATH) -> None:
    with connect(path) as c:
        c.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# 개체 · 목줄
# ---------------------------------------------------------------------------

def upsert_dog(c: sqlite3.Connection, dog_id: str, profile_json: str) -> None:
    c.execute(
        "INSERT INTO dogs(dog_id, profile, created_at) VALUES(?,?,?) "
        "ON CONFLICT(dog_id) DO UPDATE SET profile=excluded.profile",
        (dog_id, profile_json, datetime.now().isoformat(timespec="seconds")),
    )


def get_dog(c: sqlite3.Connection, dog_id: str) -> dict | None:
    row = c.execute("SELECT profile FROM dogs WHERE dog_id=?", (dog_id,)).fetchone()
    return json.loads(row["profile"]) if row else None


def bind_collar(c: sqlite3.Connection, serial: str, dog_id: str) -> None:
    c.execute(
        "INSERT INTO collars(serial, dog_id) VALUES(?,?) "
        "ON CONFLICT(serial) DO UPDATE SET dog_id=excluded.dog_id",
        (serial, dog_id),
    )


def dog_of_collar(c: sqlite3.Connection, serial: str) -> str | None:
    row = c.execute("SELECT dog_id FROM collars WHERE serial=?", (serial,)).fetchone()
    return row["dog_id"] if row else None


def touch_collar(
    c: sqlite3.Connection, serial: str, fw: str, battery: int | None
) -> None:
    c.execute(
        "UPDATE collars SET fw_version=?, battery=COALESCE(?, battery), last_seen=? "
        "WHERE serial=?",
        (fw, battery, datetime.now().isoformat(timespec="seconds"), serial),
    )


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------

def insert_events(c: sqlite3.Connection, collar: str, boot_id: int, rows: list) -> int:
    """
    중복은 DB가 막는다. 애플리케이션에서 조회해 거르는 것보다 확실하다.
    반환: 실제로 새로 들어간 개수
    """
    before = c.total_changes
    c.executemany(
        "INSERT OR IGNORE INTO events(collar, boot_id, seq, ts, type, conf, dur_s) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            (collar, boot_id, e.seq, ts.isoformat(), e.type.value, e.conf, e.dur_s)
            for e, ts in rows
        ],
    )
    return c.total_changes - before


def upsert_wear(c: sqlite3.Connection, collar: str, hour_iso: str,
                worn_sec: int, covered_sec: int, steps: int) -> None:
    """
    같은 시간대가 또 오면 더한다. 목줄이 1분 표본을 나눠 보내기 때문이다.

    한 시간을 넘지 않게 자른다. 재전송으로 같은 분이 두 번 들어와도
    착용률이 100%를 넘지 않는다.
    """
    c.execute(
        "INSERT INTO wear(collar, hour, worn_sec, covered_sec, steps) VALUES(?,?,?,?,?) "
        "ON CONFLICT(collar, hour) DO UPDATE SET "
        "  worn_sec    = MIN(3600, wear.worn_sec + excluded.worn_sec), "
        "  covered_sec = MIN(3600, wear.covered_sec + excluded.covered_sec), "
        "  steps       = wear.steps + excluded.steps",
        (collar, hour_iso, worn_sec, covered_sec, steps),
    )


def events_of_day(c: sqlite3.Connection, collar: str, day: date) -> list[sqlite3.Row]:
    return c.execute(
        "SELECT ts, type, conf, dur_s FROM events "
        "WHERE collar=? AND ts >= ? AND ts < ? ORDER BY ts",
        (collar, f"{day}T00:00:00", f"{day}T23:59:59.999999"),
    ).fetchall()


def wear_of_day(c: sqlite3.Connection, collar: str, day: date) -> list[sqlite3.Row]:
    return c.execute(
        "SELECT hour, worn_sec, covered_sec, steps FROM wear "
        "WHERE collar=? AND hour >= ? AND hour < ? ORDER BY hour",
        (collar, f"{day}T00:00:00", f"{day}T23:59:59"),
    ).fetchall()


# ---------------------------------------------------------------------------
# 집계 · 처방
# ---------------------------------------------------------------------------

def save_daily(c: sqlite3.Connection, dog_id: str, day: date, payload: str) -> None:
    c.execute(
        "INSERT INTO daily_metrics(dog_id, date, payload) VALUES(?,?,?) "
        "ON CONFLICT(dog_id, date) DO UPDATE SET payload=excluded.payload",
        (dog_id, str(day), payload),
    )


def load_daily(c: sqlite3.Connection, dog_id: str, limit: int = 60) -> list[dict]:
    """오래된 것부터 돌려준다. 알고리즘이 시계열 순서를 전제한다."""
    rows = c.execute(
        "SELECT payload FROM daily_metrics WHERE dog_id=? ORDER BY date DESC LIMIT ?",
        (dog_id, limit),
    ).fetchall()
    return [json.loads(r["payload"]) for r in reversed(rows)]


def save_prescription(
    c: sqlite3.Connection, dog_id: str, day: date,
    algo_version: str, payload: str, state: str = "{}",
) -> None:
    c.execute(
        "INSERT INTO prescriptions"
        "(dog_id, date, algo_version, payload, state, created_at) VALUES(?,?,?,?,?,?)",
        (dog_id, str(day), algo_version, payload, state,
         datetime.now().isoformat(timespec="seconds")),
    )


def latest_prescription(c: sqlite3.Connection, dog_id: str) -> dict | None:
    row = c.execute(
        "SELECT payload FROM prescriptions WHERE dog_id=? ORDER BY id DESC LIMIT 1",
        (dog_id,),
    ).fetchone()
    return json.loads(row["payload"]) if row else None


def latest_state(c: sqlite3.Connection, dog_id: str) -> dict:
    """
    직전 처방이 남긴 상태. 히스테리시스와 슬루율 제한이 이걸 이어받는다.
    없으면 빈 상태로 시작한다 - 첫날이라는 뜻이다.
    """
    row = c.execute(
        "SELECT state FROM prescriptions WHERE dog_id=? ORDER BY id DESC LIMIT 1",
        (dog_id,),
    ).fetchone()
    return json.loads(row["state"]) if row else {}


# ---------------------------------------------------------------------------
# 사출 명령
# ---------------------------------------------------------------------------

def queue_command(
    c: sqlite3.Connection, cmd_id: str, dog_id: str,
    scheduled: datetime, expires: datetime, payload: str,
) -> bool:
    """
    멱등키로 중복 적재를 막는다.
    재부팅이나 잡 재실행으로 같은 끼니가 두 번 나가면 그건 사고다.
    """
    cur = c.execute(
        "INSERT OR IGNORE INTO dispense_commands"
        "(id, dog_id, scheduled, payload, expires_at) VALUES(?,?,?,?,?)",
        (cmd_id, dog_id, scheduled.isoformat(), payload, expires.isoformat()),
    )
    return cur.rowcount > 0


def pending_commands(c: sqlite3.Connection, dog_id: str, now: datetime) -> list[dict]:
    """
    만료된 명령은 주지 않는다.
    아침 급여 명령이 저녁에 실행되면 안 된다.
    """
    rows = c.execute(
        "SELECT id, scheduled, payload FROM dispense_commands "
        "WHERE dog_id=? AND state='pending' AND expires_at > ? ORDER BY scheduled",
        (dog_id, now.isoformat()),
    ).fetchall()
    return [
        {"id": r["id"], "scheduled": r["scheduled"], **json.loads(r["payload"])}
        for r in rows
    ]


def ack_command(c: sqlite3.Connection, cmd_id: str, state: str, result: str) -> None:
    c.execute(
        "UPDATE dispense_commands SET state=?, result=? WHERE id=?",
        (state, result, cmd_id),
    )


def expire_stale(c: sqlite3.Connection, now: datetime) -> int:
    """
    실행되지 않은 채 만료된 명령을 정리한다.

    이게 쌓이면 '준 것'과 '먹은 것'의 대조가 틀어져서
    intake_ratio 가 오염되고, 긴급 정지 규칙이 오작동한다.
    """
    cur = c.execute(
        "UPDATE dispense_commands SET state='expired' "
        "WHERE state='pending' AND expires_at <= ?",
        (now.isoformat(),),
    )
    return cur.rowcount
