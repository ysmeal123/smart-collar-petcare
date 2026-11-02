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
 *   POST /unpair                          맺어둔 목줄을 잊는다
 *
 * 목줄 BLE 계약 (전부 리틀엔디안)
 *   now 10B  u8 클래스, u8 신뢰도, u32 t_ms, u32 부팅ID            1초마다
 *   min 20B  u32 부팅ID, u32 일련번호, u32 t_ms, u16×4 요약        쌓인 것 포함
 *   shk 13B  u32 부팅ID, u32 일련번호, u32 t_ms, u8 신뢰도
 *   ack  8B  u32 받은 min 일련번호, u32 받은 털기 일련번호          밥통 -> 목줄
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

//: 목줄에게서 받아 서버로 올리기까지 들고 있을 양.
//:
//: 넘치면 **받지 않는다** (버리지 않는다). 목줄이 ack 를 못 받으니 나중에
//: 다시 보낸다. 그래서 이 값은 '잃지 않을 양' 이 아니라 **'한 번에 얼마나
//: 빨리 비울 수 있나'** 를 정한다.
//:
//: 30이었을 때는 1분에 30개씩만 넘어와서, 목줄에 하루치(1440개)가 밀려
//: 있으면 비우는 데 48분이 걸렸다. 240이면 6분이다.
//: 메모리는 240×16 + 480×16 ≈ 11KB — ESP32 에 충분하다.
const int MAX_SAMPLES = 240;
const int MAX_EVENTS   = 480;

//: 버퍼가 이만큼 차면 60초를 기다리지 않고 바로 올린다.
//:
//: 밀린 것을 받는 중이라는 뜻이다. 그때 주기를 지키면 버퍼가 꽉 차서
//: 목줄의 전송이 막힌다.
const int UPLOAD_WHEN = MAX_SAMPLES / 2;

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
static NimBLEUUID UUID_SHK("19b10003-e8f2-537e-4f6c-d104768a1214");
static NimBLEUUID UUID_ACK("19b10004-e8f2-537e-4f6c-d104768a1214");
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

// 시각과 부팅번호는 **목줄이 준 것을 그대로 쓴다.**
//
// 밥통이 받은 시각을 쓰면 안 된다. 목줄이 끊긴 동안 모아둔 것을 몰아서
// 보내면 수백 건이 전부 '지금' 으로 찍히고, 그건 거짓이다.
struct MinuteSample {
  uint32_t bootId;
  uint32_t seq;          // 목줄이 매긴다. 유실을 알아보는 열쇠
  uint32_t t_ms;
  uint16_t rest_s, walk_s, run_s, shake_n;
};

struct ShakeEvent {
  uint32_t bootId;
  uint32_t seq;          // 목줄이 매긴다. 서버의 중복 제거 키다
  uint32_t t_ms;
  uint8_t  conf;         // 0~100
};

MinuteSample samples[MAX_SAMPLES];
int nSamples = 0;
ShakeEvent shakes[MAX_EVENTS];
int nShakes = 0;

/**
 * 뒤에 붙인다. **꽉 차면 받지 않는다** (버리지 않는다).
 *
 * 전에는 오래된 것부터 버렸다. 이제는 목줄이 ack 를 받을 때까지 들고
 * 있으므로, 여기서 안 받으면 목줄이 나중에 다시 보낸다. 버리는 쪽은
 * 데이터가 사라지고, 안 받는 쪽은 늦어질 뿐이다.
 */
static bool pushSample(const MinuteSample& s) {
  if (nSamples >= MAX_SAMPLES) return false;
  samples[nSamples++] = s;
  return true;
}

static bool pushShake(const ShakeEvent& e) {
  if (nShakes >= MAX_EVENTS) return false;
  shakes[nShakes++] = e;
  return true;
}

// 목줄의 지금 시각. ingest 의 uptime_ms 로 쓴다 - 서버가 이것과
// received_at 으로 t_ms 를 실시각으로 환산한다.
volatile uint32_t collarBootId = 0;
volatile uint32_t collarTms = 0;
volatile unsigned long collarTmsAt = 0;

static uint32_t collarNowMs() {
  if (collarTmsAt == 0) return 0;
  return collarTms + (uint32_t)(millis() - collarTmsAt);
}

// 서버가 받아준 지점. 목줄에게 "여기까지 됐다" 고 알려줄 값이다.
//
// **어느 부팅의 값인지 같이 들고 있어야 한다.** 목줄이 재부팅하면 t_ms 가
// 0 부터 다시 시작한다. 이전 부팅의 기준(예: 900000)을 그대로 두면 새 기록
// (예: 60000)이 전부 '이미 받은 것' 으로 걸러진다. 조용히 버려지므로
// 로그에도 안 남는다 - 실제로 그래서 재연결 뒤 아무것도 안 올라갔다.
uint32_t ackBootId = 0;
uint32_t ackMinSeq = 0;
uint32_t ackShakeSeq = 0;
bool ackPending = false;
NimBLERemoteCharacteristic* chAckRemote = nullptr;

//: 재부팅을 알아챈 사실만 남긴다. 출력은 loop() 에서 한다 - 이 함수는
//: BLE 콜백에서 불리고, 그건 NimBLE 태스크다.
volatile uint32_t rebootFrom = 0;
volatile bool rebootNoticed = false;

/** 목줄이 다른 부팅이 되었으면 기준을 지운다. */
static void syncAckBoot(uint32_t boot) {
  if (boot == 0 || boot == ackBootId) return;
  if (ackBootId != 0) {
    rebootFrom = ackBootId;
    rebootNoticed = true;
  }
  ackBootId = boot;
  ackMinSeq = 0;
  ackShakeSeq = 0;
  ackPending = false;
}

// --- 짝 맺기 ---------------------------------------------------------------
//
// 처음 붙은 목줄의 주소를 NVS 에 적어두고, 그 뒤로는 **그 기기에만** 붙는다.
//
// 그러지 않으면 `pebble-collar` 로 광고하는 아무 기기에나 붙는다. 발표장에
// 다른 팀 목줄이 있으면 엉뚱한 개의 행동이 우리 개체로 올라간다. 조용히
// 틀리는 종류의 사고다.
//
// BLE 본딩(암호화 페어링)까지 가지 않았다. 지금 막아야 하는 것은 '엉뚱한
// 기기에 붙는 것' 이고 주소를 못 박으면 그건 해결된다. 본딩은 도청과
// 위장을 막는 것이라 제품에서 할 일이다 - 목줄 데이터는 비밀이 아니고,
// 위장으로 얻을 것도 없다.
String pairedAddr = "";          // 비어 있으면 아직 안 맺음

static void savePair(const String& addr) {
  Preferences nvs;
  nvs.begin("pebble", false);
  nvs.putString("collar", addr);
  nvs.end();
  pairedAddr = addr;
  Serial.printf("[짝] 목줄 %s 로 맺었습니다 (앞으로 이 기기에만 붙습니다)\n",
                addr.c_str());
}

static void loadPair() {
  Preferences nvs;
  nvs.begin("pebble", true);
  pairedAddr = nvs.getString("collar", "");
  nvs.end();
  if (pairedAddr.length()) {
    Serial.printf("[짝] 맺어둔 목줄 %s\n", pairedAddr.c_str());
  } else {
    Serial.println("[짝] 아직 없습니다 — 처음 찾은 목줄과 맺습니다");
  }
}

class CollarScan : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice* d) override {
    if (collarFound) return;
    if (!(d->isAdvertisingService(UUID_SVC) || d->getName() == COLLAR_NAME)) return;

    String addr = d->getAddress().toString().c_str();
    // 맺어둔 기기가 있으면 그것만 받는다.
    if (pairedAddr.length() && pairedAddr != addr) return;

    collarAddr = d->getAddress();
    collarFound = true;
    NimBLEDevice::getScan()->stop();
  }
} collarScan;

class CollarClientCb : public NimBLEClientCallbacks {
  void onDisconnect(NimBLEClient* c, int reason) override {
    collarConnected = false;
    collarLost = true;
  }
} collarClientCb;

static inline uint32_t rd32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline uint16_t rd16(const uint8_t* p) {
  return (uint16_t)(p[0] | (p[1] << 8));
}

static void onNow(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
  if (len < 10 || data[0] > 3) return;
  nowCls = data[0];
  nowConf = data[1];
  nowAt = millis();
  nowFresh = true;
  // 목줄의 시계. 이걸 알아야 모아둔 t_ms 를 실시각으로 환산할 수 있다.
  collarTms = rd32(data + 2);
  collarBootId = rd32(data + 6);
  collarTmsAt = millis();
  // 1초 알림이 1분 요약보다 먼저 온다. 여기서 알아채면 요약이 걸러지지 않는다.
  syncAckBoot(collarBootId);
}

/**
 * 1분 요약. **지금 것일 수도, 끊겼을 때 모아둔 것일 수도 있다.**
 *
 * 둘을 구분하지 않는다. 목줄이 실어 보낸 t_ms 를 그대로 쓰면 둘 다
 * 제 시각에 꽂힌다. 화면에 띄울 '최근 1분' 만 따로 갱신한다.
 */
static void onMin(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
  if (len < 20) return;
  MinuteSample s;
  s.bootId  = rd32(data);
  s.seq     = rd32(data + 4);
  s.t_ms    = rd32(data + 8);
  s.rest_s  = rd16(data + 12);
  s.walk_s  = rd16(data + 14);
  s.run_s   = rd16(data + 16);
  s.shake_n = rd16(data + 18);

  // **이미 서버에 넣은 것은 받지 않는다.**
  //
  // 목줄은 ack 를 못 받으면 다시 보낸다. 그걸 그대로 쌓으면 버퍼가
  // 사본으로 차서 새 기록이 들어갈 자리가 없어진다. 서버가 중복을
  // 막아주긴 하지만, 여기서 거르면 올릴 일 자체가 없다.
  syncAckBoot(s.bootId);
  if (s.bootId == ackBootId && s.seq <= ackMinSeq) return;
  for (int i = 0; i < nSamples; i++) {
    if (samples[i].bootId == s.bootId && samples[i].seq == s.seq) return;
  }
  if (!pushSample(s)) return;        // 자리가 없으면 안 받는다. 목줄이 다시 보낸다

  // 화면에 띄울 '최근 1분' 은 **가장 최근 것만** 쓴다. 밀린 것을 받는
  // 동안 옛 기록으로 덮으면 앱이 몇 시간 전 값을 지금이라고 보여준다.
  static uint32_t shownT = 0;
  if (s.t_ms >= shownT) {
    shownT = s.t_ms;
    minVals[0] = s.rest_s; minVals[1] = s.walk_s;
    minVals[2] = s.run_s;  minVals[3] = s.shake_n;
    minAt = millis();
    minFresh = true;
  }
}

/**
 * 털기 하나. **목줄이 시각과 일련번호를 매겨 보낸다.**
 *
 * 전에는 밥통이 1초 알림의 전이에서 잡았다. 그러면 BLE 가 끊긴 동안의
 * 털기는 아예 못 본다. 긁기는 피부축의 핵심 지표라서 빠지면 처방 근거가
 * 통째로 약해진다.
 */
static void onShake(NimBLERemoteCharacteristic*, uint8_t* data, size_t len, bool) {
  if (len < 13) return;
  ShakeEvent e;
  e.bootId = rd32(data);
  e.seq    = rd32(data + 4);
  e.t_ms   = rd32(data + 8);
  e.conf   = data[12];

  // 재전송된 것은 받지 않는다 (onMin 과 같은 이유).
  syncAckBoot(e.bootId);
  if (e.bootId == ackBootId && e.seq <= ackShakeSeq) return;
  for (int i = 0; i < nShakes; i++) {
    if (shakes[i].bootId == e.bootId && shakes[i].seq == e.seq) return;
  }
  pushShake(e);
}

static void startCollarScan() {
  collarFound = false;
  NimBLEScan* scan = NimBLEDevice::getScan();
  scan->setScanCallbacks(&collarScan, false);
  scan->setActiveScan(true);          // 이름은 스캔 응답에 실려 온다
  scan->setInterval(100);
  scan->setWindow(50);                // Wi-Fi 와 안테나를 나눠 쓰므로 절반만 듣는다

  // **발견한 기기를 저장하지 않는다.** 콜백으로만 받는다.
  //
  // 기본값은 255개까지 쌓는다. 끝날 때까지 스캔하는 우리 방식에서는
  // 주변 기기가 계속 누적돼 힙이 바닥난다. 실제로 목줄을 들고 2분 나가
  // 있는 동안 터졌다 - NimBLEScan 의 new 가 실패하고 abort() 가 났다.
  // 우리는 목줄 하나를 찾으면 끝이라 저장할 이유가 없다.
  scan->setMaxResults(0);
  scan->clearResults();

  scan->start(0, false, true);        // 찾을 때까지 계속
  Serial.printf("[BLE] 목줄 찾는 중…  (빈 메모리 %u바이트)\n", ESP.getFreeHeap());
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
  NimBLERemoteCharacteristic* chShk = svc ? svc->getCharacteristic(UUID_SHK) : nullptr;
  chAckRemote = svc ? svc->getCharacteristic(UUID_ACK) : nullptr;
  if (!chNow || !chNow->subscribe(true, onNow) || (chMin && !chMin->subscribe(true, onMin))) {
    Serial.println("[BLE] 목줄 서비스를 못 찾음 — 끊고 다시 찾습니다");
    collarClient->disconnect();
    return;                           // onDisconnect 가 다시 스캔하게 만든다
  }
  // 털기와 ack 가 없으면 **반쪽만 동작한다** - 목줄이 모아둔 것을 비울 수
  // 없어서 영원히 재전송한다. 몇 번은 다시 붙어본다.
  //
  // 실제로 크래시 뒤 재연결에서 이 둘만 못 찾은 적이 있다. 목줄 쪽에
  // 끊기지 않은 연결이 남아 탐색이 반쪽만 된 것으로 보인다. 다시 붙으면
  // 대개 해결된다.
  static int halfTries = 0;
  if (!chShk || !chAckRemote) {
    if (++halfTries <= 3) {
      Serial.printf("[BLE] 특성을 반만 찾았습니다 (%d/3) — 끊고 다시 붙습니다\n",
                    halfTries);
      collarClient->disconnect();
      return;
    }
    Serial.println("[BLE] 털기·ack 특성을 못 찾습니다 — 구버전 목줄 펌웨어입니다");
    Serial.println("      1분 요약은 받지만 모아둔 것은 비울 수 없습니다");
  } else {
    halfTries = 0;
  }
  if (chShk) chShk->subscribe(true, onShake);
  collarConnected = true;
  // **여기서 맺는다.** 특성까지 다 확인된 뒤여야 한다. 광고만 보고 맺으면
  // 이름만 흉내낸 기기에 못 박혀서 진짜 목줄을 못 찾게 된다.
  if (pairedAddr.length() == 0) savePair(collarAddr.toString().c_str());
  // 다시 붙었으면 밀린 것부터 온다. ack 는 서버에 올라간 뒤에 보낸다.
  ackPending = (ackMinSeq || ackShakeSeq);
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
  if (rebootNoticed) {
    rebootNoticed = false;
    Serial.printf("[짝] 목줄이 재부팅했습니다 (%lu → %lu) — 받은 기준을 지웁니다\n",
                  (unsigned long)rebootFrom, (unsigned long)ackBootId);
  }

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
    // 털기는 여기서 세지 않는다. 목줄이 시각과 함께 따로 보낸다.
    Serial.printf("[목줄 1분] 쉬기 %u초 걷기 %u초 뛰기 %u초 털기 %u회  (버퍼 %d/%d)\n",
                  minVals[0], minVals[1], minVals[2], minVals[3],
                  nSamples, nShakes);
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
  j += ",\"paired\":\"" + pairedAddr + "\"";
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

/**
 * 맺어둔 목줄을 잊는다. 목줄을 바꾸거나 다른 개로 옮길 때 쓴다.
 *
 * 못 박기만 해두고 푸는 길을 안 만들면, 목줄이 고장난 순간 밥통을
 * 플래시까지 지워야 한다.
 */
static void handleUnpair() {
  Preferences nvs;
  nvs.begin("pebble", false);
  nvs.remove("collar");
  nvs.end();
  pairedAddr = "";
  Serial.println("[짝] 잊었습니다 — 다음에 찾은 목줄과 새로 맺습니다");
  if (collarConnected && collarClient) collarClient->disconnect();
  sendJson(200, "{\"ok\":true,\"paired\":\"\"}");
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
/**
 * `from` 다음부터 **끊김 없이 이어진** 마지막 번호를 돌려준다.
 *
 * 올린 번호가 {5,6,8} 이고 from 이 4면 6을 돌려준다. 7이 빠졌으니 8까지
 * 받았다고 말할 수 없다. 그 하나를 잘못 말하면 목줄이 7을 버린다.
 */
static uint32_t contiguousTo(uint32_t from, uint32_t* seqs, int n) {
  // 작은 배열이라 삽입정렬로 충분하다 (최대 수백 개).
  for (int i = 1; i < n; i++) {
    uint32_t v = seqs[i];
    int j = i - 1;
    while (j >= 0 && seqs[j] > v) { seqs[j + 1] = seqs[j]; j--; }
    seqs[j + 1] = v;
  }
  uint32_t cur = from;
  for (int i = 0; i < n; i++) {
    if (seqs[i] <= cur) continue;          // 이미 지난 번호
    if (seqs[i] == cur + 1) cur = seqs[i];
    else break;                            // 구멍. 여기서 멈춘다
  }
  return cur;
}

static void uploadCollar() {
  if (nSamples == 0 && nShakes == 0) return;
  String now = isoNow();
  if (now.length() == 0) {
    Serial.println("[서버] 시계가 아직 안 맞았습니다 — 업로드 보류");
    return;
  }

  // **한 묶음은 부팅번호 하나다.** t_ms 의 기준점이 부팅마다 다르므로
  // 섞어 보내면 환산이 틀어진다. 목줄이 재부팅했으면 두 번에 나눠 보낸다.
  uint32_t boot = nSamples ? samples[0].bootId : shakes[0].bootId;
  if (boot == 0) {
    Serial.println("[서버] 목줄 부팅번호를 아직 모릅니다 — 보류");
    return;
  }

  // 이 묶음의 uptime_ms 를 **먼저** 구한다. 목줄 시계를 모르면 가장 늦은
  // t_ms 로 대신한다 - 그보다 과거일 수는 없다.
  uint32_t maxT = 0;
  for (int i = 0; i < nSamples; i++)
    if (samples[i].bootId == boot && samples[i].t_ms > maxT) maxT = samples[i].t_ms;
  for (int i = 0; i < nShakes; i++)
    if (shakes[i].bootId == boot && shakes[i].t_ms > maxT) maxT = shakes[i].t_ms;
  uint32_t up = collarNowMs();
  if (up < maxT) up = maxT;

  int nS = 0, nE = 0;
  // 이번에 올린 번호들. ack 를 어디까지 할지 정하는 데 쓴다.
  static uint32_t upMinSeqs[MAX_SAMPLES];
  static uint32_t upShakeSeqs[MAX_EVENTS];
  int nUpMin = 0, nUpShake = 0;

  String j = "{\"collar_serial\":\"" + String(COLLAR_SERIAL) + "\"";
  j += ",\"boot_id\":" + String(boot);
  j += ",\"uptime_ms\":" + String(up);
  j += ",\"received_at\":\"" + now + "\"";
  j += ",\"fw_version\":\"esp32-dispenser-1\"";

  j += ",\"events\":[";
  for (int i = 0; i < nShakes; i++) {
    if (shakes[i].bootId != boot) continue;
    if (nE++) j += ",";
    // 일련번호는 목줄이 매긴다. 밥통이 세면 버퍼를 비운 뒤 0 으로 돌아가
    // 전부 중복으로 버려진다.
    j += "{\"seq\":" + String(shakes[i].seq);
    j += ",\"t_ms\":" + String(shakes[i].t_ms);
    j += ",\"type\":\"body_shake\"";
    j += ",\"conf\":" + String(shakes[i].conf / 100.0f, 2);
    // 판정 창이 1초다. 실제 지속시간은 모르므로 창 길이를 쓴다.
    j += ",\"dur_s\":1.0}";
    upShakeSeqs[nUpShake++] = shakes[i].seq;
  }
  j += "],\"status\":[";
  for (int i = 0; i < nSamples; i++) {
    const MinuteSample& m = samples[i];
    if (m.bootId != boot) continue;
    if (nS++) j += ",";

    // **합이 60을 넘으면 깎는다.** 목줄도 깎아서 보내지만, 구버전 펌웨어가
    // 61을 보내면 서버가 묶음 전체를 422 로 거부한다. 한 표본 때문에
    // 나머지가 다 막히는 건 손해가 너무 크다.
    int r = m.rest_s, w = m.walk_s, ru = m.run_s;
    while (r + w + ru > 60) {
      if (r >= w && r >= ru)      r--;
      else if (w >= ru)           w--;
      else                        ru--;
    }
    int worn = r + w + ru;
    j += "{\"t_ms\":" + String(m.t_ms);
    // **착용 감지가 아니다.** 목줄이 그 분에 판정을 낸 초를 센 것이다.
    // 책상에 놓여 있어도 '쉬기' 로 나오므로 착용률로 오해하면 안 된다.
    j += ",\"worn_sec\":" + String(worn);
    // 보수계가 없다. 활동은 walk_sec/run_sec 으로만 올라간다.
    j += ",\"steps\":0";
    j += ",\"rest_sec\":" + String(r);
    j += ",\"walk_sec\":" + String(w);
    j += ",\"run_sec\":" + String(ru);
    // 모델에 '격렬' 클래스가 없다. 뛰기까지만 구분한다.
    j += ",\"vigorous_sec\":0}";
    upMinSeqs[nUpMin++] = m.seq;
  }
  j += "]}";

  String out;
  int code = httpCall("POST", "/v1/ingest", j, out);
  if (code != 200) {
    Serial.printf("[서버] 목줄 업로드 실패 (%d) — 모아뒀다 다시 보냅니다\n", code);
    return;
  }

  // 올라간 것만 버린다. 다른 부팅번호의 기록은 다음 차례에 간다.
  int keepS = 0;
  for (int i = 0; i < nSamples; i++)
    if (samples[i].bootId != boot) samples[keepS++] = samples[i];
  nSamples = keepS;
  int keepE = 0;
  for (int i = 0; i < nShakes; i++)
    if (shakes[i].bootId != boot) shakes[keepE++] = shakes[i];
  nShakes = keepE;

  // **서버가 센 숫자를 같이 찍는다.** 200 만 보고 넘어가면 '보냈다' 와
  // '들어갔다' 를 구분할 수 없다. 중복이 걸러진 개수도 여기서 드러난다.
  int acc = -1, dup = -1, st = -1;
  {
    float v;
    if (jsonNumber(out, "accepted", 0, out.length(), v))       acc = (int)v;
    if (jsonNumber(out, "duplicates", 0, out.length(), v))     dup = (int)v;
    if (jsonNumber(out, "status_samples", 0, out.length(), v)) st  = (int)v;
  }
  Serial.printf("[서버] 목줄 업로드: 보냄 요약 %d·털기 %d  →  "
                "서버 저장 요약 %d·털기 %d (중복 %d)  남음 %d/%d\n",
                nS, nE, st, acc, dup, nSamples, nShakes);

  // **목줄에게 알려준다.** 서버에 들어간 뒤에만 보낸다 - 밥통이 받기만
  // 하고 알려주면, 밥통이 재부팅할 때 그 사이 기록이 사라진다.
  //
  // 그리고 **끊김 없이 이어진 구간까지만** 알려준다.
  //
  // 알림은 유실될 수 있다. 1,3 을 받았는데 "3까지 받았다" 고 하면 목줄이
  // 2까지 버려서 영구히 사라진다. 번호가 이어진 데까지만 ack 하면, 목줄은
  // 2부터 다시 보낸다 - 늦어질 뿐 잃지 않는다.
  if (boot == ackBootId) {
    ackMinSeq   = contiguousTo(ackMinSeq, upMinSeqs, nUpMin);
    ackShakeSeq = contiguousTo(ackShakeSeq, upShakeSeqs, nUpShake);
    ackPending = true;
  }
}


/** 목줄에게 "여기까지 서버에 들어갔다" 고 써 준다. */
static void sendAck() {
  if (!ackPending || !collarConnected || chAckRemote == nullptr) return;
  uint8_t buf[8];
  buf[0] = ackMinSeq;       buf[1] = ackMinSeq >> 8;
  buf[2] = ackMinSeq >> 16; buf[3] = ackMinSeq >> 24;
  buf[4] = ackShakeSeq;       buf[5] = ackShakeSeq >> 8;
  buf[6] = ackShakeSeq >> 16; buf[7] = ackShakeSeq >> 24;
  // **응답 있는 쓰기로 보낸다.**
  //
  // 처음에 false(응답 없는 쓰기)로 보냈더니 목줄이 조용히 무시했다. 목줄의
  // 특성은 BLEWrite 하나뿐이고 그건 '응답 있는 쓰기' 만 받는다. 게다가
  // 응답 없는 쓰기는 성공 여부를 안 돌려주니 밥통은 잘 된 줄 알았다 -
  // 목줄 버퍼는 계속 늘어나는데 밥통 로그는 '남음 0/0' 이었다.
  if (chAckRemote->writeValue(buf, 8, true)) {
    ackPending = false;
    Serial.printf("[ack] 목줄에 알림: 요약 %lu번, 털기 %lu번까지\n",
                  (unsigned long)ackMinSeq, (unsigned long)ackShakeSeq);
  } else {
    Serial.println("[ack] 목줄에 못 썼습니다 — 다음에 다시 시도합니다");
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
  // 주기가 되었거나, 밀린 것이 몰려와 버퍼가 반쯤 찼으면 올린다.
  if (now - lastUpload >= UPLOAD_MS ||
      nSamples >= UPLOAD_WHEN || nShakes >= MAX_EVENTS / 2) {
    lastUpload = now;
    uploadCollar();
  }
  // 목줄은 ack 를 받을 때까지 들고 있다. 안 보내주면 영원히 다시 보낸다.
  sendAck();
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
  server.on("/unpair", HTTP_POST, handleUnpair);
  server.onNotFound([]() { sendError(404, "not found"); });
  server.begin();
  Serial.println("[HTTP] 80번 포트 대기");

  loadPair();
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
