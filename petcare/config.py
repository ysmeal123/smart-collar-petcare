"""
설정 로딩.

**비밀은 코드에 없다.** 전부 환경변수로 들어온다.

    배포(Render)   Environment 탭에 넣는다. 저장소에 안 들어간다
    로컬 개발      셸에서 export 하거나, .env 파일에 둔다

`.env` 는 `.gitignore` 가 막는다. 이 저장소는 Public 이라, 키가 한 번
커밋되면 지워도 히스토리에 남고 봇이 몇 분 안에 긁어간다.

python-dotenv 를 쓰지 않는다. 하는 일이 열 줄짜리라 의존성을 늘릴 이유가 없다.
"""

from __future__ import annotations

import os
from pathlib import Path

#: petcare/ 와 저장소 루트 둘 다 본다.
#: 어디서 만들든 읽히는 편이 "왜 안 먹지" 를 겪는 것보다 낫다.
ENV_PATHS = [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / ".env",
]


def load_env() -> list[str]:
    """
    `.env` 를 환경변수로 올린다. **이미 설정된 값은 덮지 않는다.**

    배포 환경에는 진짜 값이 이미 들어와 있다. 실수로 커밋된 .env 가
    그걸 덮으면 프로덕션이 개발 키로 돌게 된다.

    반환: 실제로 읽은 파일 경로들 (로그용)
    """
    loaded = []
    for path in ENV_PATHS:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
        loaded.append(str(path))
    return loaded


def summary() -> list[str]:
    """
    시작 로그에 찍을 설정 상태.

    **값은 절대 찍지 않는다.** 켜졌는지만 말한다.
    로그는 Render 대시보드에 남고, 화면 공유·스크린샷으로도 나간다.
    """
    from agent import llm

    lines = []
    if llm.enabled():
        lines.append(f"[pebble] LLM 해석: {llm._provider()} / {llm._model()}")
    else:
        lines.append("[pebble] LLM 해석: 꺼짐 (규칙 사전으로 동작)")
    return lines
