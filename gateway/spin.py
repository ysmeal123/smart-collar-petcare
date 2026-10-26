"""
모터 진단 — 속도를 바꿔가며 돌려본다.

    python3 -m gateway.spin 1

**보정 전에 "도는가" 부터 확인한다.**

스텝모터는 가속 없이 빠른 속도로 시작하면 탈조한다 - 코일이 따라가지
못하고 제자리에서 떨거나 아예 안 움직인다. 조립 안내서가 0.02초(25Hz)를
쓴 것은 그래서다. 느리지만 확실히 돈다.

`motor.py` 의 기본값은 실제 배출용이라 더 빠르다. 그 속도에서 안 돌면
배선이 틀린 게 아니라 **그냥 너무 빠른 것일 수 있다.** 둘을 구분해야
엉뚱한 곳을 뜯지 않는다.

느린 쪽부터 올라가면서, 어느 속도까지 도는지 눈으로 확인한다.
"""

from __future__ import annotations

import sys
import time

from gateway import motor

#: 느린 쪽부터. 안내서의 0.02 가 첫 번째다.
SPEEDS = [0.02, 0.01, 0.005, 0.002, 0.0015]

#: 한 번에 보낼 펄스. 200 = 풀스텝 한 바퀴.
STEPS = 200


def main(slot: int) -> int:
    if not motor.available():
        print("gpiozero 를 찾을 수 없습니다. 라즈베리파이에서 실행하세요.")
        return 1
    if slot not in motor.PINS:
        print(f"{slot}번은 PINS 에 없습니다: {sorted(motor.PINS)}")
        return 1

    step, direction, enable = motor.PINS[slot]
    print(f"\n{slot}번 모터 진단")
    print("=" * 56)
    print(f"  STEP  GPIO{step}")
    print(f"  DIR   GPIO{direction}")
    print(f"  EN    GPIO{enable}")
    print()
    print("  ⚠ 12V 전원이 켜져 있어야 합니다.")
    print("  ⚠ 모터를 고정하고, 로더는 떼고 하세요.")
    print()
    print(f"  {len(SPEEDS)}가지 속도로 각각 {STEPS}펄스(한 바퀴)를 보냅니다.")
    print("  축에 테이프를 붙여두면 보기 쉽습니다.")
    input("\n준비되면 Enter: ")

    dev = motor.MotorDispenser()
    try:
        for pulse in SPEEDS:
            hz = int(1 / (pulse * 2))
            print(f"\n  펄스 {pulse}초 ({hz}Hz) — {STEPS}펄스 보냅니다...")
            dev.pulse_s = pulse
            t = time.time()
            dev._turn(slot, STEPS)
            print(f"    {time.time() - t:.1f}초 걸림")
            ans = input("    돌았나요? (y/n/q): ").strip().lower()
            if ans == "q":
                break
            if ans == "y":
                print()
                print(f"    ✓ 이 속도에서 돕니다.")
                print(f"      .env 에 넣으세요:  PEBBLE_PULSE_S={pulse}")
                print()
                print("      더 빠른 속도도 시험해 보려면 계속 진행하세요.")
                print("      빠를수록 배출이 빠르지만 탈조 위험이 커집니다.")
                if input("    계속? (y/n): ").strip().lower() != "y":
                    break
    finally:
        dev.close()

    print("\n" + "=" * 56)
    print("  하나도 안 돌았다면 전기 쪽입니다. 순서대로 확인하세요.")
    print()
    print("    1. 12V 어댑터가 켜져 있나")
    print("    2. VREF 가 0.15V 인가            (안내서 06장)")
    print("    3. RESET·SLEEP 이 HIGH 인가      (안내서 05장) ← 제일 흔함")
    print("    4. 파이 GND 와 드라이버 GND 가 이어져 있나")
    print("    5. 드라이버가 소켓에 거꾸로 꽂히지 않았나")
    print()
    print("  떨기만 한다면 코일 쌍이 어긋난 겁니다.")
    print("    빨강·노랑이 한 쌍(1A·1B), 초록·갈색이 한 쌍(2A·2B)")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(int(sys.argv[1])))
