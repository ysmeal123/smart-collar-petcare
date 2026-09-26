"""
센서 융합 추론.

행동 지표들을 조합해 4개 축의 상태를 판정한다.
단일 지표가 아니라 '가중 z합'을 보는 것이 핵심이다 -
개별 지표가 우연히 임계를 넘어도 가중합이 넘지 않으면 처방하지 않는다.

판정에는 세 겹의 방어가 걸린다.
    ① 가중합 임계        개별 지표의 우연한 튐을 흡수
    ② 연속성 규칙        7일 창 전체가 우연히 낮은 경우를 방어
    ③ 종속 축 억제       증상 축에 독립 처방권을 주지 않음
"""

from __future__ import annotations

from datetime import date

import numpy as np
from pydantic import BaseModel, Field

from core.constants import (
    ALLERGY_AXIS_SENSITIVITY,
    AXIS_MIN_MAD,
    AXIS_SUPPRESSED_BY,
    AXIS_WEIGHTS,
    AXIS_WINDOW_DAYS,
    ESCALATION_RULES,
    ALERT_ONLY_AXES,
    BLOCKING_AXES,
    OBSERVATION_ONLY_AXES,
    PRESCRIBING_AXES,
    SIZE_MOBILITY_SENSITIVITY,
    SURGERY_EFFECT,
    Z_ENTER,
    Z_EXIT,
)
from core.constants import MIN_MAD
from core.models import AxisScore, DailySummary, DogProfile, HealthAxis
from core.signal import acute_z, axis_windows, extract_metrics, metric_z, valid_mask

# 심각도 0%가 되는 z와 100%가 되는 z
Z_SEVERITY_FLOOR = Z_ENTER
Z_SEVERITY_FULL = 6.0
# 갓 발화한 축도 의미 있는 용량은 받아야 한다
MIN_SEVERITY = 0.30


class PrescriptionState(BaseModel):
    """
    직전까지의 처방 이력. 히스테리시스와 일몰 판정에 쓴다.

    stateless하게 짜되 이 객체만 넘겨주면 이어서 판단할 수 있게 한다.
    """
    active_axes: list[HealthAxis] = Field(default_factory=list)
    started_at: dict[str, date] = Field(default_factory=dict, description="축별 처방 시작일")
    baseline_frozen: bool = False
    prev_food_grams: int | None = None


def _profile_sensitivity(profile: DogProfile, axis: HealthAxis) -> float:
    """프로필이 축 민감도에 미치는 영향."""
    k = 1.0

    # 이미 알러지 체질이면 피부 신호를 더 빨리 잡는다
    for allergen in profile.allergies:
        k *= ALLERGY_AXIS_SENSITIVITY.get(allergen, {}).get(axis, 1.0)

    # 소형견은 슬개골, 대형견은 고관절 문제가 호발한다
    if axis is HealthAxis.MOBILITY:
        k *= SIZE_MOBILITY_SENSITIVITY[profile.size]

    return k


def _baseline_shift(profile: DogProfile, axis: HealthAxis) -> float:
    """
    수술 이력에 따른 기준선 보정.

    십자인대 수술한 개는 원래부터 덜 뛴다.
    이걸 모르면 시스템이 '관절이 악화 중'으로 오판해 영양제를 계속 늘린다.
    """
    shift = 0.0
    for surg in profile.surgeries:
        effect = SURGERY_EFFECT.get(surg.type)
        if effect and effect["axis"] is axis:
            shift += effect["baseline_shift"]
    return shift


def _message(axis: HealthAxis, contributors: dict[str, float],
             ratios: dict[str, float]) -> str:
    """
    사용자에게 보여줄 문장.

    진단 언어를 쓰지 않는다. 관찰된 사실만 서술한다.
        X  "피부염입니다"
        O  "밤에 긁는 횟수가 평소보다 2.3배 늘었어요"
    """
    if not contributors:
        return ""

    key = max(contributors, key=lambda k: abs(contributors[k]))
    ratio = ratios.get(key, 1.0)

    labels = {
        "scratch_night": "밤에 긁는 횟수",
        "body_shake": "몸을 터는 횟수",
        "head_shake": "머리를 흔드는 횟수",
        "vigorous_sec": "활발하게 노는 시간",
        "intake_ratio": "먹는 양",
        "restless": "자면서 뒤척이는 횟수",
        "restless_night": "한밤중 뒤척이는 횟수",
        "run_sec": "뛰는 시간",
        "walk_sec": "걷는 시간",
        "sleep_min": "자는 시간",
        "night_activity": "밤에 움직이는 시간",
    }
    label = labels.get(key, key)

    if ratio >= 1.15:
        return f"{label}가 평소보다 {ratio:.1f}배 늘었어요"
    if ratio <= 0.85 and ratio > 0:
        return f"{label}이 평소의 {ratio:.0%} 수준으로 줄었어요"
    return f"{label}에 변화가 있어요"


def evaluate_axes(
    profile: DogProfile, days: list[DailySummary],
    state: PrescriptionState | None = None,
) -> list[AxisScore]:
    """웰니스 5축을 판정한다. 반환 순서는 처방 권한이 있는 축부터."""
    state = state or PrescriptionState()
    valid = valid_mask(days)
    metrics = extract_metrics(days)
    n = len(days)

    results: list[AxisScore] = []
    fired: set[HealthAxis] = set()

    # 처방 권한이 있는 축을 먼저 평가해야 종속 축 억제가 제대로 걸린다
    ordered = PRESCRIBING_AXES + OBSERVATION_ONLY_AXES

    for axis in ordered:
        sens = _profile_sensitivity(profile, axis)
        shift = _baseline_shift(profile, axis)

        scores: list[float] = []
        last_contrib: dict[str, float] = {}
        last_ratios: dict[str, float] = {}

        for lo, hi in axis_windows(axis, n):
            total = 0.0
            contrib: dict[str, float] = {}
            ratios: dict[str, float] = {}

            floor = AXIS_MIN_MAD.get(axis, MIN_MAD)
            for key, weight in AXIS_WEIGHTS[axis].items():
                r = metric_z(metrics, valid, key, lo, hi, n, floor)
                contrib[key] = weight * r.z * sens
                ratios[key] = r.ratio
                total += contrib[key]

            scores.append(total + shift)
            if not last_contrib:
                last_contrib, last_ratios = contrib, ratios

        # 히스테리시스: 이미 처방 중인 축은 낮은 임계로 유지 판정한다.
        # 임계 하나로 켜고 끄면 경계에서 매일 진동한다.
        threshold = Z_EXIT if axis in state.active_axes else Z_ENTER

        over = all(abs(s) >= threshold for s in scores)
        same_sign = len({s > 0 for s in scores}) == 1
        active = over and same_sign

        # 종속 축 억제.
        # 가려움·통증 때문에 잠을 못 자는 것이라면, 진정제가 아니라
        # 원인을 먼저 치료해야 한다.
        blockers = [a for a in AXIS_SUPPRESSED_BY.get(axis, []) if a in fired]
        suppressed = active and bool(blockers)

        # 귀축은 알림만, 식욕축은 차단만 한다. 영양제를 움직이지 않는다.
        no_authority = axis in OBSERVATION_ONLY_AXES

        prescribing = active and not suppressed and not no_authority
        if prescribing:
            fired.add(axis)

        note = ""
        if suppressed:
            names = "/".join(a.value for a in blockers)
            note = f" (원인은 {names} — 그쪽을 먼저 치료합니다)"
        elif active and axis in ALERT_ONLY_AXES:
            note = " (병원에서 확인해 주세요 — 영양제로 다룰 문제가 아닙니다)"
        elif active and axis in BLOCKING_AXES:
            note = " (원인이 확인될 때까지 영양제 변경을 멈췄습니다)"

        results.append(AxisScore(
            axis=axis,
            z_score=round(scores[0], 3),
            active=prescribing,
            contributors={k: round(v, 3) for k, v in last_contrib.items()},
            message=(_message(axis, last_contrib, last_ratios) + note) if active else "",
        ))

    return results


def severity(z: float) -> float:
    """
    축 점수를 0~1 심각도로 바꾼다.

    Z_ENTER에서 시작해 Z_SEVERITY_FULL에서 100%가 된다.
    갓 발화한 축도 MIN_SEVERITY만큼은 받아야 의미 있는 용량이 나온다.
    """
    raw = (abs(z) - Z_SEVERITY_FLOOR) / (Z_SEVERITY_FULL - Z_SEVERITY_FLOOR)
    return float(np.clip(raw, MIN_SEVERITY, 1.0))


def check_escalation(days: list[DailySummary]) -> tuple[bool, str]:
    """
    Layer 0 — 긴급 정지.

    급성 이상은 영양제로 대응할 상황이 아니다.
    전 처방을 동결하고 수의사 내원을 권고한다. 다른 모든 로직에 우선한다.
    """
    valid = valid_mask(days)
    metrics = extract_metrics(days)

    rule = ESCALATION_RULES["acute_pain"]
    w, r = rule["window_days"], rule["ref_days"]

    act = acute_z(metrics, valid, "activity_sec", w, r)
    wake = acute_z(metrics, valid, "restless_night", w, r)

    if act.z <= rule["activity_z"] and wake.z >= rule["night_wake_z"]:
        return True, (
            f"최근 {w}일 활동량이 급격히 줄고(z={act.z:+.1f}) "
            f"밤중 뒤척임이 크게 늘었습니다(z={wake.z:+.1f}). "
            "통증이 있을 수 있으니 병원 진료를 권합니다."
        )

    drop = ESCALATION_RULES["appetite_drop"]
    tail = [d.intake_ratio for d in days[-drop["days"]:]]
    if tail and all(x < drop["ratio"] for x in tail):
        pct = " · ".join(f"{x:.0%}" for x in tail)
        return True, (
            f"{drop['days']}일 연속 사료를 남기고 있습니다 ({pct}). "
            "병원 진료를 권합니다."
        )

    return False, ""
