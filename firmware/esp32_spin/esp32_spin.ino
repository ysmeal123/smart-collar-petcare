/*
 * 모터 진단 — 슬롯 4개.
 *
 * **서버도 Wi-Fi도 쓰지 않는다.** 모터가 도는지만 본다.
 * 한 번에 다 붙이면 안 될 때 원인이 셋(배선·속도·네트워크)으로 늘어난다.
 *
 * 올린 뒤 시리얼 모니터(115200)를 열면 메뉴가 나온다.
 *
 * 배선
 *
 *     ESP32                DRV8825
 *     ---------            ----------------
 *     슬롯별 STEP   ───▶   STEP
 *     슬롯별 DIR    ───▶   DIR
 *     3V3           ───▶   RST, SLP        ← 네 드라이버 공통
 *     GND           ───▶   GND             ← 이게 없으면 오동작한다
 *
 * DRV8825 는 3.3V 로직이라 레벨 시프터가 필요 없다.
 *
 * 12V 는 VMOT/GND 로만 들어간다.
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
//
// 이 보드(indifrog IFLine/IFZero 계열)는 GPIO18 을 빼주지 않는다.
// 그래서 참조 예제의 18/19 중 19 를 쓴다.
// ---------------------------------------------------------------------------

/**
 * 슬롯 하나. **번호는 서버가 보내는 슬롯 번호와 같아야 한다.**
 *
 * 로더 순서는 바꿀 수 없다 - 1 오메가3 / 2 피부장벽 / 3 관절 / 4 장건강.
 * 서버가 "2번 2알" 이라고 하면 피부장벽이 나와야 한다. 배선을 옮기면
 * 이 표를 같이 고쳐야 하고, 안 고치면 **엉뚱한 영양제가 나간다.**
 */
struct Slot {
  int step;
  int dir;
  const char *name;
};

// **실물 배선 그대로다.** 2번(19/27)이 처음 검증된 조합이다 - 참조
// 예제(passion-tech)와 같은 핀으로 모터가 확실히 돌았다.
//
// GPIO4 로는 안 됐다. HIGH 를 걸어도 드라이버의 STP 핀에서 1.8V 로만
// 읽혔고, DRV8825 는 2.2V 이상을 HIGH 로 인정하므로 펄스가 아예 안
// 보였다. STP-GND 저항은 96.9kOhm 으로 데이터시트의 내부 풀다운
// 100kOhm 과 일치했으니 드라이버 입력과 배선은 멀쩡했다. 전압을 못 낸
// 쪽은 ESP32 핀이다. **그래서 4 는 쓰지 않는다.**
const Slot SLOTS[] = {
  {13, 14, "오메가3"},
  {19, 27, "피부장벽"},    // 검증됨
  {25, 26, "관절"},
  {32, 33, "장건강"},
};
const int N_SLOTS = sizeof(SLOTS) / sizeof(SLOTS[0]);

// EN 은 아직 안 꽂혀 있다.
//
// DRV8825 의 EN 은 내부 100kOhm 풀다운이 달려 있어 **안 꽂으면 LOW 로
// 떨어지고, LOW 가 활성이라 드라이버가 켜진다.** 선이 하나 줄고 변수도
// 하나 줄어든다. 영상 예제도 EN 을 안 쓴다.
//
// **그 대가로 모터가 뜨겁다.** 안 돌 때도 코일에 전류가 계속 흐른다.
// 제품에서는 반드시 꽂아야 한다 - 급여하지 않을 때 전류를 끊으면
// 발열과 전력이 줄고, 손으로 로더를 돌려볼 수 있다.
//
// 네 드라이버가 **한 핀을 공용으로 쓴다.** 한 번에 하나만 사출하므로
// 따로 둘 이유가 없다. 선을 꽂으면 이 값을 그 핀 번호로 바꾼다.
const int EN_PIN = -1;

const int EN_ACTIVE   = LOW;
const int EN_INACTIVE = HIGH;

// ---------------------------------------------------------------------------
// 속도
//
// **스텝모터는 가속 없이 빠르게 시작하면 안 돈다.** 코일이 못 따라가서
// 제자리에서 떨거나 아예 안 움직인다. 느린 쪽부터 시험한다.
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

unsigned long pulseUs = 1600;    // 검증된 값에서 시작
int slot = 1;                    // 지금 고른 슬롯 (1-based)


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
 * 1.3초라 워치독(5초)에 한참 못 미친다.
 */
static inline void waitUs(unsigned long us) {
  delayMicroseconds(us);
}


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


static const Slot &cur() { return SLOTS[slot - 1]; }


void printMenu() {
  Serial.println();
  Serial.println("-----------------------------------------");
  Serial.printf("  슬롯 %d번 (%s)   속도 %lu us (%lu Hz)\n",
                slot, cur().name, pulseUs, 500000UL / pulseUs);
  Serial.println();
  Serial.println("  1 2 3 4  슬롯 고르고 한 바퀴");
  Serial.println("  b        같은 슬롯을 반대 방향으로 한 바퀴");
  Serial.println("  j        20 펄스만 (약 36도)");
  Serial.println("  s        속도 바꾸기 (느린 쪽부터 순환)");
  Serial.println("  a        속도별로 한 바퀴씩 — 어디서 도는지 찾기");
  Serial.println("  o        네 슬롯을 차례로 한 바퀴씩 — 배선 확인");
  Serial.println("  r        검증된 속도로 길게 연속 운전");
  Serial.println("  t        EN 만 켜고 끄기 (모터 잠김 확인)");
  Serial.println("  m        30초간 느리게 계속 펄스 (멀티미터 측정용)");
  Serial.println("  h        2분간 EN 만 내려 유지 (코일 전압 측정용)");
  Serial.println("-----------------------------------------");
  Serial.print("> ");
}


void setup() {
  Serial.begin(115200);
  delay(300);

  // **모든 슬롯을 LOW 로 내려둔다.** 안 쓰는 슬롯의 STEP 이 떠 있으면
  // 노이즈가 펄스로 읽혀 엉뚱한 모터가 조금씩 움직인다.
  for (int i = 0; i < N_SLOTS; i++) {
    pinMode(SLOTS[i].step, OUTPUT);
    pinMode(SLOTS[i].dir, OUTPUT);
    digitalWrite(SLOTS[i].step, LOW);
    digitalWrite(SLOTS[i].dir, LOW);
  }
  enable(false);                       // 켜자마자 돌면 안 된다

  Serial.println();
  Serial.println("=========================================");
  Serial.println(" pebble 모터 진단 — 슬롯 4개");
  Serial.println("=========================================");
  for (int i = 0; i < N_SLOTS; i++) {
    Serial.printf("  %d번  STEP GPIO%-2d  DIR GPIO%-2d   %s\n",
                  i + 1, SLOTS[i].step, SLOTS[i].dir, SLOTS[i].name);
  }
  Serial.printf("  EN   GPIO%d", EN_PIN);
  if (EN_PIN < 0) Serial.print("   (안 꽂음 - 항상 켜져 있어 뜨겁다)");
  Serial.println();
  Serial.println();
  Serial.println("  12V 가 켜져 있어야 합니다.");
  Serial.println("  모터를 고정하고 로더는 떼고 하세요.");
  printMenu();
}


/** 고른 슬롯을 지정한 펄스 수만큼 돌린다. */
void turn(int steps, bool reverse, unsigned long us) {
  const Slot &s = cur();
  digitalWrite(s.dir, reverse ? HIGH : LOW);
  enable(true);
  delay(10);                      // 드라이버가 깨어날 시간

  unsigned long t0 = millis();
  for (int i = 0; i < steps; i++) {
    digitalWrite(s.step, HIGH);
    waitUs(us);
    digitalWrite(s.step, LOW);
    waitUs(LOW_US);
  }
  unsigned long dt = millis() - t0;

  enable(false);
  Serial.printf("    %d번 %s  %d 펄스 / %lu us / %lu ms\n",
                slot, s.name, steps, us, dt);
}


/**
 * **네 슬롯을 차례로 한 바퀴씩 돌린다.** 배선을 확인하는 모드다.
 *
 * 꽂고 나서 제일 먼저 할 일이 이것이다. 하나씩 눌러보면 "아까 그게
 * 몇 번이었나" 를 사람이 기억해야 하는데, 여기서는 번호를 찍고 돌리니
 * **도는 모터와 번호가 맞는지** 눈으로 바로 확인된다.
 *
 * 안 도는 슬롯이 있으면 그 슬롯만 틀린 것이다 - 다른 셋이 돌았다면
 * 12V·VREF·공통 접지는 이미 멀쩡하므로 그 드라이버와 두 선만 본다.
 */
void allSlots() {
  int keep = slot;
  Serial.println();
  Serial.println("  1번부터 4번까지 한 바퀴씩 돌립니다. 어느 게 도는지 보세요.");
  for (int i = 1; i <= N_SLOTS; i++) {
    slot = i;
    Serial.printf("\n  [%d/%d] %d번 %s  (STEP GPIO%d, DIR GPIO%d)\n",
                  i, N_SLOTS, i, cur().name, cur().step, cur().dir);
    turn(STEPS_PER_REV, false, pulseUs);
    delay(900);                   // 눈으로 구분할 틈
  }
  slot = keep;
  Serial.println();
  Serial.println("  번호와 실제로 돈 모터가 다르면 STEP·DIR 선이 바뀐 것입니다.");
  Serial.println("  **코드를 고치지 말고 선을 고치세요** - 서버가 보내는");
  Serial.println("  슬롯 번호가 로더 순서(1 오메가3/2 피부장벽/3 관절/4 장건강)와");
  Serial.println("  맞아야 엉뚱한 영양제가 안 나갑니다.");
}


/** 느린 쪽부터 한 바퀴씩 돌려 어느 속도까지 도는지 찾는다. */
void sweep() {
  Serial.println();
  Serial.printf("  %d번(%s)을 느린 쪽부터 한 바퀴씩 돌립니다.\n", slot, cur().name);
  for (int i = 0; i < N_SPEEDS; i++) {
    Serial.printf("\n  [%d/%d] %lu us (%lu Hz)\n",
                  i + 1, N_SPEEDS, SPEEDS_US[i], 500000UL / SPEEDS_US[i]);
    turn(STEPS_PER_REV, false, SPEEDS_US[i]);
    delay(700);
  }
  Serial.println();
  Serial.println("  어느 속도까지 돌았는지 기억해 두세요.");
  Serial.println("  하나도 안 돌았다면 전기 쪽입니다:");
  Serial.println("    1. 12V 어댑터가 켜져 있나");
  Serial.println("    2. VREF 가 0.15V 인가");
  Serial.println("    3. RESET·SLEEP 이 3V3 에 붙어 있나   <- 제일 흔함");
  Serial.println("    4. ESP32 GND 와 드라이버 GND 가 이어져 있나");
  Serial.println("  떨기만 한다면 코일 쌍이 어긋난 겁니다 (A1/A2, B1/B2).");
}


/**
 * **참조 예제와 똑같은 조건으로** 길게 돌린다.
 *
 * 속도를 바꿔가며 한 바퀴씩 돌리는 `a` 는 거칠게 느껴진다. 느린 풀스텝은
 * 원래 1.8도씩 끊어 움직여 덜컹거리고, 너무 빠른 쪽은 탈조한다. 그래서
 * "내 코드가 거친가" 를 판단할 수 없다.
 *
 * 참조 예제는 1600/100 한 속도로 4바퀴씩 계속 돌았고 매끄러웠다.
 * 같은 조건을 떼어내 비교한다.
 */
void runLikeReference() {
  const unsigned long HIGH_US = 1600;   // 검증된 값
  const int REVS = 4;
  const Slot &s = cur();

  Serial.println();
  Serial.printf("  %d번(%s)을 HIGH 1600us / LOW 100us 로 연속 운전합니다.\n",
                slot, s.name);
  Serial.println("  4바퀴 정방향 -> 0.5초 -> 4바퀴 역방향, 3번 반복.");

  enable(true);
  for (int round = 0; round < 3; round++) {
    for (int dir = 0; dir < 2; dir++) {
      digitalWrite(s.dir, dir ? HIGH : LOW);
      Serial.printf("    %d/6\n", round * 2 + dir + 1);
      for (int i = 0; i < STEPS_PER_REV * REVS; i++) {
        digitalWrite(s.step, HIGH);
        waitUs(HIGH_US);
        digitalWrite(s.step, LOW);
        waitUs(LOW_US);
      }
      delay(500);
    }
  }
  enable(false);
  Serial.println("  끝났습니다.");
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
  if (EN_PIN < 0) {
    Serial.println("  EN 이 안 꽂혀 있습니다. 드라이버가 항상 켜져 있어");
    Serial.println("  끌 방법이 없습니다. 지금도 축은 뻑뻑할 겁니다.");
    return;
  }
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
 * 30초간 느리게 계속 펄스를 보낸다. **멀티미터로 재보기 위한 모드다.**
 *
 * 모터가 안 돌 때, 신호가 정말 드라이버 핀까지 가는지를 눈이 아니라
 * 숫자로 확인해야 한다. 듀티 50% 면 DC 전압계에 평균값이 뜬다.
 *
 *     STEP  약 1.6V   (3.3V 의 절반)  <- 펄스가 오고 있다
 *     STEP  0V 또는 3.3V             <- 신호가 안 온다. 배선·핀 확인
 *     EN    약 0V                     <- 활성화됨 (LOW 가 활성)
 *     RST   3.3V, SLP 3.3V            <- 깨어 있다. 0V 면 드라이버가 잠김
 *
 * **여기서는 LOW 도 길게 준다.** 돌리는 게 목적이 아니라 평균 전압을
 * 읽는 게 목적이라 듀티가 50% 여야 한다. LOW 를 100us 로 두면 평균이
 * 3.2V 로 나와서 "항상 HIGH" 와 구분이 안 된다.
 */
void measureMode() {
  const Slot &s = cur();
  Serial.println();
  Serial.printf("  %d번(%s)에 30초간 펄스를 보냅니다. 드라이버 핀에서 재보세요.\n",
                slot, s.name);
  Serial.println();
  Serial.printf("    STEP (GPIO%d)  약 1.6V 여야 합니다 (3.3V 의 절반)\n", s.step);
  Serial.println("    EN            약 0V   여야 합니다 (LOW 가 활성)");
  Serial.println("    RST           3.3V, SLP 3.3V 여야 합니다");
  Serial.println();
  Serial.println("  검정 프로브는 드라이버의 GND 에 둡니다.");

  digitalWrite(s.dir, LOW);
  enable(true);

  unsigned long end = millis() + 30000;
  int left = 30;
  while (millis() < end) {
    digitalWrite(s.step, HIGH);
    waitUs(6400);
    digitalWrite(s.step, LOW);
    waitUs(6400);
    yield();                      // 30초짜리 모드다. 여기서는 양보해야 한다
    int now = (int)((end - millis()) / 1000) + 1;
    if (now != left) { left = now; Serial.printf("    %d\n", left); }
  }

  enable(false);
  Serial.println("  끝났습니다.");
}


/**
 * 2분간 EN 만 내리고 **펄스는 보내지 않는다.**
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

  digitalWrite(cur().step, LOW);
  enable(true);
  // 2분. **그동안 가변저항을 돌리며 전압이 올라가는지 본다.**
  for (int s = 120; s > 0; s--) {
    if (s % 10 == 0 || s <= 5) Serial.printf("    %d\n", s);
    delay(1000);
  }
  enable(false);
  Serial.println("  끝났습니다.");
}


void loop() {
  if (!Serial.available()) return;

  char c = Serial.read();
  if (c == '\n' || c == '\r') return;
  Serial.println(c);

  // 숫자는 슬롯 번호다. 고르고 바로 한 바퀴 돌린다.
  if (c >= '1' && c <= '0' + N_SLOTS) {
    slot = c - '0';
    turn(STEPS_PER_REV, false, pulseUs);
    printMenu();
    return;
  }

  switch (c) {
    case 'b': turn(STEPS_PER_REV, true,  pulseUs); break;
    case 'j': turn(20,            false, pulseUs); break;
    case 'a': sweep(); break;
    case 'o': allSlots(); break;
    case 'r': runLikeReference(); break;
    case 't': toggleEnable(); break;
    case 'm': measureMode(); break;
    case 'h': holdMode(); break;
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
