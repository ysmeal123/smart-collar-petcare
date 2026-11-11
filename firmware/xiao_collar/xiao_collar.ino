/*
 * pebble 목줄 — XIAO nRF52840 Sense 행동 판정 (시험용)
 *
 *   IMU 100Hz → |a|(g)·|g|(dps) → 2초 창(200샘플) → 1D CNN(TFLite int8) → 1초마다 판정
 *
 * 모델: activity_cnn5.h (팀원이 학습. 5클래스 REST/WALK/RUN/VIGOROUS/SHAKE)
 *       원본 예제 activity_cnn5_live.ino 의 전처리·정규화를 그대로 옮겼다.
 * 우리 클래스: 0 쉬기 / 1 걷기 / 2 뛰기 / 3 털기
 *       **VIGOROUS(격한 활동)는 쓰지 않는다** — 나머지 넷 중에서 고르고 확률을 다시 나눈다.
 *
 * **전처리는 학습 때와 같아야 한다** (MU/SD, 100Hz, 2초 창, 자이로 바이어스).
 * 켤 때 2초 가만히 둔다 (자이로 바이어스 측정, 초록 깜빡임).
 *
 * 라이브러리 추가: Arduino_TensorFlowLite (github.com/tensorflow/tflite-micro-arduino-examples)
 *   이 보드는 공식 지원 목록에 없어서 두 곳을 고쳐야 컴파일된다 (firmware/README 참고)
 *     src/peripherals/peripherals.h      NANO33BLE 조건에 SEEED_XIAO_NRF52840_SENSE 추가, D13 → LED_BUILTIN
 *     src/tensorflow/lite/micro/system_setup.cpp  같은 조건에 XIAO 추가
 *
 * 출력
 *   시리얼(115200)  "걷기  0.87  | 쉬기 0.05 걷기 0.87 뛰기 0.08 털기 0.00"
 *   RGB LED         쉬기 파랑 / 걷기 초록 / 뛰기 빨강 / 털기 흰색
 *   BLE  이름 "pebble-collar"   (전부 리틀엔디안)
 *     서비스        19b10000-e8f2-537e-4f6c-d104768a1214
 *     now  (notify) 19b10001-...  10B  u8 클래스, u8 신뢰도(0~100), u32 t_ms, u32 부팅ID
 *     min  (notify) 19b10002-...  24B  u32 부팅ID, u32 일련번호, u32 가장오래된번호,
 *                                      u32 t_ms, u16 쉬기초/걷기초/뛰기초/털기수
 *     shk  (notify) 19b10003-...  17B  u32 부팅ID, u32 일련번호, u32 가장오래된번호,
 *                                      u32 t_ms, u8 신뢰도
 *     ack  (write)  19b10004-...   8B  u32 받은 min 일련번호, u32 받은 털기 일련번호
 *
 * **일련번호가 둘 다 있어야 한다.** 알림은 유실될 수 있다 - 연결 간격보다
 * 빠르게 쓰면 전송 전에 다음 값이 덮는다. 번호가 없으면 밥통이 "1,3 을
 * 받았다" 와 "1,2,3 을 받았다" 를 구분할 수 없고, 3까지 받았다고 ack 하면
 * 유실된 2가 영구히 사라진다. 그래서 밥통은 **끊김 없이 이어진 구간까지만**
 * ack 하고, 목줄은 그 뒤부터 다시 보낸다.
 *
 * **끊긴 동안 모아둔다.** BLE 가 끊기면 1분 요약과 털기를 RAM 에 쌓고,
 * 다시 붙으면 밀린 것부터 쏟아낸다. 개는 돌아다니고 밥통은 부엌에 고정돼
 * 있으니 하루 중 상당 시간이 끊긴 상태다. 그 동안의 활동량과 긁기가
 * 사라지면 처방 근거가 통째로 약해진다.
 *
 * 그래서 기록마다 **목줄 자신의 시각(t_ms)** 을 싣는다. 밥통이 받은
 * 시각을 쓰면 모아둔 것을 보낼 때 전부 거짓이 된다.
 *
 * 밥통이 서버에 올리고 ack 를 써 주면 그때 버린다. ack 가 안 오면 다시
 * 보낸다 - 서버가 (목줄, 부팅ID, t_ms) 와 (목줄, 부팅ID, 일련번호) 로
 * 중복을 막으므로 **두 번 보내도 두 배가 되지 않는다.** 완벽한 전달을
 * 맞추는 것보다 중복을 허용하는 쪽이 훨씬 단순하다.
 *
 * 1초 판정(now)은 모아두지 않는다. 그건 '지금 뭐 하고 있나' 를 화면에
 * 띄우는 값이고, 지나간 1초는 가치가 없다.
 *
 * 보드: Tools → Board → Seeed nRF52 mbed-enabled Boards → "XIAO nRF52840 Sense"
 *       (FQBN Seeeduino:mbed:xiaonRF52840Sense)
 * 라이브러리: Seeed Arduino LSM6DS3, ArduinoBLE
 */

#include <ArduinoBLE.h>
#include <LSM6DS3.h>
#include <Wire.h>
#include <math.h>

#include <TensorFlowLite.h>
#include "tensorflow/lite/micro/micro_mutable_op_resolver.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/schema/schema_generated.h"

#include "activity_cnn5.h"

// ---------------------------------------------------------------------------

const int N_CLASSES = 4;
const char* NAMES_KO[N_CLASSES] = {"쉬기", "걷기", "뛰기", "털기"};

const int WIN = 200;                 // 100Hz × 2초
//: 0.5초마다 판정. 창은 학습 때와 같은 2초라 모델에는 영향이 없고,
//: 털기가 화면에 뜨기까지가 최대 0.5초 줄어든다 (추론은 약 4ms).
const int HOP = 50;
const unsigned long RAW_PERIOD_US = 10000;   // 100Hz

LSM6DS3 imu(I2C_MODE, 0x6A);

// 100Hz 링버퍼: 가속도 크기 |a| [g], 자이로 크기 |g| [dps]
float abuf[WIN], gbuf[WIN];
int head = 0;          // 다음에 쓸 자리
int filled = 0;
int sinceLast = 0;
float gbias[3] = {0, 0, 0};

// --- 모델 (activity_cnn5_live.ino 와 같은 값) ---
const float MU[2] = {1.2197f, 81.993f};      // 학습 때 정규화 (modelA5_final/params.json)
const float SD[2] = {1.1606f, 201.3286f};
const int N_MODEL = 5;                        // REST WALK RUN VIGOROUS SHAKE
//: 모델 출력 → 우리 클래스. -1 = 버린다 (VIGOROUS)
const int MODEL_TO_OURS[N_MODEL] = {0, 1, 2, -1, 3};

constexpr int kArenaSize = 40 * 1024;
alignas(16) uint8_t tensorArena[kArenaSize];
tflite::MicroInterpreter* interp = nullptr;
TfLiteTensor* tin = nullptr;
TfLiteTensor* tout = nullptr;

// 1분 요약
uint16_t minSec[3] = {0, 0, 0};      // 쉬기·걷기·뛰기 **반초** (판정 1회 = 0.5초)
uint16_t minShake = 0;
int lastClass = -1;
unsigned long minuteStart = 0;

// ---------------------------------------------------------------------------
// 끊긴 동안 모아둘 자리
//
// nRF52840 은 RAM 이 256KB 다. 아래 둘이 24KB 남짓이니 **하루치가 들어간다.**
// 플래시까지 쓰면 더 버틸 수 있지만, 배터리가 끊기면 어차피 판정도 멈춘다.
// 거기까지 가는 건 다음 단계다.
// ---------------------------------------------------------------------------

struct MinRec {
  uint32_t seq;                      // 부팅 후 통산 번호. 유실을 알아보는 열쇠
  uint32_t t_ms;                     // 이 1분이 끝난 시점 (목줄 uptime)
  uint16_t rest, walk, run, shake;
};

struct ShakeRec {
  uint32_t seq;                      // 부팅 후 통산 번호. 서버의 중복 제거 키
  uint32_t t_ms;
  uint8_t  conf;                     // 0~100
};

const int MIN_CAP   = 1440;          // 24시간
const int SHAKE_CAP = 600;

MinRec   minBuf[MIN_CAP];
ShakeRec shakeBuf[SHAKE_CAP];
int minCount = 0, shakeCount = 0;    // 앞에서부터 쌓여 있다 (오래된 것이 0번)
int minSent  = 0, shakeSent  = 0;    // 여기까지 보냈다. ack 오면 앞을 버린다
uint32_t minSeq = 0;
uint32_t shakeSeq = 0;

/**
 * 목줄의 부팅 번호. **t_ms 의 기준점을 식별한다.**
 *
 * 재부팅하면 t_ms 가 0 으로 돌아간다. 부팅 번호가 같으면 서버가 어제
 * 10시와 오늘 10시를 같은 표본으로 보고 전부 중복으로 버린다.
 *
 * 난수를 쓴다. 증가하는 번호가 필요하면 플래시에 적고 읽어야 하는데,
 * 서버는 이 값을 **구분**에만 쓰고 순서로 쓰지 않는다. 충돌 확률은
 * 무시할 만하다.
 *
 * **21억(2^31-1)을 넘지 않는다.** 서버 DB 의 boot_id 는 32비트 부호있는
 * 정수다. 처음에 32비트 전체를 썼더니 절반의 확률로 그 범위를 넘어
 * 업로드가 500 으로 떨어졌다. 로컬 SQLite 는 64비트라 안 걸리고
 * 배포(PostgreSQL)에서만 터져서 찾기 어려웠다.
 */
uint32_t bootId = 0;

/** 앞에서 n개를 버린다. 보낸 표시도 같이 당긴다. */
static void dropMin(int n) {
  if (n <= 0) return;
  if (n > minCount) n = minCount;
  for (int i = n; i < minCount; i++) minBuf[i - n] = minBuf[i];
  minCount -= n;
  minSent = max(0, minSent - n);
}

static void dropShake(int n) {
  if (n <= 0) return;
  if (n > shakeCount) n = shakeCount;
  for (int i = n; i < shakeCount; i++) shakeBuf[i - n] = shakeBuf[i];
  shakeCount -= n;
  shakeSent = max(0, shakeSent - n);
}

BLEService svc("19b10000-e8f2-537e-4f6c-d104768a1214");
BLECharacteristic chNow("19b10001-e8f2-537e-4f6c-d104768a1214", BLERead | BLENotify, 10);
BLECharacteristic chMin("19b10002-e8f2-537e-4f6c-d104768a1214", BLERead | BLENotify, 24);
BLECharacteristic chShake("19b10003-e8f2-537e-4f6c-d104768a1214", BLERead | BLENotify, 17);
BLECharacteristic chAck("19b10004-e8f2-537e-4f6c-d104768a1214", BLEWrite, 8);
bool bleOk = false;

// --- 바이트로 싣는다 -------------------------------------------------------
//
// 구조체를 그대로 보내지 않는다. 컴파일러가 끼워넣는 패딩이 보드마다
// 달라서, 받는 쪽(ESP32)과 어긋나면 숫자가 엉뚱하게 읽힌다.

static inline void put32(uint8_t* p, uint32_t v) {
  p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24;
}
static inline void put16(uint8_t* p, uint16_t v) {
  p[0] = v; p[1] = v >> 8;
}
static inline uint32_t get32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

// ---------------------------------------------------------------------------
// 모델 실행
// ---------------------------------------------------------------------------

/** 링버퍼(시간순으로 펼쳐서) → 우리 클래스 4개 확률. 모델이 없으면 false. */
static bool runModel(float proba[4]) {
  if (!interp) return false;
  const float sc = tin->params.scale;
  const int   zp = tin->params.zero_point;
  for (int t = 0; t < WIN; t++) {
    int k = (head + t) % WIN;                    // 오래된 것부터
    float v[2] = {(abuf[k] - MU[0]) / SD[0], (gbuf[k] - MU[1]) / SD[1]};
    for (int c = 0; c < 2; c++) {
      int q = (int)lroundf(v[c] / sc) + zp;
      tin->data.int8[t * 2 + c] = (int8_t)constrain(q, -128, 127);
    }
  }
  if (interp->Invoke() != kTfLiteOk) return false;

  float p5[N_MODEL];
  for (int i = 0; i < N_MODEL; i++)
    p5[i] = (tout->data.int8[i] - tout->params.zero_point) * tout->params.scale;

  // VIGOROUS 를 빼고 넷의 합으로 다시 나눈다.
  for (int c = 0; c < 4; c++) proba[c] = 0;
  float sum = 0;
  for (int i = 0; i < N_MODEL; i++) {
    int o = MODEL_TO_OURS[i];
    if (o < 0) continue;
    proba[o] = p5[i];
    sum += p5[i];
  }
  if (sum > 1e-6f) for (int c = 0; c < 4; c++) proba[c] /= sum;
  return true;
}

static void setupModel() {
  const tflite::Model* model = tflite::GetModel(activity_cnn5_int8_tflite);
  if (model->version() != TFLITE_SCHEMA_VERSION) {
    Serial.println("[모델] 스키마 버전 불일치");
    return;
  }
  static tflite::MicroMutableOpResolver<7> resolver;
  resolver.AddExpandDims();
  resolver.AddConv2D();
  resolver.AddReshape();
  resolver.AddMaxPool2D();
  resolver.AddMean();
  resolver.AddFullyConnected();
  resolver.AddSoftmax();
  static tflite::MicroInterpreter si(model, resolver, tensorArena, kArenaSize);
  if (si.AllocateTensors() != kTfLiteOk) {
    Serial.println("[모델] AllocateTensors 실패 (kArenaSize 를 늘려 보기)");
    return;
  }
  interp = &si;
  tin = interp->input(0);
  tout = interp->output(0);
  char line[96];
  snprintf(line, sizeof line, "[모델] CNN 5클래스 준비됨 (텐서 %u바이트, VIGOROUS 제외)",
           (unsigned)interp->arena_used_bytes());
  Serial.println(line);
}

// ---------------------------------------------------------------------------
// LED (XIAO 내장 RGB, LOW 가 켜짐)
// ---------------------------------------------------------------------------

void led(int cls) {
  bool r = false, g = false, b = false;
  switch (cls) {
    case 0: b = true; break;                       // 쉬기 파랑
    case 1: g = true; break;                       // 걷기 초록
    case 2: r = true; break;                       // 뛰기 빨강
    case 3: r = g = b = true; break;               // 털기 흰색
  }
  digitalWrite(LEDR, r ? LOW : HIGH);
  digitalWrite(LEDG, g ? LOW : HIGH);
  digitalWrite(LEDB, b ? LOW : HIGH);
}

// ---------------------------------------------------------------------------

void classifyAndPublish() {
  float proba[N_CLASSES] = {0};
  if (!runModel(proba)) return;
  int cls = 0;
  for (int c = 1; c < N_CLASSES; c++) if (proba[c] > proba[cls]) cls = c;

  // 시리얼
  Serial.print(NAMES_KO[cls]);
  Serial.print("  ");
  Serial.print(proba[cls], 2);
  Serial.print("  |");
  for (int c = 0; c < N_CLASSES; c++) {
    Serial.print(" ");
    Serial.print(NAMES_KO[c]);
    Serial.print(" ");
    Serial.print(proba[c], 2);
  }
  Serial.println();

  led(cls);

  uint32_t now = millis();
  uint8_t conf = (uint8_t)(proba[cls] * 100);

  // 1분 요약 — 판정은 1초 간격이므로 1회 = 1초
  if (cls < 3) minSec[cls]++;
  if (cls == 3 && lastClass != 3) {
    minShake++;                                   // 털기는 시작할 때만 1회로 센다
    // **시각까지 따로 남긴다.** 요약의 '털기 N회' 만으로는 몇 시에 긁었는지
    // 알 수 없다. 밤에 긁은 횟수가 피부축의 핵심 지표라서, 분 단위로
    // 뭉개면 그 지표가 못 쓰게 된다.
    if (shakeCount < SHAKE_CAP) {
      shakeBuf[shakeCount++] = {++shakeSeq, now, conf};
    } else {
      // 넘치면 오래된 것부터 버린다. 최근 쪽이 더 쓸모 있다.
      dropShake(1);
      shakeBuf[shakeCount++] = {++shakeSeq, now, conf};
    }
  }
  lastClass = cls;

  // 1초 판정은 **연결됐을 때만** 보낸다. 모아두지 않는다.
  if (BLE.connected()) {
    uint8_t buf[10];
    buf[0] = (uint8_t)cls;
    buf[1] = conf;
    put32(buf + 2, now);
    put32(buf + 6, bootId);
    chNow.writeValue(buf, 10);
  }

  if (now - minuteStart >= 60000) {
    minuteStart = now;

    // **합이 60을 넘을 수 있다.** 판정은 1초 간격이고 창 경계는 60초인데
    // 둘이 정확히 맞물리지 않아 한 창에 61번 들어오는 일이 있다. 실제로
    // '쉬기 61초' 가 찍혔다. 서버는 초 값이 60을 넘으면 거부한다(422).
    //
    // 가장 큰 쪽에서 깎는다. 넘치는 양이 1~2초라 어느 쪽을 깎아도 비율은
    // 거의 그대로다.
    uint16_t r = minSec[0] / 2, w = minSec[1] / 2, ru = minSec[2] / 2;
    while (r + w + ru > 60) {
      if (r >= w && r >= ru)      r--;
      else if (w >= ru)           w--;
      else                        ru--;
    }

    // **깎은 값을 찍는다.** 원래 값을 찍으면 '61초' 가 보이는데 저장된 건
    // 60이라, 로그와 실제가 어긋나서 버그를 찾을 때 헷갈린다.
    char line[96];
    snprintf(line, sizeof line,
             "[1분] 쉬기 %u초 걷기 %u초 뛰기 %u초 털기 %u회  (쌓임 %d/%d)",
             r, w, ru, minShake, minCount + 1, shakeCount);
    Serial.println(line);

    // **연결 여부와 무관하게 쌓는다.** 예전에는 여기서 바로 보내고 카운터를
    // 지웠는데, 끊겨 있으면 알림이 아무데도 안 가고 다음 줄에서 지워져서
    // 그 1분이 영구히 사라졌다.
    if (minCount >= MIN_CAP) dropMin(1);
    minBuf[minCount++] = {++minSeq, now, r, w, ru, minShake};

    minSec[0] = minSec[1] = minSec[2] = 0;
    minShake = 0;
  }
}


/**
 * 쌓인 것을 보낸다. **ack 가 올 때까지 버리지 않는다.**
 *
 * 한 번에 다 쏟지 않는다. **연결 간격보다 빠르게 알림을 쓰면 값이 덮여
 * 중간이 사라진다.** 50ms 로 뒀더니 실제로 2분 36초 끊긴 뒤 쌓인 3건 중
 * 1건만 도착했다. nRF52 의 기본 연결 간격이 50~100ms 라 절반 가까이
 * 날아간 것이다.
 *
 * 200ms 로 늘렸다. 초당 5개, 하루치(1440개)를 5분에 비운다. 느리지만
 * 끊긴 시간이 하루인 경우는 드물고, 빠르게 보내다 잃는 쪽이 더 나쁘다.
 *
 * 그래도 유실은 막지 못한다. 그건 일련번호와 ack 로 막는다.
 *
 * ack 를 못 받으면 다시 보낸다. 단 **오래 기다린다.** 밥통은 60초마다
 * 서버에 올리고 그 뒤에 ack 를 쓰므로, 짧게 잡으면 그 사이에 계속 전체를
 * 재전송한다. 실제로 3초로 뒀더니 1분 요약 14개를 3초마다 다시 보냈다.
 */
const unsigned long RESEND_MS = 120000;   // 밥통 업로드 주기(60초)의 두 배
static void drain() {
  static unsigned long lastSend = 0;
  static unsigned long lastProgress = 0;
  unsigned long now = millis();

  // **구독을 확인하고 보낸다.** 연결만으로는 부족하다.
  //
  // 밥통은 붙은 뒤 특성을 찾아 구독하는 데 3초쯤 쓴다. 그 전에 보낸 알림은
  // 버려지는데, 목줄은 보냈다고 믿고(minSent == minCount) 멈춘다. 그러면
  // 재전송 타이머(2분)를 기다려야 해서 밀린 것이 매번 2분씩 늦었다.
  // 실제로 2건 + 털기 3건이 그렇게 한 바퀴를 놓쳤다.
  if (!BLE.connected() || !chMin.subscribed() || !chShake.subscribed()) {
    // 다시 붙었을 때 밀린 것부터 보내도록 되돌려 둔다.
    minSent = shakeSent = 0;
    return;
  }
  if (now - lastSend < 200) return;
  lastSend = now;

  // 밀린 것을 보내기 시작할 때 한 번 찍는다. 몇 건이 밀려 있었는지가
  // 로그에 남아야 유실을 셀 수 있다.
  if (minSent == 0 && shakeSent == 0 && (minCount || shakeCount)) {
    char line[80];
    snprintf(line, sizeof line, "[전송] 밀린 요약 %d건, 털기 %d건 보냅니다",
             minCount, shakeCount);
    Serial.println(line);
  }

  if (minSent < minCount) {
    const MinRec& r = minBuf[minSent];
    uint8_t buf[24];
    put32(buf, bootId);
    put32(buf + 4, r.seq);
    // **지금 들고 있는 가장 오래된 번호.** 밥통이 기준선을 잡는 데 쓴다.
    // 이것보다 앞의 기록은 목줄에 이미 없다 - 밥통이 '받았다' 고 쳐도
    // 잃을 것이 없고, 그래야 밥통이 재부팅해도 번호가 이어진다.
    put32(buf + 8, minBuf[0].seq);
    put32(buf + 12, r.t_ms);
    put16(buf + 16, r.rest);
    put16(buf + 18, r.walk);
    put16(buf + 20, r.run);
    put16(buf + 22, r.shake);
    chMin.writeValue(buf, 24);
    minSent++;
    lastProgress = now;
    return;                          // 한 번에 하나만
  }

  if (shakeSent < shakeCount) {
    const ShakeRec& r = shakeBuf[shakeSent];
    uint8_t buf[17];
    put32(buf, bootId);
    put32(buf + 4, r.seq);
    put32(buf + 8, shakeBuf[0].seq);     // 들고 있는 가장 오래된 번호
    put32(buf + 12, r.t_ms);
    buf[16] = r.conf;
    chShake.writeValue(buf, 17);
    shakeSent++;
    lastProgress = now;
    return;
  }

  // 다 보냈는데 ack 가 안 온다. 받지 못한 것으로 보고 다시 보낸다.
  if ((minCount || shakeCount) && lastProgress && now - lastProgress > RESEND_MS) {
    Serial.println("[재전송] ack 가 없어 처음부터 다시 보냅니다");
    minSent = shakeSent = 0;
    lastProgress = now;
  }
}


/** 밥통이 "여기까지 서버에 넣었다" 고 알려주면 그만큼 버린다. */
static void serviceAck() {
  if (!chAck.written() || chAck.valueLength() < 8) return;
  const uint8_t* v = chAck.value();
  uint32_t lastMin = get32(v);
  uint32_t lastSeq = get32(v + 4);

  int n = 0;
  while (n < minCount && minBuf[n].seq <= lastMin) n++;
  dropMin(n);

  int m = 0;
  while (m < shakeCount && shakeBuf[m].seq <= lastSeq) m++;
  dropShake(m);

  if (n || m) {
    // mbed 코어의 Serial 에는 printf 가 없다. snprintf 로 만들어 찍는다.
    char line[96];
    snprintf(line, sizeof line, "[ack] 요약 %d개, 털기 %d개 버림 (남음 %d/%d)",
             n, m, minCount, shakeCount);
    Serial.println(line);
  }
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) {}    // USB 없으면 3초 뒤 그냥 진행

  pinMode(LEDR, OUTPUT); pinMode(LEDG, OUTPUT); pinMode(LEDB, OUTPUT);
  led(-1);

  // 학습 때와 같은 측정 범위 (활동 중 큰 값이 잘리지 않게)
  imu.settings.accelRange = 16;  imu.settings.gyroRange = 2000;
  imu.settings.accelSampleRate = 208;  imu.settings.gyroSampleRate = 208;
  if (imu.begin() != 0) {
    Serial.println("[IMU] 시작 실패 — Sense 보드가 맞는지 확인하세요");
    while (true) { digitalWrite(LEDR, !digitalRead(LEDR)); delay(200); }
  }
  Serial.println("[IMU] 준비됨");

  setupModel();

  // 자이로 바이어스. **켤 때 2초 가만히 둔다** (초록 깜빡임).
  // 이걸 빼면 가만히 있어도 |g| 가 몇 dps 떠서 쉬기가 걷기로 보인다.
  Serial.println("[IMU] 자이로 바이어스 측정 — 2초 동안 가만히");
  float gs[3] = {0, 0, 0};
  for (int i = 0; i < 200; i++) {
    gs[0] += imu.readFloatGyroX(); gs[1] += imu.readFloatGyroY(); gs[2] += imu.readFloatGyroZ();
    digitalWrite(LEDG, ((i / 25) % 2 == 0) ? LOW : HIGH);
    delay(10);
  }
  for (int k = 0; k < 3; k++) gbias[k] = gs[k] / 200.0f;
  led(-1);

  // 부팅 번호. IMU 가 준 첫 값으로 씨를 섞는다 - micros() 만 쓰면 매번
  // 비슷한 시점에 켜져 같은 값이 나올 수 있다.
  randomSeed((uint32_t)micros() ^
             (uint32_t)(imu.readFloatAccelX() * 100000.0f) ^
             (uint32_t)(imu.readFloatGyroZ() * 100000.0f));
  bootId = (uint32_t)random(1, 0x7FFFFFFF);   // 1 ~ 21억. 0 은 '모름' 으로 쓴다
  char bootLine[80];
  snprintf(bootLine, sizeof bootLine,
           "[부팅] 번호 %lu — 이 값이 t_ms 의 기준점을 식별합니다",
           (unsigned long)bootId);
  Serial.println(bootLine);

  if (BLE.begin()) {
    BLE.setLocalName("pebble-collar");
    BLE.setAdvertisedService(svc);
    svc.addCharacteristic(chNow);
    svc.addCharacteristic(chMin);
    svc.addCharacteristic(chShake);
    svc.addCharacteristic(chAck);
    BLE.addService(svc);
    BLE.advertise();
    bleOk = true;
    Serial.println("[BLE] 'pebble-collar' 광고 중");
  } else {
    Serial.println("[BLE] 시작 실패 — 판정은 시리얼로만 나갑니다");
    Serial.println("      쌓기는 계속 돕니다. 붙으면 밀린 것부터 보냅니다.");
  }

  Serial.println("2초 데이터를 모은 뒤 1초마다 판정합니다.");
  minuteStart = millis();
}

void loop() {
  static unsigned long next = micros();
  if (bleOk) {
    BLE.poll();
    serviceAck();
    drain();
  }

  if ((long)(micros() - next) < 0) return;
  next += RAW_PERIOD_US;

  float ax = imu.readFloatAccelX(), ay = imu.readFloatAccelY(), az = imu.readFloatAccelZ();
  float gx = imu.readFloatGyroX() - gbias[0];
  float gy = imu.readFloatGyroY() - gbias[1];
  float gz = imu.readFloatGyroZ() - gbias[2];
  abuf[head] = sqrtf(ax * ax + ay * ay + az * az);
  gbuf[head] = sqrtf(gx * gx + gy * gy + gz * gz);
  head = (head + 1) % WIN;
  if (filled < WIN) filled++;

  if (filled == WIN && ++sinceLast >= HOP) {
    sinceLast = 0;
    classifyAndPublish();
    next = micros();                 // 추론 시간만큼 밀린 것 보정 (원본 예제와 같다)
  }
}
