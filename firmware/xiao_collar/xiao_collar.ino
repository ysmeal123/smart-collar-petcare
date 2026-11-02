/*
 * pebble 목줄 — XIAO nRF52840 Sense 행동 판정 (시험용)
 *
 *   IMU 100Hz → 2샘플 평균 50Hz → 2초 창(100샘플) → 특징 14개 → 모델 → 1초마다 판정
 *
 * 모델: dogmodel.h (ml/train.py 가 생성. 공개 데이터셋 목 센서로 학습한 랜덤포레스트)
 * 클래스: 0 쉬기 / 1 걷기 / 2 뛰기 / 3 털기
 *
 * **특징 계산은 ml/features.py 와 1:1 로 같아야 한다.** 한쪽만 고치면 판정이 망가진다.
 * 다른 사람이 만든 모델로 바꿀 때는 dogmodel.h 와 computeFeatures() 를 그 모델에 맞게 교체한다.
 *
 * 출력
 *   시리얼(115200)  "걷기  0.87  | 쉬기 0.05 걷기 0.87 뛰기 0.08 털기 0.00"
 *   RGB LED         쉬기 파랑 / 걷기 초록 / 뛰기 빨강 / 털기 흰색
 *   BLE  이름 "pebble-collar"
 *     서비스        19b10000-e8f2-537e-4f6c-d104768a1214
 *     now  (notify) 19b10001-...  2바이트 [클래스, 신뢰도 0~100]          1초마다
 *     min  (notify) 19b10002-...  8바이트 uint16 LE [쉬기초, 걷기초, 뛰기초, 털기횟수]  1분마다
 *
 * 보드: Tools → Board → Seeed nRF52 mbed-enabled Boards → "XIAO nRF52840 Sense"
 *       (FQBN Seeeduino:mbed:xiaonRF52840Sense)
 * 라이브러리: Seeed Arduino LSM6DS3, ArduinoBLE
 */

#include <ArduinoBLE.h>
#include <LSM6DS3.h>
#include <Wire.h>
#include <math.h>

#include "dogmodel.h"

// ---------------------------------------------------------------------------

const int N_CLASSES = 4;
const char* NAMES_KO[N_CLASSES] = {"쉬기", "걷기", "뛰기", "털기"};

const int WIN = 100;                 // 50Hz × 2초
const int HOP = 50;                  // 1초마다 판정
const unsigned long RAW_PERIOD_US = 10000;   // 100Hz

LSM6DS3 imu(I2C_MODE, 0x6A);

// 50Hz 링버퍼
float ax[WIN], ay[WIN], az[WIN], gx[WIN], gy[WIN], gz[WIN];
int head = 0;          // 다음에 쓸 자리
int filled = 0;
int sinceLast = 0;

// 100Hz → 50Hz 평균용
float acc2[6];
int acc2n = 0;

// 1분 요약
uint16_t minSec[3] = {0, 0, 0};      // 쉬기·걷기·뛰기 초
uint16_t minShake = 0;
int lastClass = -1;
unsigned long minuteStart = 0;

BLEService svc("19b10000-e8f2-537e-4f6c-d104768a1214");
BLECharacteristic chNow("19b10001-e8f2-537e-4f6c-d104768a1214", BLERead | BLENotify, 2);
BLECharacteristic chMin("19b10002-e8f2-537e-4f6c-d104768a1214", BLERead | BLENotify, 8);
bool bleOk = false;

// ---------------------------------------------------------------------------
// 특징 — ml/features.py 와 같은 순서·같은 식
// ---------------------------------------------------------------------------

static float meanOf(const float* x, int n) {
  double s = 0; for (int i = 0; i < n; i++) s += x[i]; return (float)(s / n);
}
static float stdOf(const float* x, int n) {           // numpy std (ddof=0)
  float m = meanOf(x, n); double s = 0;
  for (int i = 0; i < n; i++) { double d = x[i] - m; s += d * d; }
  return (float)sqrt(s / n);
}
static float minOf(const float* x, int n) { float m = x[0]; for (int i = 1; i < n; i++) if (x[i] < m) m = x[i]; return m; }
static float maxOf(const float* x, int n) { float m = x[0]; for (int i = 1; i < n; i++) if (x[i] > m) m = x[i]; return m; }

void computeFeatures(float* f) {
  static float A[WIN][3], G[WIN][3];
  static float v[WIN], h[WIN], am[WIN], gm[WIN];

  // 링버퍼 → 시간순
  for (int i = 0; i < WIN; i++) {
    int k = (head + i) % WIN;
    A[i][0] = ax[k]; A[i][1] = ay[k]; A[i][2] = az[k];
    G[i][0] = gx[k]; G[i][1] = gy[k]; G[i][2] = gz[k];
  }

  float g[3] = {0, 0, 0};
  for (int i = 0; i < WIN; i++) for (int j = 0; j < 3; j++) g[j] += A[i][j];
  for (int j = 0; j < 3; j++) g[j] /= WIN;
  float gn = sqrtf(g[0] * g[0] + g[1] * g[1] + g[2] * g[2]);
  float u[3] = {0, 0, 1};
  if (gn > 1e-6f) for (int j = 0; j < 3; j++) u[j] = g[j] / gn;

  for (int i = 0; i < WIN; i++) {
    float d[3];
    for (int j = 0; j < 3; j++) d[j] = A[i][j] - g[j];
    v[i] = d[0] * u[0] + d[1] * u[1] + d[2] * u[2];
    float hx = d[0] - v[i] * u[0], hy = d[1] - v[i] * u[1], hz = d[2] - v[i] * u[2];
    h[i] = sqrtf(hx * hx + hy * hy + hz * hz);
    am[i] = sqrtf(A[i][0] * A[i][0] + A[i][1] * A[i][1] + A[i][2] * A[i][2]);
    gm[i] = sqrtf(G[i][0] * G[i][0] + G[i][1] * G[i][1] + G[i][2] * G[i][2]);
  }

  int zcr = 0;
  for (int i = 1; i < WIN; i++) if (signbit(v[i]) != signbit(v[i - 1])) zcr++;
  double jerk = 0;
  for (int i = 1; i < WIN; i++) jerk += fabsf(am[i] - am[i - 1]);
  jerk /= (WIN - 1);

  f[0] = stdOf(am, WIN);  f[1] = minOf(am, WIN);  f[2] = maxOf(am, WIN);  f[3] = (float)jerk;
  f[4] = stdOf(v, WIN);   f[5] = minOf(v, WIN);   f[6] = maxOf(v, WIN);   f[7] = (float)zcr;
  f[8] = meanOf(h, WIN);  f[9] = stdOf(h, WIN);   f[10] = maxOf(h, WIN);
  f[11] = meanOf(gm, WIN); f[12] = stdOf(gm, WIN); f[13] = maxOf(gm, WIN);
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
  float f[14];
  computeFeatures(f);

  float proba[N_CLASSES] = {0};
  dogmodel_predict_proba(f, 14, proba, N_CLASSES);
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

  // 1분 요약 — 판정은 1초 간격이므로 1회 = 1초
  if (cls < 3) minSec[cls]++;
  if (cls == 3 && lastClass != 3) minShake++;     // 털기는 시작할 때만 1회로 센다
  lastClass = cls;

  if (bleOk) {
    uint8_t now[2] = {(uint8_t)cls, (uint8_t)(proba[cls] * 100)};
    chNow.writeValue(now, 2);
  }

  if (millis() - minuteStart >= 60000) {
    minuteStart = millis();
    uint16_t vals[4] = {minSec[0], minSec[1], minSec[2], minShake};
    char line[80];
    snprintf(line, sizeof line, "[1분] 쉬기 %u초 걷기 %u초 뛰기 %u초 털기 %u회",
             vals[0], vals[1], vals[2], vals[3]);
    Serial.println(line);
    if (bleOk) chMin.writeValue((uint8_t*)vals, 8);
    minSec[0] = minSec[1] = minSec[2] = 0;
    minShake = 0;
  }
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) {}    // USB 없으면 3초 뒤 그냥 진행

  pinMode(LEDR, OUTPUT); pinMode(LEDG, OUTPUT); pinMode(LEDB, OUTPUT);
  led(-1);

  if (imu.begin() != 0) {
    Serial.println("[IMU] 시작 실패 — Sense 보드가 맞는지 확인하세요");
    while (true) { digitalWrite(LEDR, !digitalRead(LEDR)); delay(200); }
  }
  Serial.println("[IMU] 준비됨");

  if (BLE.begin()) {
    BLE.setLocalName("pebble-collar");
    BLE.setAdvertisedService(svc);
    svc.addCharacteristic(chNow);
    svc.addCharacteristic(chMin);
    BLE.addService(svc);
    BLE.advertise();
    bleOk = true;
    Serial.println("[BLE] 'pebble-collar' 광고 중");
  } else {
    Serial.println("[BLE] 시작 실패 — 판정은 시리얼로만 나갑니다");
  }

  Serial.println("2초 데이터를 모은 뒤 1초마다 판정합니다.");
  minuteStart = millis();
}

void loop() {
  static unsigned long next = micros();
  if (bleOk) BLE.poll();

  if ((long)(micros() - next) < 0) return;
  next += RAW_PERIOD_US;

  acc2[0] += imu.readFloatAccelX();
  acc2[1] += imu.readFloatAccelY();
  acc2[2] += imu.readFloatAccelZ();
  acc2[3] += imu.readFloatGyroX();
  acc2[4] += imu.readFloatGyroY();
  acc2[5] += imu.readFloatGyroZ();
  if (++acc2n < 2) return;

  // 100Hz 두 샘플 평균 → 50Hz 한 샘플 (학습 때와 같은 처리)
  ax[head] = acc2[0] / 2; ay[head] = acc2[1] / 2; az[head] = acc2[2] / 2;
  gx[head] = acc2[3] / 2; gy[head] = acc2[4] / 2; gz[head] = acc2[5] / 2;
  for (int j = 0; j < 6; j++) acc2[j] = 0;
  acc2n = 0;
  head = (head + 1) % WIN;
  if (filled < WIN) filled++;

  if (filled == WIN && ++sinceLast >= HOP) {
    sinceLast = 0;
    classifyAndPublish();
  }
}
