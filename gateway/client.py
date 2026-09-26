"""
서버 통신.

의존성을 쓰지 않는다. 라즈베리파이에 pip install 없이 그대로 올라간다.

**시계가 이 파일의 핵심이다.**

라즈베리파이에는 RTC가 없다. 전원이 끊겼다 들어오면 NTP를 받기 전까지
현재 시각을 모르고, 보통 1970년이거나 마지막 종료 시각이다.

서버는 부팅 시각을 이렇게 역산한다.

    목줄 부팅시각 = 게이트웨이 수신시각 - uptime

즉 게이트웨이 시각이 틀리면 **목줄 데이터가 통째로 엉뚱한 날짜에 꽂힌다.**
하루치가 1970년으로 가면 baseline이 오염되고 복구가 어렵다.

그래서 시각이 확실해지기 전에는 업로드하지 않는다. 버퍼에 쌓아두면 된다.
어차피 인터넷이 없으면 업로드도 못 한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# 이 연도보다 이전이면 시계가 초기화된 것이다
SANE_YEAR = 2025

# systemd-timesyncd 가 동기화에 성공하면 만드는 파일
TIMESYNC_FLAG = Path("/run/systemd/timesync/synchronized")


class ServerDown(Exception):
    """서버에 닿지 못했다. 버퍼에 쌓아두고 나중에 다시 시도한다."""


class ClockNotReady(Exception):
    """시각이 확정되지 않았다. 지금 올리면 데이터가 엉뚱한 날짜에 꽂힌다."""


class BadPayload(Exception):
    """
    서버가 형식이 틀렸다고 거부했다. 재시도해도 영원히 실패한다.

    이걸 ServerDown 과 구분하지 않으면 잘못된 레코드 하나가 큐를 영구히 막고,
    그 뒤에 쌓인 멀쩡한 데이터까지 전부 못 올라간다.
    """


def clock_is_trustworthy() -> tuple[bool, str]:
    """
    이 기기의 시각을 믿어도 되는가.

    세 가지를 본다. 하나라도 확실하면 통과시킨다 -
    라즈베리파이가 아닌 환경(개발 PC, 도커)에서도 돌아가야 하기 때문이다.
    """
    now = datetime.now()

    # 1. 명백히 초기화된 시계
    if now.year < SANE_YEAR:
        return False, f"시계가 {now.year}년을 가리킨다 — NTP 동기화 전"

    # 2. systemd-timesyncd 플래그
    if TIMESYNC_FLAG.exists():
        return True, "NTP 동기화 확인"

    # 3. timedatectl
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            if out.stdout.strip() == "yes":
                return True, "NTP 동기화 확인"
            return False, "NTP 미동기화 — 업로드를 미룬다"
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    # timedatectl 이 없는 환경(개발 PC 등). 연도가 정상이면 믿는다.
    return True, "시계 확인 생략 (systemd 아님)"


def _request(url: str, payload: dict | None, timeout: int, method: str = "POST") -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        # 408(타임아웃)과 429(과부하)는 기다리면 되는 것이라 재시도 대상이다.
        if 400 <= e.code < 500 and e.code not in (408, 429):
            raise BadPayload(f"HTTP {e.code}: {detail}") from e
        raise ServerDown(f"HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ServerDown(str(e)) from e


class Client:
    def __init__(self, cfg) -> None:
        self.cfg = cfg

    # -- 업로드 -------------------------------------------------------------

    def upload(self, batch) -> dict:
        """
        목줄 데이터를 올린다.

        received_at 은 **게이트웨이의 현재 시각**이고, uptime_ms 는
        이 묶음에서 가장 늦은 t_ms 다. 서버가 이 둘로 부팅 시각을 역산한다.
        """
        ok, why = clock_is_trustworthy()
        if not ok:
            raise ClockNotReady(why)

        payload = {
            "collar_serial": self.cfg.collar_serial,
            "boot_id": batch.boot_id,
            "uptime_ms": batch.max_t_ms,
            "received_at": datetime.now().isoformat(),
            "fw_version": self.cfg.fw_version,
            "events": batch.events,
            "status": batch.status,
        }
        return _request(self.cfg.url("/v1/ingest"), payload, self.cfg.timeout_sec)

    def send_weight(self, sessions: list[dict]) -> dict:
        """급식판 체중계 세션."""
        return _request(
            self.cfg.url(f"/v1/feeder/{self.cfg.dog_id}/weight"),
            {"sessions": sessions}, self.cfg.timeout_sec,
        )

    # -- 사출 명령 ----------------------------------------------------------

    def fetch_commands(self) -> list[dict]:
        r = _request(
            self.cfg.url(f"/v1/feeder/{self.cfg.dog_id}/commands"),
            None, self.cfg.timeout_sec, method="GET",
        )
        return r.get("commands", [])

    def ack(self, cmd_id: str, state: str, result: str = "") -> dict:
        from urllib.parse import quote, urlencode

        qs = urlencode({"state": state, "result": result})
        return _request(
            self.cfg.url(f"/v1/feeder/commands/{quote(cmd_id, safe='')}/ack?{qs}"),
            None, self.cfg.timeout_sec,
        )

    def health(self) -> bool:
        try:
            _request(self.cfg.url("/health"), None, 5, method="GET")
            return True
        except ServerDown:
            return False
