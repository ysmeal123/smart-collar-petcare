"""
배출량 보정.

    python -m gateway.calibrate 1          슬롯 1번 로더
    python -m gateway.calibrate food       사료 이송

조립 안내서가 짚은 그대로다.

    *"배출량은 회전각과 항상 1대1로 대응하지 않으므로
      각도별 배출 개수를 별도로 기록한다."*

그래서 `motor.py` 의 기본값은 **추정치**다. 여기서 실측해서 덮어쓴다.
이걸 안 하면 "오메가3 4알" 이 실제로는 2알이거나 9알이 된다.
화면 숫자와 실물이 어긋나는 건 이 시스템에서 제일 나쁜 종류의 버그다.

재는 방법은 단순하다. **정해진 펄스를 보내고, 사람이 세어서 입력한다.**
로드셀로 자동화할 수도 있지만 BB 1개의 무게 분해능이 보장되지 않는다
(안내서도 같은 경고를 한다). 처음에는 눈으로 세는 편이 확실하다.
"""

from __future__ import annotations

import sys

from gateway import motor

#: 한 번에 보낼 펄스. 여러 번 반복해서 평균을 낸다.
#: 한 번만 재면 우연히 2개 떨어진 회차에 속는다.
TRIAL_STEPS = 200
TRIALS = 3


def ask_int(prompt: str) -> int:
    while True:
        raw = input(prompt).strip()
        try:
            return int(raw)
        except ValueError:
            print("  숫자만 입력하세요.")


def main(target: str) -> int:
    if not motor.available():
        print("gpiozero 를 찾을 수 없습니다. 라즈베리파이에서 실행하세요.")
        return 1

    is_food = target == "food"
    slot = motor.FOOD_SLOT if is_food else int(target)
    unit = "g" if is_food else "알"

    if slot is None:
        print("FOOD_SLOT 이 설정되지 않았습니다. motor.py 를 확인하세요.")
        return 1
    if slot not in motor.PINS:
        print(f"{slot}번은 PINS 에 없습니다. 배선을 확인하세요.")
        return 1

    print(f"\n{'사료 이송' if is_food else f'{slot}번 로더'} 보정")
    print("=" * 60)
    print(f"  {TRIAL_STEPS} 펄스를 {TRIALS}회 보냅니다.")
    print(f"  매회 떨어진 {unit} 수를 세어 입력하세요.")
    print()
    print("  ⚠ 로더에 BB 를 충분히 채우고, 받을 그릇을 두세요.")
    print("  ⚠ 12V 전원이 켜져 있어야 합니다.")
    input("\n준비되면 Enter: ")

    dev = motor.MotorDispenser()
    counts: list[int] = []
    try:
        for i in range(1, TRIALS + 1):
            print(f"\n[{i}/{TRIALS}] {TRIAL_STEPS} 펄스 보냅니다...")
            moved = dev._turn(slot, TRIAL_STEPS)
            if moved < TRIAL_STEPS:
                print(f"  ⚠ 상한에 걸려 {moved} 펄스만 보냈습니다.")
            n = ask_int(f"  떨어진 {unit} 수: ")
            counts.append(n)
    finally:
        dev.close()

    if not any(counts):
        print("\n하나도 안 떨어졌습니다. 확인할 것:")
        print("  · 로더가 비었는지")
        print("  · 축 정렬과 커플러")
        print("  · VREF 가 너무 낮아 토크가 모자란지")
        print("  · 펄스가 너무 빨라 탈조가 났는지 (PEBBLE_PULSE_S 를 올려보세요)")
        return 1

    total = sum(counts)
    steps_per_unit = round(TRIAL_STEPS * TRIALS / total)

    print("\n" + "=" * 60)
    print(f"  회차별: {counts}")
    print(f"  합계:   {TRIAL_STEPS * TRIALS} 펄스 → {total}{unit}")
    print(f"  결과:   {unit} 1개당 약 {steps_per_unit} 펄스")

    spread = max(counts) - min(counts)
    if spread > max(1, total / TRIALS * 0.3):
        print()
        print(f"  ⚠ 회차 편차가 큽니다 ({spread}{unit}).")
        print("    배출이 불안정하다는 뜻입니다. 이 값으로 처방을 맞추면")
        print("    실제 투여량이 매번 달라집니다. 로더 구조를 먼저 보세요.")

    env = "PEBBLE_STEPS_PER_GRAM" if is_food else "PEBBLE_STEPS_PER_PELLET"
    print()
    print("  .env 또는 셸에 넣으세요:")
    print(f"    {env}={steps_per_unit}")
    print()
    print("  로더마다 값이 다르면 가장 느린 쪽에 맞추지 말고,")
    print("  로더 구조를 똑같이 맞추는 편이 낫습니다 —")
    print("  슬롯별로 다른 값을 쓰려면 motor.py 를 고쳐야 합니다.")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
