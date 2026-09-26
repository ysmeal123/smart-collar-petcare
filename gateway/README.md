# 게이트웨이 (라즈베리파이)

밥통에 올라가는 프로그램. **목줄과 서버 사이를 잇고, 실제로 사료를 사출한다.**

```
목줄 ──BLE──> [버퍼] ──HTTPS──> 서버
                │
밥통 <──명령──── [계획 캐시] <──┘
```

---

## 바로 돌려보기

하드웨어 없이 전체 루프가 돕니다.

```bash
cd /path/to/repo
python -m gateway.agent --mock
```

서버까지 붙이려면:

```bash
# 터미널 1 — 서버
cd petcare && python seed_live.py && uvicorn api.main:app --port 8000

# 터미널 2 — 게이트웨이
PEBBLE_SERVER=http://localhost:8000 PEBBLE_DOG=dog_choco \
python -m gateway.agent --mock
```

사출이 걸리는 상황도 볼 수 있습니다.

```bash
python -m gateway.agent --mock --jam 0.3
```

**의존성이 없습니다.** `pip install` 없이 파이썬만 있으면 됩니다.

---

## 구현해야 할 것 — `hardware.py` 세 개뿐

나머지(버퍼·전송·재시도·시계·폴백·멱등성)는 **이미 다 돼 있습니다.**
아래 세 클래스만 채우면 실물로 돌아갑니다.

### 1. `CollarLink.poll()` — BLE 수신

```python
def poll(self) -> tuple[int, list[dict], list[dict]]:
    return boot_id, events, status
```

| | 필드 |
|---|---|
| 이벤트 | `seq` `t_ms` `type` `conf` `dur_s` |
| 상태(1분) | `t_ms` `worn_sec` `steps` `battery` `rest_sec` `walk_sec` `run_sec` `vigorous_sec` |

`type`은 넷 중 하나: `scratch` `head_shake` `body_shake` `posture_change`

BLE 레코드는 12바이트 고정입니다.

```python
t_ms, typ, conf, dur_ds, seq = struct.unpack('<IBBHI', chunk)
```

- `bleak` 권장 (비동기라 편합니다)
- 목줄이 notify로 밀어주는 구조가 폴링보다 전력에 낫습니다
- **연결이 끊겨 있으면 빈 리스트를 돌려주세요.** 재연결은 이 클래스 안에서 처리하면 됩니다

### 2. `Dispenser.dispense()` — 사출

```python
def dispense(self, food_g: int, pellets: list[dict]) -> DispenseResult:
    # pellets: [{"slot": 1, "count": 4, "name": "오메가3"}, ...]
```

- 사료: 스텝모터 회전수 × 1회전당 그램. 개체마다 보정이 필요합니다
- 영양제: 슬롯별 서보로 1알씩. 광센서로 통과를 세는 걸 권합니다
- **요청한 개수를 못 채웠으면 성공이라고 하지 마세요.** 실제 사출 수를 그대로 돌려주셔야 서버가 섭취량을 대조합니다
- 걸리면 재시도 2회까지, 그 뒤엔 실패 보고

### 3. `Scale` — 로드셀

```python
def read_bowl_g(self) -> float | None            # 밥그릇
def read_platform_session(self) -> list[float] | None   # 급식판 체중계
```

- 밥그릇: 배식 직후와 식사 종료 후를 재면 실제 섭취량이 나옵니다
- 급식판: 개가 올라선 동안 **0.2초 간격으로 여러 번** 읽으세요. 한 번만 읽으면 움직임 때문에 값이 튑니다
- **판정은 서버가 합니다.** 원시 표본을 그대로 올려주시면 서버가 중앙값·변동계수·타당성을 봅니다

다 만들면 `agent.py`의 `main()`에서 Mock 대신 끼우면 됩니다.

---

## 이미 처리해 둔 것들

실기기에서 **반드시 생기는** 상황들입니다.

| 상황 | 대응 |
|---|---|
| 인터넷 끊김 | SQLite 버퍼에 쌓고 복구되면 순서대로 전송 |
| **전송 성공 전 삭제 금지** | 서버가 받았다고 확인한 것만 지웁니다 |
| 목줄이 같은 걸 다시 뱉음 | `(boot_id, seq)` 기본키로 차단 |
| 목줄 재부팅 | `boot_id`가 바뀌어 같은 `seq`도 구분됩니다 |
| 서버가 형식 거부(4xx) | **버리고 다음으로 넘어감** — 안 그러면 큐가 영구히 막힙니다 |
| 서버 다운(5xx·네트워크) | 버퍼에 유지하고 재시도 |
| 7일 넘게 못 보낸 데이터 | 폐기 (SD 카드 보호) |
| 같은 명령 재수신 | 이미 실행했으면 무시 — **이중 급여는 사고입니다** |
| 사출 실패 | 실패로 기록하고 서버에 보고 |
| **서버 다운 중 급여 시각** | 캐시된 계획으로 정상 사출 |
| **7일 넘게 서버 끊김** | 사료만 주고 **영양제는 중단** |

### 시계 — 라즈베리파이에 RTC가 없습니다

이게 제일 조용하고 위험한 함정입니다.

서버는 목줄 부팅 시각을 이렇게 역산합니다.

```
목줄 부팅시각 = 게이트웨이 수신시각 − uptime
```

**게이트웨이 시각이 틀리면 목줄 데이터가 통째로 엉뚱한 날짜에 꽂힙니다.**
정전 후 재부팅 → NTP 받기 전에 업로드 → 하루치가 1970년으로 갑니다.

그래서 `client.py`가 업로드 전에 시계를 확인합니다. 동기화 전이면
**올리지 않고 버퍼에 쌓아둡니다.** 어차피 인터넷이 없으면 업로드도 못 합니다.

> **DS3231 RTC 모듈(2천원대)을 다는 걸 권합니다.** I2C로 물리면 끝이고,
> 그러면 이 문제 자체가 사라집니다.

### 폴백 — 강아지는 클라우드 사정과 무관하게 밥을 먹어야 합니다

```
정상        서버에서 받은 명령대로 사출
서버 다운   캐시된 마지막 계획으로 계속
7일 초과    사료만 주고 영양제 중단
```

영양제를 끊는 이유: **영양제는 상태 추론의 결과인데, 그 추론이 몇 주 전 것**이기
때문입니다. 사료는 굶기면 안 되니 계속 주고, 영양제는 근거가 낡으면 멈춥니다.

---

## 설정

환경변수로 덮습니다. (`config.py`)

| | 기본값 |
|---|---|
| `PEBBLE_SERVER` | `http://localhost:8000` |
| `PEBBLE_COLLAR` | `PBL-0001` |
| `PEBBLE_DOG` | `dog_choco` |
| `PEBBLE_DB` | `gateway.db` |

주기는 `Config`에서 조정합니다 — 목줄 폴링 10초 / 업로드 5분 / 명령 확인 1분.

---

## 라즈베리파이에 올리기

```bash
sudo tee /etc/systemd/system/pebble-gateway.service > /dev/null <<'EOF'
[Unit]
Description=pebble gateway
After=network-online.target time-sync.target

[Service]
WorkingDirectory=/home/pi/pebble
ExecStart=/usr/bin/python3 -m gateway.agent
Environment=PEBBLE_SERVER=https://your-server
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now pebble-gateway
journalctl -u pebble-gateway -f
```

`After=time-sync.target`이 중요합니다 — NTP를 받은 뒤에 시작합니다.

---

## 테스트

```bash
python -m pytest gateway/tests -v
```

19개입니다. **"돌아간다"가 아니라 "끊기고 꺼져도 데이터를 잃지 않고 밥이 나간다"**를
검증합니다. 마지막 하나는 게이트웨이가 만든 JSON을 서버 스키마로 파싱해서
**계약이 갈라지지 않았는지** 확인합니다.
