# Pet Wellness Digital Twin — V1 Architecture

## 0. 목적

이 문서는 기존 `pebble` 프로토타입을 기반으로, 캡스톤디자인 V1에서 실제 구현할 범위를 정의한다.

핵심 목표는 다음과 같다.

> **강아지는 자신의 불편함을 말로 설명할 수 없다.  
> 따라서 AI 목줄과 스마트 급식기가 행동·수면·섭취 변화를 관찰하고, Pet Digital Twin이 각 강아지의 평소와 비교해 현재 웰니스 상태를 이해한다.  
> 필요한 경우 AI가 보호자에게 추가 맥락을 질문하고, 수의영양학적 안전 규칙 안에서 개인화된 식사와 보조영양 계획을 생성·검증한 뒤 자동 배식한다.**

---

# 1. 프로젝트 포지셔닝

## 1.1 프로젝트명

**Pet Wellness Digital Twin 기반 AI Personalized Nutrition System**

## 1.2 한 줄 정의

**온디바이스 AI 스마트 목줄 + Pet Wellness Digital Twin + AI Wellness Agent + 개인화 영양 자동급식기**

## 1.3 해결하려는 문제

기존 스마트 급식기:

```text
정해진 시간
    ↓
정해진 양
    ↓
배식
```

본 프로젝트:

```text
강아지 행동
+
수면 추정
+
실제 섭취량
+
개체 프로필
+
개인 baseline
+
보호자 맥락
    ↓
현재 웰니스 상태 이해
    ↓
영양 관리 후보 생성
    ↓
Safety Rule Engine
    ↓
Digital Twin 사전검증
    ↓
자동 배식
```

---

# 2. 핵심 원칙

## 2.1 진단하지 않는다

다음 표현은 사용하지 않는다.

- 피부염 진단
- 외이도염 진단
- 관절염 진단
- 질병 처방

대신:

- 긁기 행동이 평소보다 증가
- 머리 흔들기 행동이 개인 baseline보다 증가
- 추정 수면시간 감소
- 활동량 감소
- 실제 섭취율 감소

처럼 **웰니스 변화**를 표현한다.

## 2.2 다른 강아지와 비교하지 않는다

핵심 질문은:

> **“이 강아지가 다른 강아지와 다른가?”**

가 아니라:

> **“오늘의 이 강아지가 평소의 이 강아지와 다른가?”**

이다.

## 2.3 AI에게 최종 급여 권한을 주지 않는다

AI는 다음을 수행한다.

- 행동 인식
- 상태 해석
- 추가 질문 생성
- 영양 전략 후보 생성

실제 급여 가능 여부는 **Safety Rule Engine**이 결정한다.

---

# 3. 전체 구조

```text
                         [ 실제 강아지 ]
                               │
              ┌────────────────┴────────────────┐
              │                                 │
              ▼                                 ▼
    ┌───────────────────┐             ┌────────────────────┐
    │   AI Smart Collar │             │   Smart Feeder     │
    │                   │             │                    │
    │ XIAO nRF52840     │             │ ESP32 / Gateway    │
    │ 6-axis IMU        │             │ Food Dispenser     │
    │ TinyML            │             │ Supplement x 4     │
    └─────────┬─────────┘             │ Load Cell          │
              │                       └─────────┬──────────┘
              │ BLE                             │ Wi-Fi
              └──────────────┬──────────────────┘
                             ▼
                    ┌─────────────────┐
                    │      Cloud      │
                    │                 │
                    │ Pet Digital Twin│
                    │ Wellness Engine │
                    │ AI Agent        │
                    │ Nutrition Engine│
                    │ Safety Engine   │
                    └────────┬────────┘
                             │
                   ┌─────────┴─────────┐
                   ▼                   ▼
              Mobile App         Feeder Command
                                      │
                                      ▼
                              실제 사료/영양제 배식
```

---

# 4. 기존 프로토타입에서 유지할 구조

기존 구조는 최대한 유지한다.

```text
목줄
  ↓ BLE
밥통
  - BLE Gateway
  - Wi-Fi
  - Local Cache
  - Dispenser
  ↓ HTTPS
Cloud
  ↓
App
```

유지해야 하는 설계:

- 목줄은 저전력 장치
- RAW IMU를 지속적으로 서버로 보내지 않음
- 목줄은 행동 분류 결과 중심으로 전송
- 밥통이 BLE Gateway 역할
- 밥통이 Wi-Fi를 통해 Cloud와 통신
- 서버 장애 시 마지막 정상 급여 계획을 밥통에 캐시
- 중복 이벤트 방지
- BLE 끊김 시 로컬 버퍼링
- confidence 저장
- 처방/배식 이력 append-only
- `algo_version` 저장
- 급여 명령 idempotency 보장
- 만료된 급여 명령 실행 금지

---

# 5. V1 범위

## 반드시 구현

1. 목줄 Activity TinyML
2. 목줄 Scratch / Head Shake Event Detector
3. 밥통 Gateway
4. 사료 + 영양제 4슬롯 배식
5. Load Cell 기반 실제 섭취량 측정
6. Pet Digital Twin
7. 개인 baseline
8. Wellness State
9. 보호자 질문 Agent
10. Nutrition Candidate 생성
11. Safety Rule Engine
12. Digital Twin Simulation
13. 실제 배식

## 선택 구현

- Active Learning
- SmartThings
- Self-Lick
- Rubbing
- 8슬롯 전체 구현
- 비지도 이상탐지 모델

---

# 6. 스마트 목줄 V1

## 6.1 하드웨어

추천:

**Seeed Studio XIAO nRF52840 Sense**

주요 이유:

- 소형
- BLE
- nRF52840
- 6축 IMU 내장
- TinyML 실행 가능
- 별도 PCB 없이 프로토타입 가능

V1은 **IMU 기반 행동 인식에 집중**한다.

심박/HRV/PVDF 등 추가 생체센서는 V1에서 제외한다.

---

# 7. On-device AI 구조

목줄의 AI는 하나의 거대한 모델보다 두 역할로 나눈다.

## 7.1 Model A — Activity Classifier

분류:

```text
REST
WALK
RUN
VIGOROUS
```

의미:

- REST: 휴식 / 비활동
- WALK: 걷기
- RUN: 뛰기 / trot
- VIGOROUS: 고강도 활동 / play

사용 목적:

- Activity Load
- Energy OUT
- Mobility Trend
- Estimated Sleep 계산 기초

## 7.2 Model B — Health Behavior Event Detector

분류:

```text
SCRATCH
HEAD_SHAKE
OTHER
```

사용 목적:

- SCRATCH → 피부 관련 행동 변화
- HEAD_SHAKE → 귀 관련 행동 변화
- OTHER → 오탐 방지

Scratch / Head Shake는 짧은 이벤트이므로 Activity Classifier와 분리한다.

---

# 8. Estimated Sleep

수면은 단일 TinyML 클래스로 직접 판단하지 않는다.

서버가 다음을 종합한다.

```text
야간 시간대
+
장시간 REST
+
활동 감소
+
중간 움직임 이벤트
    ↓
Estimated Sleep
```

계산:

- Estimated Sleep Time
- Longest Rest/Sleep Period
- Night Interruption Count
- Sleep Regularity

제품 표현:

- `Estimated Sleep`
- `Probable Sleep`

사용 금지:

- “실제 수면시간”
- “수면장애 진단”

---

# 9. 목줄 → Gateway 전송 데이터

RAW IMU 전체를 보내지 않는다.

Event 예시:

```json
{
  "seq": 125,
  "boot_id": 3,
  "t_ms": 1234567,
  "event_type": "scratch",
  "confidence": 0.87,
  "duration_ms": 1800
}
```

Activity summary 예시:

```json
{
  "t_ms": 1234567,
  "window_sec": 60,
  "rest_sec": 35,
  "walk_sec": 15,
  "run_sec": 5,
  "vigorous_sec": 5,
  "worn_sec": 60,
  "battery": 81
}
```

필수 필드:

- seq
- boot_id
- t_ms
- type/class
- confidence
- duration
- worn_sec
- battery
- activity summary

---

# 10. 행동 모델 학습 전략

## 10.1 공개 데이터 사용

공개 Dog IMU Dataset으로 우선:

- REST
- WALK
- RUN
- VIGOROUS

를 학습한다.

라벨 매핑 예:

```text
Sitting  ─┐
Standing ─┼─→ REST
Lying    ─┘

Walking ─────→ WALK

Trotting ────→ RUN

Playing ─────→ VIGOROUS
```

## 10.2 자체 데이터 수집

공개 RAW 데이터가 부족한:

- SCRATCH
- HEAD_SHAKE

는 직접 수집한다.

추천:

- 약 8~15마리
- 소형견 비중 높게
- 중형견/대형견 일부 포함

수집 방식:

```text
XIAO IMU
+
스마트폰 영상
```

영상 timestamp로 ground truth 라벨을 만든다.

질환견일 필요는 없다.

학습 목표:

```text
피부염 학습 X
외이도염 학습 X

긁는 행동 학습 O
머리 흔드는 행동 학습 O
```

---

# 11. Train / Validation / Test 원칙

같은 강아지가 Train과 Test에 동시에 들어가면 안 된다.

권장:

```text
Train
Dog 1 ~ 35

Validation
Dog 36 ~ 40

Test
Dog 41 ~ 45
```

또는:

**Leave-One-Dog-Out Cross Validation**

목표:

> 처음 보는 강아지에서도 일반화되는가?

---

# 12. Smart Feeder V1

## 역할

- BLE Gateway
- Wi-Fi Gateway
- 사료 정량 배식
- 영양제 정량 배식
- Local Cache
- Load Cell 기반 실제 섭취량 측정

## 영양제 슬롯

하드웨어 설계:

**최대 8슬롯 확장 가능**

V1 실제 구현:

**4슬롯**

---

# 13. Load Cell

밥그릇 아래 Load Cell 설치.

측정:

- 배식량
- 잔량
- 실제 섭취량
- 섭취율
- 식사 시작 시각
- 식사 종료 시각
- 식사시간
- 섭취속도
- 식사를 건너뜀

예:

```text
배식량 = 100g
식후 잔량 = 18g

실제 섭취량 = 82g
섭취율 = 82%
```

핵심 개념:

```text
목줄 = Energy OUT
급식기 = Energy IN
```

---

# 14. Pet Wellness Digital Twin

## 14.1 정의

V1 Digital Twin은 의료/생리학적 Digital Twin이 아니다.

정의:

> **목줄 행동 데이터, 실제 섭취 데이터, 개체 프로필, 개인 baseline, 영양 이력을 지속적으로 반영해 각 강아지의 현재 웰니스 상태를 서버에 유지하는 Behavioral & Nutritional Digital Twin**

Digital Twin의 핵심은 단순 DB 저장이 아니라:

1. 현실 강아지와 지속 동기화
2. 현재 상태 유지
3. 개인 baseline과 비교
4. 실제 적용 전 Nutrition Plan 가상 검증
5. 실제 섭취 결과를 다시 반영

이다.

---

# 15. DogTwin 데이터 모델

```text
DogTwin

profile
 ├─ dog_id
 ├─ breed
 ├─ age
 ├─ sex
 ├─ neutered
 ├─ weight
 ├─ target_weight
 └─ BCS

baseline
 ├─ rest
 ├─ walk
 ├─ run
 ├─ vigorous
 ├─ scratch
 ├─ head_shake
 ├─ estimated_sleep
 └─ intake

current_state
 ├─ activity
 ├─ mobility
 ├─ skin
 ├─ ear
 ├─ sleep_recovery
 └─ appetite

nutrition
 ├─ food
 ├─ kcal_per_g
 ├─ supplements
 ├─ intake_history
 └─ prescription_history

context
 ├─ guardian_answers
 ├─ recent_food_change
 ├─ recent_shampoo_change
 └─ notes

safety
 ├─ allergies
 ├─ medications
 ├─ contraindications
 └─ nutrient_limits
```

---

# 16. Digital Twin 핵심 상태 6개

## 16.1 Activity Load

입력:

- Walk
- Run
- Vigorous

사용:

- 당일 활동 강도
- 사료량 미세조정

## 16.2 Mobility Trend

입력:

- Walk 감소
- Run 감소
- Vigorous 감소
- Rest 증가

사용:

- 장기 이동성 변화 추적
- 영양 지원 후보 판단

## 16.3 Skin Behavior Index

입력:

- Scratch
- Night Scratch
- Sleep disruption

향후:

- Self-Lick
- Rubbing

표현:

- “피부 관련 행동 변화”

금지:

- “피부염”

## 16.4 Ear Behavior Index

입력:

- Head Shake

향후:

- Ear Scratch
- Ear Rub

기본 액션:

- 알림
- 보호자 추가 질문

영양제 자동 변경 권한 없음.

## 16.5 Sleep & Recovery

입력:

- Estimated Sleep
- Night interruptions
- Rest
- Activity pattern

## 16.6 Appetite & Intake

입력:

- 실제 섭취량
- 섭취율
- 식사시간
- 섭취속도

---

# 17. 개인 baseline

강아지마다 TinyML 모델 전체를 다시 학습하지 않는다.

공통 행동모델:

```text
Universal Behavior Model
```

서버 개인화:

```text
0~7일
Provisional Baseline

7~14일
Intermediate Baseline

14~30일
Maturing Baseline

30일 이후
Mature Baseline
```

초기에는 Supplement 자동조정을 제한한다.

---

# 18. Wellness Engine

역할:

> **“이 강아지의 현재 상태가 평소와 어떻게 다른가?”**

V1에서 복잡한 딥러닝은 필수가 아니다.

우선 구현:

- rolling median
- robust z-score
- MAD
- persistence
- hysteresis
- rule-based fusion

출력 예:

```text
Activity    NORMAL
Mobility    NORMAL
Skin        ATTENTION
Ear         NORMAL
Sleep       SLIGHT_CHANGE
Appetite    NORMAL
```

Optional:

- Isolation Forest
- One-Class model
- 작은 Autoencoder

---

# 19. AI Wellness Agent

강아지는 대화할 수 없으므로 AI가 보호자에게 필요한 질문을 한다.

예:

```text
Scratch +170%
Sleep -18%
Head Shake normal
Appetite normal
```

Agent 질문:

```text
최근 샴푸나 목욕 제품을 변경했나요?
최근 사료나 간식을 변경했나요?
피부가 붉거나 털이 빠진 부분이 보이나요?
```

보호자 답변 예:

```text
3일 전에 샴푸 변경
```

저장:

```text
DogTwin.context
```

원칙:

> 센서가 이미 아는 것은 묻지 않고, 센서만으로 알 수 없는 맥락만 질문한다.

---

# 20. Active Learning — Optional

confidence가 애매한 이벤트에 대해서만 보호자에게 질문한다.

예:

```text
Scratch    0.44
HeadShake  0.08
Other      0.48
```

앱:

> 오늘 21:34경 반복적인 움직임이 감지됐어요. 당시 몽이가 몸을 긁고 있었나요?

선택:

```text
[맞아요]
[아니에요]
[모르겠어요]
```

이 응답은 향후 모델 개선용 개인 calibration data로 저장한다.

V1 필수는 아니다.

---

# 21. Nutrition Engine

역할:

> 현재 상태에서 가능한 영양 관리 전략 후보를 생성한다.

입력:

```text
DogTwin
+
Dog Profile
+
Food Information
+
Supplement History
+
Guardian Context
```

출력:

```text
NutritionCandidate
```

예:

```text
Food: 105g
Omega-3: 1 unit
Fiber: 1 unit
```

AI 또는 알고리즘은 후보만 만든다.

최종 허용 여부는 Safety Rule Engine이 결정한다.

---

# 22. V1 영양제 4슬롯

권장:

1. **Omega-3 EPA/DHA**
2. **Joint Support**
3. **Prebiotic / Fiber**
4. **Calm Support**

5~8번:

- Future Expansion

---

# 23. 상태 → 영양 연결 원칙

## Activity 증가

```text
Walk / Run / Vigorous 증가
```

주 액션:

- 사료량 / Energy Budget 조정

영양제 자동증량 X

## Skin Behavior 증가

```text
Scratch 증가
+
Night Scratch 증가
+
Sleep 감소
```

주 액션:

- Skin Wellness 변화 표시
- 장기 지속 시 Omega-3/EFA 전략 후보

하루 증가만으로 자동 증량 X

## Mobility 감소

```text
Walk 감소
Run 감소
Vigorous 감소
Rest 증가
```

주 액션:

- 장기 Mobility Trend
- Omega-3 / Joint Support 후보

하루 변화만으로 자동 투여 X

## Ear Behavior 증가

```text
Head Shake 증가
```

주 액션:

- 영양제 자동 변경 X
- 보호자 알림
- 추가 질문

## Sleep 저하

먼저:

- Scratch 증가?
- Head Shake 증가?

를 확인한다.

다른 원인이 없고 사용자가 Calm 관리 목표를 선택한 경우에만 Calm Support 후보.

## Appetite 급락

```text
평소 섭취율 95%
오늘 40%
```

주 액션:

- 영양제 변경 X
- 보호자 알림 우선

---

# 24. Safety Rule Engine

Nutrition Engine보다 우선한다.

권장 순서:

```text
1. Acute / Emergency Gate
2. State Inference
3. Food Amount Calculation
4. Nutrition Candidate Generation
5. Allergy Check
6. Contraindication Check
7. Existing Food Contribution Check
8. Existing Supplement Check
9. Nutrient Safety Limit Check
10. Dispensing Quantization
11. Final Validation
```

`DENY`면 실제 배식하지 않는다.

---

# 25. Digital Twin Simulation

Nutrition Candidate를 바로 배식하지 않는다.

먼저 Digital Twin에 가상 적용한다.

예:

```text
Candidate

Food       105g
Omega-3    1 unit
Fiber      1 unit
```

Simulation:

```text
Total calories       PASS
Existing food        PASS
Duplicate nutrients  PASS
Allergy              PASS
Contraindication     PASS
Configured limits    PASS
```

통과:

```text
SIMULATION PASSED
```

→ 실제 배식

실패:

```text
SIMULATION FAILED
Reason: ...
```

→ 배식 차단 / 후보 재생성

---

# 26. Closed Loop

```text
실제 강아지
    ↓
AI 목줄
    ↓
Pet Digital Twin
    ↓
Wellness 분석
    ↓
AI Guardian Agent
    ↓
Nutrition Candidate
    ↓
Safety Rule Engine
    ↓
Digital Twin Simulation
    ↓
Smart Feeder
    ↓
Load Cell
    ↓
실제 섭취량
    ↓
Pet Digital Twin Update
```

이 Closed Loop가 프로젝트의 핵심이다.

---

# 27. 시간축

## Fast Loop — Daily

입력:

- Activity
- Intake
- Weight / BCS

출력:

- 사료량 미세조정

## Slow Loop — 7~30일

입력:

- Skin Trend
- Mobility Trend
- Sleep Trend
- Weight Trend

출력:

- Supplement Plan

## Safety Loop — Immediate

예:

- Appetite 급락
- Head Shake 급증
- 행동 전체 급변
- 착용률 낮음
- 분류 confidence 낮음

출력:

```text
Supplement change blocked
+
Guardian notification
```

---

# 28. 서버 권장 구조

기존 구조를 최대한 유지한다.

```text
petcare/
├─ core/
│  ├─ telemetry.py
│  ├─ aggregate.py
│  ├─ signal.py
│  ├─ activity.py
│  ├─ behavior_events.py
│  ├─ sleep.py
│  ├─ baseline.py
│  ├─ wellness.py
│  ├─ energy.py
│  ├─ nutrition.py
│  ├─ safety.py
│  ├─ twin.py
│  ├─ simulation.py
│  ├─ dispense.py
│  ├─ prescribe.py
│  ├─ constants.py
│  └─ models.py
│
├─ agent/
│  ├─ wellness_agent.py
│  ├─ question_policy.py
│  └─ context.py
│
├─ store/
│  └─ db.py
│
├─ jobs/
│  └─ pipeline.py
│
├─ api/
│  ├─ main.py
│  ├─ ingest.py
│  ├─ twin.py
│  └─ agent.py
│
├─ mock/
│  └─ generator.py
│
└─ tests/
```

---

# 29. 기존 서버에 추가할 핵심 모듈

## `core/twin.py`

역할:

- DogTwin 생성
- 최신 state 반영
- baseline 반영
- nutrition/context 반영

## `core/wellness.py`

역할:

- Activity
- Mobility
- Skin
- Ear
- Sleep
- Appetite

6개 state 계산

## `core/simulation.py`

역할:

Nutrition Candidate를 실제 배식 전에 가상 적용.

검사:

- kcal
- duplicate
- allergies
- contraindications
- nutrient limits
- current supplements

## `agent/wellness_agent.py`

역할:

- 상태 변화 설명
- 추가 질문 생성
- 보호자 답변 context 저장

---

# 30. 데이터 모델 추가

기존 유지:

```text
dogs
collars
events
wear
daily_metrics
prescriptions
dispense_commands
```

추가 추천:

```text
dog_twin_state
guardian_context
meal_intake
supplement_catalog
food_catalog
simulation_runs
agent_questions
agent_answers
```

---

# 31. SmartThings

V1에서는 제외한다.

핵심 기능은 SmartThings 없이 완전히 동작해야 한다.

다만 확장 인터페이스는 남긴다.

```text
Pet Digital Twin
    │
    ├─ Mobile App
    ├─ Smart Feeder
    ├─ AI Agent
    └─ External Integration API
              │
              └─ Future: SmartThings
```

향후 SmartThings 입력:

- 실내 온도
- 습도
- Home/Away
- 관련 가전 상태

향후 역할:

> **Pet Digital Twin의 Home Context + Home Action Layer**

---

# 32. V1에서 하지 않을 것

- 질병 진단 AI
- 피부염 진단
- 외이도염 진단
- 관절염 진단
- 강아지마다 TinyML 전체 재학습
- 심박/HRV 센서
- 의료 Digital Twin
- 생리학적 장기 시뮬레이션
- 영양제 8슬롯 전체 구현
- LLM의 직접 영양제 투여량 결정
- SmartThings 필수 의존
- 영양제 하루 단위 과도한 조정
- 수의사 진단을 대체하는 표현

---

# 33. V1 개발 순서

## Phase 1 — 기존 서버 유지 + 구조 정리

추가:

- `twin.py`
- `wellness.py`
- `simulation.py`

## Phase 2 — Activity TinyML

오픈 데이터로:

```text
REST
WALK
RUN
VIGOROUS
```

학습 및 XIAO 배포.

## Phase 3 — Event TinyML

자체 데이터로:

```text
SCRATCH
HEAD_SHAKE
OTHER
```

수집 및 학습.

## Phase 4 — Feeder + Load Cell

구현:

- 사료 배식
- 영양제 4슬롯
- Load Cell
- 실제 섭취량

## Phase 5 — Pet Digital Twin

대시보드:

```text
Activity
Mobility
Skin
Ear
Sleep
Appetite
```

## Phase 6 — AI Wellness Agent

이상 패턴 발생 시 보호자에게 추가 질문.

## Phase 7 — Nutrition + Safety + Simulation

```text
상태
 ↓
Nutrition Candidate
 ↓
Safety
 ↓
Digital Twin Simulation
 ↓
Dispense
```

## Phase 8 — Optional

- Active Learning
- SmartThings
- Self-Lick
- 추가 Supplement Slot

---

# 34. 데모 시나리오

심사 시 하나의 스토리로 시연한다.

```text
1. 정상 상태의 몽이 Digital Twin

2. Recorded / Mock Data로
   Scratch 증가
   Estimated Sleep 감소

3. Skin State → ATTENTION

4. AI Agent:
   "최근 샴푸를 변경했나요?"

5. 보호자 답변 입력

6. Nutrition Engine이 후보 생성

7. Safety Rule Engine 검증

8. Digital Twin Simulation

   ✓ kcal
   ✓ allergy
   ✓ duplicate
   ✓ configured limits

9. SIMULATION PASSED

10. 실제 Feeder에서
    사료 + 영양제 배식

11. Load Cell로 실제 섭취량 측정

12. Pet Digital Twin 업데이트
```

---

# 35. 심사에서 강조할 메시지

## 문제

> 강아지는 자신의 불편함을 말할 수 없다.

## 관찰

> 목줄 AI가 행동 변화를 24시간 관찰한다.

## 개인화

> 다른 개가 아니라 “이 강아지의 평소”와 비교한다.

## Agent

> 센서만으로 알 수 없는 맥락은 AI가 보호자에게 먼저 질문한다.

## Nutrition

> 현재 상태를 바탕으로 영양 전략 후보를 생성한다.

## Safety

> AI가 자유롭게 투여하지 않는다. Safety Rule Engine이 최종 허용권을 가진다.

## Digital Twin

> 실제 적용 전에 가상 상태에서 영양 계획을 먼저 검증한다.

## Closed Loop

> 관찰 → 이해 → 질문 → 영양 후보 → 안전검증 → 가상검증 → 실제배식 → 섭취확인 → 재관찰

---

# 36. Claude Code 구현 원칙

1. 기존 서버/API/DB 구조를 최대한 유지한다.
2. 기존 안전필터와 장애대응 로직을 삭제하지 않는다.
3. 새 기능은 독립 모듈로 추가한다.
4. Digital Twin은 `core/twin.py`로 분리한다.
5. Wellness State는 `core/wellness.py`에서 계산한다.
6. Simulation은 `core/simulation.py`로 분리한다.
7. Safety는 AI보다 항상 우선한다.
8. Agent의 자연어 답변을 실제 배식 로직에 직접 연결하지 않는다.
9. 흐름은 반드시 아래 순서를 따른다.

```text
Agent
→ structured context
→ Nutrition Engine
→ Safety Rule Engine
→ Digital Twin Simulation
→ Dispense
```

10. 모든 처방/시뮬레이션/배식 결과는 추적 가능해야 한다.
11. `algo_version`을 유지한다.
12. SmartThings는 V1 dependency로 만들지 않는다.
13. 하드웨어가 없는 동안 mock generator로 end-to-end 테스트 가능하게 유지한다.
14. 목줄 firmware가 완성되기 전까지 기존 mock data path를 깨지 않는다.
15. AI/LLM이 만든 텍스트와 실제 actuator command를 직접 연결하지 않는다.

---

# 37. 최종 프로젝트 문장

> **말할 수 없는 반려견의 행동·수면·섭취 변화를 AI가 지속적으로 관찰하고, Pet Wellness Digital Twin이 각 강아지의 평소와 비교해 현재 상태를 이해한다. 센서만으로 알 수 없는 정보는 AI가 보호자에게 질문해 보완하고, 수의영양학적 안전 규칙 안에서 개인화된 식사와 보조영양 계획을 가상 검증한 뒤 실제 급식기로 제공한다.**
