"""
LLM 설정 확인.

    python -m check_llm

키를 넣은 직후에 한 번 돌린다. **실제로 한 번 호출해서** 되는지 본다.

왜 필요한가. 평소 동작은 실패하면 조용히 규칙 사전으로 떨어진다.
서비스가 안 멈추는 건 맞지만, 설정이 틀렸을 때도 똑같이 조용해서
"키를 넣었는데 왜 그대로지" 를 겪게 된다. 여기서만 시끄럽게 말한다.

키 값은 찍지 않는다. 어디까지 설정됐는지와 실패 원인만 보여준다.
"""

from __future__ import annotations

import sys

from config import load_env


def main() -> int:
    files = load_env()
    from agent import llm

    for f in files:
        print(f".env 읽음: {f}")

    provider = llm._provider()
    has_key = bool(__import__("os").environ.get("PEBBLE_LLM_KEY", "").strip())

    print()
    print(f"  공급자   {provider or '(비어 있음)'}")
    print(f"  키       {'설정됨' if has_key else '(비어 있음)'}")
    print(f"  모델     {llm._model() or '(비어 있음)'}")
    print()

    if not llm.enabled():
        print("LLM 이 꺼져 있습니다. 규칙 사전으로 동작합니다.")
        print()
        print("켜려면 .env 에 다음을 넣으세요:")
        print("  PEBBLE_LLM_PROVIDER=gemini")
        print("  PEBBLE_LLM_KEY=<AI Studio 에서 받은 키>")
        print()
        print("키 받는 곳: https://aistudio.google.com/apikey")
        return 1

    print("실제로 한 번 호출해 봅니다...")
    ok, detail = llm.probe()
    print()

    if ok:
        print(f"  정상 — {detail}")
        print()
        print("이제 성능을 재 보세요:")
        print("  python -m bench_extract")
        return 0

    print(f"  실패 — {detail}")
    print()
    if "키" in detail:
        print("① 키부터 확인하세요")
        print("   https://aistudio.google.com/apikey")
        print("   복사할 때 앞뒤 공백이나 줄바꿈이 섞이지 않았는지 보세요.")
        print()

    if "모델" in detail:
        print("② 모델 이름 (2026-10 기준):")
        print("   gemini-3.5-flash-lite    제일 싸고 빠름")
        print("   gemini-3.5-flash         기본값")
        print("   gemini-3.8-flash         제일 똑똑함")
        print()
        print("   .env 의 PEBBLE_LLM_MODEL 에 넣으면 됩니다.")
        print("   최신 목록: https://ai.google.dev/gemini-api/docs/models")

    print()
    print("고치기 전까지는 규칙 사전으로 계속 동작합니다. 서비스는 멈추지 않습니다.")
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
