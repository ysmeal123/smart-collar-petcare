"""
앱 <-> 서버 <-> 밥통 계약 점검. **ESP32 가 보내는 JSON 모양 그대로** 쳐본다.

띄워 놓고 다른 터미널에서 돌린다.

    cd petcare
    PEBBLE_APP_TOKEN=apptok PEBBLE_DEVICE_TOKEN=devtok PEBBLE_SLOTS=1,2,3 python -m uvicorn api.main:app --port 8777
    python check_e2e.py

배포된 서버를 보려면 주소와 토큰을 인자로 준다.

    python check_e2e.py https://pebble-api-0ro8.onrender.com 앱토큰 기기토큰

펌웨어를 보드에 올리기 전에 계약부터 확인한다. 여기서 틀리면 보드에
올려도 안 되고, 그때는 원인이 배선인지 JSON 인지 가릴 수 없다.

pytest 로 돌리지 않는다. 띄워진 서버가 있어야 하고, 확인하려는 것이
함수가 아니라 **HTTP 로 주고받는 모양**이다. 단위 테스트는 tests/ 에 있다.
"""

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8777"
APP = sys.argv[2] if len(sys.argv) > 2 else "apptok-test"
DEV = sys.argv[3] if len(sys.argv) > 3 else "devtok-test"
DOG = sys.argv[4] if len(sys.argv) > 4 else "dog_choco"
COLLAR = sys.argv[5] if len(sys.argv) > 5 else "PBL-0001"

# 멱등키는 매번 새로 만든다. 같은 키를 다시 쓰면 서버가 걸러내는데,
# 그건 제대로 동작하는 것이고 테스트가 두 번 안 도는 이유가 된다.
RUN = datetime.now().strftime("%H%M%S")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

ok = 0
bad = 0


def call(method, path, token, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")}


def check(label, cond, detail=""):
    global ok, bad
    if cond:
        ok += 1
        print(f"  OK   {label}")
    else:
        bad += 1
        print(f"  FAIL {label}  {detail}")


print("\n[1] 앱 -> 서버  수동 급여를 쌓는다")
code, out = call("POST", f"/v1/dogs/{DOG}/dispense", APP,
                 {"pellets": {"1": 2}, "food_g": 40, "request_id": f"e2e-1-{RUN}"})
check("201/200 으로 받는다", code == 200, f"code={code} {out}")
cmd_id = out.get("command_id", "")
check("명령 id 를 돌려준다", bool(cmd_id), out)

print("\n[2] 안전장치")
code, out = call("POST", f"/v1/dogs/{DOG}/dispense", APP,
                 {"pellets": {"1": 1}, "request_id": f"e2e-dup-{RUN}"})
check("대기 중이면 409", code == 409, f"code={code} {out}")

print("\n[3] 밥통 -> 서버  명령을 가져간다")
code, out = call("GET", f"/v1/feeder/{DOG}/commands", DEV)
check("200", code == 200, f"code={code}")
cmds = out.get("commands", [])
mine = [c for c in cmds if c.get("id") == cmd_id]
check("방금 쌓은 명령이 보인다", len(mine) == 1, cmds)
if mine:
    c = mine[0]
    check("food_g 가 그램으로 온다", c.get("food_g") == 40.0, c)
    check("pellets 에 1번 2알", c.get("pellets") == [
        {"slot": 1, "cartridge_id": "omega3", "name": "오메가3", "count": 2}], c)
    check("source=manual", c.get("source") == "manual", c)

print("\n[4] 밥통 -> 서버  실행 결과를 보고한다")
code, out = call("POST", f"/v1/feeder/commands/{cmd_id}/ack?state=done", DEV, {})
check("200", code == 200, f"code={code} {out}")
code, out = call("GET", f"/v1/feeder/{DOG}/commands", DEV)
check("ack 한 명령은 다시 안 준다",
      all(c["id"] != cmd_id for c in out.get("commands", [])), out)

print("\n[5] 밥통 -> 서버  내 상태를 올린다 (ESP32 가 보내는 모양 그대로)")
state = {
    "busy": False, "last_job": "manual", "collar_connected": True,
    "collar_class": "walk", "collar_conf": 0.87,
    "minute": {"rest_s": 12, "walk_s": 40, "run_s": 8, "shake_n": 1},
}
code, out = call("POST", f"/v1/feeder/{DOG}/state", DEV, state)
check("200", code == 200, f"code={code} {out}")

print("\n[6] 앱 -> 서버  밥통 화면이 읽는다")
code, view = call("GET", f"/v1/dogs/{DOG}/feeder", APP)
check("200", code == 200, f"code={code}")
check("online", view.get("online") is True, view)
age = view.get("age_s")
# 0 은 거짓이다. `or` 로 기본값을 주면 방금 보고한 경우를 놓친다.
check("나이가 몇 초 안쪽", age is not None and 0 <= age <= 5, view)
check("목줄 행동이 넘어온다", view.get("state", {}).get("collar_class") == "walk", view)
check("1분 요약도", view.get("state", {}).get("minute", {}).get("walk_s") == 40, view)
check("마지막 실행이 done", (view.get("last_ack") or {}).get("state") == "done", view)
check("대기 0", view.get("pending") == 0, view)

print("\n[7] 밥통 -> 서버  목줄 데이터 (ESP32 가 보내는 모양 그대로)")
boot = 90000 + int(RUN) % 9000
t0 = 120_000
batch = {
    "collar_serial": COLLAR,
    "boot_id": boot,
    "uptime_ms": t0 + 500,
    "received_at": datetime.now().isoformat(timespec="seconds"),
    "fw_version": "esp32-dispenser-1",
    "events": [
        {"seq": 1, "t_ms": t0 - 30_000, "type": "body_shake", "conf": 0.91, "dur_s": 1.0},
    ],
    "status": [
        {"t_ms": t0, "worn_sec": 60, "steps": 0,
         "rest_sec": 12, "walk_sec": 40, "run_sec": 8, "vigorous_sec": 0},
    ],
}
code, out = call("POST", "/v1/ingest", DEV, batch)
check("200", code == 200, f"code={code} {out}")
check("이벤트 1건 들어갔다", out.get("accepted") == 1, out)
check("상태 표본 1건 들어갔다", out.get("status_samples") == 1, out)

print("\n[8] 같은 묶음을 다시 보낸다 (재시도)")
code, out = call("POST", "/v1/ingest", DEV, batch)
check("중복은 0건으로 걸러진다", out.get("accepted") == 0, out)
check("중복 개수를 알려준다", out.get("duplicates") == 1, out)

print("\n[9] 배터리를 안 보내도 받는다 (시험용 XIAO 는 못 보낸다)")
b2 = dict(batch)
b2["boot_id"] = boot + 1
code, out = call("POST", "/v1/ingest", DEV, b2)
check("200", code == 200, f"code={code} {out}")

print("\n[10] 사료 자리에 알을 보내면 막는다")
code, out = call("POST", f"/v1/dogs/{DOG}/dispense", APP,
                 {"pellets": {"4": 2}, "request_id": f"e2e-slot4-{RUN}"})
check("422 로 거부", code == 422, f"code={code} {out}")
check("이유를 말해준다", "로더" in json.dumps(out, ensure_ascii=False), out)

print("\n[11] 하루 상한 초과")
code, out = call("POST", f"/v1/dogs/{DOG}/dispense", APP,
                 {"pellets": {"2": 99}, "request_id": f"e2e-cap-{RUN}"})
check("422 로 거부", code == 422, f"code={code} {out}")

print("\n[12] 취소하면 다시 받는다")
call("POST", f"/v1/dogs/{DOG}/dispense", APP, {"pellets": {"1": 1}, "request_id": f"e2e-c1-{RUN}"})
code, out = call("POST", f"/v1/dogs/{DOG}/dispense/cancel", APP, {})
check("취소 1건", out.get("cancelled") == 1, out)
code, out = call("POST", f"/v1/dogs/{DOG}/dispense", APP, {"pellets": {"1": 1}, "request_id": f"e2e-c2-{RUN}"})
check("취소 뒤에는 다시 받는다", code == 200, f"code={code} {out}")
call("POST", f"/v1/dogs/{DOG}/dispense/cancel", APP, {})

print("\n[13] 토큰을 가른다")
code, _ = call("GET", f"/v1/feeder/{DOG}/commands", APP)
check("앱 토큰으로 기기 경로는 401", code == 401, f"code={code}")
code, _ = call("GET", f"/v1/dogs/{DOG}/feeder", DEV)
check("기기 토큰으로 앱 경로는 401", code == 401, f"code={code}")

print(f"\n{'=' * 50}\n  통과 {ok}   실패 {bad}\n{'=' * 50}")
if bad:
    print("  목줄 데이터가 안 들어가면 목줄이 개체에 연결됐는지 보세요.")
    print(f"  (collars 표에 {COLLAR} -> {DOG} 가 있어야 합니다)")
sys.exit(1 if bad else 0)
