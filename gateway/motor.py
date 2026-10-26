"""
실물 디스펜서 — DRV8825 + NEMA17 + BB 로더.

`스마트_펫케어_하드웨어_조립_안내서.docx` 의 배선을 그대로 옮겼다.
안내서는 **모터 한 개를 20펄스 돌리는 것**에서 끝난다. 여기서부터는
"영양제 n알을 뽑는다" 로 바뀐다.

    안내서      motor_test.py → 모터 1개가 20펄스 회전
    여기        dispense(food_g, pellets) → 슬롯별 n알

**조립이 끝나기 전에도 import 된다.** gpiozero 가 없거나 라즈베리파이가
아니면 `available()` 이 False 를 돌려주고, 게이트웨이는 Mock 으로 돌아간다.
하드웨어가 없다고 전체가 안 뜨면 개발이 막힌다.

조립 후 **반드시 보정해야 하는 값**은 두 개다.

    STEPS_PER_PELLET   로더가 알갱이 1개를 떨어뜨리는 데 필요한 펄스
    STEPS_PER_GRAM     사료 1g 에 해당하는 펄스

안내서도 같은 말을 한다 — *"배출량은 회전각과 항상 1대1로 대응하지 않으므로
각도별 배출 개수를 별도로 기록한다."* 그래서 기본값은 **추정치이고,
`python -m gateway.calibrate` 로 실측해서 덮어쓴다.**
"""

from __future__ import annotations

import os
import time

from gateway.hardware import Dispenser, DispenseResult

# ---------------------------------------------------------------------------
# 배선 — 조립 안내서 08장
# ---------------------------------------------------------------------------

#: 슬롯 번호 -> (STEP, DIR, EN) BCM GPIO.
#:
#: 안내서의 표를 그대로 옮겼다. 물리 핀이 아니라 BCM 번호다.
#:
#:     1번  물리 11/13/15   2번  물리 29/31/33
#:     3번  물리 36/38/40   4번  물리 16/18/22
#:
#: 슬롯 번호는 `core/constants.py` 의 CartridgeSpec.slot 과 같은 번호다.
#: 1=오메가3, 2=피부장벽, 3=관절, 4=장건강.
#: 로더를 다른 순서로 꽂았다면 **여기를 고친다**. 서버는 안 건드린다.
PINS: dict[int, tuple[int, int, int]] = {
    1: (17, 27, 22),
    2: (5, 6, 13),
    3: (16, 20, 21),
    4: (23, 24, 25),
}

#: 사료 이송용 슬롯. 로더가 아니라 사료 스크류/디스크에 연결한다.
#: 아직 안 달았으면 None 으로 두면 사료 사출을 건너뛴다.
FOOD_SLOT: int | None = None

# ---------------------------------------------------------------------------
# 보정값 — 조립 후 실측해서 덮어쓴다
# ---------------------------------------------------------------------------

#: 알갱이 1개를 떨어뜨리는 펄스 수.
#:
#: **이 값은 추정이다.** NEMA17 풀스텝 200 step/rev 기준으로 1회전의
#: 1/10 을 잡았다. 실제 로더의 배출 구조에 따라 크게 다르다.
#: `python -m gateway.calibrate` 로 재서 환경변수에 넣는다.
STEPS_PER_PELLET = int(os.environ.get("PEBBLE_STEPS_PER_PELLET", "20"))

#: 사료 1g 당 펄스 수. 역시 추정이다.
STEPS_PER_GRAM = int(os.environ.get("PEBBLE_STEPS_PER_GRAM", "8"))

#: 펄스 반주기(초). 작을수록 빠르다.
#:
#: 안내서 코드는 0.02 초를 쓴다(=25Hz, 아주 느림). 첫 시험에는 그게 맞지만
#: 실제 배출에는 너무 느리다 — 알갱이 20개에 16초가 걸린다.
#: 탈조가 나지 않는 선에서 올린다. 막히면 느리게 되돌린다.
PULSE_S = float(os.environ.get("PEBBLE_PULSE_S", "0.0015"))

#: 한 번에 돌릴 수 있는 최대 펄스. 폭주 방지.
#:
#: 보정값이 잘못 들어가면(0 이 하나 더 붙는다든지) 모터가 끝없이 돈다.
#: 로더가 통째로 비워지고 BB 가 바닥에 쏟아진다. 상한을 둔다.
MAX_STEPS = int(os.environ.get("PEBBLE_MAX_STEPS", "4000"))

#: 슬롯을 바꿀 때 쉬는 시간. 전원 과도를 피한다.
SETTLE_S = 0.1


def available() -> bool:
    """
    실물 구동이 가능한가.

    gpiozero 가 없거나 PC 에서 돌리면 False 다. 호출하는 쪽이 Mock 으로
    내려간다 — 하드웨어가 없다고 게이트웨이 전체가 안 뜨면 개발이 막힌다.
    """
    try:
        import gpiozero  # noqa: F401
    except Exception:
        return False
    return True


class MotorDispenser(Dispenser):
    """
    DRV8825 네 채널로 로더를 돌린다.

    **EN 은 LOW 가 활성화**다(안내서 08장). 평소에는 HIGH 로 두어 출력을
    끊어 놓고, 돌릴 때만 LOW 로 내린다. 10kΩ 풀업이 부팅·종료 중에도
    HIGH 를 유지해 주므로, 프로그램이 죽어도 모터가 풀리지 않는다.
    """

    def __init__(
        self,
        pins: dict[int, tuple[int, int, int]] | None = None,
        *,
        steps_per_pellet: int = STEPS_PER_PELLET,
        steps_per_gram: int = STEPS_PER_GRAM,
        pulse_s: float = PULSE_S,
        food_slot: int | None = FOOD_SLOT,
    ) -> None:
        from gpiozero import OutputDevice

        self.pins = pins or PINS
        self.steps_per_pellet = steps_per_pellet
        self.steps_per_gram = steps_per_gram
        self.pulse_s = pulse_s
        self.food_slot = food_slot

        self._step: dict[int, OutputDevice] = {}
        self._dir: dict[int, OutputDevice] = {}
        self._en: dict[int, OutputDevice] = {}

        for slot, (step, direction, enable) in self.pins.items():
            self._step[slot] = OutputDevice(step, initial_value=False)
            self._dir[slot] = OutputDevice(direction, initial_value=False)
            # 시작부터 비활성(HIGH). 켜자마자 돌면 안 된다.
            self._en[slot] = OutputDevice(enable, initial_value=True)

    # -- 저수준 -------------------------------------------------------------

    def _turn(self, slot: int, steps: int) -> int:
        """
        한 슬롯을 `steps` 펄스만큼 돌린다. 실제로 돈 펄스를 돌려준다.

        상한을 넘으면 **상한까지만** 돌린다. 보정값이 잘못 들어갔을 때
        로더가 통째로 비워지는 것을 막는다.
        """
        steps = max(0, min(steps, MAX_STEPS))
        if steps == 0:
            return 0

        en, st = self._en[slot], self._step[slot]
        en.off()                      # LOW = 활성화
        time.sleep(0.01)              # 드라이버가 깨어날 시간
        try:
            for _ in range(steps):
                st.on()
                time.sleep(self.pulse_s)
                st.off()
                time.sleep(self.pulse_s)
        finally:
            st.off()
            en.on()                   # HIGH = 출력 차단
        return steps

    # -- Dispenser 구현 -----------------------------------------------------

    def dispense(self, food_g: int, pellets: list[dict]) -> DispenseResult:
        """
        사료와 영양제를 사출한다.

        **요청한 개수를 못 채우면 성공이라고 하지 않는다.** 실제 사출 수를
        그대로 돌려줘야 서버가 섭취량 대조를 할 수 있다.

        슬롯 하나가 실패해도 나머지는 계속한다. 하나 막혔다고 그날 영양제를
        통째로 거르는 것보다, 되는 것만 주고 무엇이 안 됐는지 보고하는 게 낫다.
        """
        done: dict[int, int] = {}
        errors: list[str] = []

        for p in pellets:
            slot, want = int(p["slot"]), int(p["count"])
            name = p.get("name", f"슬롯{slot}")

            if slot not in self._step:
                errors.append(f"{name}: {slot}번 로더가 배선되지 않았습니다")
                continue

            try:
                moved = self._turn(slot, want * self.steps_per_pellet)
            except Exception as e:
                errors.append(f"{name}: 구동 실패 ({type(e).__name__})")
                continue

            got = moved // self.steps_per_pellet
            done[slot] = got
            if got < want:
                errors.append(f"{name}: {want}알 중 {got}알만 사출")
            time.sleep(SETTLE_S)

        # -- 사료 --
        gave_food = 0
        if food_g > 0 and self.food_slot is not None:
            if self.food_slot in self._step:
                try:
                    moved = self._turn(self.food_slot, food_g * self.steps_per_gram)
                    gave_food = moved // self.steps_per_gram
                    if gave_food < food_g:
                        errors.append(f"사료: {food_g}g 중 {gave_food}g 만 사출")
                except Exception as e:
                    errors.append(f"사료: 구동 실패 ({type(e).__name__})")
            else:
                errors.append("사료: 이송 모터가 배선되지 않았습니다")
        elif food_g > 0:
            # 사료 모터를 아직 안 달았다. 영양제 시연에는 문제가 없으므로
            # 실패로 보지 않되, 안 줬다는 사실은 남긴다.
            errors.append(f"사료 {food_g}g: 이송 모터 미장착 — 수동 급여 필요")

        return DispenseResult(
            ok=not errors,
            food_g=gave_food,
            pellets=done,
            error=" / ".join(errors),
        )

    def close(self) -> None:
        """
        전부 비활성으로 돌려놓고 핀을 놓는다.

        게이트웨이가 죽어도 10kΩ 풀업이 EN 을 HIGH 로 잡아주지만,
        정상 종료에서는 우리가 명시적으로 끈다.
        """
        for slot in self.pins:
            try:
                self._step[slot].off()
                self._en[slot].on()
            except Exception:
                pass
        for group in (self._step, self._dir, self._en):
            for dev in group.values():
                try:
                    dev.close()
                except Exception:
                    pass
