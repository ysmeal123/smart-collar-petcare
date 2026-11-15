"""
문진이 **시점**을 본다.

질문지는 "최근 2주 안에" 를 묻는데 답은 yes/no 로만 들어왔다. 그래서
"한 달 전에 샴푸 바꿨어요" 가 "2주 안에 바꿨다" 와 똑같이 처리돼 **엉뚱한
유예**가 걸렸다 - 한 달 전 샴푸는 지금 가려움의 설명이 못 된다.

반대 방향도 있다. "3일째 밥을 안 먹어요" 는 하루 덜 먹은 것과 다르다.
지속 기간이 진료 권고를 가르는 기준인데 보지 않았다.

그리고 `food_changed` 는 질문지의 두 축에 다 있으면서 interpret() 에
한 줄도 없었다. 보호자가 답해도 저장만 되고 아무 일이 없었다.
"""

from __future__ import annotations

import pytest

from agent.extract import duration_days, extract
from agent.wellness_agent import (
    DEFER_WINDOW_DAYS,
    PERSIST_VET_DAYS,
    Context,
    interpret,
)
from core.models import HealthAxis


# ---------------------------------------------------------------------------
# 시점 읽기
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,days", [
    ("오늘 사료 바꿨어요", 0),
    ("어제부터 밥을 안 먹어요", 1),
    ("그제 샴푸 바꿨어요", 2),
    ("그저께 목욕했어요", 2),
    ("3일 전에 사료를 바꿨어요", 3),
    ("3일째 밥을 남겨요", 3),
    ("일주일 전에 샴푸를 바꿨어요", 7),
    ("2주 전부터 긁어요", 14),
    ("한 달 전에 사료 바꿨어요", 30),
    ("2개월 전에 바꿨어요", 60),
])
def test_기간을_읽는다(text, days):
    assert duration_days(text) == days


@pytest.mark.parametrize("text", [
    "사료를 바꿨어요",            # 시점 없음
    "하루에 두 번 산책해요",      # 횟수지 기간이 아니다
    "2살이에요",                  # 나이지 기간이 아니다
])
def test_기간이_아닌_것은_읽지_않는다(text):
    """
    숫자가 있다고 다 기간이 아니다. '전·째·동안·부터' 같은 꼬리가 있어야
    기간으로 본다. 없으면 **모른다고 해야** 한다 - 0 으로 읽으면 '오늘' 이
    되어 엉뚱한 유예가 걸린다.
    """
    assert duration_days(text) is None


def test_여러_기간이_있으면_오래된_쪽을_쓴다():
    """
    "3일 전부터 긁는데 한 달 전에 사료를 바꿨어요" 에서 원인 후보는 더 먼
    쪽이다. 가까운 쪽을 쓰면 오래된 변화가 '최근' 으로 둔갑한다.
    """
    assert duration_days("3일 전부터 긁는데 한 달 전에 사료를 바꿨어요") == 30


def test_추출이_시점을_붙여_내놓는다():
    ex = extract("한 달 전에 사료를 바꿨어요")
    assert ex.answers["food_changed"] == "yes"
    assert ex.answers["food_changed_days"] == "30"


def test_시점을_안_말하면_비워서_내놓는다():
    """
    **지우지 않고 비운다.** 답변은 key 단위로 덮어써지므로, 키를 아예 안
    내놓으면 예전 메모의 "한 달 전" 이 그대로 남아 오늘 답변에 들러붙는다.
    """
    ex = extract("사료를 바꿨어요")
    assert ex.answers["food_changed"] == "yes"
    assert ex.answers["food_changed_days"] == ""


# ---------------------------------------------------------------------------
# food_changed — 묻기만 하고 쓰지 않던 것
# ---------------------------------------------------------------------------

def test_사료를_바꾸면_피부축을_지켜본다():
    """
    식이 반응은 2~6주에 걸쳐 나타난다. 바꾸자마자 생긴 변화를 사료 탓으로
    보기도, 아니라고 보기도 이르다.
    """
    eff = interpret(Context(answers={"food_changed": "yes"}))
    assert HealthAxis.SKIN in eff.defer_axes
    assert any("사료" in n for n in eff.notes)


def test_사료_답변이_저장만_되고_끝나지_않는다():
    """
    질문지에 있는 key 는 interpret() 에도 있어야 한다. 어긋나면 보호자가
    답해도 **아무 일이 일어나지 않고**, 왜 안 바뀌는지 알 길이 없다.
    """
    from agent.wellness_agent import QUESTIONS, AnswerType

    for questions in QUESTIONS.values():
        for q in questions:
            # 선택형은 "yes" 가 아니라 제 선택지를 받는다. 첫 번째가
            # '증상이 있다' 쪽이다.
            value = q.choices[0] if q.type == AnswerType.CHOICE else "yes"
            eff = interpret(Context(answers={q.key: value}))
            changed = bool(
                eff.defer_axes or eff.notes or eff.block_all or eff.vet_referral
            )
            assert changed, f"{q.key} 는 묻기만 하고 쓰지 않는다"


# ---------------------------------------------------------------------------
# 오래된 원인은 설명이 못 된다
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["shampoo_changed", "food_changed", "env_exposure"])
def test_오래된_변화로는_유예하지_않는다(key):
    old = str(DEFER_WINDOW_DAYS + 20)
    eff = interpret(Context(answers={key: "yes", key + "_days": old}))
    assert HealthAxis.SKIN not in eff.defer_axes
    assert any("오래돼" in n for n in eff.notes)


@pytest.mark.parametrize("key", ["shampoo_changed", "food_changed", "env_exposure"])
def test_최근_변화는_유예한다(key):
    eff = interpret(Context(answers={key: "yes", key + "_days": "3"}))
    assert HealthAxis.SKIN in eff.defer_axes


@pytest.mark.parametrize("key", ["shampoo_changed", "food_changed", "env_exposure"])
def test_시점을_모르면_지금까지처럼_유예한다(key):
    """
    모호할 때는 지켜보는 쪽이 안전하다. 시점을 안 말했다고 유예를 풀면
    확신 없이 처방하는 쪽으로 기운다.
    """
    eff = interpret(Context(answers={key: "yes", key + "_days": ""}))
    assert HealthAxis.SKIN in eff.defer_axes


def test_경계값은_최근으로_본다():
    """14일 전은 '최근 2주 안에' 에 들어간다. 넘어야 오래된 것이다."""
    at = str(DEFER_WINDOW_DAYS)
    over = str(DEFER_WINDOW_DAYS + 1)
    assert HealthAxis.SKIN in interpret(
        Context(answers={"food_changed": "yes", "food_changed_days": at})
    ).defer_axes
    assert HealthAxis.SKIN not in interpret(
        Context(answers={"food_changed": "yes", "food_changed_days": over})
    ).defer_axes


# ---------------------------------------------------------------------------
# 오래 가는 증상은 멈추는 것만으로 부족하다
# ---------------------------------------------------------------------------

def test_며칠째_안_먹으면_진료를_권한다():
    eff = interpret(Context(answers={
        "eating_less": "yes",
        "eating_less_days": str(PERSIST_VET_DAYS),
    }))
    assert eff.block_all
    assert eff.vet_referral
    assert any("진료" in n for n in eff.notes)


def test_하루_덜_먹은_것은_멈추기만_한다():
    eff = interpret(Context(answers={
        "eating_less": "yes", "eating_less_days": "1",
    }))
    assert eff.block_all
    assert not eff.vet_referral


def test_새_사료면_덜_먹는_이유를_설명한다():
    eff = interpret(Context(answers={
        "eating_less": "yes", "eating_less_days": "1",
        "food_changed": "yes", "food_changed_days": "2",
    }))
    # 멈추는 것은 그대로다. 설명이 붙을 뿐이다.
    assert eff.block_all
    assert any("새 사료" in n for n in eff.notes)


def test_시점이_생겨도_용량은_못_올린다():
    """
    시점을 보게 됐다고 해서 문진이 액셀이 되면 안 된다. interpret() 가
    내놓는 것은 여전히 보류·차단·진료 권고뿐이다.
    """
    fields = set(interpret(Context(answers={"food_changed": "yes"})).model_dump())
    assert fields == {"defer_axes", "block_all", "vet_referral", "notes"}
