"""
FastAPI 엔드포인트.

알고리즘 코어를 감싸는 얇은 어댑터일 뿐이다.
비즈니스 로직은 전부 core/에 있고 여기엔 두지 않는다.
그래야 서버 없이도 코어를 단독 실행·테스트할 수 있다.

실행:
    cd petcare
    uvicorn api.main:app --reload
"""

from __future__ import annotations

# .env 를 **가장 먼저** 올린다.
# api.auth 는 import 시점에 os.environ 을 읽으므로, 그 뒤에 로드하면
# 토큰을 설정했는데도 인증이 꺼진 채로 뜬다.
from config import load_env

_ENV_FILES = load_env()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from core.constants import CARTRIDGES, FOODS
from core.inference import PrescriptionState
from core.models import DailySummary, DogProfile, Prescription
from core.prescribe import prescribe
from api.auth import status as auth_status
from api.ingest import router as device_router
from store import db
from store.dialect import backend

app = FastAPI(
    title="Pet Nutrition API",
    description="스마트 목줄 행동 데이터 기반 사료·영양제 처방",
    version="0.1.0",
)

# 기기(목줄·밥통) 및 실데이터 엔드포인트
app.include_router(device_router)


@app.on_event("startup")
def _startup() -> None:
    """
    스키마를 확인하고 설정 상태를 로그로 남긴다.

    배포 환경에서 인증이 꺼진 채로 떠 있는 것을 눈치채지 못하면
    공개 인터넷에 무방비로 노출된다. 시작할 때 크게 알린다.
    """
    import config

    _init_db_with_retry()

    auth = auth_status()
    for path in _ENV_FILES:
        print(f"[pebble] .env 읽음: {path}", flush=True)
    print(f"[pebble] 저장소: {backend()}", flush=True)
    if auth["enabled"]:
        print(
            f"[pebble] 인증: 기기={auth['device_token']} 앱={auth['app_token']}",
            flush=True,
        )
    else:
        print(
            "[pebble] ⚠ 인증이 꺼져 있습니다. "
            "공개 배포라면 PEBBLE_DEVICE_TOKEN / PEBBLE_APP_TOKEN 을 설정하세요.",
            flush=True,
        )

    # 값은 찍지 않는다. 켜졌는지만.
    for line in config.summary():
        print(line, flush=True)

    _seed_if_asked()


def _init_db_with_retry(attempts: int = 6, wait_s: float = 5.0) -> None:
    """
    스키마를 만든다. DB 가 아직 안 깨어났으면 기다렸다 다시 한다.

    첫 배포에서는 웹 서비스가 DB 보다 먼저 뜨는 경합이 흔하다.
    그때 바로 죽으면 "배포 실패" 로 보이고, 원인이 설정 오류인지
    단순한 순서 문제인지 구분이 안 된다.

    끝내 안 되면 그때는 **죽는 게 맞다.** DB 없이 뜬 서버는 모든 요청에서
    500 을 내고, 그게 더 찾기 어렵다.
    """
    import time

    for i in range(1, attempts + 1):
        try:
            db.init()
            if i > 1:
                print(f"[pebble] DB 연결 성공 ({i}번째 시도)", flush=True)
            return
        except Exception as e:                               # noqa: BLE001
            if i == attempts:
                print(
                    f"[pebble] ✗ DB 연결 실패 ({type(e).__name__}). "
                    "DATABASE_URL 과 DB 리전을 확인하세요 — "
                    "내부 주소는 같은 리전에서만 풀립니다.",
                    flush=True,
                )
                raise
            print(
                f"[pebble] DB 준비 대기 {i}/{attempts} "
                f"({type(e).__name__}) — {wait_s:.0f}초 뒤 재시도",
                flush=True,
            )
            time.sleep(wait_s)


def _seed_if_asked() -> None:
    """
    PEBBLE_SEED=1 이면 비어 있는 DB 에 시연용 데이터를 넣는다.

    **무료 플랜에는 Shell 접속이 없다.** `python -m seed_remote` 를 손으로
    돌릴 방법이 없어서, 시작 훅에서 대신 한다.

    **이미 데이터가 있으면 아무것도 하지 않는다.** 무료 인스턴스는 15분마다
    잠들고 깨므로 이 훅이 수시로 돈다. 매번 덮어쓰면 그동안 쌓인 실데이터가
    날아간다 - seed_remote 가 개체 수를 먼저 세는 이유다.

    시드가 실패해도 서버는 뜬다. 데이터가 없는 건 고칠 수 있지만,
    서버가 안 뜨면 고칠 방법이 없다.
    """
    import os

    if os.environ.get("PEBBLE_SEED", "").strip() not in ("1", "true", "yes"):
        return

    try:
        from seed_remote import main as seed
        seed()
    except Exception as e:                                   # noqa: BLE001
        print(f"[pebble] ⚠ 시드 실패 ({type(e).__name__}: {e}) — 서버는 계속 뜹니다",
              flush=True)


class PrescribeRequest(BaseModel):
    profile: DogProfile
    days: list[DailySummary] = Field(
        min_length=31,
        description="일별 요약. baseline 30일 + 관찰 최소 14일이 필요하다",
    )
    state: PrescriptionState | None = None


class PrescribeResponse(BaseModel):
    prescription: Prescription
    state: PrescriptionState = Field(description="다음 호출에 그대로 넘기면 이어서 판단한다")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/cartridges")
def cartridges() -> dict:
    """디스펜서에 꽂힌 카트리지 목록. 앱이 색상·이름을 가져간다."""
    return {"cartridges": CARTRIDGES, "foods": FOODS}


@app.post("/prescribe", response_model=PrescribeResponse)
def create_prescription(req: PrescribeRequest) -> PrescribeResponse:
    """오늘의 사료량과 영양제 배합을 계산한다."""
    if len(req.days) < 44:
        raise HTTPException(
            422,
            "관찰 데이터가 부족합니다. baseline 30일 + 관찰 14일 = 최소 44일이 필요합니다.",
        )
    rx, state = prescribe(req.profile, req.days, req.state)
    return PrescribeResponse(prescription=rx, state=state)


@app.get("/demo/{scenario}", response_model=PrescribeResponse)
def demo(scenario: str) -> PrescribeResponse:
    """
    Mock 데이터로 바로 돌려보는 데모 엔드포인트.

    앱 개발 중에 실제 목줄 없이 화면을 붙여볼 수 있게 열어둔다.
    """
    from mock.generator import SCENARIOS, generate

    if scenario not in SCENARIOS:
        raise HTTPException(404, f"알 수 없는 시나리오입니다. 가능: {SCENARIOS}")

    ds = generate(scenario)
    rx, state = prescribe(ds.profile, ds.days)
    return PrescribeResponse(prescription=rx, state=state)


@app.get("/demo/{scenario}/dashboard")
def demo_dashboard(scenario: str) -> dict:
    """앱 대시보드가 한 번에 받아갈 묶음 (프로필 + 최근 7일 + 처방)."""
    from mock.generator import SCENARIOS, generate

    if scenario not in SCENARIOS:
        raise HTTPException(404, f"알 수 없는 시나리오입니다. 가능: {SCENARIOS}")

    ds = generate(scenario)
    rx, _ = prescribe(ds.profile, ds.days)
    return {
        "profile": ds.profile,
        "recent_days": ds.days[-7:],     # 차트용
        "prescription": rx,
    }
