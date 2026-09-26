"""
목줄 -> 서버 계약 검증.

여기서 지키려는 것은 "데이터를 받았다"가 아니라
"실기기에서 반드시 생기는 상황을 견딘다"이다.
    - 목줄에 RTC가 없어 시각을 모른다
    - BLE가 끊겨 과거 데이터가 몰려서 뒤늦게 온다
    - 같은 이벤트가 두 번 온다
    - 목줄을 벗겨 놓았다
"""

from __future__ import annotations

from datetime import datetime, timedelta

from core.aggregate import build_daily_summary, wear_ratio_from_seconds
from core.constants import CONFIDENCE_GATE
from core.models import BehaviorType
from core.telemetry import (
    CollarEvent,
    CollarStatus,
    IngestBatch,
    dedupe,
    resolve_clock,
    steps_by_hour,
    to_behavior_events,
    wear_seconds_by_hour,
)

DAY = datetime(2026, 9, 26, 0, 0, 0)
HOUR_MS = 3_600_000


def ev(seq: int, hour: float, kind: BehaviorType, conf: float = 0.9, dur: float = 2.0):
    return CollarEvent(seq=seq, t_ms=int(hour * HOUR_MS), type=kind, conf=conf, dur_s=dur)


def batch(events, status=None, boot_id: int = 1, uptime_h: float = 24.0) -> IngestBatch:
    """부팅 시각이 DAY 자정이 되도록 수신 시각을 역산해 만든다."""
    return IngestBatch(
        collar_serial="PBL-0001",
        boot_id=boot_id,
        uptime_ms=int(uptime_h * HOUR_MS),
        received_at=DAY + timedelta(hours=uptime_h),
        fw_version="0.1.0",
        events=events,
        status=status or [],
    )


# ---------------------------------------------------------------------------
# 시계
# ---------------------------------------------------------------------------

def test_부팅_시각을_역산한다():
    """목줄은 부팅 후 경과 ms만 안다. 실시각은 게이트웨이가 만들어준다."""
    assert resolve_clock(batch([])) == DAY


def test_이벤트가_실시각으로_환산된다():
    events = to_behavior_events(batch([ev(1, 23.5, BehaviorType.SCRATCH)]))
    assert events[0].ts == DAY + timedelta(hours=23.5)
    assert events[0].ts.hour == 23


def test_재부팅하면_같은_t_ms라도_다른_시각이_된다():
    """
    t_ms는 부팅마다 0으로 돌아간다. 같은 값이라도 언제 부팅했느냐에 따라
    실시각이 달라진다. t_ms를 그대로 시각으로 믿으면 안 되는 이유다.
    """
    def one(boot_id: int, received_h: float) -> IngestBatch:
        return IngestBatch(
            collar_serial="PBL-0001",
            boot_id=boot_id,
            uptime_ms=int(2 * HOUR_MS),
            received_at=DAY + timedelta(hours=received_h),
            events=[ev(1, 1.0, BehaviorType.SCRATCH)],   # 둘 다 부팅 1시간째
        )

    # 자정에 부팅 -> 01시 / 정오에 부팅 -> 13시
    assert to_behavior_events(one(1, 2.0))[0].ts.hour == 1
    assert to_behavior_events(one(2, 14.0))[0].ts.hour == 13


# ---------------------------------------------------------------------------
# 중복 · 재전송
# ---------------------------------------------------------------------------

def test_같은_이벤트가_두_번_오면_한_번만_센다():
    """
    게이트웨이 재시도나 목줄 버퍼 재전송으로 중복이 온다.
    막지 않으면 긁은 횟수가 부풀려져 없는 이상을 만들어낸다.
    """
    seen: set[tuple[int, int]] = set()
    first = batch([ev(1, 1, BehaviorType.SCRATCH), ev(2, 2, BehaviorType.SCRATCH)])

    fresh, dropped = dedupe(first, seen)
    assert len(fresh) == 2 and dropped == 0

    fresh, dropped = dedupe(first, seen)       # 같은 묶음이 다시 도착
    assert len(fresh) == 0 and dropped == 2


def test_재부팅_후_seq가_겹쳐도_구분한다():
    """seq는 부팅마다 0으로 돌아간다. boot_id까지 봐야 한다."""
    seen: set[tuple[int, int]] = set()
    dedupe(batch([ev(1, 1, BehaviorType.SCRATCH)], boot_id=1), seen)
    fresh, dropped = dedupe(batch([ev(1, 1, BehaviorType.SCRATCH)], boot_id=2), seen)
    assert len(fresh) == 1 and dropped == 0


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------

def test_밤에_긁은_것만_따로_센다():
    """피부 축의 핵심 지표다. 22~04시 것만 골라내야 한다."""
    events = to_behavior_events(batch([
        ev(1, 23.0, BehaviorType.SCRATCH),   # 밤
        ev(2, 2.0, BehaviorType.SCRATCH),    # 밤
        ev(3, 14.0, BehaviorType.SCRATCH),   # 낮
    ]))
    d = build_daily_summary(DAY.date(), events, wear_ratio=1.0)

    assert d.summary.scratch_total == 3
    assert d.summary.scratch_night == 2


def test_활동은_초로_보존된다():
    """
    분으로 반올림해 저장하면 하루 2~5분만 뛰는 개의 신호가 통째로 사라진다.
    알고리즘은 초를 쓰고 화면만 분을 쓴다.
    """
    events = to_behavior_events(batch([
        ev(1, 10.0, BehaviorType.RUN, dur=150.0),
        ev(2, 11.0, BehaviorType.WALK, dur=600.0),
    ]))
    d = build_daily_summary(DAY.date(), events, wear_ratio=1.0)

    assert d.summary.run_sec == 150
    assert d.summary.walk_sec == 600
    assert d.summary.run_min == 2          # 화면용은 버림
    assert d.hourly.activity_sec[10] == 150
    assert d.hourly.activity_sec[11] == 600


def test_신뢰도_미달_이벤트는_버리고_비율을_남긴다():
    """버린 비율이 높은 날은 분류기가 헤맸다는 뜻이라 그 날 자체를 의심해야 한다."""
    low = CONFIDENCE_GATE - 0.1
    events = to_behavior_events(batch([
        ev(1, 23.0, BehaviorType.SCRATCH, conf=0.95),
        ev(2, 23.0, BehaviorType.SCRATCH, conf=low),
        ev(3, 23.0, BehaviorType.SCRATCH, conf=low),
        ev(4, 23.0, BehaviorType.SCRATCH, conf=0.95),
    ]))
    d = build_daily_summary(DAY.date(), events, wear_ratio=1.0)

    assert d.summary.scratch_total == 2
    assert d.low_confidence_ratio == 0.5


def test_보수계_값이_있으면_추정치_대신_쓴다():
    events = to_behavior_events(batch([ev(1, 10.0, BehaviorType.WALK, dur=600.0)]))

    derived = build_daily_summary(DAY.date(), events, wear_ratio=1.0)
    measured = build_daily_summary(DAY.date(), events, wear_ratio=1.0, steps=1234)

    assert derived.summary.steps == int(600 * 1.2)
    assert measured.summary.steps == 1234


# ---------------------------------------------------------------------------
# 착용
# ---------------------------------------------------------------------------

def test_착용률은_이벤트가_아니라_상태_표본에서_나온다():
    """
    이벤트만으로는 '안 움직인 것'과 '목줄을 안 찬 것'을 구분할 수 없다.
    목줄이 착용 신호를 따로 보내야 착용률 게이트가 작동한다.
    """
    status = [
        CollarStatus(t_ms=h * HOUR_MS, worn_sec=60 if h < 12 else 0, steps=0, battery=80)
        for h in range(24)
    ]
    b = batch([], status=status)
    per_hour = wear_seconds_by_hour(b)

    worn_3, covered_3 = per_hour[DAY + timedelta(hours=3)]
    worn_20, covered_20 = per_hour[DAY + timedelta(hours=20)]
    assert worn_3 == 60 and covered_3 == 60      # 찼다
    assert worn_20 == 0 and covered_20 == 60     # 안 찼다 (표본은 도착했다)


def test_착용률_미달인_날은_통계에서_빠진다():
    """착용률 60% 미만이면 DailySummary.valid 가 False 가 된다."""
    worn = build_daily_summary(DAY.date(), [], wear_ratio=0.95)
    off = build_daily_summary(DAY.date(), [], wear_ratio=0.30)

    assert worn.valid is True
    assert off.valid is False


def test_기기가_꺼져_있던_시간은_미착용으로_치지_않는다():
    """
    표본이 도착한 시간만 분모로 잡는다.
    기기 문제로 데이터가 없던 시간까지 '안 찼다'로 치면 착용률이 부당하게 낮아진다.
    """
    # 12시간치 표본만 도착했고 그동안 전부 착용 상태였다 -> 100%
    assert wear_ratio_from_seconds(12 * 3600, covered_sec=12 * 3600) == 1.0
    # 24시간치 표본이 왔는데 절반만 착용 -> 50%
    assert wear_ratio_from_seconds(12 * 3600, covered_sec=24 * 3600) == 0.5
    # 표본이 아예 없으면 판단 불가 -> 0 (그 날은 통계에서 빠진다)
    assert wear_ratio_from_seconds(0, covered_sec=0) == 0.0


def test_걸음_수를_시간대별로_모은다():
    status = [
        CollarStatus(t_ms=int((10 * 60 + m) * 60_000), worn_sec=60, steps=10, battery=80)
        for m in range(5)
    ]
    per_hour = steps_by_hour(batch([], status=status))
    assert per_hour[DAY + timedelta(hours=10)] == 50


# ---------------------------------------------------------------------------
# 오프라인 복구
# ---------------------------------------------------------------------------

def test_뒤늦게_도착한_과거_데이터도_제_시각에_꽂힌다():
    """
    BLE가 끊긴 동안 목줄이 플래시에 쌓아둔 걸 재연결 때 몰아서 보낸다.
    수신 시각이 아니라 t_ms 기준으로 자리를 잡아야 한다.
    """
    late = IngestBatch(
        collar_serial="PBL-0001",
        boot_id=1,
        uptime_ms=int(30 * HOUR_MS),                  # 30시간째 전송
        received_at=DAY + timedelta(hours=30),
        events=[ev(1, 2.0, BehaviorType.SCRATCH)],    # 실제로는 부팅 2시간째 일
    )
    events = to_behavior_events(late)
    assert events[0].ts == DAY + timedelta(hours=2)
