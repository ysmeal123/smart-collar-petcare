"""
오케스트레이터.

앞의 모듈들을 정해진 순서로 호출해 하루치 처방을 만든다.
순서 자체가 안전 설계의 일부다.

    1. 긴급 정지 검사        <- 통과 못 하면 여기서 끝. 영양제 0
    2. 축 추론
    3. DER -> 급여량         <- 사료가 먼저다. 사료 영양소를 알아야 헤드룸이 나온다
    4. 축 -> 목표 용량
    5. 금기 차단 / 대체
    6. 알갱이 정수화
    7. 상한 검증             <- 최종 사출량 그대로 검증
    8. 끼니 배분

모든 단계는 PrescriptionTrace에 기록되어 앱에서 그대로 펼쳐볼 수 있다.
"""

from __future__ import annotations

from datetime import date

from core.constants import (
    AXIS_DOSE,
    AXIS_DOSE_NUTRIENT,
    CARTRIDGE_BY_ID,
    GUT_DOSE_PER_KG,
    GUT_TRIGGERS,
    SUNSET_WEEKS,
)
from core.dispense import apply_single_meal_penalty, assign_meals, to_pellets
from core.energy import compute_energy
from core.inference import PrescriptionState, check_escalation, evaluate_axes, severity
from core.models import (
    DailySummary,
    DogProfile,
    Medication,
    Prescription,
    SurgeryType,
    TraceStep,
)
from core.safety import check_zinc_copper, enforce_upper_limits, resolve_blocks, soft_limits


def _gut_trigger(profile: DogProfile) -> str | None:
    """
    장건강은 IMU가 아니라 문진으로 처방한다.

    "식후 몸털기 = 소화 불편"은 근거가 약하고 피부축과 교차 오염된다.
    센서로 추론할 수 없는 것은 센서로 처방하지 않는다.
    """
    if Medication.ANTIBIOTIC in profile.medications:
        return "항생제 복용 중 — 장내 세균총 회복"
    if profile.gi_symptom_reported:
        return "설사·구토 보고됨"
    for s in profile.surgeries:
        if s.type is SurgeryType.GI and s.years_ago <= GUT_TRIGGERS["gi_surgery_recent_years"]:
            return "최근 소화기 수술 이력"
    return None


def prescribe(
    profile: DogProfile,
    days: list[DailySummary],
    state: PrescriptionState | None = None,
    today: date | None = None,
) -> tuple[Prescription, PrescriptionState]:
    state = state or PrescriptionState()
    today = today or days[-1].date
    trace: list[TraceStep] = []

    # ── 1. 긴급 정지 (Layer 0) ─────────────────────────────────────────
    # 다른 모든 로직에 우선한다. 급성 이상은 영양제로 대응할 상황이 아니다.
    escalated, reason = check_escalation(days)

    energy = compute_energy(profile, days, state.prev_food_grams, frozen=escalated)

    if escalated:
        trace.append(TraceStep(step="🚨 긴급 정지", detail=reason, changed=True))
        trace.append(TraceStep(
            step="처방 동결",
            detail="영양제를 전부 중단했습니다. 사료만 정상 급여합니다.",
            changed=True,
        ))
        rx = Prescription(
            dog_id=profile.dog_id, date=today,
            der_kcal=energy.der_kcal, food_grams=energy.food_grams,
            axes=evaluate_axes(profile, days, state),
            items=[], escalated=True, escalation_reason=reason,
            trace=trace + energy.trace,
        )
        return rx, PrescriptionState(prev_food_grams=energy.food_grams)

    # ── 2. 축 추론 ─────────────────────────────────────────────────────
    axes = evaluate_axes(profile, days, state)
    fired = [a for a in axes if a.active]

    if fired:
        trace.append(TraceStep(
            step="상태 추론",
            detail=" / ".join(f"{a.axis.value} z={a.z_score:+.2f}" for a in fired),
            changed=True,
        ))
    else:
        trace.append(TraceStep(
            step="상태 추론",
            detail="모든 지표가 평소 범위입니다. 영양제를 처방하지 않습니다.",
        ))

    # ── 3. 사료량 ──────────────────────────────────────────────────────
    trace.extend(energy.trace)

    # ── 4. 축 -> 목표 용량 ─────────────────────────────────────────────
    # 오메가3는 피부·관절 두 축이 함께 요구한다.
    # 합산하면 두 배가 되므로 max()로 결합한다.
    targets: dict[str, float] = {}
    reasons: dict[str, str] = {}

    for a in fired:
        sev = severity(a.z_score)
        for cart_id, per_kg in AXIS_DOSE.get(a.axis, {}).items():
            mg = per_kg * profile.weight_kg * sev
            if mg > targets.get(cart_id, 0.0):
                targets[cart_id] = mg
                reasons[cart_id] = f"{a.axis.value} (심각도 {sev:.0%})"
            elif cart_id in targets:
                trace.append(TraceStep(
                    step="다축 결합",
                    detail=f"{CARTRIDGE_BY_ID[cart_id].name}을 "
                           f"{a.axis.value}축도 요구 → 합산하지 않고 큰 쪽만 채택",
                    changed=True,
                ))

    # 장건강은 문진 트리거로만
    gut = _gut_trigger(profile)
    if gut:
        targets["gut"] = GUT_DOSE_PER_KG * profile.weight_kg
        reasons["gut"] = gut
        trace.append(TraceStep(step="문진 트리거", detail=f"장건강 — {gut}", changed=True))

    # ── 5. Layer 1 금기 차단 / 대체 ────────────────────────────────────
    blocks = resolve_blocks(profile)
    for cart_id, block in blocks.items():
        if cart_id not in targets:
            continue
        mg = targets.pop(cart_id)
        why = reasons.pop(cart_id, "")
        if block.substitute:
            targets[block.substitute] = max(targets.get(block.substitute, 0.0), mg)
            reasons[block.substitute] = why
            trace.append(TraceStep(
                step="🛡 금기 차단",
                detail=f"{CARTRIDGE_BY_ID[cart_id].name} 차단 ({block.reason}) → "
                       f"{CARTRIDGE_BY_ID[block.substitute].name}로 대체",
                changed=True,
            ))
        else:
            trace.append(TraceStep(
                step="🛡 금기 차단",
                detail=f"{CARTRIDGE_BY_ID[cart_id].name} 차단 ({block.reason}) — 대체재 없음",
                changed=True,
            ))

    for cart_id, factor in soft_limits(profile).items():
        if cart_id in targets:
            targets[cart_id] *= factor
            trace.append(TraceStep(
                step="🛡 용량 제한",
                detail=f"{CARTRIDGE_BY_ID[cart_id].name} 용량 {factor:.0%}로 축소 "
                       f"(복용약 상호작용)",
                changed=True,
            ))

    # ── 6. 알갱이 정수화 ───────────────────────────────────────────────
    targets = apply_single_meal_penalty(targets, profile.meals_per_day, trace)
    pellets = {cid: to_pellets(cid, mg) for cid, mg in targets.items()}
    pellets = {k: v for k, v in pellets.items() if v > 0}

    for cid, n in pellets.items():
        nutrient = AXIS_DOSE_NUTRIENT[cid]
        per = CARTRIDGE_BY_ID[cid].nutrients_per_pellet[nutrient]
        trace.append(TraceStep(
            step="알갱이 환산",
            detail=f"{CARTRIDGE_BY_ID[cid].name} {targets[cid]:.0f} → "
                   f"{n}알 ({n * per:.0f})",
        ))

    # ── 7. Layer 2 상한 검증 ───────────────────────────────────────────
    # 정수화 '후'에 검증한다. 반올림으로 다시 넘을 수 있기 때문이다.
    before = dict(pellets)
    pellets = enforce_upper_limits(pellets, profile, energy.food_grams, trace)
    pellets = check_zinc_copper(pellets, trace)
    if pellets == before:
        trace.append(TraceStep(step="🛡 상한 검증", detail="모든 영양소가 안전 범위 내"))

    # ── 8. 끼니 배분 ───────────────────────────────────────────────────
    items = assign_meals(pellets, profile.meals_per_day, trace)
    for it in items:
        it.reason = reasons.get(it.cartridge_id, "")

    # ── 상태 갱신 ──────────────────────────────────────────────────────
    started = dict(state.started_at)
    for a in fired:
        started.setdefault(a.axis.value, today)

    # 일몰: 오래 줬는데 나아지지 않으면 중단하고 병원으로 보낸다
    for axis_name, start in list(started.items()):
        if (today - start).days >= SUNSET_WEEKS * 7:
            if axis_name in {a.axis.value for a in fired}:
                trace.append(TraceStep(
                    step="일몰 검토",
                    detail=f"{axis_name}축을 {SUNSET_WEEKS}주째 처방 중인데 "
                           "개선이 없습니다. 병원 진료를 권합니다.",
                    changed=True,
                ))

    new_state = PrescriptionState(
        active_axes=[a.axis for a in fired],
        started_at=started,
        baseline_frozen=bool(fired),
        prev_food_grams=energy.food_grams,
    )

    rx = Prescription(
        dog_id=profile.dog_id, date=today,
        der_kcal=energy.der_kcal, food_grams=energy.food_grams,
        axes=axes, items=items, escalated=False, trace=trace,
    )
    return rx, new_state
