"""
목줄에서 올라오는 원격 측정 데이터의 계약.

목줄은 '언제 무엇을 얼마나 했다'만 말한다. 나머지 지표는 서버가 파생시킨다.
분류는 보드에서 끝내고 분류 결과만 올린다 - 원시 IMU 파형을 올리면
전력과 대역폭이 감당이 안 된다.

시각 처리가 이 파일의 핵심이다.
nRF52840에는 RTC 백업 배터리가 없어서 재부팅하면 현재 시각을 모른다.
그래서 목줄은 '부팅 후 경과 ms'만 찍고, 게이트웨이가 수신 시각을 기준으로
실시각으로 환산한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from core.models import BehaviorEvent, BehaviorType


# ---------------------------------------------------------------------------
# 목줄 -> 게이트웨이 (BLE)
# ---------------------------------------------------------------------------

class CollarEvent(BaseModel):
    """
    짧고 드문 행동 하나. BLE로는 12바이트 고정 레코드로 보낸다.

        uint32 t_ms      부팅 후 경과 ms
        uint8  type      1=scratch 2=head_shake 3=body_shake 4=posture_change
        uint8  conf      0~255  (255로 나누면 0~1)
        uint16 dur_ds    지속시간, 0.1초 단위
        uint32 seq       이벤트 일련번호

    연속 활동은 여기 없다 - CollarStatus 의 1분 요약으로 올라간다.
    이벤트는 하루 수백~1천 건이라 12KB 정도다. BLE로 충분하다.
    """

    seq: int = Field(ge=0, description="부팅 후 단조 증가. 중복 제거의 키")
    t_ms: int = Field(ge=0, description="부팅 후 경과 밀리초")
    type: BehaviorType
    conf: float = Field(ge=0.0, le=1.0)
    dur_s: float = Field(gt=0)


class CollarStatus(BaseModel):
    """
    1분마다 올리는 요약. Model A 결과와 기기 상태를 함께 담는다.

    **활동을 이벤트로 보내지 않는 이유.**
    Model A는 1~2초 창마다 분류하므로 하루 4만 회가 넘는다.
    이걸 이벤트로 올리면 대역폭도 배터리도 감당이 안 된다.
    목줄이 1분 동안 각 클래스에 머문 초를 세어 요약만 올린다.

    **worn_sec 이 반드시 필요한 이유.**
    이벤트만으로는 '안 움직인 것'과 '목줄을 안 찬 것'을 구분할 수 없어
    착용률을 역산할 방법이 없다. 착용률 60% 미만인 날은 통계에서 빼야
    하는데 그 판단이 불가능해진다.
    """

    t_ms: int = Field(ge=0)
    worn_sec: int = Field(ge=0, le=60, description="직전 1분 중 착용 상태였던 초")
    steps: int = Field(ge=0, description="직전 1분 걸음 수. 보수계가 직접 센다")
    battery: int = Field(ge=0, le=100)

    # --- Model A 출력. 네 값의 합이 60을 넘지 않는다 ---
    rest_sec: int = Field(default=0, ge=0, le=60)
    walk_sec: int = Field(default=0, ge=0, le=60)
    run_sec: int = Field(default=0, ge=0, le=60)
    vigorous_sec: int = Field(default=0, ge=0, le=60)

    @property
    def active_sec(self) -> int:
        """휴식을 뺀 활동 초."""
        return self.walk_sec + self.run_sec + self.vigorous_sec


# ---------------------------------------------------------------------------
# 게이트웨이 -> 서버 (HTTPS)
# ---------------------------------------------------------------------------

class IngestBatch(BaseModel):
    """
    게이트웨이가 한 번에 올리는 묶음.

    BLE가 끊기면 목줄이 플래시에 쌓아뒀다가 재연결 시 몰아서 보낸다.
    그래서 과거 데이터가 뒤늦게, 순서가 뒤바뀐 채로 도착하는 게 기본이다.
    (seq, boot_id) 조합으로 중복을 걸러낸다.
    """

    collar_serial: str
    boot_id: int = Field(description="부팅할 때마다 증가. t_ms의 기준점을 식별한다")
    uptime_ms: int = Field(ge=0, description="게이트웨이가 이 묶음을 만든 시점의 t_ms")
    received_at: datetime = Field(description="게이트웨이의 실시각. 시계 환산의 기준")
    fw_version: str = ""

    events: list[CollarEvent] = Field(default_factory=list)
    status: list[CollarStatus] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 시계 환산
# ---------------------------------------------------------------------------

def resolve_clock(batch: IngestBatch) -> datetime:
    """
    부팅 시점의 실시각을 역산한다.

        부팅시각 = 게이트웨이 수신시각 - 그 시점의 uptime

    게이트웨이는 Wi-Fi로 NTP를 받으므로 실시각을 안다.
    목줄의 크리스탈 드리프트(보통 ±20ppm, 하루 약 2초)는 무시한다.
    시간대별 집계가 목적이라 초 단위 오차는 영향이 없다.
    """
    return batch.received_at - timedelta(milliseconds=batch.uptime_ms)


def to_behavior_events(batch: IngestBatch) -> list[BehaviorEvent]:
    """목줄 이벤트를 알고리즘이 쓰는 형태로 바꾼다."""
    boot_at = resolve_clock(batch)
    return [
        BehaviorEvent(
            ts=boot_at + timedelta(milliseconds=e.t_ms),
            type=e.type,
            confidence=e.conf,
            duration_s=e.dur_s,
        )
        for e in batch.events
    ]


#: 상태 표본 하나가 설명하는 시간(초). 목줄은 1분마다 찍는다.
SAMPLE_SPAN_SEC = 60


def wear_seconds_by_hour(batch: IngestBatch) -> dict[datetime, tuple[int, int]]:
    """
    상태 표본을 '시각 -> (착용 초, 표본이 덮은 초)' 로 편다.

    덮은 초를 따로 세는 게 핵심이다. 표본 하나는 1분만 설명하므로,
    한 시간에 표본이 10개 왔다면 그 시간의 10분만 판단 근거가 있는 것이다.
    분모를 무조건 3600으로 두면 기기가 잠깐 꺼져 있던 시간까지
    '안 찼다'로 계산되어 착용률이 부당하게 낮아진다.

    키는 시(hour) 단위로 자른 datetime이다. 하루 경계를 넘는 묶음도 그대로 처리된다.
    """
    boot_at = resolve_clock(batch)
    out: dict[datetime, tuple[int, int]] = {}
    for s in batch.status:
        ts = boot_at + timedelta(milliseconds=s.t_ms)
        key = ts.replace(minute=0, second=0, microsecond=0)
        worn, covered = out.get(key, (0, 0))
        out[key] = (worn + s.worn_sec, covered + SAMPLE_SPAN_SEC)
    return out


def resolved_status(batch: IngestBatch) -> list[tuple[CollarStatus, datetime]]:
    """상태 표본에 실시각을 붙여서 돌려준다. 저장은 표본 단위로 한다."""
    boot_at = resolve_clock(batch)
    return [
        (s, boot_at + timedelta(milliseconds=s.t_ms)) for s in batch.status
    ]


def steps_by_hour(batch: IngestBatch) -> dict[datetime, int]:
    """상태 표본의 걸음 수를 시간대별로 모은다."""
    boot_at = resolve_clock(batch)
    out: dict[datetime, int] = {}
    for s in batch.status:
        ts = boot_at + timedelta(milliseconds=s.t_ms)
        key = ts.replace(minute=0, second=0, microsecond=0)
        out[key] = out.get(key, 0) + s.steps
    return out


def activity_by_hour(batch: IngestBatch) -> dict[datetime, dict[str, int]]:
    """
    Model A 요약을 시간대별로 모은다.

    반환: 시각 -> {walk_sec, run_sec, vigorous_sec, rest_sec}
    """
    boot_at = resolve_clock(batch)
    out: dict[datetime, dict[str, int]] = {}
    for s in batch.status:
        ts = boot_at + timedelta(milliseconds=s.t_ms)
        key = ts.replace(minute=0, second=0, microsecond=0)
        acc = out.setdefault(
            key, {"walk_sec": 0, "run_sec": 0, "vigorous_sec": 0, "rest_sec": 0}
        )
        acc["walk_sec"] += s.walk_sec
        acc["run_sec"] += s.run_sec
        acc["vigorous_sec"] += s.vigorous_sec
        acc["rest_sec"] += s.rest_sec
    return out


def dedupe(
    batch: IngestBatch, seen: set[tuple[int, int]]
) -> tuple[list[CollarEvent], int]:
    """
    (boot_id, seq) 로 중복을 제거한다.

    게이트웨이 재시도나 목줄 버퍼 재전송으로 같은 이벤트가 여러 번 온다.
    이걸 막지 않으면 긁은 횟수가 부풀려져 없는 이상을 만들어낸다.

    반환: (새 이벤트, 버린 개수)
    """
    fresh: list[CollarEvent] = []
    dropped = 0
    for e in batch.events:
        key = (batch.boot_id, e.seq)
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        fresh.append(e)
    return fresh, dropped
