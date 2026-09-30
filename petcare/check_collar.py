"""
목줄 데이터 검증기.

    python -m check_collar sample_collar.json

온디바이스 AI / 펌웨어 담당이 **서버에 넘기기 전에 스스로 확인**하는 도구다.
하드웨어가 없어도 JSON 파일 하나만 있으면 돌아간다.

입력은 IngestBatch 하나 또는 리스트. 형식은 HANDOFF-COLLAR.md 참조.
통과하면 exit 0, 하나라도 틀리면 exit 1 과 함께 무엇이 틀렸는지 찍는다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import timedelta

from pydantic import ValidationError

from core.constants import CONFIDENCE_GATE
from core.telemetry import (
    SAMPLE_SPAN_SEC,
    IngestBatch,
    activity_by_hour,
    resolve_clock,
    wear_seconds_by_hour,
)

# 윈도우 기본 콘솔은 cp949 다. 표에 쓰는 기호 하나 때문에 도구가 죽으면
# 아무도 안 쓴다. 인코딩할 수 없는 글자는 조용히 '?' 로 바꾼다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

PASS = "  OK  "
FAIL = " 실패 "


class Report:
    """
    검사 결과를 찍는다.

    통과할 때와 실패할 때 **다른 문구**를 받는다. 실패 문구를 양쪽에 다 쓰면
    통과한 항목에 경고문이 붙어서 무엇이 문제인지 알 수 없게 된다.
    """

    def __init__(self) -> None:
        self.failed = False

    def check(self, ok: bool, label: str, good: str = "", bad: str = "") -> None:
        detail = good if ok else bad
        print(f"[{PASS if ok else FAIL}] {label}" + (f" - {detail}" if detail else ""))
        if not ok:
            self.failed = True

    def note(self, label: str, value: str) -> None:
        print(f"         {label:<22} {value}")


def load(path: str) -> list[IngestBatch]:
    raw = json.loads(open(path, encoding="utf-8").read())
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for i, item in enumerate(items):
        try:
            out.append(IngestBatch.model_validate(item))
        except ValidationError as e:
            print(f"[{FAIL}] {i}번째 묶음이 스키마에 맞지 않습니다\n")
            print(e)
            sys.exit(1)
    return out


def check_events(batches: list[IngestBatch], r: Report) -> None:
    events = [e for b in batches for e in b.events]
    if not events:
        r.check(False, "이벤트", bad="한 건도 없습니다. 최소 scratch 는 있어야 합니다")
        return

    # 같은 boot_id 안에서 seq 가 겹치면 서버의 중복 제거가 실제 이벤트를 지운다
    by_boot: dict[int, list[int]] = {}
    for b in batches:
        by_boot.setdefault(b.boot_id, []).extend(e.seq for e in b.events)
    dup = {boot: n for boot, seqs in by_boot.items()
           if (n := len(seqs) - len(set(seqs)))}
    r.check(
        not dup, "seq 중복 없음",
        good=f"{len(events)}건 전부 고유",
        bad=f"같은 boot_id 안에서 seq 가 겹칩니다 {dup}. "
            "서버의 중복 제거가 실제 이벤트를 삭제합니다",
    )

    kinds = Counter(e.type.value for e in events)
    r.note("행동 분포", ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
    if "scratch" not in kinds:
        r.note("참고", "scratch 가 없습니다. 피부축이 아예 판단을 못 합니다")

    low = sum(1 for e in events if e.conf < CONFIDENCE_GATE)
    ratio = low / len(events)
    r.check(
        ratio < 0.30, "신뢰도",
        good=f"{CONFIDENCE_GATE} 미만 {ratio:.0%}",
        bad=f"{ratio:.0%} 가 {CONFIDENCE_GATE} 미만입니다. 서버가 그만큼 버립니다",
    )

    odd = [e for e in events if not 0.1 <= e.dur_s <= 120]
    r.check(
        not odd, "지속시간",
        good="전부 0.1~120초",
        # bad= 는 통과할 때도 평가된다. odd 가 비어 있으면 odd[0] 이 터진다.
        bad=(f"{len(odd)}건이 범위를 벗어났습니다 (예: {odd[0].dur_s}초)"
             if odd else ""),
    )


def check_status(batches: list[IngestBatch], r: Report) -> None:
    status = [s for b in batches for s in b.status]
    if not status:
        r.check(False, "1분 요약",
                bad="한 건도 없습니다. 이게 없으면 급여량을 정할 수 없습니다")
        return

    over = [s for s in status
            if s.rest_sec + s.walk_sec + s.run_sec + s.vigorous_sec > SAMPLE_SPAN_SEC]
    r.check(
        not over, "활동 초 합계",
        good="모든 표본이 60초 이내",
        bad=f"{len(over)}개 표본에서 네 클래스 합이 60초를 넘습니다",
    )

    worn = sum(s.worn_sec for s in status)
    covered = len(status) * SAMPLE_SPAN_SEC
    wr = worn / covered if covered else 0.0
    r.check(
        wr >= 0.60, "착용률",
        good=f"{wr:.0%}",
        bad=f"{wr:.0%} - 60% 미만인 날은 서버가 통계에서 제외합니다",
    )
    if {s.worn_sec for s in status} == {SAMPLE_SPAN_SEC}:
        r.note("참고", "worn_sec 이 전부 60입니다. 착용 감지가 실제로 도는지 확인")

    act = Counter()
    for s in status:
        act.update({"rest": s.rest_sec, "walk": s.walk_sec,
                    "run": s.run_sec, "vigorous": s.vigorous_sec})
    total = sum(act.values()) or 1
    r.note("활동 비율", ", ".join(f"{k} {v / total:.0%}" for k, v in act.items()))
    r.check(
        act["walk"] + act["run"] + act["vigorous"] > 0, "활동 클래스",
        good=f"활동 {(total - act['rest']) / total:.0%}",
        bad="휴식만 있습니다. Model A 가 걷기를 한 번도 못 잡았습니다",
    )

    hours = set()
    for b in batches:
        hours |= set(wear_seconds_by_hour(b))
    r.check(
        len(hours) >= 12, "관측 길이",
        good=f"{len(hours)}시간",
        bad=f"{len(hours)}시간뿐입니다. 하루 전체(20시간 이상)를 권합니다",
    )

    # 모든 표본이 한 시간대에 몰려 있으면 t_ms 가 안 흐른다는 뜻이다
    spread = {h for b in batches for h in activity_by_hour(b)}
    r.check(
        len(spread) >= 2, "t_ms 진행",
        good=f"{len(spread)}개 시간대에 분포",
        bad="모든 표본이 같은 시간대에 있습니다. t_ms 가 증가하지 않습니다",
    )


def check_clock(batches: list[IngestBatch], r: Report) -> None:
    for b in batches:
        boot_at = resolve_clock(b)
        last = max([e.t_ms for e in b.events] + [s.t_ms for s in b.status] + [0])
        end = boot_at + timedelta(milliseconds=last)
        r.check(
            last <= b.uptime_ms, f"시계 (boot_id={b.boot_id})",
            good=f"부팅 {boot_at:%m-%d %H:%M} ~ 마지막 {end:%m-%d %H:%M}",
            bad=f"uptime_ms({b.uptime_ms}) 보다 미래의 t_ms({last}) 가 있습니다",
        )


def main(path: str) -> int:
    batches = load(path)
    r = Report()
    print(f"\n{path} - 묶음 {len(batches)}개\n")
    r.check(True, "스키마", good="IngestBatch 로 파싱됨")

    check_events(batches, r)
    check_status(batches, r)
    check_clock(batches, r)

    print()
    if r.failed:
        print("실패한 항목이 있습니다. HANDOFF-COLLAR.md 의 '절대 규칙' 을 보세요.")
        return 1
    print("전부 통과. 이 형식으로 보내면 서버가 받습니다.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
