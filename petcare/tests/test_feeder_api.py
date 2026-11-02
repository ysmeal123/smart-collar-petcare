"""
앱 -> 서버 -> 밥통 경로 검증.

앱은 밥통에 직접 붙지 않는다. 명령은 서버에 쌓이고 밥통이 폴링해서
가져간다. 그래서 **앱이 꺼져 있어도, 같은 Wi-Fi 가 아니어도** 급여가 된다.

여기서 지키는 것은 넷이다.
    수동 급여 명령이 예약 급여와 **같은 모양**으로 쌓인다
    장착되지 않은 슬롯은 거부한다
    하루 상한을 한 번에 넘기지 못한다
    대기 중인 명령이 있으면 받지 않는다 (이중 급여 방지)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from api import ingest
from core import constants as const
from store import db, dialect

DOG = "dog_test"


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    """엔드포인트는 경로를 받지 않는다. 기본 경로 자체를 임시 파일로 돌린다."""
    path = tmp_path / "feeder.db"
    monkeypatch.setattr(dialect, "DEFAULT_SQLITE", path)
    db.init(path)
    with db.connect(path) as c:
        db.upsert_dog(c, DOG, "{}")
        c.commit()
    return path


@pytest.fixture()
def loaded_123(monkeypatch):
    """B 안: 하드웨어 슬롯 4번은 사료다. 카트리지 로더는 1~3 뿐이다."""
    monkeypatch.setattr(const, "LOADED_SLOTS", frozenset({1, 2, 3}))


def commands_of(path) -> list[dict]:
    with db.connect(path) as c:
        return db.pending_commands(c, DOG, datetime.now())


# ---------------------------------------------------------------------------
# 수동 급여
# ---------------------------------------------------------------------------

def test_수동급여가_예약급여와_같은_모양으로_쌓인다(tmpdb, loaded_123):
    out = ingest.manual_dispense(
        DOG, ingest.ManualDispense(pellets={1: 2}, food_g=40.0)
    )
    assert out["ok"]

    cmds = commands_of(tmpdb)
    assert len(cmds) == 1
    cmd = cmds[0]

    # jobs/pipeline.enqueue_dispense 와 같은 키를 쓴다.
    # 밥통에 코드가 두 벌 생기면 한쪽만 고치는 사고가 난다.
    assert cmd["food_g"] == 40.0
    assert cmd["pellets"] == [
        {"slot": 1, "cartridge_id": "omega3", "name": "오메가3", "count": 2}
    ]
    # 예약과 구분되어야 "왜 이 시각에 나갔나" 를 나중에 설명할 수 있다.
    assert cmd["source"] == "manual"


def test_사료만_보낼_수_있다(tmpdb, loaded_123):
    ingest.manual_dispense(DOG, ingest.ManualDispense(food_g=120.0))
    cmd = commands_of(tmpdb)[0]
    assert cmd["food_g"] == 120.0
    assert cmd["pellets"] == []


def test_아무것도_없으면_거부한다(tmpdb, loaded_123):
    with pytest.raises(HTTPException) as e:
        ingest.manual_dispense(DOG, ingest.ManualDispense())
    assert e.value.status_code == 422


def test_수량_0은_무시하고_나머지만_보낸다(tmpdb, loaded_123):
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 0, 2: 1}))
    cmd = commands_of(tmpdb)[0]
    assert [p["slot"] for p in cmd["pellets"]] == [2]


# ---------------------------------------------------------------------------
# 슬롯 — 화면과 실물이 어긋나는 것이 제일 나쁘다
# ---------------------------------------------------------------------------

def test_장착되지_않은_슬롯은_거부한다(tmpdb, loaded_123):
    """
    4번 자리에는 사료 모터가 달려 있다.

    여기를 통과시키면 **장건강 2알 자리에 사료가 나간다.** 서버가 거부하지
    않으면 밥통은 명령을 그대로 믿는다.
    """
    with pytest.raises(HTTPException) as e:
        ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={4: 2}))
    assert e.value.status_code == 422
    assert "로더" in e.value.detail
    assert commands_of(tmpdb) == []


def test_하루_상한을_한_번에_넘기지_못한다(tmpdb, loaded_123):
    cap = const.CARTRIDGE_BY_ID["skin_barrier"].max_pellets_per_day
    with pytest.raises(HTTPException) as e:
        ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={2: cap + 1}))
    assert e.value.status_code == 422

    # 상한까지는 통과한다
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={2: cap}))
    assert commands_of(tmpdb)[0]["pellets"][0]["count"] == cap


# ---------------------------------------------------------------------------
# 이중 급여
# ---------------------------------------------------------------------------

def test_대기_중인_명령이_있으면_받지_않는다(tmpdb, loaded_123):
    """
    버튼을 두 번 누르거나 밥통이 꺼진 동안 여러 번 누르면, 깬 순간 전부
    실행된다. **이중 급여는 사고다.**
    """
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 1}))
    with pytest.raises(HTTPException) as e:
        ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 1}))
    assert e.value.status_code == 409
    assert len(commands_of(tmpdb)) == 1


def test_실행이_보고되면_다시_받는다(tmpdb, loaded_123):
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 1}))
    first = commands_of(tmpdb)[0]["id"]

    with db.connect(tmpdb) as c:
        db.ack_command(c, first, "done", "")
        c.commit()

    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 1}))
    assert len(commands_of(tmpdb)) == 1


def test_같은_요청키는_두_번_쌓이지_않는다(tmpdb, loaded_123):
    """
    앱이 응답을 못 받고 재시도하는 것은 정상 동작이다. 그때 같은 키를
    보내면 서버가 걸러낸다 - **같은 버튼 한 번이 두 번 나가면 사고다.**
    """
    req = ingest.ManualDispense(pellets={1: 1}, request_id="btn-abc")
    ingest.manual_dispense(DOG, req)

    with db.connect(tmpdb) as c:
        db.ack_command(c, commands_of(tmpdb)[0]["id"], "done", "")
        c.commit()

    # 실행이 끝난 뒤에도 같은 키는 거부된다
    with pytest.raises(HTTPException) as e:
        ingest.manual_dispense(DOG, req)
    assert e.value.status_code == 409


def test_취소하면_다시_받는다(tmpdb, loaded_123):
    """
    밥통이 꺼진 동안 버튼을 누르면 명령이 쌓이고, 중복 급여 검사 때문에
    만료될 때까지 다음 요청이 거절된다. **물러날 길이 있어야 한다.**
    """
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 1}))
    assert ingest.cancel_dispense(DOG)["cancelled"] == 1
    assert commands_of(tmpdb) == []

    # 취소한 뒤에는 다시 받는다
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 1}))
    assert len(commands_of(tmpdb)) == 1


def test_취소한_명령은_지우지_않고_남긴다(tmpdb, loaded_123):
    """'준 것' 과 '먹은 것' 의 대조에서 사라진 명령은 설명할 수 없는 구멍이다."""
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 1}))
    ingest.cancel_dispense(DOG)
    ack = ingest.feeder_view(DOG)["last_ack"]
    assert ack["state"] == "skipped"
    assert ack["result"] == "cancelled"


def test_취소할_것이_없으면_0(tmpdb, loaded_123):
    assert ingest.cancel_dispense(DOG)["cancelled"] == 0


def test_등록되지_않은_개체는_404(tmpdb, loaded_123):
    with pytest.raises(HTTPException) as e:
        ingest.manual_dispense("dog_없음", ingest.ManualDispense(pellets={1: 1}))
    assert e.value.status_code == 404


# ---------------------------------------------------------------------------
# 밥통 상태
# ---------------------------------------------------------------------------

def test_밥통이_올린_상태를_앱이_읽는다(tmpdb):
    ingest.put_feeder_state(DOG, ingest.FeederStateIn(
        busy=False, collar_connected=True, collar_class="walk", collar_conf=0.87,
        minute={"rest_s": 12, "walk_s": 40, "run_s": 8, "shake_n": 1},
    ))
    view = ingest.feeder_view(DOG)

    assert view["online"] is True
    assert view["age_s"] <= 1
    assert view["state"]["collar_class"] == "walk"
    assert view["state"]["minute"]["walk_s"] == 40
    assert view["pending"] == 0


def test_상태를_올리면_명령을_받아_간다(tmpdb, loaded_123):
    """
    왕복을 하나로 합친다.

    상태 보고와 명령 조회가 따로였을 때는 버튼을 누르고 모터가 돌기까지
    최악 60초였다. 같이 주고받으면 주기를 줄여도 요청 수가 안 늘어난다.
    """
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={3: 1}))
    out = ingest.put_feeder_state(DOG, ingest.FeederStateIn(busy=False))

    assert out["ok"]
    assert len(out["commands"]) == 1
    assert out["commands"][0]["pellets"][0]["slot"] == 3


def test_줄_명령이_없으면_빈_목록(tmpdb):
    assert ingest.put_feeder_state(DOG, ingest.FeederStateIn())["commands"] == []


def test_보고가_없으면_연결_안_된_것이다(tmpdb):
    view = ingest.feeder_view(DOG)
    assert view["online"] is False
    assert view["age_s"] is None
    assert view["state"] == {}


def test_보고가_오래되면_끊긴_것으로_본다(tmpdb):
    stale = datetime.now() - ingest.FEEDER_ONLINE - timedelta(seconds=30)
    with db.connect(tmpdb) as c:
        db.save_feeder_state(c, DOG, json.dumps({"busy": False}), stale)
        c.commit()

    view = ingest.feeder_view(DOG)
    assert view["online"] is False
    assert view["age_s"] > ingest.FEEDER_ONLINE.total_seconds()


def test_상태를_올리면_덮어쓴다(tmpdb):
    """개체당 한 줄만 둔다. 이력이 아니라 지금 상태다."""
    ingest.put_feeder_state(DOG, ingest.FeederStateIn(busy=True))
    ingest.put_feeder_state(DOG, ingest.FeederStateIn(busy=False))
    assert ingest.feeder_view(DOG)["state"]["busy"] is False


def test_앱이_대기_중인_명령_수를_본다(tmpdb, loaded_123):
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 1}))
    assert ingest.feeder_view(DOG)["pending"] == 1


def test_마지막_실행_결과를_본다(tmpdb, loaded_123):
    ingest.manual_dispense(DOG, ingest.ManualDispense(pellets={1: 3}))
    cmd_id = commands_of(tmpdb)[0]["id"]
    with db.connect(tmpdb) as c:
        db.ack_command(c, cmd_id, "done", "3알")
        c.commit()

    ack = ingest.feeder_view(DOG)["last_ack"]
    assert ack["id"] == cmd_id
    assert ack["state"] == "done"
    assert ack["result"] == "3알"
