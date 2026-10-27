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
 *     GPIO32   ───▶  STEP (S 열)
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

// **실물에서 검증된 핀이다.** 영상 예제(passion-tech)와 같은 조합으로,
// 이 둘에서 모터가 확실히 돌았다.
//
// GPIO4 로는 안 됐다. HIGH 를 걸어도 드라이버의 STP 핀에서 1.8V 로만
// 읽혔고, DRV8825 는 2.2V 이상을 HIGH 로 인정하므로 펄스가 아예 안
// 보였다. STP-GND 저항은 96.9kOhm 으로 데이터시트의 내부 풀다운
// 100kOhm 과 일치했으니 드라이버 입력과 확장보드 배선은 멀쩡했다.
// 전압을 못 낸 쪽은 ESP32 핀이다.
//
// 이 보드(indifrog IFLine/IFZero 계열)는 GPIO18 을 빼주지 않는다.
// 그래서 검증된 18/19 중 19 를 STEP 으로 올리고 DIR 은 27 로 옮겼다.
const int STEP_PIN = 19;
const int DIR_PIN  = 27;

// EN 은 쓰지 않는다.
//
// DRV8825 의 EN 은 내부 100kOhm 풀다운이 달려 있어 **안 꽂으면 LOW 로
// 떨어지고, LOW 가 활성이라 드라이버가 켜진다.** 선이 하나 줄고 변수도
// 하나 줄어든다. 영상 예제도 EN 을 안 쓴다.
//
// 제품에서는 다시 쓸 값이 있다 - 급여하지 않을 때 전류를 끊으면
// 발열과 전력이 줄고, 손으로 로더를 돌려볼 수 있다. 그때 이 값을
// 실제 핀 번호로 바꾸면 된다.
const int EN_PIN = -1;

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

//: HIGH 를 유지하는 시간. 느린 쪽부터.
//
// **검증된 값은 1600us 다.** 참조 예제(passion-tech)가 쓰는 값이고,
// 실물에서 이 설정으로 매끄럽게 돌았다.
const unsigned long SPEEDS_US[] = {6400, 3200, 1600, 1000};
const int N_SPEEDS = sizeof(SPEEDS_US) / sizeof(SPEEDS_US[0]);

//: LOW 로 떨어뜨리는 시간. **짧게 고정한다.**
//
// 처음에는 HIGH 와 LOW 를 같게 줬는데 모터가 전혀 안 돌았다. 참조 예제는
// HIGH 를 길게, LOW 를 짧게 주고 그 쪽은 돌았다. 같은 핀, 같은 배선,
// 같은 드라이버에서 갈린 유일한 차이가 이 모양이었다.
const unsigned long LOW_US = 100;

const int STEPS_PER_REV = 200;   // NEMA17 풀스텝 한 바퀴

unsigned long pulseUs = 1600;   // 검증된 값에서 시작


/**
 * 드라이버 출력을 켜고 끈다. **EN 을 안 쓰면 아무 일도 하지 않는다.**
 *
 * EN_PIN 이 음수면 선이 안 꽂힌 것이고, 그때 드라이버는 내부 풀다운으로
 * 항상 켜져 있다. 끌 방법이 없으므로 조용히 넘어간다.
 */
static void enable(bool on) {
  if (EN_PIN < 0) return;
  pinMode(EN_PIN, OUTPUT);
  digitalWrite(EN_PIN, on ? EN_ACTIVE : EN_INACTIVE);
}


void setup() {
  Serial.begin(115200);
  delay(300);

  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(DIR_PIN, LOW);
  enable(false);                       // 켜자마자 돌면 안 된다

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
  Serial.println("  m  30초간 25Hz 로 계속 펄스 (멀티미터 측정용)");
  Serial.println("  h  2분간 EN 만 내려 유지 (코일 전압 측정용)");
  Serial.println("  f  EN 을 띄운 채로 한 바퀴 (EN 처리가 범인인지 확인)");
  Serial.println("  p  핀 확인 패턴을 90초 유지 (보드 핀 번호 검증)");
  Serial.println("  r  검증된 속도로 길게 연속 운전 (매끄러운지 확인)");
  Serial.println("-----------------------------------------");
  Serial.print("> ");
}


/**
 * 30초간 느리게 계속 펄스를 보낸다. **멀티미터로 재보기 위한 모드다.**
 *
 * 모터가 안 돌 때, 신호가 정말 드라이버 핀까지 가는지를 눈이 아니라
 * 숫자로 확인해야 한다. 25Hz 듀티 50% 면 DC 전압계에 평균값이 뜬다.
 *
 *     STEP  약 1.6V   (3.3V 의 절반)  <- 펄스가 오고 있다
 *     STEP  0V 또는 3.3V             <- 신호가 안 온다. 배선·핀 확인
 *     EN    약 0V                     <- 활성화됨 (LOW 가 활성)
 *     EN    3.3V                      <- 꺼져 있다
 *     RST   3.3V, SLP 3.3V            <- 깨어 있다. 0V 면 드라이버가 잠김
 */
void measureMode() {
  Serial.println();
  Serial.println("  30초간 25Hz 로 펄스를 보냅니다. 드라이버 핀에서 재보세요.");
  Serial.println();
  Serial.println("    STEP  약 1.6V 여야 합니다 (3.3V 의 절반)");
  Serial.println("    EN    약 0V   여야 합니다 (LOW 가 활성)");
  Serial.println("    RST   3.3V, SLP 3.3V 여야 합니다");
  Serial.println();
  Serial.println("  검정 프로브는 드라이버의 GND 에 둡니다.");

  digitalWrite(DIR_PIN, LOW);
  enable(true);

  unsigned long end = millis() + 30000;
  int left = 30;
  while (millis() < end) {
    digitalWrite(STEP_PIN, HIGH);
    waitUs(6400);
    digitalWrite(STEP_PIN, LOW);
    waitUs(LOW_US);
    yield();                      // 30초짜리 모드다. 여기서는 양보해야 한다
    int now = (int)((end - millis()) / 1000) + 1;
    if (now != left) { left = now; Serial.printf("    %d\n", left); }
  }

  enable(false);
  Serial.println("  끝났습니다.");
}


/**
 * 기다린다. **스텝 간격은 정확해야 한다.**
 *
 * 처음에는 1ms 이상을 `delay()` 로 넘겼다. 워치독을 먹이려고 그랬는데,
 * `delay()` 는 vTaskDelay 라서 다음 틱까지만 기다린다. 1ms 를 요청해도
 * 실제로는 0~1ms 사이로 들쭉날쭉하다. 그 지터가 그대로 스텝 간격에
 * 실려서 **모터가 뻐걱거렸다.** 로그에도 드러났다 - 1600us 로 200펄스면
 * 340ms 여야 하는데 200ms 로 끝났다.
 *
 * 이제는 전부 busy-wait 한다. 가장 느린 6400us 로 200펄스를 보내도
 * 1.3초라 워치독(5초)에 한참 못 미친다. 느린 속도를 20000us 까지
 * 늘리지 않는 한 이대로 안전하다.
 */
static inline void waitUs(unsigned long us) {
  delayMicroseconds(us);
}


/** 지정한 펄스 수만큼 돌린다. */
void turn(int steps, bool reverse, unsigned long us) {
  digitalWrite(DIR_PIN, reverse ? HIGH : LOW);
  enable(true);
  delay(10);                      // 드라이버가 깨어날 시간

  unsigned long t0 = millis();
  for (int i = 0; i < steps; i++) {
    digitalWrite(STEP_PIN, HIGH);
    waitUs(us);
    digitalWrite(STEP_PIN, LOW);
    waitUs(LOW_US);
  }
  unsigned long dt = millis() - t0;

  enable(false);
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
  Serial.println("  EN 을 10초간 내립니다. 손으로 축을 돌려보세요.");
  enable(true);
  for (int s = 10; s > 0; s--) {
    Serial.printf("    %d\n", s);
    delay(1000);
  }
  enable(false);
  Serial.println("  EN 을 올렸습니다. 이제 헐거워야 합니다.");
  Serial.println();
  Serial.println("  뻑뻑했다 -> 전원·드라이버 정상. STEP 신호를 보면 됨");
  Serial.println("  차이 없다 -> 12V, VREF, RESET/SLEEP 을 확인");
}


/**
 * 30초간 EN 만 내리고 **펄스는 보내지 않는다.**
 *
 * 펄스를 보내면 코일 전압이 계속 바뀌어 DC 전압계로 못 읽는다.
 * 가만히 잡고 있으면 한쪽 코일에 일정한 전류가 흘러, 코일 양단에
 * I x R 만큼의 전압이 걸린다. 그 숫자가 **전류가 정말 흐르는지** 말해준다.
 *
 *     A1-A2 사이  0.3 ~ 1V      전류가 흐른다 (정상)
 *     A1-A2 사이  0V            전류가 없다 - VREF 0 이거나 드라이버 불량
 *     A1-GND      0V 또는 12V 고정, 양쪽 같음  -> H 브리지가 안 움직인다
 */
void holdMode() {
  Serial.println();
  Serial.println("  2분간 EN 을 내린 채 유지합니다. 펄스는 안 보냅니다.");
  Serial.println();
  Serial.println("    A1 - A2 사이    0.3~1V 면 전류가 흐르는 중");
  Serial.println("    B1 - B2 사이    같음");
  Serial.println("    0V 면 전류가 없는 것 - VREF 나 드라이버를 봐야 함");
  Serial.println();
  Serial.println("  축을 손으로 돌려보면 뻑뻑해야 합니다.");

  digitalWrite(STEP_PIN, LOW);
  digitalWrite(EN_PIN, EN_ACTIVE);
  // 2분. **그동안 가변저항을 돌리며 전압이 올라가는지 본다.**
  for (int s = 120; s > 0; s--) {
    if (s % 10 == 0 || s <= 5) Serial.printf("    %d\n", s);
    delay(1000);
  }
  enable(false);
  Serial.println("  끝났습니다.");
}


/**
 * EN 핀을 **입력으로 바꿔 띄운 채로** 돌려본다.
 *
 * DRV8825 의 EN 은 내부 100kOhm 풀다운이 달려 있다. 즉 아무것도 안
 * 꽂으면 LOW 로 떨어지고, LOW 가 활성이므로 **드라이버가 켜진다.**
 *
 * 이 모드가 돌고 아까 것이 안 돌았다면, 범인은 EN 처리다 - 확장보드가
 * EN 을 반전시키거나, 내가 꽂은 핀이 엉뚱한 슬롯의 EN 이거나.
 *
 * 멀티미터가 필요 없고 배선도 안 건드린다. 그래서 먼저 해볼 값이 있다.
 */
void floatEnable() {
  Serial.println();
  Serial.println("  EN 을 띄웁니다(INPUT). 드라이버 내부 풀다운이 켜줄 겁니다.");
  if (EN_PIN >= 0) pinMode(EN_PIN, INPUT);
  delay(50);

  digitalWrite(DIR_PIN, LOW);
  Serial.println("  25Hz 로 한 바퀴 돌립니다 (8초). 모터를 보세요.");
  for (int i = 0; i < STEPS_PER_REV; i++) {
    digitalWrite(STEP_PIN, HIGH);
    waitUs(6400);
    digitalWrite(STEP_PIN, LOW);
    waitUs(LOW_US);
  }

  enable(false);
  Serial.println("  끝났습니다. EN 을 출력으로 되돌렸습니다.");
}


/**
 * **보드의 D 번호가 정말 GPIO 번호인지** 실측으로 확인한다.
 *
 * ESP32 개발보드는 실크 인쇄가 제각각이다. DOIT 계열은 D4 = GPIO4 지만,
 * 아두이노 우노 모양인 D1 R32 는 D4 = GPIO17 이다. 전제가 틀리면
 * 배선이 완벽해도 신호가 엉뚱한 핀에서 나온다.
 *
 * 구별되는 패턴을 90초 걸어두고 사람이 멀티미터로 읽는다.
 * 전부 HIGH 로 두면 "우연히 맞은 것" 과 구분이 안 되므로 섞는다.
 */
void pinCheck() {
  digitalWrite(STEP_PIN, HIGH);   // GPIO4   -> 3.3V
  digitalWrite(DIR_PIN,  LOW);    // GPIO5   -> 0V
  enable(false);                 // EN 은 안 쓰면 무시된다

  Serial.println();
  Serial.println("  90초간 이 패턴을 유지합니다. 보드 핀을 재보세요.");
  Serial.println();
  Serial.print("    GPIO");   Serial.print(STEP_PIN);
  Serial.println("  (STEP)  HIGH  -> 3.3V 여야 함");
  Serial.print("    GPIO");   Serial.print(DIR_PIN);
  Serial.println("  (DIR )  LOW   -> 0V   여야 함");
  Serial.print("    GPIO");   Serial.print(EN_PIN);
  Serial.println("  (EN  )  HIGH  -> 3.3V 여야 함");
  Serial.println();
  Serial.println("  패턴이 그대로 읽히면 D번호 = GPIO번호 가 맞다.");

  for (int s = 90; s > 0; s--) {
    if (s % 10 == 0 || s <= 3) { Serial.print("    "); Serial.println(s); }
    delay(1000);
  }

  digitalWrite(STEP_PIN, LOW);
  enable(false);
  Serial.println("  끝났습니다.");
}


/**
 * **참조 예제와 똑같은 조건으로** 길게 돌린다.
 *
 * 속도를 바꿔가며 한 바퀴씩 돌리는 `a` 는 거칠게 느껴진다. 느린 풀스텝은
 * 원래 1.8도씩 끊어 움직여 덜컹거리고, 너무 빠른 쪽은 탈조한다. 그래서
 * "내 코드가 거친가" 를 판단할 수 없다.
 *
 * 참조 예제는 1600/100 한 속도로 4바퀴씩 계속 돌았고 매끄러웠다.
 * 같은 조건을 떼어내 비교한다. 여기서도 매끄러우면 거친 느낌은 속도
 * 전환 탓이고, 여기서도 거칠면 내 코드에 아직 차이가 있다.
 */
void runLikeReference() {
  const unsigned long HIGH_US = 1600;   // 검증된 값
  const int REVS = 4;

  Serial.println();
  Serial.println("  검증된 속도(HIGH 1600us / LOW 100us)로 연속 운전합니다.");
  Serial.println("  4바퀴 정방향 -> 0.5초 -> 4바퀴 역방향, 3번 반복.");

  enable(true);
  for (int round = 0; round < 3; round++) {
    for (int dir = 0; dir < 2; dir++) {
      digitalWrite(DIR_PIN, dir ? HIGH : LOW);
      Serial.print("    ");
      Serial.print(round * 2 + dir + 1);
      Serial.println("/6");
      for (int i = 0; i < STEPS_PER_REV * REVS; i++) {
        digitalWrite(STEP_PIN, HIGH);
        delayMicroseconds(HIGH_US);
        digitalWrite(STEP_PIN, LOW);
        delayMicroseconds(LOW_US);
      }
      delay(500);
    }
  }
  enable(false);
  Serial.println("  끝났습니다.");
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
    case 'm': measureMode(); break;
    case 'h': holdMode(); break;
    case 'f': floatEnable(); break;
    case 'p': pinCheck(); break;
    case 'r': runLikeReference(); break;
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
