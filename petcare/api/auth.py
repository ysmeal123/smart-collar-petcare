"""
인증.

**공개 인터넷에 올리는 순간 필요해진다.**
인증이 없으면 누구나 남의 목줄로 가짜 이벤트를 밀어넣을 수 있고,
그러면 엉뚱한 영양제가 처방된다. 조용하고 위험한 공격이다.

두 종류의 주체가 있고 권한이 다르다.

    기기 토큰    밥통(게이트웨이). 데이터를 올리고 사출 명령을 받아간다
    앱 토큰      보호자 앱. 대시보드를 읽고 답변을 올린다

기기가 사용자 토큰을 들고 있으면 안 된다. 밥통은 물리적으로 접근 가능한
기기라 토큰이 유출될 수 있고, 그때 피해 범위를 기기 하나로 묶어야 한다.

**이건 캡스톤 수준의 최소 방어다.** 상용이라면 기기별 발급·회전·폐기와
사용자 계정(OAuth)이 필요하다. 그건 MVP_SCOPE.md 에 적어 뒀다.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

#: 토큰을 하나도 설정하지 않으면 인증을 끈다.
#: 로컬 개발에서 매번 헤더를 붙이는 건 번거롭고, 로컬은 공개돼 있지 않다.
#: 배포 환경에서는 반드시 설정해야 한다 - 안 하면 시작할 때 경고한다.
DEVICE_TOKEN = os.environ.get("PEBBLE_DEVICE_TOKEN", "").strip()
APP_TOKEN = os.environ.get("PEBBLE_APP_TOKEN", "").strip()

ENABLED = bool(DEVICE_TOKEN or APP_TOKEN)


def _matches(given: str | None, expected: str) -> bool:
    """
    타이밍 공격을 피하려고 상수 시간 비교를 쓴다.

    토큰 길이가 짧아 실익이 크진 않지만, 비교 방식을 틀리게 배우면
    나중에 더 중요한 곳에서 같은 실수를 한다.
    """
    if not expected:
        return True            # 설정 안 함 = 검사 안 함
    if not given:
        return False
    if given.lower().startswith("bearer "):
        given = given[7:]
    return hmac.compare_digest(given.strip(), expected)


def require_device(authorization: str | None = Header(default=None)) -> None:
    """밥통(게이트웨이) 전용 엔드포인트."""
    if not _matches(authorization, DEVICE_TOKEN):
        raise HTTPException(401, "기기 토큰이 필요합니다")


def require_app(authorization: str | None = Header(default=None)) -> None:
    """앱 전용 엔드포인트."""
    if not _matches(authorization, APP_TOKEN):
        raise HTTPException(401, "앱 토큰이 필요합니다")


def status() -> dict:
    """시작할 때 로그로 남긴다. 켜져 있는지 눈으로 확인할 수 있어야 한다."""
    return {
        "enabled": ENABLED,
        "device_token": bool(DEVICE_TOKEN),
        "app_token": bool(APP_TOKEN),
    }
