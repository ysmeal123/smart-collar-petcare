"""
배포 직후 한 번 돌린다.

    python -m seed_remote

DATABASE_URL 이 있으면 PostgreSQL, 없으면 로컬 SQLite 에 쓴다.

**이미 데이터가 있으면 아무것도 하지 않는다.** 재배포 때마다 덮어쓰면
그동안 쌓인 실제 데이터가 날아간다.
"""

from __future__ import annotations

import sys

from seed_live import COLLARS, seed
from store import db
from store.dialect import backend


def main() -> int:
    db.init()
    print(f"저장소: {backend()}")

    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM dogs").fetchone()["n"]
        if n:
            print(f"이미 개체 {n}마리가 있습니다. 시드를 건너뜁니다.")
            return 0

        for scenario in COLLARS:
            r = seed(scenario, conn)
            print(
                f"  [{r['scenario']}] {r['dog_id']}  "
                f"사료 {r['food_grams']}g  명령 {r['commands']}건"
            )

    print()
    print("시드 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
