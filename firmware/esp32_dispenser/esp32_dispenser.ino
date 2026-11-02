/*
 * pebble 밥통 — Wi-Fi 로 명령을 받아 모터 4개를 따로 돌린다.
 *
 *   앱 / 브라우저 ──HTTP(핫스팟)──▶ ESP32 ──▶ DRV8825 ×4 ──▶ NEMA17 ×4
 *
 * 그리고 **서버하고도 말한다.** 이쪽이 제품 경로다.
 *
 *   앱 ──▶ 서버 ──(60초 폴링)──▶ 밥통        급여 명령
 *   앱 ◀── 서버 ◀──────────────  밥통        밥통 상태 · 목줄 행동
 *
 * 직접 HTTP 는 같은 핫스팟에 있을 때만 된다. 서버를 거치면 밥통이
 * 가져가므로 앱이 켜져 있지 않아도 되고 집 밖에서도 된다.
 * 직접 경로는 **시연용으로 남겨둔다** - 인터넷이 말을 안 들을 때 쓴다.
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
 *   POST /dispense   slot=4 g=N             사료는 그램으로도 (서버와 같은 단위)
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
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <Preferences.h>
#include <time.h>
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

// 서버 설정도 secrets.h 에서 온다. 없으면 서버 경로를 끄고 직접 HTTP 만 쓴다.
// 팀원이 받은 secrets.h 에 서버 칸이 없을 수 있어서 기본값을 둔다.
#ifndef PEBBLE_SERVER
#define PEBBLE_SERVER ""
#endif
#ifndef PEBBLE_TOKEN
#define PEBBLE_TOKEN ""
#endif
#ifndef PEBBLE_DOG
#define PEBBLE_DOG "dog_choco"
#endif
#ifndef PEBBLE_COLLAR
#define PEBBLE_COLLAR "PBL-0001"
#endif

//: 서버에 명령을 물어보는 주기.
//:
//: 급여 시각보다 30분 먼저 명령이 쌓이므로(COMMAND_LEAD) 1분 간격이면
//: 충분하다. 더 자주 치면 무료 호스팅의 요청 수만 깎는다.
const unsigned long POLL_MS = 60000;

//: 목줄 데이터를 올리는 주기. 1분 요약이 오는 대로 보낸다.
//: 여러 번 몰아서 보내도 서버가 (부팅ID, t_ms) 로 중복을 막는다.
const unsigned long UPLOAD_MS = 60000;

//: Wi-Fi 가 끊긴 동안 쌓아둘 양. 넘치면 **오래된 것부터 버린다.**
//:
//: 최신 값이 더 쓸모 있다 - 지금 상태를 보여주는 게 먼저고, 지난 데이터는
//: 어차피 추세용이다. 메모리가 터지면 둘 다 잃는다.
const int MAX_SAMPLES = 30;
const int MAX_EVENTS   = 60;

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

//: 사료 슬롯의 번호(0-based). 서버는 사료를 **그램**으로 보낸다.
//:
//: 영양제는 "알" 이라 세면 되지만 사료는 양이다. 그래서 서버 명령의
//: pellets 에는 1~3번만 오고, 사료는 food_g 로 따로 온다.
const int FOOD_SLOT = 3;

//: 사료 1g 당 펄스. **보정 전 값이다.**
//:
//: 실물로 재야 한다 - /calibrate 로 1000펄스를 돌려 나온 양을 저울로
//: 재고 1000/그램 을 넣는다. 지금 값으로는 120g 요청이 60g 이나 200g 이
//: 될 수 있다.
const float FOOD_STEPS_PER_G = 4.0f;

//: 한 번에 돌릴 사료 펄스 상한. 서버가 이상한 값을 보내도 통을 비우지 않는다.
const int FOOD_STEPS_MAX = 4000;

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

//: 사료는 단위로 쪼개지 않고 한 번에 돌린다. 0 이면 사료 없음.
int foodSteps = 0;

//: 지금 실행 중인 서버 명령의 id. 비어 있으면 손으로 시킨 작업이다.
//: 끝나면 이 id 로 ack 를 보낸다 - 보내지 않으면 서버가 다음 폴링에서
//: 같은 명령을 또 준다.
String jobCmdId = "";
String jobSource = "";

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

// --- 서버로 올릴 버퍼 -------------------------------------------------------
//
// 1분 요약은 status_samples 로, 털기는 events 로 간다. 서버가 이미 그렇게
// 설계돼 있다 — ActivityClass(쉬기/걷기/뛰기) 는 1분 요약, BODY_SHAKE 는
// 이벤트다. 여기서 모양만 맞춰 주면 집계·처방·대시보드가 그대로 돈다.

struct MinuteSample {
  unsigned long t_ms;
  uint16_t rest_s, walk_s, run_s;
};

struct ShakeEvent {
  unsigned long t_ms;
  uint8_t conf;          // 0~100
};

MinuteSample samples[MAX_SAMPLES];
int nSamples = 0;
ShakeEvent shakes[MAX_EVENTS];
int nShakes = 0;
uint32_t shakeSeq = 0;         // 부팅 후 단조 증가. 서버의 중복 제거 키다

/** 오래된 것부터 버리고 뒤에 붙인다. */
static void pushSample(unsigned long t, uint16_t r, uint16_t w, uint16_t run) {
  if (nSamples >= MAX_SAMPLES) {
    for (int i = 1; i < MAX_SAMPLES; i++) samples[i - 1] = samples[i];
    nSamples = MAX_SAMPLES - 1;
  }
  samples[nSamples++] = {t, r, w, run};
}

static void pushShake(unsigned long t, uint8_t conf) {
  if (nShakes >= MAX_EVENTS) {
    for (int i = 1; i < MAX_EVENTS; i++) shakes[i - 1] = shakes[i];
    nShakes = MAX_EVENTS - 1;
  }
  shakes[nShakes++] = {t, conf};
}

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
  static int lastCls = -1;
  if (nowFresh) {
    nowFresh = false;
    if (nowCls != lastPrinted) {
      lastPrinted = nowCls;
      Serial.printf("[목줄] %s %d%%\n", BEHAVIOR_KO[nowCls], nowConf);
    }
    // **털기는 시작할 때만 한 번** 이벤트로 잡는다.
    //
    // 1분 요약의 '털기 N회' 를 쓰지 않는 이유는 시각이 없기 때문이다.
    // 그걸로 이벤트를 만들려면 분 안의 어딘가로 **시각을 지어내야** 한다.
    // 여기서 잡으면 알림이 온 그 시점이 실제 시각이고 신뢰도도 실제값이다.
    if (nowCls == 3 && lastCls != 3) {
      pushShake(nowAt, (uint8_t)nowConf);
      shakeSeq++;
    }
    lastCls = nowCls;
  }
  if (minFresh) {
    minFresh = false;
    Serial.printf("[목줄 1분] 쉬기 %u초 걷기 %u초 뛰기 %u초 털기 %u회\n",
                  minVals[0], minVals[1], minVals[2], minVals[3]);
    pushSample(minAt, minVals[0], minVals[1], minVals[2]);
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
    // 사료는 **단위로 쪼개지 않고 한 번에** 돌린다.
    //
    // 알은 하나씩 떨어져야 해서 사이에 틈을 두지만, 사료는 연속으로
    // 흘려보내는 게 맞다. 중간에 멈추면 스크류에 물려 뭉친다.
    if (!stopReq && foodSteps > 0) {
      Serial.printf("[배출] %s %d 펄스\n", SLOTS[FOOD_SLOT].name, foodSteps);
      turnSteps(FOOD_SLOT, foodSteps);
      foodSteps = 0;
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
  // 손으로 시킨 작업이다. 서버 명령의 흔적을 지운다 — 안 지우면 끝난 뒤에
  // 엉뚱한 명령에 ack 가 가고, 서버는 안 나간 급여를 나갔다고 믿는다.
  jobCmdId = "";
  jobSource = "local";
  foodSteps = 0;
  sendJson(202, "{\"ok\":true,\"job\":" + String(jobCounter) + "}");
}

static void handleDispense() {
  if (rejectIfBusy()) return;
  int slot  = argInt("slot", 0);
  if (slot < 1 || slot > N_SLOTS) { sendError(400, "slot must be 1-4"); return; }

  // 사료는 **그램**으로 받는다. 서버도 food_g 로 보내므로 앱이 두 경로에
  // 같은 숫자를 쓸 수 있다. 단위가 경로마다 다르면 보호자가 틀린 양을
  // 입력하는 날이 온다.
  //
  // count(회) 도 계속 받는다 - 저울 없이 손으로 시험할 때 쓴다.
  if (slot == FOOD_SLOT + 1 && server.hasArg("g")) {
    int grams = argInt("g", 0);
    int steps = (int)(grams * FOOD_STEPS_PER_G + 0.5f);
    if (grams < 1 || steps > FOOD_STEPS_MAX) {
      sendError(400, "g must be 1-" + String((int)(FOOD_STEPS_MAX / FOOD_STEPS_PER_G)));
      return;
    }
    for (int s = 0; s < N_SLOTS; s++) pendingUnits[s] = 0;
    acceptJob();
    foodSteps = steps;          // acceptJob 이 0 으로 지우므로 그 뒤에 넣는다
    return;
  }

  int count = argInt("count", 1);
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
// 서버 — 제품 경로
//
//   GET  /v1/feeder/{dog}/commands        쌓인 급여 명령을 가져온다
//   POST /v1/feeder/commands/{id}/ack     실행 결과를 보고한다
//   POST /v1/feeder/{dog}/state           내 상태를 올린다 (앱이 읽는다)
//   POST /v1/ingest                       목줄 1분 요약 · 털기 이벤트
//
// **ack 를 안 보내면 서버가 같은 명령을 또 준다.** 그게 이중 급여가 되므로
// 실행이 끝나면 성공이든 실패든 반드시 보고한다.
//
// ArduinoJson 을 넣지 않는다. 받는 모양이 서버가 보내는 한 가지뿐이고,
// 아래 파서는 그 모양만 읽는다. 서버 응답이 바뀌면 여기도 바꿔야 한다.
// ---------------------------------------------------------------------------

const char* SERVER = PEBBLE_SERVER;
const char* TOKEN   = PEBBLE_TOKEN;
const char* DOG_ID  = PEBBLE_DOG;
const char* COLLAR_SERIAL = PEBBLE_COLLAR;

bool serverOn = false;            // secrets 에 주소·토큰이 있으면 켜진다
uint32_t bootId = 0;              // 부팅마다 증가. t_ms 의 기준점을 식별한다
unsigned long lastPoll = 0;
unsigned long lastUpload = 0;

/**
 * 서버에 한 번 다녀온다. 응답 본문을 out 에 담고 HTTP 코드를 돌려준다.
 *
 * TLS 를 매번 새로 맺으면 ESP32 에서 2~4초가 걸리고 힙도 많이 먹는다.
 * 클라이언트를 정적으로 두고 keep-alive 로 재사용한다.
 */
static int httpCall(const char* method, const String& path,
                    const String& body, String& out) {
  static WiFiClientSecure tls;
  static WiFiClient plain;
  static bool tlsReady = false;
  if (!tlsReady) {
    // 인증서를 검증하지 않는다. 루트 CA 번들을 플래시에 넣고 갱신까지
    // 관리할 단계가 아니다. **토큰이 평문으로 나가지는 않는다** - TLS 는
    // 맺으므로 도청은 막힌다. 막지 못하는 것은 가짜 서버로의 유도다.
    tls.setInsecure();
    tlsReady = true;
  }

  bool https = String(SERVER).startsWith("https:");
  HTTPClient http;
  http.setReuse(true);
  http.setConnectTimeout(8000);
  http.setTimeout(15000);

  bool ok = https ? http.begin(tls, SERVER + path) : http.begin(plain, SERVER + path);
  if (!ok) return -1;

  http.addHeader("Authorization", String("Bearer ") + TOKEN);
  if (body.length()) http.addHeader("Content-Type", "application/json");

  int code;
  if (String(method) == "GET") code = http.GET();
  else                         code = http.POST(body);

  out = (code > 0) ? http.getString() : String("");
  http.end();
  return code;
}

// --- 아주 작은 JSON 리더 ----------------------------------------------------
//
// 서버가 보내는 모양만 읽는다. 중첩 객체 하나, 배열 하나가 전부다.

/** "key": 뒤의 첫 글자 위치. 없으면 -1. */
static int jsonAfterKey(const String& s, const char* key, int from, int to) {
  String pat = String("\"") + key + "\"";
  int i = s.indexOf(pat, from);
  if (i < 0 || i >= to) return -1;
  i = s.indexOf(':', i + pat.length());
  if (i < 0 || i >= to) return -1;
  i++;
  while (i < to && (s[i] == ' ' || s[i] == '\t')) i++;
  return i;
}

static bool jsonNumber(const String& s, const char* key, int from, int to, float& out) {
  int i = jsonAfterKey(s, key, from, to);
  if (i < 0) return false;
  int j = i;
  while (j < to && (isDigit(s[j]) || s[j] == '-' || s[j] == '+' ||
                    s[j] == '.' || s[j] == 'e' || s[j] == 'E')) j++;
  if (j == i) return false;
  out = s.substring(i, j).toFloat();
  return true;
}

static bool jsonText(const String& s, const char* key, int from, int to, String& out) {
  int i = jsonAfterKey(s, key, from, to);
  if (i < 0 || i >= to || s[i] != '"') return false;
  int j = s.indexOf('"', i + 1);
  if (j < 0 || j > to) return false;
  out = s.substring(i + 1, j);
  return true;
}

/** from 위치의 여는 괄호에 대응하는 닫는 괄호. 없으면 -1. */
static int matchBracket(const String& s, int from, char open, char close) {
  int depth = 0;
  bool inStr = false;
  for (int i = from; i < (int)s.length(); i++) {
    char c = s[i];
    if (inStr) {
      if (c == '\\') i++;
      else if (c == '"') inStr = false;
      continue;
    }
    if (c == '"') inStr = true;
    else if (c == open) depth++;
    else if (c == close && --depth == 0) return i;
  }
  return -1;
}

// --- 명령 ------------------------------------------------------------------

static void ackCommand(const String& id, const char* state, const String& result) {
  String out;
  String path = "/v1/feeder/commands/" + id + "/ack?state=" + state;
  if (result.length()) path += "&result=" + result;
  int code = httpCall("POST", path, "{}", out);
  if (code == 200) Serial.printf("[서버] ack %s %s\n", id.c_str(), state);
  else             Serial.printf("[서버] ack 실패 (%d) — 다음 폴링에 또 옵니다\n", code);
}

/**
 * 명령 하나를 가져와 작업으로 세운다.
 *
 * **한 번에 하나만 받는다.** 모터는 한 번에 하나만 돌고, 여러 개를 받아두면
 * 중간에 꺼졌을 때 어디까지 했는지 서버와 어긋난다.
 */
static void pollCommands() {
  String body;
  int code = httpCall("GET", String("/v1/feeder/") + DOG_ID + "/commands", "", body);
  if (code != 200) {
    if (code != -1) Serial.printf("[서버] 명령 조회 실패 (%d)\n", code);
    return;
  }

  int arr = body.indexOf('[');
  if (arr < 0) return;
  int first = body.indexOf('{', arr);
  if (first < 0) return;
  int last = matchBracket(body, first, '{', '}');
  if (last < 0) return;

  String id;
  if (!jsonText(body, "id", first, last, id)) return;

  float foodG = 0;
  jsonNumber(body, "food_g", first, last, foodG);
  String source = "scheduled";
  jsonText(body, "source", first, last, source);

  // --- pellets ---
  int units[N_SLOTS] = {0};
  bool badSlot = false;
  int pel = jsonAfterKey(body, "pellets", first, last);
  if (pel >= 0 && body[pel] == '[') {
    int pelEnd = matchBracket(body, pel, '[', ']');
    int o = body.indexOf('{', pel);
    while (o > 0 && o < pelEnd) {
      int oEnd = matchBracket(body, o, '{', '}');
      if (oEnd < 0) break;
      float slot = 0, count = 0;
      jsonNumber(body, "slot", o, oEnd, slot);
      jsonNumber(body, "count", o, oEnd, count);
      int si = (int)slot;
      // **사료 슬롯으로 알을 보내면 거부한다.**
      //
      // 4번 자리에는 사료 모터가 달려 있다. 서버 설정(PEBBLE_SLOTS)이
      // 잘못되면 '장건강 2알' 명령이 올 수 있고, 그대로 실행하면
      // 영양제 대신 사료가 나간다. 조용히 틀리는 것보다 거부가 낫다.
      if (si == FOOD_SLOT + 1) { badSlot = true; break; }
      if (si >= 1 && si <= N_SLOTS && count > 0) {
        units[si - 1] = min((int)count, SLOTS[si - 1].maxUnits);
      }
      o = body.indexOf('{', oEnd);
    }
  }

  if (badSlot) {
    Serial.printf("[서버] %d번은 사료 자리입니다. 명령을 거부합니다\n", FOOD_SLOT + 1);
    ackCommand(id, "failed", "slot4-is-food");
    return;
  }

  int steps = (int)(foodG * FOOD_STEPS_PER_G + 0.5f);
  if (steps > FOOD_STEPS_MAX) {
    Serial.printf("[서버] 사료 %dg 은 너무 많습니다 (상한 %d펄스)\n",
                  (int)foodG, FOOD_STEPS_MAX);
    ackCommand(id, "failed", "food-too-much");
    return;
  }

  int total = steps;
  for (int s = 0; s < N_SLOTS; s++) total += units[s];
  if (total == 0) {
    ackCommand(id, "skipped", "empty");
    return;
  }

  for (int s = 0; s < N_SLOTS; s++) pendingUnits[s] = units[s];
  foodSteps = steps;
  jobCmdId = id;
  jobSource = source;
  hasJob = true;
  jobCounter++;
  Serial.printf("[서버] 명령 %s (%s) — 사료 %dg, 알 %d/%d/%d\n",
                id.c_str(), source.c_str(), (int)foodG,
                units[0], units[1], units[2]);
}

// --- 상태 보고 --------------------------------------------------------------

static void reportState() {
  String j = "{\"busy\":";
  j += busy ? "true" : "false";
  j += ",\"last_job\":\"" + jobSource + "\"";
  j += ",\"collar_connected\":";
  j += collarConnected ? "true" : "false";
  const char* ids[4] = {"rest", "walk", "run", "body_shake"};
  j += ",\"collar_class\":\"";
  j += (nowCls >= 0 && nowCls <= 3) ? ids[nowCls] : "";
  j += "\",\"collar_conf\":" + String(nowConf / 100.0f, 2);
  j += ",\"minute\":{\"rest_s\":" + String(minVals[0]);
  j += ",\"walk_s\":" + String(minVals[1]);
  j += ",\"run_s\":" + String(minVals[2]);
  j += ",\"shake_n\":" + String(minVals[3]) + "}}";

  String out;
  int code = httpCall("POST", String("/v1/feeder/") + DOG_ID + "/state", j, out);
  if (code != 200) Serial.printf("[서버] 상태 보고 실패 (%d)\n", code);
}

// --- 목줄 데이터 ------------------------------------------------------------

/** 지금 시각을 ISO 로. NTP 가 아직 안 맞았으면 빈 문자열. */
static String isoNow() {
  time_t t = time(nullptr);
  if (t < 1700000000) return "";      // 2023년 이전 = 아직 못 맞췄다
  struct tm tm;
  localtime_r(&t, &tm);
  char buf[32];
  strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%S", &tm);
  return String(buf);
}

/**
 * 모아둔 1분 요약과 털기를 서버에 올린다.
 *
 * 실패하면 버퍼를 비우지 않는다. 다시 보내도 서버가 (부팅ID, t_ms) 와
 * (부팅ID, 일련번호) 로 중복을 막으므로 두 배가 되지 않는다.
 *
 * **시계가 안 맞으면 보내지 않는다.** t_ms 를 실시각으로 환산하는 기준이
 * received_at 인데, 그게 1970년이면 데이터가 전부 1970년에 꽂힌다.
 */
static void uploadCollar() {
  if (nSamples == 0 && nShakes == 0) return;
  String now = isoNow();
  if (now.length() == 0) {
    Serial.println("[서버] 시계가 아직 안 맞았습니다 — 업로드 보류");
    return;
  }

  String j = "{\"collar_serial\":\"" + String(COLLAR_SERIAL) + "\"";
  j += ",\"boot_id\":" + String(bootId);
  j += ",\"uptime_ms\":" + String(millis());
  j += ",\"received_at\":\"" + now + "\"";
  j += ",\"fw_version\":\"esp32-dispenser-1\"";

  j += ",\"events\":[";
  for (int i = 0; i < nShakes; i++) {
    if (i) j += ",";
    // 일련번호는 버퍼 안의 자리가 아니라 **부팅 후 통산 번호**여야 한다.
    // 자리를 쓰면 버퍼를 비운 뒤 0 부터 다시 시작해 전부 중복으로 버려진다.
    uint32_t seq = shakeSeq - (uint32_t)(nShakes - i);
    j += "{\"seq\":" + String(seq);
    j += ",\"t_ms\":" + String(shakes[i].t_ms);
    j += ",\"type\":\"body_shake\"";
    j += ",\"conf\":" + String(shakes[i].conf / 100.0f, 2);
    // 판정 창이 1초다. 실제 지속시간은 모르므로 창 길이를 쓴다.
    j += ",\"dur_s\":1.0}";
  }
  j += "],\"status\":[";
  for (int i = 0; i < nSamples; i++) {
    if (i) j += ",";
    const MinuteSample& m = samples[i];
    int worn = m.rest_s + m.walk_s + m.run_s;
    if (worn > 60) worn = 60;
    j += "{\"t_ms\":" + String(m.t_ms);
    // **착용 감지가 아니다.** 목줄이 그 분에 판정을 낸 초를 센 것이다.
    // 책상에 놓여 있어도 '쉬기' 로 나오므로 착용률로 오해하면 안 된다.
    j += ",\"worn_sec\":" + String(worn);
    // 보수계가 없다. 활동은 walk_sec/run_sec 으로만 올라간다.
    j += ",\"steps\":0";
    j += ",\"rest_sec\":" + String(m.rest_s);
    j += ",\"walk_sec\":" + String(m.walk_s);
    j += ",\"run_sec\":" + String(m.run_s);
    // 모델에 '격렬' 클래스가 없다. 뛰기까지만 구분한다.
    j += ",\"vigorous_sec\":0}";
  }
  j += "]}";

  String out;
  int code = httpCall("POST", "/v1/ingest", j, out);
  if (code == 200) {
    Serial.printf("[서버] 목줄 업로드: 요약 %d개, 털기 %d개\n", nSamples, nShakes);
    nSamples = 0;
    nShakes = 0;
  } else {
    Serial.printf("[서버] 목줄 업로드 실패 (%d) — 모아뒀다 다시 보냅니다\n", code);
  }
}

// --- 한 바퀴 ---------------------------------------------------------------

static void serverCycle() {
  if (!serverOn || WiFi.status() != WL_CONNECTED) return;
  if (busy || hasJob) return;            // 배출 중에는 네트워크를 건드리지 않는다

  unsigned long now = millis();

  // 실행이 끝난 서버 명령부터 보고한다. 조회를 먼저 하면 같은 명령을
  // 또 받아서 두 번 나간다.
  if (jobCmdId.length()) {
    ackCommand(jobCmdId, lastStopped ? "failed" : "done",
               lastStopped ? "stopped" : "");
    jobCmdId = "";
  }

  if (now - lastPoll >= POLL_MS || lastPoll == 0) {
    lastPoll = now;
    reportState();
    pollCommands();
  }
  if (now - lastUpload >= UPLOAD_MS) {
    lastUpload = now;
    uploadCollar();
  }
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

  // 서버 경로. 주소나 토큰이 비어 있으면 끈다 — 직접 HTTP 만으로도 돈다.
  serverOn = strlen(SERVER) > 0 && strlen(TOKEN) > 0;
  if (serverOn) {
    // 부팅 번호. t_ms 의 기준점을 식별한다. 이게 매 부팅마다 바뀌지 않으면
    // 재부팅 뒤 t_ms 가 0 으로 돌아가면서 **전부 중복으로 버려진다.**
    Preferences nvs;
    nvs.begin("pebble", false);
    bootId = nvs.getUInt("boot", 0) + 1;
    nvs.putUInt("boot", bootId);
    nvs.end();

    // 시계. 목줄 데이터의 실시각 환산 기준이다.
    configTime(9 * 3600, 0, "pool.ntp.org", "time.google.com");

    Serial.printf("[서버] %s  개체 %s  목줄 %s  (부팅 #%u)\n",
                  SERVER, DOG_ID, COLLAR_SERIAL, bootId);
  } else {
    Serial.println("[서버] 주소나 토큰이 없어 서버 경로를 끕니다 (직접 HTTP 만)");
  }

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
  serverCycle();
}
