"""
체중 측정 정제 검증.

체중은 급여량 캐스케이드 제어의 외부 루프 입력이다.
오염되면 활동계수가 잘못 교정되고 그 오차가 몇 달에 걸쳐 누적된다.

여기서 지키려는 것은 "값을 받았다"가 아니라
"믿을 수 없는 값은 채우지 않고 버린다"이다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from core.weight import (
    MAX_JUMP_RATIO,
    MIN_SAMPLES,
    ScaleSession,
    daily_weight,
    filter_session,
)
from jobs import pipeline
from store import db

NOON = datetime(2026, 9, 27, 12, 0, 0)


def session(*vals: float, at: datetime = NOON) -> ScaleSession:
    return ScaleSession(started_at=at, samples_kg=list(vals))


# ---------------------------------------------------------------------------
# 세션 정제
# ---------------------------------------------------------------------------

def test_가만히_서_있었으면_중앙값을_쓴다():
    s = session(5.20, 5.22, 5.19, 5.21, 5.20, 5.23)
    assert filter_session(s) == pytest.approx(5.21, abs=0.02)


def test_스쳐_지나간_건_측정으로_치지_않는다():
    """표본이 몇 개 안 되면 올라선 게 아니라 지나간 것이다."""
    assert filter_session(session(5.2, 5.2)) is None
    assert filter_session(session(*[5.2] * (MIN_SAMPLES - 1))) is None
    assert filter_session(session(*[5.2] * MIN_SAMPLES)) is not None


def test_계속_움직였으면_버린다():
    """
    개가 발을 뗐다 붙였다 하면 어떤 값을 골라도 믿을 수 없다.
    평균을 내면 그럴듯한 숫자가 나오지만 그게 더 위험하다.
    """
    assert filter_session(session(5.2, 3.1, 6.8, 4.0, 7.2, 2.9)) is None


def test_극단값이_섞여도_중앙값이_버텨낸다():
    """발 하나를 뗐다 붙인 정도는 흡수해야 한다."""
    s = session(5.20, 5.21, 5.19, 5.20, 5.22, 5.05)
    got = filter_session(s)
    assert got is not None and 5.1 < got < 5.3


def test_물리적으로_불가능한_값은_제외한다():
    # 로드셀 고장으로 0 근처와 거대값이 섞인 경우
    s = session(0.0, 5.2, 5.2, 5.2, 5.2, 999.0, 5.2)
    got = filter_session(s)
    assert got == pytest.approx(5.2, abs=0.05)


# ---------------------------------------------------------------------------
# 하루 대표값
# ---------------------------------------------------------------------------

def test_여러_번_올라가면_세션_중앙값을_쓴다():
    r = daily_weight(
        [session(*[5.2] * 6), session(*[5.3] * 6), session(*[5.25] * 6)],
        manual_kg=None, last_known_kg=5.2,
    )
    assert r.kg == pytest.approx(5.25, abs=0.02)
    assert r.source == "scale"


def test_다른_개가_올라가면_채택하지_않는다():
    """5kg 개가 갑자기 8kg이면 그건 다른 개다."""
    r = daily_weight([session(*[8.0] * 8)], manual_kg=None, last_known_kg=5.2)
    assert r.kg is None
    assert "의심" in r.reason


def test_정상적인_체중_변화는_통과시킨다():
    """실제 증감까지 막으면 외부 루프가 영영 작동하지 않는다."""
    ok = 5.2 * (1 + MAX_JUMP_RATIO * 0.5)
    r = daily_weight([session(*[ok] * 8)], manual_kg=None, last_known_kg=5.2)
    assert r.kg is not None


def test_첫_측정은_비교_기준이_없으므로_그냥_받는다():
    r = daily_weight([session(*[5.2] * 8)], manual_kg=None, last_known_kg=None)
    assert r.kg == pytest.approx(5.2, abs=0.05)


def test_유효한_세션이_없으면_채우지_않는다():
    """추측해서 채우는 것보다 그날을 비워두는 게 낫다."""
    r = daily_weight([session(5.2, 5.2)], manual_kg=None, last_known_kg=5.2)
    assert r.kg is None


# ---------------------------------------------------------------------------
# 보호자 입력
# ---------------------------------------------------------------------------

def test_보호자_입력이_체중계보다_우선한다():
    r = daily_weight([session(*[5.9] * 8)], manual_kg=5.2, last_known_kg=5.2)
    assert r.kg == 5.2
    assert r.source == "manual"


def test_오타는_보류한다():
    """5.8을 58로 잘못 친 경우. 버리지 않고 채택만 안 한다."""
    r = daily_weight([], manual_kg=58.0, last_known_kg=5.8)
    assert r.kg is None
    assert "보류" in r.reason


def test_오랜만에_입력해도_적당한_변화는_받는다():
    """몇 달 만에 입력할 수 있다. 체중계보다 관대하게 본다."""
    r = daily_weight([], manual_kg=6.8, last_known_kg=5.8)
    assert r.kg == 6.8


# ---------------------------------------------------------------------------
# 파이프라인 연결
# ---------------------------------------------------------------------------

@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "w.db"
    db.init(path)
    with db.connect(path) as c:
        db.upsert_dog(c, "d1", '{"dog_id":"d1"}')
        yield c


def test_채택된_체중이_외부_루프에_들어간다(conn):
    day = date(2026, 9, 27)
    pipeline.record_scale_sessions(conn, "d1", day, [session(*[5.2] * 8)])
    assert pipeline._accepted_weight(conn, "d1", day) == pytest.approx(5.2, abs=0.05)


def test_버린_측정도_이유와_함께_남는다(conn):
    """왜 그날 체중이 반영 안 됐는지 답할 수 있어야 한다."""
    day = date(2026, 9, 27)
    pipeline.record_scale_sessions(conn, "d1", day, [session(*[5.2] * 8)])

    next_day = day + timedelta(days=1)
    r = pipeline.record_scale_sessions(
        conn, "d1", next_day, [session(*[9.0] * 8, at=NOON + timedelta(days=1))]
    )

    assert r["accepted"] is False
    rows = db.weights_of_day(conn, "d1", next_day)
    assert len(rows) == 1 and rows[0]["accepted"] == 0
    assert pipeline._accepted_weight(conn, "d1", next_day) is None


def test_같은_날_보호자_입력이_체중계를_이긴다(conn):
    day = date(2026, 9, 27)
    pipeline.record_scale_sessions(conn, "d1", day, [session(*[5.5] * 8)])
    pipeline.record_manual_weight(conn, "d1", NOON, 5.2)
    assert pipeline._accepted_weight(conn, "d1", day) == 5.2
