"""
게이트웨이 검증.

여기서 지키려는 것은 "돌아간다"가 아니라
**"인터넷이 끊기고 전원이 나가도 데이터를 잃지 않고 밥이 나간다"** 이다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from gateway.buffer import Buffer
from gateway.config import Config


@pytest.fixture()
def buf(tmp_path) -> Buffer:
    return Buffer(str(tmp_path / "gw.db"))


def evs(boot_id: int, n: int, start: int = 0) -> list[dict]:
    return [
        {"seq": start + i, "t_ms": (start + i) * 1000, "type": "scratch",
         "conf": 0.9, "dur_s": 2.0}
        for i in range(n)
    ]


def sts(n: int, start: int = 0) -> list[dict]:
    return [
        {"t_ms": (start + i) * 60_000, "worn_sec": 60, "steps": 20, "battery": 80,
         "rest_sec": 40, "walk_sec": 15, "run_sec": 3, "vigorous_sec": 2}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 버퍼 — 데이터를 잃지 않는다
# ---------------------------------------------------------------------------

def test_전송_성공_전에는_지우지_않는다(buf):
    """지우고 나서 전송이 실패하면 그 데이터는 영영 없다."""
    buf.add_events(1, evs(1, 5))
    batch = buf.next_batch(100)

    assert len(batch.events) == 5
    assert buf.pending()[0] == 5      # 꺼내도 아직 남아 있다

    buf.drop(batch)                   # 서버가 받았다고 확인한 뒤에만
    assert buf.pending()[0] == 0


def test_목줄이_같은_걸_다시_뱉어도_한_번만_쌓인다(buf):
    """재연결 시 버퍼를 다시 보내는 건 정상 동작이다."""
    assert buf.add_events(1, evs(1, 10)) == 10
    assert buf.add_events(1, evs(1, 10)) == 0
    assert buf.pending()[0] == 10


def test_재부팅하면_seq가_겹쳐도_구분한다(buf):
    buf.add_events(1, evs(1, 5))
    added = buf.add_events(2, evs(2, 5))      # 같은 seq, 다른 부팅
    assert added == 5
    assert buf.pending()[0] == 10


def test_묶음에_부팅ID가_섞이지_않는다(buf):
    """
    섞이면 서버가 시각을 역산할 수 없다.
    한 묶음은 부팅 하나에서만 나와야 한다.
    """
    buf.add_events(1, evs(1, 3))
    buf.add_events(2, evs(2, 3))

    first = buf.next_batch(100)
    assert first.boot_id == 1
    assert len(first.events) == 3

    buf.drop(first)
    second = buf.next_batch(100)
    assert second.boot_id == 2


def test_오래된_미전송_데이터는_버린다(buf):
    """몇 주치를 쌓아두면 SD 카드가 찬다."""
    buf.add_events(1, evs(1, 5))
    with buf._conn() as c:
        old = (datetime.now() - timedelta(days=30)).isoformat()
        c.execute("UPDATE outbox_events SET added=?", (old,))

    assert buf.prune(keep_days=7) == 5
    assert buf.pending()[0] == 0


def test_상태_표본도_같이_실린다(buf):
    buf.add_events(1, evs(1, 2))
    buf.add_status(1, sts(3))
    batch = buf.next_batch(100)

    assert len(batch.events) == 2
    assert len(batch.status) == 3
    # 서버가 부팅 시각을 역산할 기준
    assert batch.max_t_ms == max(
        max(e["t_ms"] for e in batch.events),
        max(s["t_ms"] for s in batch.status),
    )


# ---------------------------------------------------------------------------
# 사출 계획 — 이중 급여를 막는다
# ---------------------------------------------------------------------------

def cmd(cid: str, at: datetime, food: int = 50) -> dict:
    return {
        "id": cid,
        "scheduled": at.isoformat(),
        "expires_at": (at + timedelta(hours=2)).isoformat(),
        "food_g": food,
        "pellets": [{"slot": 1, "count": 4, "name": "오메가3"}],
    }


def test_같은_명령을_다시_받아도_두_번_사출하지_않는다(buf):
    """이중 급여는 사고다. 서버가 같은 명령을 다시 내려도 막아야 한다."""
    at = datetime(2026, 9, 27, 8, 0)
    buf.save_plan([cmd("d:2026-09-27:0", at)])
    buf.mark("d:2026-09-27:0", "done")

    added = buf.save_plan([cmd("d:2026-09-27:0", at)])   # 서버가 또 내려줌
    assert added == 0
    assert buf.due_commands(at + timedelta(minutes=30)) == []


def test_시각이_안_되면_사출하지_않는다(buf):
    at = datetime(2026, 9, 27, 8, 0)
    buf.save_plan([cmd("c1", at)])

    assert buf.due_commands(at - timedelta(minutes=10)) == []
    assert len(buf.due_commands(at + timedelta(minutes=1))) == 1


def test_실행_결과를_서버에_보고할_때까지_들고_있는다(buf):
    """
    명령과 실행을 대조하지 못하면 섭취량 계산이 틀어지고
    긴급 정지 규칙이 오작동한다.
    """
    at = datetime(2026, 9, 27, 8, 0)
    buf.save_plan([cmd("c1", at)])
    buf.mark("c1", "done", '{"food_g": 50}')

    pending = buf.unacked()
    assert len(pending) == 1 and pending[0]["state"] == "done"

    buf.forget("c1")                  # 서버 보고 성공 후에만
    assert buf.unacked() == []


def test_사출_실패도_보고_대상이다(buf):
    at = datetime(2026, 9, 27, 8, 0)
    buf.save_plan([cmd("c1", at)])
    buf.mark("c1", "failed", "카트리지 걸림")

    assert buf.unacked()[0]["state"] == "failed"


# ---------------------------------------------------------------------------
# 폴백 — 서버가 죽어도 밥은 나간다
# ---------------------------------------------------------------------------

def test_계획_나이를_잰다(buf):
    """
    fetched_at 은 저장 시점의 실제 시각이다. 테스트가 가짜 날짜를 쓰면
    실제 시계와 섞여 하루 중 언제 돌리느냐에 따라 결과가 달라진다.
    그래서 기준 시각을 명시적으로 박아 두고 잰다.
    """
    now = datetime(2026, 9, 27, 12, 0)
    assert buf.plan_age(now) is None          # 받은 적 없음

    buf.save_plan([cmd("c1", now)])
    with buf._conn() as c:
        c.execute("UPDATE plan SET fetched_at=?", (now.isoformat(),))

    age = buf.plan_age(now + timedelta(days=3))
    assert age is not None and age.days == 3


def test_서버가_죽어도_캐시된_계획으로_사출한다(buf, tmp_path):
    """클라우드 사정과 무관하게 강아지는 밥을 먹어야 한다."""
    from gateway.agent import Agent
    from gateway.hardware import MockCollar, MockDispenser, MockScale

    cfg = Config(db_path=str(tmp_path / "a.db"), server="http://127.0.0.1:1")
    disp = MockDispenser()
    agent = Agent(cfg, MockCollar(), disp, MockScale())

    at = datetime(2026, 9, 27, 8, 0)
    agent.buffer.save_plan([cmd("c1", at)])

    # 서버는 죽어 있다 — 명령 조회는 실패하지만 실행은 되어야 한다
    agent.sync_commands()
    agent.execute(at + timedelta(minutes=5))

    assert len(disp.log) == 1 and disp.log[0].ok
    assert disp.log[0].food_g == 50


def test_계획이_너무_오래되면_사료만_주고_영양제는_끊는다(buf, tmp_path):
    """
    오래된 처방을 무한정 반복하는 것도 위험하다.
    영양제는 상태 추론의 결과인데 그 추론이 몇 주 전 것이기 때문이다.
    """
    from gateway.agent import K_FALLBACK_G, K_MEAL_HOURS, Agent
    from gateway.hardware import MockCollar, MockDispenser, MockScale

    cfg = Config(db_path=str(tmp_path / "b.db"), plan_max_age_days=7)
    disp = MockDispenser()
    agent = Agent(cfg, MockCollar(), disp, MockScale())

    old = datetime(2026, 9, 1, 8, 0)
    agent.buffer.save_plan([cmd("old", old)])
    agent.buffer.mark("old", "done")
    agent.buffer.put(K_FALLBACK_G, "100")
    agent.buffer.put(K_MEAL_HOURS, "8,19")

    with agent.buffer._conn() as c:      # 계획을 20일 전 것으로 만든다
        c.execute("UPDATE plan SET fetched_at=?", (old.isoformat(),))

    agent.fallback(datetime(2026, 9, 21, 8, 30))

    assert len(disp.log) == 1
    assert disp.log[0].food_g == 50       # 100g을 두 끼로 나눔
    assert disp.log[0].pellets == {}      # 영양제는 없다


def test_폴백이_같은_끼니를_두_번_주지_않는다(tmp_path):
    from gateway.agent import K_FALLBACK_G, K_MEAL_HOURS, Agent
    from gateway.hardware import MockCollar, MockDispenser, MockScale

    cfg = Config(db_path=str(tmp_path / "c.db"), plan_max_age_days=7)
    disp = MockDispenser()
    agent = Agent(cfg, MockCollar(), disp, MockScale())

    old = datetime(2026, 9, 1, 8, 0)
    agent.buffer.save_plan([cmd("old", old)])
    agent.buffer.mark("old", "done")
    agent.buffer.put(K_FALLBACK_G, "100")
    agent.buffer.put(K_MEAL_HOURS, "8")
    with agent.buffer._conn() as c:
        c.execute("UPDATE plan SET fetched_at=?", (old.isoformat(),))

    now = datetime(2026, 9, 21, 8, 30)
    agent.fallback(now)
    agent.fallback(now + timedelta(minutes=10))

    assert len(disp.log) == 1


# ---------------------------------------------------------------------------
# 시계 — 라즈베리파이에는 RTC가 없다
# ---------------------------------------------------------------------------

def test_시계가_초기화됐으면_업로드하지_않는다(monkeypatch, tmp_path):
    """
    부팅 직후 NTP 전에 올리면 목줄 데이터가 1970년에 꽂힌다.
    baseline이 통째로 오염되고 복구가 어렵다.
    """
    from gateway import client as C

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(1970, 1, 1, 0, 0, 30)

    monkeypatch.setattr(C, "datetime", FakeDT)
    ok, why = C.clock_is_trustworthy()

    assert ok is False
    assert "1970" in why


def test_시계가_멀쩡하면_통과한다():
    from gateway.client import clock_is_trustworthy

    ok, _ = clock_is_trustworthy()
    assert ok is True


def test_시계가_안_맞으면_전송을_거부한다(monkeypatch, tmp_path):
    from gateway import client as C
    from gateway.client import Client, ClockNotReady

    monkeypatch.setattr(C, "clock_is_trustworthy", lambda: (False, "NTP 미동기화"))

    buf = Buffer(str(tmp_path / "d.db"))
    buf.add_events(1, evs(1, 3))
    batch = buf.next_batch(100)

    with pytest.raises(ClockNotReady):
        Client(Config()).upload(batch)


# ---------------------------------------------------------------------------
# 계약 — 서버 스키마와 어긋나지 않는다
# ---------------------------------------------------------------------------

def test_게이트웨이가_만든_페이로드를_서버가_받아들인다(tmp_path):
    """
    게이트웨이는 의존성 없이 돌아야 해서 서버 모델을 import 하지 않는다.
    그만큼 계약이 갈라질 위험이 있으므로 여기서 대조한다.
    """
    pydantic = pytest.importorskip("pydantic")          # noqa: F841
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "petcare"))
    from core.telemetry import IngestBatch

    buf = Buffer(str(tmp_path / "e.db"))
    buf.add_events(1, evs(1, 3))
    buf.add_status(1, sts(2))
    batch = buf.next_batch(100)

    payload = {
        "collar_serial": "PBL-0001",
        "boot_id": batch.boot_id,
        "uptime_ms": batch.max_t_ms,
        "received_at": datetime.now().isoformat(),
        "fw_version": "0.1.0",
        "events": batch.events,
        "status": batch.status,
    }

    parsed = IngestBatch.model_validate(payload)
    assert len(parsed.events) == 3
    assert len(parsed.status) == 2
    assert parsed.status[0].active_sec == 20        # walk 15 + run 3 + vigorous 2


def test_서버가_거부한_묶음은_버리고_다음으로_넘어간다(monkeypatch, tmp_path):
    """
    4xx는 재시도해도 영원히 실패한다. 버리지 않으면 잘못된 레코드 하나가
    큐를 영구히 막고, 뒤에 쌓인 멀쩡한 데이터까지 전부 못 올라간다.
    """
    from gateway.agent import Agent
    from gateway.client import BadPayload
    from gateway.hardware import MockCollar, MockDispenser, MockScale

    cfg = Config(db_path=str(tmp_path / "f.db"))
    agent = Agent(cfg, MockCollar(), MockDispenser(), MockScale())
    agent.buffer.add_events(1, evs(1, 3))
    agent.buffer.add_events(2, evs(2, 3))

    def reject(batch):
        raise BadPayload("HTTP 422: t_ms must be >= 0")

    monkeypatch.setattr(agent.client, "upload", reject)
    agent.upload()

    assert agent.buffer.pending()[0] == 0      # 둘 다 비워졌다 (큐가 안 막힌다)


# ---------------------------------------------------------------------------
# 실물 디스펜서 (gpiozero 없이 검증 가능한 부분)
#
# 모터를 못 돌려도 "몇 펄스를 보낼지" 와 "결과를 어떻게 보고하는지" 는
# 잴 수 있다. 조립 전에 로직 실수를 잡아두는 게 목적이다.
# ---------------------------------------------------------------------------

class FakePin:
    """gpiozero.OutputDevice 를 대신한다. 켜진 횟수를 센다."""

    def __init__(self, pin, initial_value=False):
        self.pin = pin
        self.value = initial_value
        self.pulses = 0

    def on(self):
        if not self.value:
            self.pulses += 1
        self.value = True

    def off(self):
        self.value = False

    def close(self):
        pass


@pytest.fixture
def motor_dev(monkeypatch):
    """gpiozero 없이 MotorDispenser 를 만든다."""
    import sys
    import types

    fake = types.ModuleType("gpiozero")
    fake.OutputDevice = FakePin
    monkeypatch.setitem(sys.modules, "gpiozero", fake)

    from gateway import motor
    monkeypatch.setattr(motor, "PULSE_S", 0)        # 테스트는 기다리지 않는다
    return motor


def test_알갱이_수만큼_펄스를_보낸다(motor_dev):
    dev = motor_dev.MotorDispenser(steps_per_pellet=20)
    r = dev.dispense(0, [{"slot": 1, "count": 4, "name": "오메가3"}])

    assert r.pellets == {1: 4}
    # EN 을 내렸다 올리는 것까지 포함해 STEP 만 센다
    assert dev._step[1].pulses == 80, "4알 × 20펄스 = 80"


def test_여러_슬롯을_각각_돌린다(motor_dev):
    dev = motor_dev.MotorDispenser(steps_per_pellet=10)
    r = dev.dispense(0, [
        {"slot": 1, "count": 3, "name": "오메가3"},
        {"slot": 3, "count": 2, "name": "관절"},
    ])

    assert r.pellets == {1: 3, 3: 2}
    assert dev._step[1].pulses == 30
    assert dev._step[3].pulses == 20
    assert dev._step[2].pulses == 0, "요청하지 않은 슬롯이 돌았다"


def test_EN_은_평소에_HIGH_다(motor_dev):
    """
    EN HIGH = 출력 차단. 평소에 LOW 로 두면 모터가 계속 전류를 먹고
    뜨거워지며, 손으로 돌릴 수도 없다.
    """
    dev = motor_dev.MotorDispenser()
    for slot in dev.pins:
        assert dev._en[slot].value is True, f"{slot}번 EN 이 시작부터 LOW 다"

    dev.dispense(0, [{"slot": 1, "count": 1}])
    assert dev._en[1].value is True, "사출 후 EN 을 HIGH 로 되돌리지 않았다"


def test_배선되지_않은_슬롯은_실패로_보고한다(motor_dev):
    """
    **조용히 넘어가면 안 된다.** 서버는 준 걸로 알고 섭취량을 대조한다.
    """
    dev = motor_dev.MotorDispenser(pins={1: (17, 27, 22)})
    r = dev.dispense(0, [{"slot": 5, "count": 2, "name": "진정·수면"}])

    assert r.ok is False
    assert "진정·수면" in r.error
    assert r.pellets == {}


def test_폭주를_상한에서_막는다(motor_dev, monkeypatch):
    """
    보정값에 0 이 하나 더 붙으면 로더가 통째로 비워진다.
    상한이 없으면 BB 가 바닥에 쏟아진다.
    """
    monkeypatch.setattr(motor_dev, "MAX_STEPS", 100)
    dev = motor_dev.MotorDispenser(steps_per_pellet=1000)
    r = dev.dispense(0, [{"slot": 1, "count": 5, "name": "오메가3"}])

    assert dev._step[1].pulses == 100, "상한을 넘겨 돌았다"
    assert r.ok is False, "못 채웠는데 성공이라고 했다"
    assert "5알 중" in r.error


def test_사료_모터가_없으면_안_줬다고_말한다(motor_dev):
    """
    영양제 시연에는 지장이 없지만, 밥을 줬다고 거짓말하면 안 된다.
    서버의 섭취량 대조가 통째로 틀어진다.
    """
    dev = motor_dev.MotorDispenser(food_slot=None)
    r = dev.dispense(90, [])

    assert r.food_g == 0
    assert r.ok is False
    assert "미장착" in r.error


def test_한_슬롯이_실패해도_나머지는_계속한다(motor_dev):
    """
    하나 막혔다고 그날 영양제를 통째로 거르는 것보다,
    되는 것만 주고 무엇이 안 됐는지 보고하는 게 낫다.
    """
    dev = motor_dev.MotorDispenser(pins={1: (17, 27, 22), 3: (16, 20, 21)},
                                   steps_per_pellet=10)
    r = dev.dispense(0, [
        {"slot": 1, "count": 2, "name": "오메가3"},
        {"slot": 9, "count": 1, "name": "없는슬롯"},
        {"slot": 3, "count": 1, "name": "관절"},
    ])

    assert r.pellets == {1: 2, 3: 1}, "실패 뒤 슬롯이 건너뛰어졌다"
    assert r.ok is False
    assert "없는슬롯" in r.error


def test_핀_배치가_안내서와_같다():
    """
    조립 안내서 08장 표. 여기가 어긋나면 엉뚱한 모터가 돈다.
    배선을 바꿨다면 이 테스트도 같이 바꿔야 한다 - 그게 의도다.
    """
    from gateway.motor import PINS

    assert PINS[1] == (17, 27, 22)   # 물리 11 / 13 / 15
    assert PINS[2] == (5, 6, 13)     # 물리 29 / 31 / 33
    assert PINS[3] == (16, 20, 21)   # 물리 36 / 38 / 40
    assert PINS[4] == (23, 24, 25)   # 물리 16 / 18 / 22

    # I²C(물리 3·5 = GPIO2·3)와 겹치면 로드셀이 죽는다
    used = {p for pins in PINS.values() for p in pins}
    assert not (used & {2, 3}), "I²C 핀을 모터에 배정했다"
