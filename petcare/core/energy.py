"""
사료량 산출 — 캐스케이드 제어.

    [외부 루프 · 느림] 체중 추세  ->  활동계수 기준값 보정
                                        |
    [내부 루프 · 빠름] 활동량      ->  활동계수  ->  DER  ->  급여량(g)

외부 루프가 없으면 활동계수의 작은 오차가 몇 달에 걸쳐 누적되어
개를 비만이나 저체중으로 서서히 몰고 간다(개루프 드리프트).
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel

from core.constants import (
    DAILY_GRAM_SLEW_RATIO,
    K_ABSOLUTE_MIN,
    FOOD_BY_ID,
    K_ACTIVITY_GAIN,
    K_BASE_INTACT,
    K_BASE_NEUTERED,
    K_BASE_PUPPY_EARLY,
    K_BASE_PUPPY_LATE,
    K_BASE_SENIOR,
    K_BASE_WEIGHT_LOSS,
    K_CLAMP_RATIO,
    RECENT_DAYS,
    TARGET_WEIGHT_RATIO,
    WEIGHT_CORRECTION_STEP,
    WEIGHT_DRIFT_THRESHOLD,
)
from core.models import BodyCondition, DailySummary, DogProfile, TraceStep
from core.signal import baseline_slice, extract_metrics, robust_z, valid_mask

# 외부 루프 보정 한계. 한 번에 너무 크게 흔들면 그 자체가 불안정해진다.
MAX_WEIGHT_CORRECTION = 0.10
# 목표 대비 편차가 이 이상이면 비례항이 작동한다
WEIGHT_GAP_THRESHOLD = 0.05


class EnergyResult(BaseModel):
    der_kcal: float
    food_grams: int
    k_base: float
    k_final: float
    activity_z: float
    trace: list[TraceStep]


def target_weight(profile: DogProfile) -> float:
    """목표 체중. 명시하지 않았으면 체형에서 유도한다."""
    if profile.target_weight_kg:
        return profile.target_weight_kg
    return profile.weight_kg * TARGET_WEIGHT_RATIO[profile.body_condition.value]


def base_multiplier(profile: DogProfile) -> tuple[float, str]:
    """정적 데이터만으로 정하는 활동계수 기준값."""
    if profile.age_months < 4:
        return K_BASE_PUPPY_EARLY, "성장기(4개월 미만)"
    if profile.is_puppy:
        return K_BASE_PUPPY_LATE, "성장기"
    if profile.body_condition is BodyCondition.OVERWEIGHT:
        return K_BASE_WEIGHT_LOSS, "체중 감량"
    if profile.is_senior:
        return K_BASE_SENIOR, "노령"
    if profile.neutered:
        return K_BASE_NEUTERED, "중성화 성견"
    return K_BASE_INTACT, "미중성화 성견"


def _measured_weights(days: list[DailySummary]) -> list[float]:
    """체중계는 개가 올라간 날만 값을 준다. 결측이 기본이다."""
    return [d.weight_kg for d in days if d.weight_kg is not None]


def weight_correction(
    profile: DogProfile, days: list[DailySummary]
) -> tuple[float, str]:
    """
    외부 루프 — 체중 되먹임.

    변화율(미분항)과 목표 편차(비례항)를 함께 본다.
    변화율만 보면 '목표보다 계속 무거운' 정체 상태를 영영 놓친다.
    (실측: 목표 25kg인데 28.7kg인 개가 변화율 +0.27%/주라 아무것도 발동 안 함)
    """
    w = _measured_weights(days)
    if len(w) < 8:
        return 0.0, "체중 측정 부족 — 외부 루프 미적용"

    first = float(np.median(w[:5]))
    last = float(np.median(w[-5:]))
    weeks = max(len(days) / 7.0, 1.0)

    drift = (last - first) / first / weeks          # 주당 변화율
    gap = (last - target_weight(profile)) / target_weight(profile)

    correction = 0.0
    reasons = []

    if abs(drift) >= WEIGHT_DRIFT_THRESHOLD:
        correction -= np.sign(drift) * WEIGHT_CORRECTION_STEP
        reasons.append(f"변화율 {drift:+.1%}/주")

    if abs(gap) >= WEIGHT_GAP_THRESHOLD:
        correction -= np.sign(gap) * WEIGHT_CORRECTION_STEP
        reasons.append(f"목표 대비 {gap:+.0%}")

    correction = float(np.clip(correction, -MAX_WEIGHT_CORRECTION, MAX_WEIGHT_CORRECTION))

    if not reasons:
        return 0.0, f"체중 {last:.1f}kg 안정 — 보정 없음"
    return correction, f"체중 {last:.1f}kg ({' · '.join(reasons)}) → 계수 {correction:+.0%}"


def compute_energy(
    profile: DogProfile, days: list[DailySummary],
    prev_grams: int | None = None, frozen: bool = False,
) -> EnergyResult:
    """
    frozen=True 이면 외부 루프(체중 되먹임)를 정지한다.

    급성 이상으로 긴급 정지된 상태에서는 체중이 병 때문에 빠진다.
    이걸 '사료가 부족하다'로 해석해 급여량을 늘리면 위험하다.
    (실측: 사료를 52%만 먹는 개에게 계수를 +10% 올리려 했다)
    """
    trace: list[TraceStep] = []

    # --- RER: 체중만으로 정해지는 휴식기 요구량 -------------------------
    rer = profile.rer
    trace.append(TraceStep(
        step="RER",
        detail=f"70 × {profile.weight_kg}kg^0.75 = {rer:.0f} kcal",
    ))

    # --- 활동계수 기준값 (정적) -----------------------------------------
    k_base, label = base_multiplier(profile)
    trace.append(TraceStep(step="활동계수 기준", detail=f"{label} → k_base = {k_base}"))

    # --- 외부 루프: 체중 되먹임 -----------------------------------------
    if frozen:
        trace.append(TraceStep(
            step="외부 루프(체중)",
            detail="긴급 정지 중 — 체중 되먹임을 정지합니다 "
                   "(병으로 인한 체중 감소를 사료 부족으로 해석하지 않기 위해)",
            changed=True,
        ))
    else:
        corr, corr_msg = weight_correction(profile, days)
        if corr:
            k_base *= (1 + corr)
        trace.append(TraceStep(step="외부 루프(체중)", detail=corr_msg, changed=bool(corr)))

    # --- 내부 루프: 당일 활동량 -----------------------------------------
    valid = valid_mask(days)
    metrics = extract_metrics(days)
    act = metrics["activity_sec"]
    n = len(days)
    z = robust_z(
        baseline_slice(act, valid, n),
        [v for v, ok in zip(act[-RECENT_DAYS:], valid[-RECENT_DAYS:]) if ok],
    ).z

    k_raw = k_base + K_ACTIVITY_GAIN * z
    k = float(np.clip(k_raw, k_base * (1 - K_CLAMP_RATIO), k_base * (1 + K_CLAMP_RATIO)))
    clamped = abs(k - k_raw) > 1e-9

    notes = " (일일 변동폭 제한)" if clamped else ""
    trace.append(TraceStep(
        step="내부 루프(활동량)",
        detail=f"활동 z={z:+.2f} → k = {k:.2f}{notes}",
        changed=True,
    ))

    # RER 아래로는 절대 내려가지 않는다.
    # 활동량이 아무리 낮아도 기초대사량은 채워야 한다.
    if k < K_ABSOLUTE_MIN:
        trace.append(TraceStep(
            step="🛡 최소 급여 보장",
            detail=f"k {k:.2f} → {K_ABSOLUTE_MIN:.2f} (DER이 RER 미만이 되지 않도록)",
            changed=True,
        ))
        k = K_ABSOLUTE_MIN

    # --- DER -> 급여량 ---------------------------------------------------
    der = rer * k
    food = FOOD_BY_ID[profile.food_id]
    grams = der / food.kcal_per_g
    trace.append(TraceStep(
        step="DER",
        detail=f"{rer:.0f} × {k:.2f} = {der:.0f} kcal → "
               f"{food.name} {grams:.0f}g ({food.kcal_per_g} kcal/g)",
    ))

    # --- 슬루율 제한 ------------------------------------------------------
    # 급여량이 하루 만에 크게 흔들리면 위장에 부담이 된다.
    if prev_grams:
        lo = prev_grams * (1 - DAILY_GRAM_SLEW_RATIO)
        hi = prev_grams * (1 + DAILY_GRAM_SLEW_RATIO)
        limited = float(np.clip(grams, lo, hi))
        if abs(limited - grams) > 0.5:
            trace.append(TraceStep(
                step="슬루율 제한",
                detail=f"{grams:.0f}g → {limited:.0f}g "
                       f"(전일 {prev_grams}g 대비 ±{DAILY_GRAM_SLEW_RATIO:.0%} 이내)",
                changed=True,
            ))
        grams = limited

    return EnergyResult(
        der_kcal=round(der, 1),
        food_grams=int(round(grams)),
        k_base=round(k_base, 3),
        k_final=round(k, 3),
        activity_z=round(z, 3),
        trace=trace,
    )
