"""
자유 텍스트 해석 · 대화록 검증.

여기서 지키려는 것:

    - 부정을 놓치지 않는다 ("토는 안 했어요"가 구토 보고가 되면 안 된다)
    - 증상이 부정형인 표현을 뒤집지 않는다 ("계단을 안 올라가요"는 증상이다)
    - 새 key 를 만들지 않는다 (interpret() 가 모르는 값은 아무 일도 안 한다)
    - 못 알아들으면 못 알아들었다고 말한다
    - 자유 텍스트가 **처방까지** 닿는다
    - 자유 텍스트로도 용량을 올릴 수 없다
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from agent import chat as chat_mod
from agent.extract import RULES, extract
from agent.wellness_agent import QUESTIONS, Context, interpret
from core import twin as twin_mod
from core.models import HealthAxis
from core.plan import build
from core.prescribe import prescribe
from mock.generator import generate


@pytest.fixture(scope="module")
def skin_ds():
    return generate("skin")


# ---------------------------------------------------------------------------
# 추출
# ---------------------------------------------------------------------------

def test_평범한_보고를_알아듣는다():
    cases = {
        "샴푸를 새로 바꿨어요": "shampoo_changed",
        "사료도 바꿨어요": "food_changed",
        "풀밭에서 한참 뒹굴었어요": "env_exposure",
        "피부가 빨갛게 됐어요": "skin_visible",
        "귀에서 냄새가 나요": "ear_smell",
        "다리를 절어요": "limping",
        "날이 추워서 산책을 줄였어요": "weather_cold",
        "이사를 했어요": "env_changed",
        "어제 토했어요": "vomit",
    }
    for text, key in cases.items():
        assert key in extract(text).answers, f"{text!r} 에서 {key} 를 놓쳤다"


@pytest.mark.parametrize("text", [
    "토는 안 했어요",
    "설사는 없어요",
    "절지 않아요",
    "사료는 안 바꿨어요",
    "샴푸 바꾼 적 없어요",
])
def test_부정을_긍정으로_읽지_않는다(text):
    """
    규칙 기반 추출에서 가장 위험한 실패다.

    "토는 안 했어요"를 구토 보고로 읽으면 영양제를 잘못 멈춘다.
    보호자는 왜 멈췄는지 알 수 없다.
    """
    assert extract(text).answers == {}, f"{text!r} 를 긍정으로 읽었다"


def test_증상이_부정형인_표현은_뒤집지_않는다():
    """
    "계단을 안 올라가요"는 부정문이지만 **증상 보고**다.
    부정 검사를 그대로 걸면 뜻이 반대가 된다.
    """
    assert "stairs_avoid" in extract("요즘 계단을 안 올라가려고 해요").answers
    assert "weather_cold" in extract("산책을 안 하고 있어요").answers

    # 그렇다고 아무 문장이나 잡으면 안 된다
    assert extract("계단 잘 올라가요").answers == {}


def test_한_문장에_섞여_있어도_각각_판정한다():
    """
    절 단위로 쪼개지 않으면 부정어가 엉뚱한 키워드에 걸린다.
    "목욕은 했는데 토는 안 했어요" 에서 목욕까지 부정되면 안 된다.
    """
    got = extract("목욕은 했는데 토는 안 했어요").answers
    assert "recent_bath" in got
    assert "vomit" not in got


def test_여러_건을_한꺼번에_알아듣는다():
    got = extract("목욕을 시켰고 사료도 바꿨어요").answers
    assert {"recent_bath", "food_changed"} <= set(got)


def test_새_key_를_만들지_않는다():
    """
    QUESTIONS 에 없는 key 를 내놓으면 interpret() 가 그걸 모른다.
    저장은 되는데 아무 일도 일어나지 않는, 찾기 어려운 실패가 된다.
    """
    known = {q.key for qs in QUESTIONS.values() for q in qs}
    for key, _, _ in RULES:
        assert key in known, f"{key} 는 질문 목록에 없다"


def test_못_알아들으면_그렇다고_말한다():
    """조용히 버리면 보호자는 전달했다고 믿는다. 침묵보다 나쁘다."""
    ex = extract("오늘 기분이 좋아 보여요")
    assert ex.answers == {}
    assert ex.unmatched is True


def test_빈_입력은_아무것도_하지_않는다():
    for text in ("", "   ", "\n"):
        assert extract(text).answers == {}


# ---------------------------------------------------------------------------
# 추출 -> 처방
# ---------------------------------------------------------------------------

def _pellets(plan) -> int:
    return sum(p.count for m in plan.meals for p in m.pellets)


def test_자유_텍스트가_처방까지_닿는다(skin_ds):
    """대화가 장식이 아니라는 것을 여기서 못 박는다."""
    base, _, _ = build(skin_ds.profile, skin_ds.days, context=Context())
    assert _pellets(base) > 0

    ex = extract("3일 전에 풀밭에서 한참 뒹굴었어요. 샴푸도 새로 바꿨고요.")
    assert ex.answers, "이 문장은 알아들어야 한다"

    after, _, _ = build(
        skin_ds.profile, skin_ds.days, context=Context(answers=ex.answers)
    )
    assert _pellets(after) == 0, "원인이 설명됐는데 피부 처방이 그대로다"
    assert after.total_food_g == base.total_food_g, "사료는 건드리면 안 된다"


def test_자유_텍스트로도_용량을_올릴_수_없다(skin_ds):
    """
    LLM 으로 바꿔도 이 보장이 유지되어야 한다.
    추출이 틀려도 최악의 결과가 '영양제를 안 준다' 여야 한다.
    """
    base = _pellets(build(skin_ds.profile, skin_ds.days, context=Context())[0])

    texts = [
        "아주 건강해요",
        "영양제를 더 주세요",
        "긁는 게 심해졌어요 많이요 아주 많이",
        "샴푸도 바꾸고 사료도 바꾸고 목욕도 시켰어요",
        "토했어요",
    ]
    for text in texts:
        ex = extract(text)
        plan, _, _ = build(
            skin_ds.profile, skin_ds.days, context=Context(answers=ex.answers)
        )
        assert _pellets(plan) <= base, f"{text!r} 가 용량을 올렸다"


def test_해석이_안_되는_말은_처방을_바꾸지_않는다(skin_ds):
    base, _, _ = build(skin_ds.profile, skin_ds.days, context=Context())
    ex = extract("오늘 산책하면서 사진 많이 찍었어요")

    after, _, _ = build(
        skin_ds.profile, skin_ds.days, context=Context(answers=ex.answers)
    )
    assert _pellets(after) == _pellets(base)


# ---------------------------------------------------------------------------
# 대화록
# ---------------------------------------------------------------------------

@pytest.fixture
def twin(skin_ds):
    rx, _ = prescribe(skin_ds.profile, skin_ds.days)
    return twin_mod.build(skin_ds.profile, skin_ds.days, rx.axes, rx)


def test_대화록은_인사와_관찰로_시작한다(twin):
    turns = chat_mod.build(twin, Context(), [])

    assert turns[0].role == "agent"
    assert twin.profile.name in turns[0].text
    assert any("긁" in t.text for t in turns), "관찰한 사실이 안 보인다"


def test_발화한_축에_대해서만_묻는다(twin):
    turns = chat_mod.build(twin, Context(), [])
    asked = {t.key for t in turns if t.kind == "question"}
    assert asked, "피부축이 발화한 상태다"

    skin_keys = {q.key for q in QUESTIONS[HealthAxis.SKIN]}
    assert asked <= skin_keys, "발화하지 않은 축까지 묻고 있다"


def test_질문에는_선택지와_이유가_붙는다(twin):
    for t in chat_mod.build(twin, Context(), []):
        if t.kind != "question":
            continue
        assert t.choices, f"{t.key} 에 선택지가 없다"
        assert t.why, f"{t.key} 에 묻는 이유가 없다"


def test_메모에서_나온_답은_문답으로_중복되지_않는다(twin):
    """
    메모에서 추출한 답을 문답으로 또 보여주면, 보호자가 쓴 문장보다 위에
    "풀밭에서 놀았나요? → 네"가 먼저 나온다. 순서가 거꾸로 읽힌다.
    """
    note = chat_mod.Note(
        text="풀밭에서 뒹굴었어요",
        at=datetime(2026, 9, 30, 10, 0),
        understood={"env_exposure": "yes"},
    )
    # updated_at 을 반드시 준다. 실제 경로는 db.load_context 가 답변 시각을
    # 함께 돌려주고, 그게 없으면 ask() 가 쿨다운을 쿨다운으로 보지 않는다.
    turns = chat_mod.build(
        twin,
        Context(answers={"env_exposure": "yes"}, updated_at=twin.as_of),
        [note],
    )

    questions = [t for t in turns if t.text == "최근에 풀밭이나 흙에서 놀았나요?"]
    assert not questions, "메모로 답한 질문이 문답으로 또 나왔다"
    assert any(t.kind == "understood" for t in turns)


def test_못_알아들은_메모도_대화록에_남는다(twin):
    note = chat_mod.Note(
        text="오늘 기분이 좋아 보여요",
        at=datetime(2026, 9, 30, 10, 0),
        understood={},
    )
    turns = chat_mod.build(twin, Context(), [note])

    assert any(t.role == "guardian" and "기분" in t.text for t in turns), \
        "보호자가 쓴 문장이 사라졌다"
    assert any(t.kind == "understood" and "이해하지 못" in t.text for t in turns), \
        "못 알아들었다는 말을 안 했다"


def test_저장된_yes_를_그대로_보여주지_않는다(twin):
    """화면에 'yes' 가 뜨면 안 된다. 사람이 읽는 말로 바꿔야 한다."""
    turns = chat_mod.build(twin, Context(answers={"shampoo_changed": "yes"}), [])
    guardian = [t.text for t in turns if t.role == "guardian"]
    assert "yes" not in guardian
    assert "네" in guardian


def test_기준선이_안_여물면_그걸_먼저_말한다(skin_ds):
    """
    비교할 평소가 없는 상태에서 "이상 없습니다"는 거짓말이다.
    모를 뿐이라는 것을 먼저 말해야 한다.
    """
    short = skin_ds.days[:5]
    rx, _ = prescribe(skin_ds.profile, short)
    t = twin_mod.build(skin_ds.profile, short, rx.axes, rx)

    turns = chat_mod.build(t, Context(), [])
    assert any("사료만" in x.text for x in turns)


def test_대화가_판단하지_않는다():
    """
    처방을 바꾸는 것은 interpret() 뿐이다. chat 모듈에 판단이 들어가면
    같은 결정이 두 군데 생기고 반드시 갈라진다.
    """
    import inspect

    src = inspect.getsource(chat_mod)
    for banned in ("defer_axes", "block_all", "AXIS_DOSE", "severity("):
        assert banned not in src, f"대화 모듈이 처방을 만지고 있다: {banned}"


def test_계획_요약은_숫자를_틀리지_않는다(skin_ds):
    plan, _, _ = build(skin_ds.profile, skin_ds.days, context=Context())
    turn = chat_mod.plan_summary(plan)

    assert str(plan.total_food_g) in turn.text
    pellets = _pellets(plan)
    if pellets:
        assert str(pellets) in turn.text


def test_해석_결과는_사람_말로_보여준다():
    """'env_exposure' 같은 내부 key 가 화면에 뜨면 안 된다."""
    note = chat_mod.Note(
        text="풀밭에서 놀았어요",
        at=datetime(2026, 9, 30, 10, 0),
        understood={"env_exposure": "yes"},
    )
    text = chat_mod.understood(note).text
    assert "env_exposure" not in text
    assert "풀밭" in text


# ---------------------------------------------------------------------------
# LLM 추출기
#
# 키 없이도 검증할 수 있는 것만 본다 - 방어층과 계약.
# 실제 호출은 키가 있어야 하므로 여기서 하지 않는다.
# ---------------------------------------------------------------------------

def test_LLM_이_모르는_key_를_만들_수_없다():
    """
    ALLOWED 가 QUESTIONS 와 어긋나면, LLM 이 내놓은 값이 저장은 되는데
    interpret() 가 몰라서 아무 일도 안 일어난다. 찾기 어려운 실패다.
    """
    from agent.llm import ALLOWED, MEANING

    known = {q.key for qs in QUESTIONS.values() for q in qs}
    assert set(ALLOWED) <= known, f"질문 목록에 없는 key: {set(ALLOWED) - known}"
    assert set(MEANING) == set(ALLOWED), "설명과 허용 목록이 어긋난다"


@pytest.mark.parametrize("raw, expect", [
    ({"vomit": "yes"}, {"vomit": "yes"}),
    ({"limping": "네"}, {"limping": "yes"}),
    ({"skin_visible": "보여요"}, {"skin_visible": "보여요"}),
    # --- 아래는 전부 버려져야 한다 ---
    ({"unknown_key": "yes"}, {}),
    ({"skin_visible": "아무거나"}, {}),
    ({"vomit": 123}, {}),
    ({"vomit": {"nested": 1}}, {}),
    ("문자열", {}),
    (None, {}),
    ([], {}),
])
def test_LLM_응답을_걸러낸다(raw, expect):
    """
    **모르는 것은 전부 버린다.**

    환각이든 프롬프트 인젝션이든 여기서 멈춘다. LLM 이 무엇을 내놓든
    ALLOWED 를 통과한 것만 시스템 안으로 들어온다.
    """
    from agent.llm import sanitize
    assert sanitize(raw) == expect


def test_프롬프트가_진단을_시키지_않는다():
    """
    LLM 에게 판단을 시키면 그 말이 화면에 뜨고, 그때부터 이 서비스는
    진단을 하는 서비스가 된다. 추출만 시킨다.
    """
    from agent.llm import build_prompt

    p = build_prompt("털이 빠져요")
    assert "진단하지 말고" in p
    assert "조언하지 말고" in p
    assert "없는 key 는 절대 만들지 마라" in p
    # 부정 처리 지침이 들어 있어야 한다 - 규칙 기반이 제일 많이 틀린 곳이다
    assert "안 했어요" in p


def test_키가_없으면_규칙으로_떨어진다(monkeypatch):
    """외부 API 하나가 죽었다고 화면이 멈추면 안 된다."""
    monkeypatch.delenv("PEBBLE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("PEBBLE_LLM_KEY", raising=False)

    from agent import llm
    assert llm.enabled() is False
    assert llm.extract_llm("털이 빠져요") is None

    e = extract("털이 빠져요")
    assert e.engine == "rules"
    assert "skin_visible" in e.answers


def test_LLM_이_실패해도_규칙이_받는다(monkeypatch):
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", "dummy")

    import agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "_call_gemini",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("network")))

    e = extract("토했어요")
    assert e.engine == "rules"
    assert e.answers == {"vomit": "yes"}


def test_LLM_이_켜지면_그_결과를_쓴다(monkeypatch):
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", "dummy")

    import agent.llm as llm_mod
    # 규칙 사전이 절대 못 잡는 문장. LLM 이 붙으면 이런 게 잡힌다.
    monkeypatch.setattr(
        llm_mod, "_call_gemini",
        lambda *a, **k: '{"eating_less": "yes", "noise": "yes"}',
    )

    e = extract("어제부터 통 입에 안 대고, 윗집이 밤새 쿵쿵거렸어요")
    assert e.engine == "llm"
    assert e.answers == {"eating_less": "yes", "noise": "yes"}


def test_코드펜스가_붙어도_읽는다(monkeypatch):
    """responseMimeType 를 줘도 모델이 펜스를 붙이는 경우가 있다."""
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", "dummy")

    import agent.llm as llm_mod
    monkeypatch.setattr(
        llm_mod, "_call_gemini",
        lambda *a, **k: '```json\n{"vomit": "yes"}\n```',
    )
    assert extract("토했어요").answers == {"vomit": "yes"}


def test_LLM_로도_용량을_올릴_수_없다(skin_ds, monkeypatch):
    """
    **이 보장이 LLM 을 여기 놓을 수 있는 이유다.**

    추출이 어떻게 틀리든 최악의 결과가 '영양제를 안 준다' 여야 한다.
    과다 급여가 아니다.
    """
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", "dummy")

    base = _pellets(build(skin_ds.profile, skin_ds.days, context=Context())[0])

    import agent.llm as llm_mod
    from agent.llm import ALLOWED

    # LLM 이 모든 항목에 '예'를 뱉는 최악의 경우
    everything = {k: v[0] for k, v in ALLOWED.items()}
    monkeypatch.setattr(
        llm_mod, "_call_gemini", lambda *a, **k: json.dumps(everything),
    )

    ex = extract("아무 말이나")
    plan, _, _ = build(
        skin_ds.profile, skin_ds.days, context=Context(answers=ex.answers)
    )
    assert _pellets(plan) <= base


def test_모든_key_가_사람_말을_가진다():
    """
    UNDERSTOOD_WORD 에 없는 key 는 화면에 **내부 이름 그대로** 뜬다.
    "eating_less — 이렇게 이해했어요" 같은 말이 보호자에게 나간다.
    """
    from agent.llm import ALLOWED

    missing = set(ALLOWED) - set(chat_mod.UNDERSTOOD_WORD)
    assert not missing, f"사람 말이 없는 key: {missing}"


def test_해석한_key_가_화면에_노출되지_않는다():
    """내부 key 가 하나라도 말풍선에 섞이면 안 된다."""
    from agent.llm import ALLOWED

    for key, values in ALLOWED.items():
        note = chat_mod.Note(
            text="테스트", at=datetime(2026, 9, 30, 10, 0),
            understood={key: values[0]},
        )
        text = chat_mod.understood(note).text
        assert key not in text, f"{key} 가 화면에 그대로 나온다"


# ---------------------------------------------------------------------------
# 비밀 취급
#
# 이 저장소는 Public 이다. 키가 로그·응답·예외 어디로든 새면 안 된다.
# ---------------------------------------------------------------------------

FAKE_KEY = "AIzaSyFAKE0000000000000000000000000000"


def test_시작_로그에_키가_없다(monkeypatch, capsys):
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", FAKE_KEY)

    import config
    for line in config.summary():
        print(line)

    out = capsys.readouterr().out
    assert FAKE_KEY not in out, "시작 로그에 키가 찍힌다"
    assert "gemini" in out, "켜졌다는 것은 알려야 한다"


def test_실패_로그에_키가_없다(monkeypatch, capsys):
    """
    HTTP 예외는 요청 URL·헤더를 문자열에 담는 경우가 있다.
    그걸 그대로 찍으면 키가 평문으로 로그에 남는다.
    """
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", FAKE_KEY)

    import agent.llm as llm_mod

    def boom(*a, **k):
        # 실제 urllib 예외가 하는 것처럼 URL 을 메시지에 담는다
        raise OSError(f"failed: https://example.com/v1?key={FAKE_KEY}")

    monkeypatch.setattr(llm_mod, "_call_gemini", boom)

    assert llm_mod.extract_llm("토했어요") is None
    out = capsys.readouterr().out
    assert FAKE_KEY not in out, "예외 메시지로 키가 샜다"


def test_키를_URL_이_아니라_헤더로_보낸다(monkeypatch):
    """
    ?key= 쿼리로 보내면 URL 에 키가 박히고, URL 은 예외 메시지·
    프록시 로그·스택트레이스 어디로든 샌다.
    """
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", FAKE_KEY)

    seen = {}

    import agent.llm as llm_mod

    def fake_post(url, payload, headers):
        seen["url"] = url
        seen["headers"] = headers
        return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}

    monkeypatch.setattr(llm_mod, "_post", fake_post)
    llm_mod.extract_llm("토했어요")

    assert FAKE_KEY not in seen["url"], "URL 에 키가 들어갔다"
    assert seen["headers"].get("x-goog-api-key") == FAKE_KEY


def test_응답에_키가_섞이지_않는다(monkeypatch):
    """앱으로 나가는 응답에 설정값이 새면 안 된다."""
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", FAKE_KEY)

    import agent.llm as llm_mod
    monkeypatch.setattr(llm_mod, "_call_gemini", lambda *a, **k: '{"vomit":"yes"}')

    e = extract("토했어요")
    assert FAKE_KEY not in e.model_dump_json()


def test_env_가_기존_값을_덮지_않는다(tmp_path, monkeypatch):
    """
    배포 환경에는 진짜 값이 이미 들어와 있다. 실수로 커밋된 .env 가
    그걸 덮으면 프로덕션이 개발 키로 돈다.
    """
    import config

    env = tmp_path / ".env"
    env.write_text("PEBBLE_LLM_KEY=from_file\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATHS", [env])

    monkeypatch.setenv("PEBBLE_LLM_KEY", "from_environment")
    config.load_env()
    assert os.environ["PEBBLE_LLM_KEY"] == "from_environment"

    monkeypatch.delenv("PEBBLE_LLM_KEY")
    config.load_env()
    assert os.environ["PEBBLE_LLM_KEY"] == "from_file"


def test_기본_모델이_살아있는_이름이다():
    """
    모델 이름은 생각보다 자주 죽는다. gemini-2.0-flash 를 기본값으로 뒀다가
    종료된 것을 뒤늦게 알았다 - 키를 넣어도 호출이 실패하고 조용히 규칙
    사전으로 떨어져서, "키를 넣었는데 왜 그대로지" 가 된다.

    이름까지 검증할 수는 없지만(호출해야 안다), 죽은 것으로 확인된 이름이
    다시 들어오는 것은 막는다. python -m check_llm 이 실물 확인을 맡는다.
    """
    from agent.llm import DEFAULT_MODEL

    retired = {"gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro",
               "gemini-pro", "claude-3-haiku-20240307"}
    for provider, model in DEFAULT_MODEL.items():
        assert model not in retired, f"{provider} 기본 모델이 종료된 이름이다: {model}"
        assert model, f"{provider} 기본 모델이 비어 있다"


def test_probe_가_키를_흘리지_않는다(monkeypatch, capsys):
    """진단은 시끄러워야 하지만, 키까지 떠들면 안 된다."""
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", FAKE_KEY)

    import agent.llm as llm_mod

    def boom(*a, **k):
        raise OSError(f"https://x/v1?key={FAKE_KEY}")

    monkeypatch.setattr(llm_mod, "_call_gemini", boom)

    ok, detail = llm_mod.probe()
    assert ok is False
    assert FAKE_KEY not in detail, "진단 메시지로 키가 샜다"
    assert FAKE_KEY not in capsys.readouterr().out


def test_probe_가_설정_누락을_구분한다(monkeypatch):
    monkeypatch.delenv("PEBBLE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("PEBBLE_LLM_KEY", raising=False)

    import agent.llm as llm_mod

    ok, detail = llm_mod.probe()
    assert ok is False and "PROVIDER" in detail

    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    ok, detail = llm_mod.probe()
    assert ok is False and "KEY" in detail

    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("PEBBLE_LLM_KEY", FAKE_KEY)
    ok, detail = llm_mod.probe()
    assert ok is False and "공급자" in detail


# ---------------------------------------------------------------------------
# 규칙 + LLM 결합
#
# 규칙은 재현율이 낮은 대신 부정 판정이 정확하다(실측 정밀도 100%).
# LLM 은 반대로 재현율이 높은 대신 부정을 가끔 놓친다.
# 약점이 겹치지 않으니 합치면 둘 다 얻는다.
# ---------------------------------------------------------------------------

def test_규칙이_부정을_LLM_보다_먼저_본다():
    """
    실측에서 LLM 이 "토는 안 했어요" 를 vomit 으로 읽었다.
    프롬프트에 그 문장을 예시로 박아뒀는데도 그랬다.

    부정 오탐은 제일 나쁜 종류다 - 보호자가 "안 그랬다" 고 말했는데
    그 반대로 처리된다.
    """
    from agent.extract import negated_keys

    assert "vomit" in negated_keys("토는 안 했어요")
    assert "vomit" in negated_keys("설사는 없었어요")
    assert "limping" in negated_keys("절지는 않아요")
    assert "shampoo_changed" in negated_keys("샴푸 바꾼 적 없어요")


def test_긍정_보고는_거부하지_않는다():
    """거부가 과하면 LLM 이 제대로 잡은 것을 지운다."""
    from agent.extract import negated_keys

    for text in ("어제 토했어요", "풀밭에서 놀았어요", "샴푸를 바꿨어요",
                 "목욕시켰어요", "다리를 절어요"):
        assert negated_keys(text) == set(), f"{text!r} 를 부정으로 봤다"


def test_한_절의_부정이_다른_절을_오염시키지_않는다():
    from agent.extract import negated_keys

    got = negated_keys("목욕은 했는데 토는 안 했어요")
    assert "vomit" in got
    assert "recent_bath" not in got, "목욕까지 부정으로 봤다"


def test_증상이_부정형인_key_는_거부_대상이_아니다():
    """
    "계단을 안 올라가요" 는 부정문이지만 증상이다.
    여기에 거부를 걸면 LLM 이 맞게 잡은 것을 지운다.
    """
    from agent.extract import NEGATION_EXEMPT, negated_keys

    assert {"stairs_avoid", "weather_cold", "eating_less"} <= NEGATION_EXEMPT
    for text in ("계단을 안 올라가요", "밥을 잘 안 먹어요", "산책을 못 했어요"):
        assert not (negated_keys(text) & NEGATION_EXEMPT)


def test_LLM_이_부정을_놓쳐도_규칙이_막는다(monkeypatch):
    """결합의 핵심. 이게 깨지면 합친 의미가 없다."""
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", FAKE_KEY)

    import agent.llm as llm_mod
    # LLM 이 부정을 놓치는 상황을 그대로 재현한다
    monkeypatch.setattr(llm_mod, "_call_gemini", lambda *a, **k: '{"vomit":"yes"}')

    e = extract("토는 안 했어요")
    assert e.engine == "llm", "LLM 경로를 탔어야 한다"
    assert "vomit" not in e.answers, "규칙이 막지 못했다"


def test_거부가_정상_추출까지_지우지_않는다(monkeypatch):
    monkeypatch.setenv("PEBBLE_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("PEBBLE_LLM_KEY", FAKE_KEY)

    import agent.llm as llm_mod
    monkeypatch.setattr(
        llm_mod, "_call_gemini",
        lambda *a, **k: '{"recent_bath":"yes","vomit":"yes"}',
    )

    e = extract("목욕은 했는데 토는 안 했어요")
    assert e.answers == {"recent_bath": "yes"}
