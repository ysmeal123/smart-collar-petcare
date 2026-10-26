/*
 * 모터 진단 — 속도를 바꿔가며 돌려본다.
 *
 * **서버도 Wi-Fi도 쓰지 않는다.** 모터가 도는지만 본다.
 * 한 번에 다 붙이면 안 될 때 원인이 셋(배선·속도·네트워크)으로 늘어난다.
 *
 * 올린 뒤 시리얼 모니터(115200)를 열면 메뉴가 나온다.
 *
 * 배선 (슬롯 1번 = 오메가3)
 *
 *     ESP32          DRV8825 확장보드
 *     ---------      ----------------
 *     GPIO4    ───▶  STEP (S 열)
 *     GPIO5    ───▶  DIR  (S 열)
 *     GPIO13   ───▶  EN   (S 열)
 *     GND      ───▶  GND  (G 열)        ← 이게 없으면 오동작한다
 *
 * DRV8825 는 3.3V 로직이라 레벨 시프터가 필요 없다.
 *
 * 12V 는 확장보드의 초록 나사 단자(VIN/GND)로만 들어간다.
 * **ESP32 핀에는 절대 12V 를 넣지 않는다.**
 */

// ---------------------------------------------------------------------------
// 핀
//
// ESP32-WROOM-32 는 쓸 수 있는 핀이 빠듯하다. 아래는 피한 것들이다.
//   GPIO 1,3      USB 시리얼 (쓰면 업로드·로그가 깨진다)
//   GPIO 6~11     내장 플래시
//   GPIO 0,2,12,15  부팅 때 상태를 읽는 핀
//   GPIO 34~39    입력 전용 — 출력이 안 된다
//   GPIO 21,22    로드셀 I2C 로 남겨둔다
// ---------------------------------------------------------------------------

const int STEP_PIN = 4;
const int DIR_PIN  = 5;
const int EN_PIN   = 13;

// EN 은 LOW 가 활성화다. 평소 HIGH 로 두어 출력을 끊어 놓는다.
// 그래야 모터가 전류를 안 먹고, 손으로 축을 돌려볼 수도 있다.
const int EN_ACTIVE   = LOW;
const int EN_INACTIVE = HIGH;

// ---------------------------------------------------------------------------
// 속도
//
// **스텝모터는 가속 없이 빠르게 시작하면 안 돈다.** 코일이 못 따라가서
// 제자리에서 떨거나 아예 안 움직인다. 느린 쪽부터 시험한다.
//
// 조립 안내서가 쓴 20000us(25Hz)가 첫 번째다. 느리지만 확실히 돈다.
// ---------------------------------------------------------------------------

const unsigned long SPEEDS_US[] = {20000, 10000, 5000, 2000, 1500, 800};
const int N_SPEEDS = sizeof(SPEEDS_US) / sizeof(SPEEDS_US[0]);

const int STEPS_PER_REV = 200;   // NEMA17 풀스텝 한 바퀴

unsigned long pulseUs = SPEEDS_US[0];


void setup() {
  Serial.begin(115200);
  delay(300);

  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  pinMode(EN_PIN, OUTPUT);

  digitalWrite(STEP_PIN, LOW);
  digitalWrite(DIR_PIN, LOW);
  digitalWrite(EN_PIN, EN_INACTIVE);   // 켜자마자 돌면 안 된다

  Serial.println();
  Serial.println("=========================================");
  Serial.println(" pebble 모터 진단");
  Serial.println("=========================================");
  Serial.printf("  STEP  GPIO%d\n", STEP_PIN);
  Serial.printf("  DIR   GPIO%d\n", DIR_PIN);
  Serial.printf("  EN    GPIO%d\n", EN_PIN);
  Serial.println();
  Serial.println("  12V 가 켜져 있어야 합니다.");
  Serial.println("  모터를 고정하고 로더는 떼고 하세요.");
  printMenu();
}


void printMenu() {
  Serial.println();
  Serial.println("-----------------------------------------");
  Serial.printf("  현재 속도: %lu us (%lu Hz)\n", pulseUs, 500000UL / pulseUs);
  Serial.println();
  Serial.println("  1  한 바퀴 (200 펄스)");
  Serial.println("  2  반대 방향으로 한 바퀴");
  Serial.println("  3  20 펄스만 (약 36도)");
  Serial.println("  s  속도 바꾸기 (느린 쪽부터 순환)");
  Serial.println("  a  모든 속도로 한 바퀴씩 — 어디서 도는지 찾기");
  Serial.println("  t  EN 을 켜고 끄기만 (모터 잠김 확인)");
  Serial.println("-----------------------------------------");
  Serial.print("> ");
}


/** 지정한 펄스 수만큼 돌린다. */
void turn(int steps, bool reverse, unsigned long us) {
  digitalWrite(DIR_PIN, reverse ? HIGH : LOW);
  digitalWrite(EN_PIN, EN_ACTIVE);
  delay(10);                      // 드라이버가 깨어날 시간

  unsigned long t0 = millis();
  for (int i = 0; i < steps; i++) {
    digitalWrite(STEP_PIN, HIGH);
    delayMicroseconds(us);
    digitalWrite(STEP_PIN, LOW);
    delayMicroseconds(us);
  }
  unsigned long dt = millis() - t0;

  digitalWrite(EN_PIN, EN_INACTIVE);
  Serial.printf("    %d 펄스 / %lu us / %lu ms\n", steps, us, dt);
}


/** 느린 쪽부터 한 바퀴씩 돌려 어느 속도까지 도는지 찾는다. */
void sweep() {
  Serial.println();
  Serial.println("  느린 쪽부터 한 바퀴씩 돌립니다. 모터를 보세요.");
  for (int i = 0; i < N_SPEEDS; i++) {
    Serial.printf("\n  [%d/%d] %lu us (%lu Hz)\n",
                  i + 1, N_SPEEDS, SPEEDS_US[i], 500000UL / SPEEDS_US[i]);
    turn(STEPS_PER_REV, false, SPEEDS_US[i]);
    delay(700);                   // 눈으로 구분할 틈
  }
  Serial.println();
  Serial.println("  어느 속도까지 돌았는지 기억해 두세요.");
  Serial.println("  하나도 안 돌았다면 전기 쪽입니다:");
  Serial.println("    1. 12V 어댑터가 켜져 있나");
  Serial.println("    2. VREF 가 0.15V 인가");
  Serial.println("    3. RESET·SLEEP 이 HIGH 인가   <- 제일 흔함");
  Serial.println("    4. ESP32 GND 와 드라이버 GND 가 이어져 있나");
  Serial.println("  떨기만 한다면 코일 쌍이 어긋난 겁니다 (1A/1B, 2A/2B).");
}


/**
 * EN 만 켜고 끈다.
 *
 * 모터가 돌지 않아도 **EN 을 내리면 축이 뻑뻑해진다.** 전류가 흘러
 * 코일이 자리를 잡기 때문이다. 손으로 돌려보면 바로 안다.
 *
 * 이걸로 "전원·드라이버는 살아 있는데 STEP 신호만 문제" 인지 가를 수 있다.
 */
void toggleEnable() {
  Serial.println();
  Serial.println("  EN 을 3초간 내립니다. 손으로 축을 돌려보세요.");
  digitalWrite(EN_PIN, EN_ACTIVE);
  delay(3000);
  digitalWrite(EN_PIN, EN_INACTIVE);
  Serial.println("  EN 을 올렸습니다. 이제 헐거워야 합니다.");
  Serial.println();
  Serial.println("  뻑뻑했다 -> 전원·드라이버 정상. STEP 신호를 보면 됨");
  Serial.println("  차이 없다 -> 12V, VREF, RESET/SLEEP 을 확인");
}


void loop() {
  if (!Serial.available()) return;

  char c = Serial.read();
  if (c == '\n' || c == '\r') return;
  Serial.println(c);

  switch (c) {
    case '1': turn(STEPS_PER_REV, false, pulseUs); break;
    case '2': turn(STEPS_PER_REV, true,  pulseUs); break;
    case '3': turn(20,            false, pulseUs); break;
    case 'a': sweep(); break;
    case 't': toggleEnable(); break;
    case 's': {
      int i = 0;
      while (i < N_SPEEDS && SPEEDS_US[i] != pulseUs) i++;
      pulseUs = SPEEDS_US[(i + 1) % N_SPEEDS];
      break;
    }
    default:
      Serial.println("  모르는 명령입니다.");
  }
  printMenu();
}
