"""
게이트웨이 설정.

라즈베리파이에 그대로 올려 쓴다. 환경변수로 덮을 수 있다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _int(key: str, default: int) -> int:
    return int(os.environ.get(key, default))


@dataclass
class Config:
    # --- 서버 ---
    server: str = field(default_factory=lambda: _env("PEBBLE_SERVER", "http://localhost:8000"))
    timeout_sec: int = field(default_factory=lambda: _int("PEBBLE_TIMEOUT", 10))

    # --- 기기 식별 ---
    collar_serial: str = field(default_factory=lambda: _env("PEBBLE_COLLAR", "PBL-0001"))
    dog_id: str = field(default_factory=lambda: _env("PEBBLE_DOG", "dog_choco"))
    fw_version: str = field(default_factory=lambda: _env("PEBBLE_FW", "0.1.0"))

    # --- 주기(초) ---
    poll_collar_sec: int = 10        # 목줄에서 읽어오는 주기
    upload_sec: int = 300            # 서버로 올리는 주기
    command_sec: int = 60            # 사출 명령 확인 주기

    # --- 버퍼 ---
    db_path: str = field(default_factory=lambda: _env("PEBBLE_DB", "gateway.db"))
    upload_batch: int = 2000         # 한 번에 올릴 최대 레코드 수
    keep_days: int = 7               # 전송 못 한 데이터 보관 기간

    # --- 폴백 ---
    #
    # 서버와 이만큼 연락이 끊기면 캐시된 계획을 버리고
    # 기본 급여로 내려간다. 오래된 처방을 무한정 반복하는 것이
    # 안 주는 것보다 위험할 수 있기 때문이다.
    plan_max_age_days: int = 7

    def url(self, path: str) -> str:
        return f"{self.server.rstrip('/')}{path}"


CONFIG = Config()
