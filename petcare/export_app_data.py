"""
앱에 넣을 데모 데이터를 내보낸다.

Flutter 앱이 서버 없이도 실행되도록, 실제 알고리즘을 돌린 결과를
asset JSON으로 떨어뜨린다. 발표 시연 때 백엔드를 안 띄워도 된다.

실행:
    cd petcare
    python export_app_data.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core.constants import CARTRIDGES
from core.prescribe import prescribe
from core.signal import baseline_slice, extract_metrics, valid_mask
from mock.generator import SCENARIOS, generate

# Android 프로젝트의 assets 폴더로 바로 내보낸다
OUT = (
    Path(__file__).resolve().parent.parent
    / "app-android" / "app" / "src" / "main" / "assets"
)

# 앱 차트가 "평소 수준" 점선을 그리려면 baseline 값이 있어야 한다
TREND_KEYS = ["scratch_night", "activity_sec", "restless", "run_sec"]


def build(scenario: str) -> dict:
    ds = generate(scenario)
    rx, _ = prescribe(ds.profile, ds.days)

    valid = valid_mask(ds.days)
    metrics = extract_metrics(ds.days)
    n = len(ds.days)

    trend = {}
    for key in TREND_KEYS:
        base = baseline_slice(metrics[key], valid, n)
        trend[key] = {
            "values": [round(v, 1) for v in metrics[key][-7:]],
            "baseline": round(float(np.median(base)), 1) if base else 0.0,
        }

    return {
        "scenario": scenario,
        "profile": ds.profile.model_dump(mode="json"),
        # 앱은 최근 7일만 그린다 (baseline 30일은 계산용이라 보내지 않는다)
        "recent_days": [d.model_dump(mode="json") for d in ds.days[-7:]],
        "prescription": rx.model_dump(mode="json"),
        "trend": trend,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    for scenario in SCENARIOS:
        path = OUT / f"demo_{scenario}.json"
        payload = build(scenario)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        rx = payload["prescription"]
        items = " · ".join(f"{i['name']} {i['pellets']}알" for i in rx["items"]) or "없음"
        print(f"  {path.name:<20} {rx['food_grams']:>4}g   {items}")

    (OUT / "cartridges.json").write_text(
        json.dumps([c.model_dump(mode="json") for c in CARTRIDGES],
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"  cartridges.json      {len(CARTRIDGES)} slots")
    print(f"\n  → {OUT}")


if __name__ == "__main__":
    main()
