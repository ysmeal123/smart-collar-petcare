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
    db.init()

    auth = auth_status()
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
