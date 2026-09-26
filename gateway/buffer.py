"""
로컬 버퍼.

인터넷이 끊겨도 목줄 데이터를 잃지 않게 SQLite에 쌓아둔다.
복구되면 순서대로 올리고, 서버가 받았다고 확인한 것만 지운다.

설계상 지켜야 할 것 두 가지.

1. **전송 성공 전에는 지우지 않는다.**
   지우고 나서 전송이 실패하면 그 데이터는 영영 없다.

2. **같은 레코드를 두 번 쌓지 않는다.**
   목줄이 재연결하며 버퍼를 다시 뱉는 게 정상 동작이다.
   기본키로 막는다. 서버도 막지만 여기서 막으면 대역폭을 아낀다.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 아직 못 올린 행동 이벤트
CREATE TABLE IF NOT EXISTS outbox_events (
    boot_id INTEGER NOT NULL,
    seq     INTEGER NOT NULL,
    t_ms    INTEGER NOT NULL,
    type    TEXT NOT NULL,
    conf    REAL NOT NULL,
    dur_s   REAL NOT NULL,
    added   TEXT NOT NULL,
    PRIMARY KEY (boot_id, seq)
);

-- 아직 못 올린 1분 상태 표본
CREATE TABLE IF NOT EXISTS outbox_status (
    boot_id      INTEGER NOT NULL,
    t_ms         INTEGER NOT NULL,
    worn_sec     INTEGER NOT NULL,
    steps        INTEGER NOT NULL,
    battery      INTEGER NOT NULL,
    rest_sec     INTEGER NOT NULL,
    walk_sec     INTEGER NOT NULL,
    run_sec      INTEGER NOT NULL,
    vigorous_sec INTEGER NOT NULL,
    added        TEXT NOT NULL,
    PRIMARY KEY (boot_id, t_ms)
);

-- 서버에서 받아둔 사출 계획.
-- 서버가 죽어도 이것만 있으면 급여를 계속할 수 있다.
CREATE TABLE IF NOT EXISTS plan (
    id         TEXT PRIMARY KEY,
    scheduled  TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    payload    TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending',
    result     TEXT DEFAULT '',
    fetched_at TEXT NOT NULL
);

-- 마지막 서버 동기화 시각 등
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Batch:
    """한 번에 올릴 묶음. boot_id 가 같은 것끼리만 묶는다."""

    boot_id: int
    events: list[dict]
    status: list[dict]

    @property
    def empty(self) -> bool:
        return not self.events and not self.status

    @property
    def max_t_ms(self) -> int:
        """이 묶음에서 가장 늦은 t_ms. 서버가 부팅 시각을 역산하는 기준이 된다."""
        t = [e["t_ms"] for e in self.events] + [s["t_ms"] for s in self.status]
        return max(t) if t else 0


class Buffer:
    def __init__(self, path: str) -> None:
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- 쌓기 ---------------------------------------------------------------

    def add_events(self, boot_id: int, events: list[dict]) -> int:
        """목줄에서 받은 이벤트를 쌓는다. 중복은 기본키가 막는다."""
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            before = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO outbox_events"
                "(boot_id, seq, t_ms, type, conf, dur_s, added) VALUES(?,?,?,?,?,?,?)",
                [
                    (boot_id, e["seq"], e["t_ms"], e["type"],
                     e["conf"], e["dur_s"], now)
                    for e in events
                ],
            )
            return c.total_changes - before

    def add_status(self, boot_id: int, samples: list[dict]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            before = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO outbox_status"
                "(boot_id, t_ms, worn_sec, steps, battery, "
                " rest_sec, walk_sec, run_sec, vigorous_sec, added) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                [
                    (boot_id, s["t_ms"], s["worn_sec"], s.get("steps", 0),
                     s.get("battery", 0), s.get("rest_sec", 0), s.get("walk_sec", 0),
                     s.get("run_sec", 0), s.get("vigorous_sec", 0), now)
                    for s in samples
                ],
            )
            return c.total_changes - before

    # -- 꺼내기 -------------------------------------------------------------

    def next_batch(self, limit: int) -> Batch | None:
        """
        올릴 묶음 하나를 꺼낸다.

        boot_id 가 섞이면 서버가 시각을 역산할 수 없다.
        가장 오래된 boot_id 것부터 하나씩 비운다.
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT boot_id FROM ("
                "  SELECT boot_id, MIN(t_ms) m FROM outbox_events GROUP BY boot_id"
                "  UNION ALL"
                "  SELECT boot_id, MIN(t_ms) m FROM outbox_status GROUP BY boot_id"
                ") ORDER BY boot_id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            boot_id = row["boot_id"]

            evs = c.execute(
                "SELECT seq, t_ms, type, conf, dur_s FROM outbox_events "
                "WHERE boot_id=? ORDER BY t_ms LIMIT ?",
                (boot_id, limit),
            ).fetchall()
            sts = c.execute(
                "SELECT t_ms, worn_sec, steps, battery, rest_sec, "
                "       walk_sec, run_sec, vigorous_sec FROM outbox_status "
                "WHERE boot_id=? ORDER BY t_ms LIMIT ?",
                (boot_id, limit),
            ).fetchall()

        return Batch(
            boot_id=boot_id,
            events=[dict(r) for r in evs],
            status=[dict(r) for r in sts],
        )

    def drop(self, batch: Batch) -> None:
        """서버가 받았다고 확인한 뒤에만 부른다."""
        with self._conn() as c:
            if batch.events:
                c.executemany(
                    "DELETE FROM outbox_events WHERE boot_id=? AND seq=?",
                    [(batch.boot_id, e["seq"]) for e in batch.events],
                )
            if batch.status:
                c.executemany(
                    "DELETE FROM outbox_status WHERE boot_id=? AND t_ms=?",
                    [(batch.boot_id, s["t_ms"]) for s in batch.status],
                )

    def prune(self, keep_days: int) -> int:
        """
        너무 오래된 미전송 데이터는 버린다.

        몇 주치를 쌓아두면 SD 카드가 찬다. 그리고 그만큼 오래된 데이터는
        baseline 계산에도 의미가 없다.
        """
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat(timespec="seconds")
        with self._conn() as c:
            before = c.total_changes
            c.execute("DELETE FROM outbox_events WHERE added < ?", (cutoff,))
            c.execute("DELETE FROM outbox_status WHERE added < ?", (cutoff,))
            return c.total_changes - before

    def pending(self) -> tuple[int, int]:
        with self._conn() as c:
            e = c.execute("SELECT COUNT(*) n FROM outbox_events").fetchone()["n"]
            s = c.execute("SELECT COUNT(*) n FROM outbox_status").fetchone()["n"]
        return e, s

    # -- 사출 계획 ----------------------------------------------------------

    def save_plan(self, commands: list[dict]) -> int:
        """
        서버에서 받은 사출 명령을 저장한다.

        이미 실행한 명령은 덮어쓰지 않는다. 서버가 같은 명령을 다시 내려도
        두 번 사출하면 안 된다 - 이중 급여는 사고다.
        """
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            before = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO plan"
                "(id, scheduled, expires_at, payload, fetched_at) VALUES(?,?,?,?,?)",
                [
                    (cmd["id"], cmd["scheduled"], cmd.get("expires_at", ""),
                     json.dumps(cmd, ensure_ascii=False), now)
                    for cmd in commands
                ],
            )
            return c.total_changes - before

    def due_commands(self, now: datetime) -> list[dict]:
        """지금 실행해야 할 명령. 아직 시각이 안 됐거나 이미 한 건 제외한다."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, payload FROM plan "
                "WHERE state='pending' AND scheduled <= ? ORDER BY scheduled",
                (now.isoformat(),),
            ).fetchall()
        return [{"id": r["id"], **json.loads(r["payload"])} for r in rows]

    def mark(self, cmd_id: str, state: str, result: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE plan SET state=?, result=? WHERE id=?", (state, result, cmd_id)
            )

    def unacked(self) -> list[dict]:
        """실행은 했는데 서버에 아직 보고 못 한 것들."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, state, result FROM plan "
                "WHERE state IN ('done','failed','skipped')"
            ).fetchall()
        return [dict(r) for r in rows]

    def forget(self, cmd_id: str) -> None:
        """서버에 보고까지 끝난 명령을 지운다."""
        with self._conn() as c:
            c.execute("DELETE FROM plan WHERE id=?", (cmd_id,))

    def plan_age(self, now: datetime) -> timedelta | None:
        """가장 최근에 서버에서 계획을 받아온 뒤 얼마나 지났는가."""
        with self._conn() as c:
            row = c.execute("SELECT MAX(fetched_at) f FROM plan").fetchone()
        if not row or not row["f"]:
            return None
        return now - datetime.fromisoformat(row["f"])

    # -- 메타 ---------------------------------------------------------------

    def put(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
