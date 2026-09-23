"""
Step 4 확인용 실행 스크립트.

Mock 데이터 4종을 알고리즘 코어에 통과시켜, 처방과 그 근거를 출력한다.
서버 없이 이것만 돌려도 로직 전체를 볼 수 있다.

실행:
    cd petcare
    python run_step4.py
"""

from __future__ import annotations

from core.constants import CARTRIDGE_BY_ID, AXIS_DOSE_NUTRIENT
from core.models import MealSlot
from core.prescribe import prescribe
from mock.generator import SCENARIOS, generate

BAR = "=" * 76


def show(scenario: str) -> None:
    ds = generate(scenario)
    rx, state = prescribe(ds.profile, ds.days)
    p = ds.profile

    print(f"\n{BAR}")
    print(f"  {p.name} · {p.age_months // 12}세 · {p.weight_kg}kg · {p.size.value}"
          f"   [{scenario}]")
    tags = []
    if p.allergies:
        tags.append("알러지 " + ", ".join(a.value for a in p.allergies))
    if p.medications:
        tags.append("복용약 " + ", ".join(m.value for m in p.medications))
    if p.conditions:
        tags.append("질환 " + ", ".join(c.value for c in p.conditions))
    if not p.breeds:
        tags.append("잡종/견종 모름")
    if tags:
        print(f"  {' · '.join(tags)}")
    print(BAR)

    # ── 상태 추론 ──────────────────────────────────────────────────
    print("\n  상태 추론")
    for a in rx.axes:
        mark = "◀ 처방" if a.active else "  "
        msg = f"  {a.message}" if a.message else ""
        print(f"    {a.axis.value:<8} z={a.z_score:+6.2f}  {mark}{msg}")

    # ── 처방 결과 ──────────────────────────────────────────────────
    if rx.escalated:
        print(f"\n  🚨 긴급 정지")
        print(f"     {rx.escalation_reason}")
        print(f"\n  오늘의 급여   사료 {rx.food_grams}g ({rx.der_kcal:.0f} kcal)")
        print("                 영양제 없음 (처방 동결)")
    else:
        print(f"\n  오늘의 급여   사료 {rx.food_grams}g ({rx.der_kcal:.0f} kcal)")
        if rx.items:
            for slot in (MealSlot.MORNING, MealSlot.EVENING):
                group = [i for i in rx.items if i.meal_slot is slot]
                if not group:
                    continue
                label = "아침" if slot is MealSlot.MORNING else "저녁"
                print(f"\n    {label}")
                for it in group:
                    cart = CARTRIDGE_BY_ID[it.cartridge_id]
                    nut = AXIS_DOSE_NUTRIENT[it.cartridge_id]
                    amount = cart.nutrients_per_pellet[nut] * it.pellets
                    unit = "억 CFU" if "cfu" in nut else "mg"
                    val = amount / 1e8 if "cfu" in nut else amount
                    print(f"      {cart.name:<12} {it.pellets:>2}알  "
                          f"({val:.0f}{unit})   ← {it.reason}")
        else:
            print("                 영양제 없음")

    # ── 근거 (앱의 '왜 이렇게 나왔나요?') ──────────────────────────
    print("\n  왜 이렇게 나왔나요?")
    for t in rx.trace:
        mark = "•" if t.changed else " "
        print(f"    {mark} {t.step:<14} {t.detail}")


def main() -> None:
    for s in SCENARIOS:
        show(s)
    print(f"\n{BAR}\n")


if __name__ == "__main__":
    main()
