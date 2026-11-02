/*
 * pebble 밥통 — Wi-Fi 로 명령을 받아 모터 4개를 따로 돌린다.
 *
 *   앱 / 브라우저 ──HTTP(핫스팟)──▶ ESP32 ──▶ DRV8825 ×4 ──▶ NEMA17 ×4
 *
 * 슬롯
 *   1 오메가3   STEP 13  DIR 14
 *   2 피부장벽  STEP 19  DIR 27
 *   3 관절      STEP 25  DIR 26
 *   4 사료      STEP 32  DIR 33
 *   (실물 배선에서 버튼 번호 = 모터 위치가 되도록 맞춘 값. 2026-11-01)
 *
 * 사용
 *   1. 아래 WIFI_SSID / WIFI_PASS 를 폰 핫스팟 값으로 바꾼다.
 *      **핫스팟은 2.4GHz 여야 한다.** ESP32 는 5GHz 에 붙지 못한다.
 *      (갤럭시: 핫스팟 설정 → 대역 2.4GHz / 아이폰: "호환성 최대화" 켜기)
 *   2. Arduino IDE → Board "ESP32 Dev Module" → 업로드 → 시리얼 115200
 *   3. 시리얼에 찍힌 IP 를 같은 핫스팟에 붙은 폰 브라우저로 연다.
 *      http://<IP>/  에 버튼 페이지가 뜬다. 앱 없이도 시험할 수 있다.
 *
 * HTTP (전부 form 인자. 외부 라이브러리 없이 core 만 쓴다)
 *   GET  /status                          상태 JSON
 *   POST /dispense   slot=1..4 count=N    한 슬롯만
 *   POST /meal       s1=N s2=N s3=N s4=N  한 끼 전체를 순서대로
 *   POST /stop                            진행 중인 배출을 다음 단위에서 멈춤
 *   POST /calibrate  slot=1..4 steps=N    펄스 수를 직접 지정 (양 보정용)
 *
 * 배출은 요청을 받자마자 응답(202)하고 loop() 에서 실행한다.
 * 사료를 길게 돌리면 수십 초가 걸려 HTTP 가 타임아웃 나기 때문이다.
 * 진행 상황은 /status 로 본다. 배출 중 새 요청은 409 로 거절한다 —
 * 한 번에 하나만 돌린다 (EN 공용 배선과도 맞다).
 *
 * **ESP32 핀에는 절대 12V 를 넣지 않는다.** 12V 는 확장보드 나사 단자로만.
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <NimBLEDevice.h>   // 목줄(XIAO) 수신. 라이브러리 NimBLE-Arduino 2.x
// BLE 를 넣으면 기본 파티션(1.3MB)을 넘는다.
// Tools → Partition Scheme → "Huge APP (3MB No OTA/1MB SPIFFS)" 로 올린다.

// ---------------------------------------------------------------------------
// 설정 — 여기만 고치면 된다
// ---------------------------------------------------------------------------

// Wi-Fi 는 secrets.h 에서 온다 (커밋 안 됨 — 저장소가 Public 이다).
//   cp firmware/secrets.example.h firmware/esp32_dispenser/secrets.h
#include "secrets.h"
const char* WIFI_PASS = WIFI_PASSWORD;
const char* HOSTNAME  = "pebble";          // 지원되는 기기에선 http://pebble.local

// 검증된 펄스 모양 (esp32_spin 참고). HIGH 길게, LOW 짧게.
const unsigned long HIGH_US = 1600;
const unsigned long LOW_US  = 100;

// EN (4개 드라이버 공용) → GPIO5. 배출하는 동안만 켠다.
// 안 쓰면 DRV8825 가 대기 중에도 전류를 흘려 금방 뜨거워진다 (실물 확인).
// GPIO5 는 부팅 중 HIGH 라 켜지는 순간 모터가 움찔하지 않는다.
// -1 로 두면 EN 제어를 끈다 (드라이버 항상 켜짐).
// 2026-11-01: GPIO5·22 둘 다 EN 을 연결하면 모터가 안 돌았다 (빼면 돎).
// 원인 미확인 — 멀티미터로 /entest 중 EN 전압을 재 봐야 안다.
// 그때까지 EN 은 빼고, 발열은 VREF 를 낮춰서 잡는다.
const int EN_PIN = -1;

struct Slot {
  const char* id;
  const char* name;
  int  stepPin;
  int  dirPin;
  bool reverse;        // 배출 방향이 반대면 true
  int  stepsPerUnit;   // **1단위(1알 / 사료 1회) 당 펄스 수 — 실물 보고 보정할 값**
  int  maxUnits;       // 한 번 요청 상한. 오타로 통이 비는 걸 막는다
  const char* unit;
};

Slot SLOTS[4] = {
  // 핀은 실물 배선 기준 (2026-11-01 확인). 슬롯 번호 = 모터 물리 위치.
  {"omega3",       "오메가3", 13, 14, false, 200, 10, "알"},
  {"skin_barrier", "피부장벽", 19, 27, false, 200, 10, "알"},
  {"joint",        "관절",    25, 26, false, 200, 10, "알"},
  {"food",         "사료",    32, 33, false, 200, 30, "회"},
};
const int N_SLOTS = 4;

// 단위 사이 쉬는 시간. 알이 떨어질 틈을 준다.
const unsigned long GAP_MS = 150;

// ---------------------------------------------------------------------------
// 상태
// ---------------------------------------------------------------------------

WebServer server(80);

int  pendingUnits[N_SLOTS] = {0};   // 이번 작업에서 돌릴 단위 수
int  doneUnits[N_SLOTS]    = {0};   // 실제로 돈 단위 수
int  calibSteps = 0;                // >0 이면 보정 작업 (calibSlot 에 펄스 직접)
int  calibSlot  = -1;
bool hasJob   = false;
bool busy     = false;
bool stopReq  = false;
bool lastStopped = false;
unsigned long jobCounter = 0;

// ---------------------------------------------------------------------------
// 목줄 (BLE) — XIAO "pebble-collar" 를 찾아 붙고 알림을 받는다
//
//   XIAO = 주변기기(광고·알림)   ESP32 = 중심기기(스캔·연결·구독)
//   now  2바이트 [클래스, 신뢰도%]                     1초마다
//   min  8바이트 uint16 LE [쉬기초, 걷기초, 뛰기초, 털기회]  1분마다
//
// BLE 콜백은 NimBLE 태스크에서 돈다. 여기서는 값만 저장하고
// 출력·HTTP 는 loop() 쪽에서 한다.
// ---------------------------------------------------------------------------

const char* COLLAR_NAME = "pebble-collar";
static NimBLEUUID UUID_SVC("19b10000-e8f2-537e-4f6c-d104768a1214");
static NimBLEUUID UUID_NOW("19b10001-e8f2-537e-4f6c-d104768a1214");
static NimBLEUUID UUID_MIN("19b10002-e8f2-537e-4f6c-d104768a1214");
const char* BEHAVIOR_KO[4] = {"쉬기", "걷기", "뛰기", "털기"};

NimBLEClient* collarClient = nullptr;
volatile bool collarFound = false;
NimBLEAddress collarAddr;
volatile bool collarConnected = false;
volatile bool collarLost = false;

volatile int  nowCls = -1;
volatile int  nowConf = 0;
volatile unsigned long nowAt = 0;
volatile bool nowFresh = false;

volatile uint16_t minVals[4] = {0, 0, 0, 0};
volatile unsigned long minAt = 0;
volatile bool minFresh = false;

class CollarScan : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice* d) override {
    if (collarFound) return;
    if (d->isAdvertisingService(UUID_SVC) || d->getName() == COLLAR_NAME) {
      collarAddr = d->getAddress();
      collarFound = true;
      NimBLEDevice::getScan()->stop();
    }
  }
} collarScan;

class CollarClientCb : public NimBLEClientCallbacks {
  void onDisconnect(NimBLEClient* c, int reason) override {
    collarConnected = false;
    collarLost = true;
  }
} collarClientCb;

static void onNow(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
  if (len < 2 || data[0] > 3) return;
  nowCls = data[0];
  nowConf = data[1];
  nowAt = millis();
  nowFresh = true;
}

static void onMin(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
  if (len < 8) return;
  for (int i = 0; i < 4; i++) minVals[i] = data[i * 2] | (data[i * 2 + 1] << 8);
  minAt = millis();
  minFresh = true;
}

static void startCollarScan() {
  collarFound = false;
  NimBLEScan* scan = NimBLEDevice::getScan();
  scan->setScanCallbacks(&collarScan, false);
  scan->setActiveScan(true);          // 이름은 스캔 응답에 실려 온다
  scan->setInterval(100);
  scan->setWindow(50);                // Wi-Fi 와 안테나를 나눠 쓰므로 절반만 듣는다
  scan->start(0, false, true);        // 찾을 때까지 계속
  Serial.println("[BLE] 목줄 찾는 중…");
}

static void connectCollar() {
  collarFound = false;
  Serial.printf("[BLE] 목줄 발견 %s — 연결 시도\n", collarAddr.toString().c_str());
  if (!collarClient) {
    collarClient = NimBLEDevice::createClient();
    collarClient->setClientCallbacks(&collarClientCb, false);
    collarClient->setConnectTimeout(5000);
  }
  if (!collarClient->connect(collarAddr)) {
    Serial.println("[BLE] 연결 실패 — 다시 찾습니다");
    startCollarScan();
    return;
  }
  NimBLERemoteService* svc = collarClient->getService(UUID_SVC);
  NimBLERemoteCharacteristic* chNow = svc ? svc->getCharacteristic(UUID_NOW) : nullptr;
  NimBLERemoteCharacteristic* chMin = svc ? svc->getCharacteristic(UUID_MIN) : nullptr;
  if (!chNow || !chNow->subscribe(true, onNow) || (chMin && !chMin->subscribe(true, onMin))) {
    Serial.println("[BLE] 목줄 서비스를 못 찾음 — 끊고 다시 찾습니다");
    collarClient->disconnect();
    return;                           // onDisconnect 가 다시 스캔하게 만든다
  }
  collarConnected = true;
  Serial.println("[BLE] 목줄 연결됨 — 1초마다 행동, 1분마다 요약을 받습니다");
}

static void serviceCollar() {
  if (collarLost) {
    collarLost = false;
    nowCls = -1;
    Serial.println("[BLE] 목줄 연결 끊김 — 다시 찾습니다");
    startCollarScan();
  }
  if (collarFound && !collarConnected) connectCollar();

  // 1초 판정은 바뀔 때만 찍는다. 같은 줄이 초마다 쏟아지면 로그를 못 읽는다.
  static int lastPrinted = -2;
  if (nowFresh) {
    nowFresh = false;
    if (nowCls != lastPrinted) {
      lastPrinted = nowCls;
      Serial.printf("[목줄] %s %d%%\n", BEHAVIOR_KO[nowCls], nowConf);
    }
  }
  if (minFresh) {
    minFresh = false;
    Serial.printf("[목줄 1분] 쉬기 %u초 걷기 %u초 뛰기 %u초 털기 %u회\n",
                  minVals[0], minVals[1], minVals[2], minVals[3]);
  }
}

// ---------------------------------------------------------------------------
// 모터
// ---------------------------------------------------------------------------

static void enableDrivers(bool on) {
  if (EN_PIN < 0) return;
  digitalWrite(EN_PIN, on ? LOW : HIGH);   // LOW 가 활성
}

/** 슬롯 하나를 지정 펄스만큼 돌린다. 간격은 busy-wait 로 정확하게. */
static void turnSteps(int s, int steps) {
  const Slot& sl = SLOTS[s];
  digitalWrite(sl.dirPin, sl.reverse ? HIGH : LOW);
  delayMicroseconds(5);
  for (int i = 0; i < steps; i++) {
    digitalWrite(sl.stepPin, HIGH);
    delayMicroseconds(HIGH_US);
    digitalWrite(sl.stepPin, LOW);
    delayMicroseconds(LOW_US);
  }
}

/** 쌓인 작업을 실행한다. 단위 사이마다 HTTP 를 받아 /status·/stop 에 답한다. */
static void runJob() {
  busy = true;
  hasJob = false;
  stopReq = false;
  for (int s = 0; s < N_SLOTS; s++) doneUnits[s] = 0;

  enableDrivers(true);
  delay(10);

  if (calibSteps > 0) {
    Serial.printf("[보정] 슬롯 %d  %d 펄스\n", calibSlot + 1, calibSteps);
    turnSteps(calibSlot, calibSteps);
    calibSteps = 0;
  } else {
    for (int s = 0; s < N_SLOTS && !stopReq; s++) {
      for (int u = 0; u < pendingUnits[s] && !stopReq; u++) {
        turnSteps(s, SLOTS[s].stepsPerUnit);
        doneUnits[s]++;
        Serial.printf("[배출] %s %d/%d\n", SLOTS[s].name, doneUnits[s], pendingUnits[s]);
        unsigned long t = millis();
        while (millis() - t < GAP_MS) server.handleClient();
      }
    }
  }

  enableDrivers(false);
  lastStopped = stopReq;
  stopReq = false;
  busy = false;
  Serial.println(lastStopped ? "[배출] 중단됨" : "[배출] 완료");
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

static void sendJson(int code, const String& body) {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(code, "application/json; charset=utf-8", body);
}

static void sendError(int code, const String& msg) {
  sendJson(code, "{\"ok\":false,\"error\":\"" + msg + "\"}");
}

static String statusJson() {
  String j = "{\"ok\":true,\"busy\":";
  j += busy ? "true" : "false";
  j += ",\"job\":" + String(jobCounter);
  j += ",\"ip\":\"" + WiFi.localIP().toString() + "\"";
  j += ",\"rssi\":" + String(WiFi.RSSI());
  j += ",\"stopped\":";
  j += lastStopped ? "true" : "false";
  j += ",\"slots\":[";
  for (int s = 0; s < N_SLOTS; s++) {
    if (s) j += ",";
    j += "{\"slot\":" + String(s + 1);
    j += ",\"id\":\"" + String(SLOTS[s].id) + "\"";
    j += ",\"name\":\"" + String(SLOTS[s].name) + "\"";
    j += ",\"unit\":\"" + String(SLOTS[s].unit) + "\"";
    j += ",\"max\":" + String(SLOTS[s].maxUnits);
    j += ",\"requested\":" + String(pendingUnits[s]);
    j += ",\"done\":" + String(doneUnits[s]) + "}";
  }
  j += "]";

  // 목줄. age 는 마지막으로 받은 뒤 지난 초 (-1 = 아직 못 받음)
  unsigned long now = millis();
  j += ",\"collar\":{\"connected\":";
  j += collarConnected ? "true" : "false";
  j += ",\"cls\":" + String(nowCls);
  j += ",\"conf\":" + String(nowConf);
  j += ",\"age\":" + String(nowAt ? (long)((now - nowAt) / 1000) : -1L);
  j += ",\"min\":[" + String(minVals[0]) + "," + String(minVals[1]) + "," +
       String(minVals[2]) + "," + String(minVals[3]) + "]";
  j += ",\"min_age\":" + String(minAt ? (long)((now - minAt) / 1000) : -1L);
  j += "}}";
  return j;
}

/** 정수 인자를 읽는다. 없거나 숫자가 아니면 def. */
static int argInt(const char* name, int def) {
  if (!server.hasArg(name)) return def;
  String v = server.arg(name);
  v.trim();
  if (v.length() == 0) return def;
  for (unsigned i = 0; i < v.length(); i++) {
    if (!isDigit(v[i]) && !(i == 0 && v[i] == '-')) return def;
  }
  return v.toInt();
}

static bool rejectIfBusy() {
  if (busy || hasJob) { sendError(409, "busy"); return true; }
  return false;
}

static void acceptJob() {
  hasJob = true;
  jobCounter++;
  sendJson(202, "{\"ok\":true,\"job\":" + String(jobCounter) + "}");
}

static void handleDispense() {
  if (rejectIfBusy()) return;
  int slot  = argInt("slot", 0);
  int count = argInt("count", 1);
  if (slot < 1 || slot > N_SLOTS) { sendError(400, "slot must be 1-4"); return; }
  const Slot& sl = SLOTS[slot - 1];
  if (count < 1 || count > sl.maxUnits) {
    sendError(400, "count must be 1-" + String(sl.maxUnits));
    return;
  }
  for (int s = 0; s < N_SLOTS; s++) pendingUnits[s] = 0;
  pendingUnits[slot - 1] = count;
  acceptJob();
}

static void handleMeal() {
  if (rejectIfBusy()) return;
  int counts[N_SLOTS];
  int total = 0;
  for (int s = 0; s < N_SLOTS; s++) {
    String key = "s" + String(s + 1);
    counts[s] = argInt(key.c_str(), 0);
    if (counts[s] < 0 || counts[s] > SLOTS[s].maxUnits) {
      sendError(400, key + " must be 0-" + String(SLOTS[s].maxUnits));
      return;
    }
    total += counts[s];
  }
  if (total == 0) { sendError(400, "nothing to dispense"); return; }
  for (int s = 0; s < N_SLOTS; s++) pendingUnits[s] = counts[s];
  acceptJob();
}

static void handleCalibrate() {
  if (rejectIfBusy()) return;
  int slot  = argInt("slot", 0);
  int steps = argInt("steps", 0);
  if (slot < 1 || slot > N_SLOTS) { sendError(400, "slot must be 1-4"); return; }
  if (steps < 1 || steps > 4000) { sendError(400, "steps must be 1-4000"); return; }
  for (int s = 0; s < N_SLOTS; s++) pendingUnits[s] = 0;
  calibSlot  = slot - 1;
  calibSteps = steps;
  acceptJob();
}

// EN 진단. 브라우저로 http://<IP>/entest 를 열면
// EN 을 10초 LOW(켜짐) → 10초 HIGH(꺼짐) 로 바꾼다. 그동안 축을 손으로 돌려보거나
// EN 핀 전압을 잰다. 켜짐 구간에 축이 뻑뻑해지면 EN 제어가 정상이다.
bool enTestReq = false;

static void handleEnTest() {
  if (rejectIfBusy()) return;
  if (EN_PIN < 0) { sendError(400, "EN_PIN is disabled"); return; }
  enTestReq = true;
  sendJson(202, "{\"ok\":true,\"note\":\"EN LOW 10s then HIGH 10s - watch serial\"}");
}

static void waitServing(unsigned long ms) {
  unsigned long t = millis();
  while (millis() - t < ms) { server.handleClient(); delay(5); }
}

static void runEnTest() {
  enTestReq = false;
  busy = true;
  Serial.printf("[EN 시험] GPIO%d LOW 10초 — 드라이버 켜짐. 축이 뻑뻑해야 함 / EN 핀 0V\n", EN_PIN);
  enableDrivers(true);
  Serial.printf("[EN 시험] 읽은 값: %d (0 이어야 함)\n", digitalRead(EN_PIN));
  waitServing(10000);
  Serial.printf("[EN 시험] GPIO%d HIGH 10초 — 드라이버 꺼짐. 축이 부드러워야 함 / EN 핀 3.3V\n", EN_PIN);
  enableDrivers(false);
  Serial.printf("[EN 시험] 읽은 값: %d (1 이어야 함)\n", digitalRead(EN_PIN));
  waitServing(10000);
  Serial.println("[EN 시험] 끝");
  busy = false;
}

static void handleStop() {
  if (busy) stopReq = true;
  sendJson(200, "{\"ok\":true,\"stopping\":" + String(busy ? "true" : "false") + "}");
}

// 앱이 나오기 전 시험용 페이지. 같은 핫스팟의 폰 브라우저로 연다.
static const char INDEX_HTML[] PROGMEM = R"HTML(<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>pebble 밥통</title>
<style>
body{font-family:system-ui,sans-serif;max-width:420px;margin:0 auto;padding:16px;background:#f5f5f7;color:#1d1d1f}
h1{font-size:20px}.row{display:flex;align-items:center;gap:8px;background:#fff;border-radius:12px;padding:12px;margin:8px 0}
.row b{flex:1}input{width:56px;font-size:16px;padding:6px}button{font-size:16px;padding:8px 14px;border:0;border-radius:8px;background:#0071e3;color:#fff}
#stop{background:#d70015;width:100%;margin-top:8px}#st{font-size:13px;color:#555;white-space:pre-wrap}
</style></head><body>
<h1>pebble 밥통</h1>
<div id="slots"></div>
<button id="stop" onclick="post('/stop',{})">정지</button>
<p id="st">연결 중…</p>
<script>
async function post(p,o){try{const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(o)});const j=await r.json();if(!j.ok)alert(j.error);}catch(e){}refresh();}
async function refresh(){try{const j=await(await fetch('/status')).json();
const box=document.getElementById('slots');
if(!box.children.length){j.slots.forEach(s=>{box.insertAdjacentHTML('beforeend',
`<div class="row"><b>${s.slot}. ${s.name}</b><input id="c${s.slot}" type="number" min="1" max="${s.max}" value="1">${s.unit}<button onclick="post('/dispense',{slot:${s.slot},count:document.getElementById('c${s.slot}').value})">배출</button></div>`);});}
document.getElementById('st').textContent=(j.busy?'배출 중\n':'대기\n')+j.slots.map(s=>`${s.name} ${s.done}/${s.requested}`).join('  ')+(j.stopped?'\n(마지막 작업 중단됨)':'')+`\nIP ${j.ip}  신호 ${j.rssi}dBm`;
}catch(e){document.getElementById('st').textContent='연결 끊김';}}
refresh();setInterval(refresh,1000);
</script></body></html>)HTML";

static void handleIndex() { server.send_P(200, "text/html; charset=utf-8", INDEX_HTML); }

// ---------------------------------------------------------------------------
// Wi-Fi
// ---------------------------------------------------------------------------

static void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[Wi-Fi] '%s' 에 연결 중", WIFI_SSID);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("[Wi-Fi] 연결됨");
    Serial.print("[Wi-Fi] 브라우저로 여세요:  http://");
    Serial.println(WiFi.localIP());
    if (MDNS.begin(HOSTNAME)) MDNS.addService("http", "tcp", 80);
  } else {
    Serial.println("[Wi-Fi] 실패 — SSID/비밀번호, 핫스팟 2.4GHz 여부를 확인하세요. 계속 재시도합니다.");
  }
}

static void keepWifi() {
  static unsigned long last = 0;
  static bool wasConnected = true;
  bool now = WiFi.status() == WL_CONNECTED;
  if (now && !wasConnected) {
    Serial.print("[Wi-Fi] 다시 연결됨:  http://");
    Serial.println(WiFi.localIP());
  }
  wasConnected = now;
  if (now) return;
  if (millis() - last < 10000) return;
  last = millis();
  Serial.println("[Wi-Fi] 끊김 — 재연결");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(300);

  // 켜자마자 모터가 움찔하지 않도록 STEP 을 먼저 LOW 로 묶는다.
  for (int s = 0; s < N_SLOTS; s++) {
    pinMode(SLOTS[s].stepPin, OUTPUT);
    digitalWrite(SLOTS[s].stepPin, LOW);
    pinMode(SLOTS[s].dirPin, OUTPUT);
    digitalWrite(SLOTS[s].dirPin, LOW);
  }
  if (EN_PIN >= 0) { pinMode(EN_PIN, OUTPUT); enableDrivers(false); }

  Serial.println();
  Serial.println("=========================================");
  Serial.println(" pebble 밥통 (Wi-Fi 배출)");
  Serial.println("=========================================");
  for (int s = 0; s < N_SLOTS; s++) {
    Serial.printf("  %d %-6s STEP %2d DIR %2d  %d펄스/%s\n", s + 1, SLOTS[s].id,
                  SLOTS[s].stepPin, SLOTS[s].dirPin, SLOTS[s].stepsPerUnit, SLOTS[s].unit);
  }

  connectWifi();

  server.on("/", HTTP_GET, handleIndex);
  server.on("/status", HTTP_GET, []() { sendJson(200, statusJson()); });
  server.on("/dispense", HTTP_POST, handleDispense);
  server.on("/meal", HTTP_POST, handleMeal);
  server.on("/calibrate", HTTP_POST, handleCalibrate);
  server.on("/stop", HTTP_POST, handleStop);
  server.on("/entest", HTTP_GET, handleEnTest);
  server.onNotFound([]() { sendError(404, "not found"); });
  server.begin();
  Serial.println("[HTTP] 80번 포트 대기");

  NimBLEDevice::init("pebble-feeder");
  startCollarScan();
}

void loop() {
  server.handleClient();
  keepWifi();
  serviceCollar();
  if (enTestReq && !busy) runEnTest();
  if (hasJob && !busy) runJob();
}
