"""
하드웨어 경계.

여기 있는 세 인터페이스가 **펌웨어/하드웨어 담당과 서버 담당의 경계**다.
구현은 라즈베리파이에서 채우고, 그 위쪽(버퍼·전송·폴백)은 이미 다 돼 있다.

Mock 구현이 함께 들어 있어서 **하드웨어 없이도 게이트웨이 전 구간이 돈다.**
한 조각씩 실물로 바꿔 끼우면 된다.

    python -m gateway.agent --mock     하드웨어 없이 전체 루프
    python -m gateway.agent            실물
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 목줄 (BLE)
# ---------------------------------------------------------------------------

class CollarLink(ABC):
    """
    목줄에서 이벤트와 상태 표본을 받아온다.

    구현 노트 (라즈베리파이 + BlueZ):
        - bluepy / bleak 중 아무거나. bleak 가 비동기라 더 쉽다.
        - 목줄이 notify 로 밀어주는 구조를 권한다. 폴링보다 전력이 낫다.
        - 12바이트 고정 레코드를 struct.unpack('<IBBHI', ...) 로 푼다.
            uint32 t_ms · uint8 type · uint8 conf · uint16 dur_ds · uint32 seq
        - 연결이 끊겨 있으면 그냥 빈 리스트를 돌려준다.
          재연결은 이 클래스 안에서 알아서 한다.
    """

    @abstractmethod
    def poll(self) -> tuple[int, list[dict], list[dict]]:
        """
        반환: (boot_id, 이벤트들, 상태표본들)

        이벤트 dict:  seq, t_ms, type, conf, dur_s
        상태 dict:    t_ms, worn_sec, steps, battery,
                      rest_sec, walk_sec, run_sec, vigorous_sec
        """

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 디스펜서
# ---------------------------------------------------------------------------

@dataclass
class DispenseResult:
    ok: bool
    food_g: int = 0
    pellets: dict[int, int] = field(default_factory=dict)   # 슬롯 -> 실제 사출 수
    error: str = ""


class Dispenser(ABC):
    """
    사료와 영양제를 사출한다.

    구현 노트:
        - 사료는 스텝모터 회전수 x 1회전당 그램으로 환산. 개체마다 보정 필요.
        - 영양제는 슬롯별 서보. 1알씩 떨어뜨리고 광센서로 통과를 센다.
        - **요청한 개수를 못 채우면 성공이라고 하지 마라.**
          실제 사출 수를 그대로 돌려줘야 서버가 섭취량 대조를 할 수 있다.
        - 걸림이 잦으면 재시도 2회까지. 그 뒤엔 실패로 보고한다.
    """

    @abstractmethod
    def dispense(self, food_g: int, pellets: list[dict]) -> DispenseResult:
        """pellets: [{"slot": 1, "count": 4, "name": "오메가3"}, ...]"""


# ---------------------------------------------------------------------------
# 체중계
# ---------------------------------------------------------------------------

class Scale(ABC):
    """
    밥그릇 로드셀과 급식판 체중계.

    구현 노트 (HX711):
        - 밥그릇: 배식 직후와 식사 종료 후를 재면 실제 섭취량이 나온다.
        - 급식판: 개가 올라선 동안 0.2초 간격으로 여러 번 읽는다.
          한 번만 읽으면 움직임 때문에 값이 튄다.
          **판정은 서버가 한다.** 여기서는 원시 표본을 그대로 올린다.
    """

    @abstractmethod
    def read_bowl_g(self) -> float | None:
        """밥그릇 무게. 측정 불가면 None."""

    @abstractmethod
    def read_platform_session(self) -> list[float] | None:
        """
        개가 올라선 한 번의 세션. 표본 리스트를 그대로 돌려준다.
        아무도 안 올라갔으면 None.
        """


# ---------------------------------------------------------------------------
# Mock — 하드웨어 없이 전체 루프를 돌리기 위한 것
# ---------------------------------------------------------------------------

BEHAVIORS = ["scratch", "head_shake", "body_shake", "posture_change"]


class MockCollar(CollarLink):
    """가짜 목줄. 부를 때마다 그럴듯한 데이터를 조금씩 만들어 준다."""

    def __init__(self, boot_id: int = 1) -> None:
        self.boot_id = boot_id
        self.started = time.monotonic()
        self.seq = 0
        self.last_minute = -1

    def _t_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def poll(self) -> tuple[int, list[dict], list[dict]]:
        t = self._t_ms()
        events, status = [], []

        for _ in range(random.randint(0, 3)):
            self.seq += 1
            events.append({
                "seq": self.seq,
                "t_ms": max(0, t - random.randint(0, 5000)),
                "type": random.choice(BEHAVIORS),
                "conf": round(random.uniform(0.5, 0.98), 2),
                "dur_s": round(random.uniform(0.8, 8.0), 1),
            })

        # 1분마다 상태 표본 하나
        minute = t // 60_000
        if minute != self.last_minute:
            self.last_minute = minute
            walk = random.randint(0, 40)
            run = random.randint(0, max(0, 60 - walk) // 3)
            vig = random.randint(0, max(0, 60 - walk - run) // 4)
            status.append({
                "t_ms": minute * 60_000,
                "worn_sec": 60,
                "steps": walk * 2,
                "battery": 85,
                "walk_sec": walk, "run_sec": run, "vigorous_sec": vig,
                "rest_sec": 60 - walk - run - vig,
            })

        return self.boot_id, events, status


class MockDispenser(Dispenser):
    """가짜 디스펜서. 가끔 걸린다 — 실패 경로도 돌려봐야 한다."""

    def __init__(self, jam_rate: float = 0.0) -> None:
        self.jam_rate = jam_rate
        self.log: list[DispenseResult] = []

    def dispense(self, food_g: int, pellets: list[dict]) -> DispenseResult:
        if random.random() < self.jam_rate:
            r = DispenseResult(ok=False, error="카트리지 걸림 (슬롯 확인 필요)")
        else:
            r = DispenseResult(
                ok=True,
                food_g=food_g,
                pellets={p["slot"]: p["count"] for p in pellets},
            )
        self.log.append(r)
        return r


class MockScale(Scale):
    def __init__(self, dog_kg: float = 5.2) -> None:
        self.dog_kg = dog_kg
        self.bowl = 0.0

    def read_bowl_g(self) -> float | None:
        return self.bowl

    def read_platform_session(self) -> list[float] | None:
        if random.random() < 0.5:
            return None
        return [round(self.dog_kg + random.uniform(-0.02, 0.02), 2) for _ in range(8)]
