"""
게이트웨이 메인 루프.

    목줄 ──BLE──> [버퍼] ──HTTPS──> 서버
                    │
    밥통 <──명령──── [계획 캐시] <──┘

네 가지를 번갈아 한다.

    1. 목줄에서 읽어 버퍼에 쌓는다          (10초)
    2. 버퍼를 서버로 올린다                  (5분)
    3. 사출 명령을 받아 계획에 저장한다        (1분)
    4. 때가 된 명령을 실행하고 결과를 보고한다  (1분)

**서버가 죽어도 2·3만 멈추고 1·4는 계속 돈다.**
강아지는 클라우드 사정과 무관하게 밥을 먹어야 한다.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta

from gateway.buffer import Buffer
from gateway.client import (
    BadPayload,
    Client,
    ClockNotReady,
    ServerDown,
    clock_is_trustworthy,
)
from gateway.config import CONFIG, Config
from gateway.hardware import (
    CollarLink,
    Dispenser,
    MockCollar,
    MockDispenser,
    MockScale,
    Scale,
)

# 폴백 급여를 기억해두는 키
K_FALLBACK_G = "fallback_food_g"
K_MEAL_HOURS = "meal_hours"


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Agent:
    def __init__(
        self, cfg: Config, collar: CollarLink, dispenser: Dispenser, scale: Scale
    ) -> None:
        self.cfg = cfg
        self.collar = collar
        self.dispenser = dispenser
        self.scale = scale
        self.buffer = Buffer(cfg.db_path)
        self.client = Client(cfg)

        self._last_upload = 0.0
        self._last_command = 0.0
        self._fallback_done: set[str] = set()

    # -- 1. 수집 ------------------------------------------------------------

    def collect(self) -> None:
        boot_id, events, status = self.collar.poll()
        if events:
            self.buffer.add_events(boot_id, events)
        if status:
            self.buffer.add_status(boot_id, status)

        session = self.scale.read_platform_session()
        if session:
            self.buffer.put("pending_weight", json.dumps({
                "started_at": datetime.now().isoformat(),
                "samples_kg": session,
            }))

    # -- 2. 업로드 ----------------------------------------------------------

    def upload(self) -> None:
        """
        버퍼를 비운다. 서버가 받았다고 확인한 것만 지운다.

        시각이 확정되기 전에는 올리지 않는다. 라즈베리파이는 RTC가 없어서
        부팅 직후 시각이 틀리고, 그대로 올리면 목줄 데이터가
        엉뚱한 날짜에 꽂힌다.
        """
        while True:
            batch = self.buffer.next_batch(self.cfg.upload_batch)
            if batch is None or batch.empty:
                break
            try:
                r = self.client.upload(batch)
            except ClockNotReady as e:
                log(f"업로드 보류 — {e}")
                return
            except ServerDown as e:
                log(f"서버 연결 실패 — 버퍼에 유지 ({e})")
                return
            except BadPayload as e:
                # 재시도해도 영원히 실패한다. 버리지 않으면 이 묶음이 큐를 막고
                # 뒤에 쌓인 멀쩡한 데이터까지 전부 못 올라간다.
                log(f"⚠ 서버가 거부한 묶음을 폐기한다 (boot {batch.boot_id}) — {e}")
                self.buffer.drop(batch)
                continue

            self.buffer.drop(batch)
            log(
                f"업로드 이벤트 {r.get('accepted', 0)}건 "
                f"(중복 {r.get('duplicates', 0)}) 표본 {r.get('status_samples', 0)}건"
            )

        raw = self.buffer.get("pending_weight")
        if raw:
            try:
                self.client.send_weight([json.loads(raw)])
                self.buffer.put("pending_weight", "")
                log("체중 세션 전송")
            except ServerDown:
                pass

        dropped = self.buffer.prune(self.cfg.keep_days)
        if dropped:
            log(f"{self.cfg.keep_days}일 넘은 미전송 데이터 {dropped}건 폐기")

    # -- 3. 계획 받기 --------------------------------------------------------

    def sync_commands(self) -> None:
        # 먼저 밀린 보고부터 올린다. 서버가 명령/실행 대조를 못 하면
        # 섭취량 계산이 틀어진다.
        for c in self.buffer.unacked():
            try:
                self.client.ack(c["id"], c["state"], c["result"])
                self.buffer.forget(c["id"])
            except ServerDown:
                return

        try:
            commands = self.client.fetch_commands()
        except ServerDown as e:
            log(f"명령 조회 실패 — 캐시된 계획으로 계속 ({e})")
            return

        n = self.buffer.save_plan(commands)
        if n:
            log(f"새 사출 명령 {n}건 수신")
            # 폴백에 쓸 기준량을 갱신해둔다
            total = sum(int(c.get("food_g", 0)) for c in commands)
            if total > 0:
                self.buffer.put(K_FALLBACK_G, str(total))
                hours = sorted({
                    datetime.fromisoformat(c["scheduled"]).hour for c in commands
                })
                self.buffer.put(K_MEAL_HOURS, ",".join(str(h + 1) for h in hours))

    # -- 4. 사출 ------------------------------------------------------------

    def execute(self, now: datetime) -> None:
        for cmd in self.buffer.due_commands(now):
            food = int(cmd.get("food_g", 0))
            pellets = cmd.get("pellets", [])
            r = self.dispenser.dispense(food, pellets)

            if r.ok:
                self.buffer.mark(cmd["id"], "done", json.dumps(
                    {"food_g": r.food_g, "pellets": r.pellets}, ensure_ascii=False
                ))
                log(f"사출 완료 사료 {r.food_g}g / 영양제 {r.pellets or '없음'}")
            else:
                self.buffer.mark(cmd["id"], "failed", r.error)
                log(f"사출 실패 — {r.error}")

    # -- 폴백 ---------------------------------------------------------------

    def fallback(self, now: datetime) -> None:
        """
        서버와 오래 끊겨 캐시된 계획이 바닥났을 때.

        **밥을 굶기지 않는 것이 최우선이다.**
        다만 오래된 처방을 무한정 반복하는 것도 위험하므로,
        사료만 마지막으로 알려진 양으로 주고 **영양제는 중단한다.**
        영양제는 상태 추론의 결과인데 그 추론이 몇 주 전 것이기 때문이다.
        """
        age = self.buffer.plan_age(now)
        if age is not None and age < timedelta(days=self.cfg.plan_max_age_days):
            return
        if self.buffer.due_commands(now):
            return

        base = self.buffer.get(K_FALLBACK_G)
        hours = self.buffer.get(K_MEAL_HOURS)
        if not base or not hours:
            return

        meal_hours = [int(h) for h in hours.split(",") if h.strip()]
        if now.hour not in meal_hours:
            return

        key = f"{now.date()}:{now.hour}"
        if key in self._fallback_done:
            return
        self._fallback_done.add(key)

        per = max(1, int(base) // max(1, len(meal_hours)))
        r = self.dispenser.dispense(per, [])
        log(
            f"⚠ 폴백 급여 — 서버와 {age.days if age else '?'}일 끊김. "
            f"사료 {per}g만 배식하고 영양제는 중단합니다 (성공={r.ok})"
        )

    # -- 루프 ---------------------------------------------------------------

    def tick(self) -> None:
        now = datetime.now()
        self.collect()

        if time.monotonic() - self._last_upload >= self.cfg.upload_sec:
            self._last_upload = time.monotonic()
            self.upload()

        if time.monotonic() - self._last_command >= self.cfg.command_sec:
            self._last_command = time.monotonic()
            self.sync_commands()
            self.execute(now)
            self.fallback(now)

    def run(self) -> None:
        ok, why = clock_is_trustworthy()
        e, s = self.buffer.pending()
        log(f"게이트웨이 시작 — 목줄 {self.cfg.collar_serial} / 개체 {self.cfg.dog_id}")
        log(f"시계: {why}")
        log(f"서버: {self.cfg.server} ({'연결됨' if self.client.health() else '응답 없음'})")
        if e or s:
            log(f"미전송 버퍼 이벤트 {e}건 · 표본 {s}건")

        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception as exc:                       # noqa: BLE001
                # 루프는 절대 죽으면 안 된다. 죽으면 밥이 안 나간다.
                log(f"오류 (계속 진행) — {type(exc).__name__}: {exc}")
            time.sleep(self.cfg.poll_collar_sec)


def main() -> None:
    ap = argparse.ArgumentParser(description="pebble 게이트웨이 (라즈베리파이)")
    ap.add_argument("--mock", action="store_true", help="하드웨어 없이 돌린다")
    ap.add_argument("--once", action="store_true", help="한 번만 돌고 끝낸다")
    ap.add_argument("--jam", type=float, default=0.0, help="mock 디스펜서 걸림 확률")
    args = ap.parse_args()

    if args.mock:
        collar, disp, scale = MockCollar(), MockDispenser(args.jam), MockScale()
    else:
        raise SystemExit(
            "실물 하드웨어 구현이 아직 없습니다.\n"
            "gateway/hardware.py 의 CollarLink / Dispenser / Scale 을 구현한 뒤\n"
            "여기서 연결하세요. 우선은 --mock 으로 전체 흐름을 확인할 수 있습니다."
        )

    agent = Agent(CONFIG, collar, disp, scale)
    if args.once:
        agent.tick()
        log("1회 실행 완료")
    else:
        agent.run()


if __name__ == "__main__":
    main()
