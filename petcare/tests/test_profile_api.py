"""
앱에서 고른 개 정보로 다시 계산한다.

시연은 행동 기록 한 벌(가상 44일)로 하고, 이름·견종·체중은 보는 사람이
앱에서 고른다. 여기서 지키는 것은 넷이다.
    고친 정보가 계획에 반영된다 (체중을 바꾸면 사료가 그만큼 바뀐다)
    '전날 대비 ±10%' 가 정보 수정까지 막지 않는다
    같은 값을 또 보내면 다시 계산하지 않는다
    잘못된 값은 저장하지 않는다
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import ingest
from seed_live import seed
from store import db, dialect

DOG = "dog_choco"


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    path = tmp_path / "profile.db"
    monkeypatch.setattr(dialect, "DEFAULT_SQLITE", path)
    db.init(path)
    with db.connect(path) as c:
        seed("skin", c)
        c.commit()
    return path


def plan_of(path) -> dict:
    with db.connect(path) as c:
        return db.latest_plan(c, DOG)


def plan_count(path) -> int:
    with db.connect(path) as c:
        return c.execute(
            "SELECT COUNT(*) AS n FROM plans WHERE dog_id=?", (DOG,)
        ).fetchone()["n"]


def test_체중을_바꾸면_사료가_그만큼_바뀐다(seeded):
    before = plan_of(seeded)
    with db.connect(seeded) as c:
        w0 = db.get_dog(c, DOG)["weight_kg"]

    out = ingest.save_profile(
        DOG, ingest.ProfileIn(name="보리", weight_kg=w0 * 3, breeds=["진돗개"])
    )
    assert out["changed"]

    after = plan_of(seeded)
    # RER 은 체중^0.75 에 비례한다. 3배면 약 2.28배.
    # ±10% 슬루율이 걸렸다면 1.1배를 넘지 못한다.
    ratio = after["total_food_g"] / before["total_food_g"]
    assert ratio > 1.8

    with db.connect(seeded) as c:
        dog = db.get_dog(c, DOG)
    assert dog["name"] == "보리"
    assert dog["breeds"] == ["진돗개"]


def test_정보를_바꿔도_행동_판정은_그대로다(seeded):
    """행동 기록은 그대로이므로 피부 쪽 영양제가 계속 나와야 한다."""
    ingest.save_profile(DOG, ingest.ProfileIn(name="보리", weight_kg=12.0))
    plan = plan_of(seeded)
    slots = {p["slot"] for m in plan["meals"] for p in m["pellets"]}
    assert 1 in slots          # 오메가3


def test_같은_값이면_다시_계산하지_않는다(seeded):
    ingest.save_profile(DOG, ingest.ProfileIn(name="보리"))
    n = plan_count(seeded)
    out = ingest.save_profile(DOG, ingest.ProfileIn(name="보리"))
    assert out == {"changed": False}
    assert plan_count(seeded) == n


def test_잘못된_값은_저장하지_않는다(seeded):
    with pytest.raises(HTTPException) as e:
        ingest.save_profile(DOG, ingest.ProfileIn(weight_kg=-3))
    assert e.value.status_code == 422
    with db.connect(seeded) as c:
        assert db.get_dog(c, DOG)["weight_kg"] > 0


def test_체중을_크게_바꾸면_전망이_입력한_체중을_쓴다(seeded):
    """저울 기록(가상 44일)과 입력 체중이 크게 다르면 입력을 믿는다."""
    with db.connect(seeded) as c:
        w0 = db.get_dog(c, DOG)["weight_kg"]
    ingest.save_profile(DOG, ingest.ProfileIn(weight_kg=w0 * 2))
    sim = plan_of(seeded)["simulation"]
    assert abs(sim["projected_kg"] - w0 * 2) / (w0 * 2) < 0.1
    assert sim["weight"] != "AWAY"
