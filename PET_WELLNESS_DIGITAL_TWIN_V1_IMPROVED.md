# Pet Wellness Digital Twin — V1 개선안

## 0. 문서 목적

이 문서는 기존 `pebble` 프로토타입과 `PET_WELLNESS_DIGITAL_TWIN_V1.md`에 대한 리뷰를 반영한 **V1 개선안**이다.

목표는 다음 두 가지다.

1. 기존에 이미 구현된 서버·안전필터·게이트웨이·장애대응 구조를 최대한 유지한다.
2. Digital Twin, 온디바이스 AI, 체중 피드백, 실제 섭취량 측정을 추가하되 **한 학기 안에 구현 가능한 범위로 제한한다.**

---

# 1. 최종 프로젝트 정의

## 1.1 프로젝트명

**Pet Wellness Digital Twin 기반 AI Personalized Nutrition System**

## 1.2 한 문장

> **말할 수 없는 반려견의 행동·수면·섭취 변화를 AI가 지속적으로 관찰하고, 각 강아지의 평소와 비교해 현재 웰니스 상태를 이해한 뒤, 수의영양학적 안전 범위 안에서 식사와 보조영양을 개인화하는 폐루프 시스템.**

## 1.3 핵심 문제의식

강아지는 사람처럼 다음과 같이 말할 수 없다.

```text
"요즘 가려워요."
"귀가 불편해요."
"잠을 잘 못 자요."
"평소보다 피곤해요."
```

따라서 시스템이 대신 관찰한다.

```text
행동
+
수면 추정
+
실제 섭취량
+
체중 변화
+
보호자 맥락
    ↓
Pet Digital Twin
    ↓
현재 상태 이해
```

---

# 2. V1 핵심 철학

## 2.1 진단하지 않는다

시스템은 다음을 하지 않는다.

- 피부염 진단
- 외이도염 진단
- 관절염 진단
- 질병 치료 목적의 영양제 처방

대신 다음을 표현한다.

```text
Scratch 행동 증가
Head Shake 행동 증가
Estimated Sleep 감소
Mobility 감소
Appetite 감소
```

즉 **질병명 대신 웰니스 변화**를 다룬다.

---

## 2.2 다른 강아지가 아니라 자기 자신과 비교한다

기준:

```text
오늘의 몽이
vs
평소의 몽이
```

가 된다.

견종 평균은 보조 정보일 수 있지만,
이상 상태 판정의 핵심 기준은 **개인 baseline**이다.

---

## 2.3 AI는 후보를 만들고, Safety Rule Engine이 거부권을 가진다

```text
AI / Algorithm
    ↓
Nutrition Candidate
    ↓
Safety Rule Engine
    ↓
ALLOW / DENY
```

LLM이나 AI의 자연어 출력이 직접 모터 명령으로 연결되면 안 된다.

---

# 3. 전체 시스템 구조

```text
                       [ 실제 강아지 ]
                              │
             ┌────────────────┴─────────────────┐
             │                                  │
             ▼                                  ▼
   ┌────────────────────┐            ┌──────────────────────┐
   │   AI Smart Collar  │            │     Smart Feeder     │
   │                    │            │                      │
   │ XIAO nRF52840      │            │ ESP32 / Gateway      │
   │ 6-axis IMU         │            │ Food Dispenser       │
   │ TinyML             │            │ Supplement x 4       │
   │ Heuristic Events   │            │ Bowl Load Cell       │
   └─────────┬──────────┘            │ Optional Scale Input │
             │                       └──────────┬───────────┘
             │ BLE                              │ Wi-Fi
             └──────────────┬───────────────────┘
                            ▼
                   ┌─────────────────┐
                   │      Cloud      │
                   │                 │
                   │ Pet Digital Twin│
                   │ Wellness Engine │
                   │ AI Wellness Agent
                   │ Nutrition Engine│
                   │ Safety Engine   │
                   │ Forward Simulation
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

# 4. 기존 구현에서 유지할 부분

기존 시스템의 다음 구조는 유지한다.

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

반드시 유지:

- RAW IMU 지속 업로드 금지
- 목줄에서 행동 분류 후 결과 중심 전송
- 밥통이 BLE Gateway 역할
- 밥통이 Wi-Fi로 Cloud와 통신
- 서버 장애 시 마지막 정상 급여계획 캐시
- 이벤트 중복 방지
- `boot_id` / `seq` 사용
- BLE 끊김 시 버퍼링
- confidence 저장
- 처방 이력 append-only
- `algo_version` 저장
- 급여 명령 idempotency
- 급여 명령 expiration
- mock generator와 실제 수집 경로가 동일한 집계 코드를 사용

---

# 5. 개선안 핵심 변경점

기존 V1 문서에서 다음을 수정한다.

## 5.1 BODY_SHAKE 복원

`BODY_SHAKE`와 `HEAD_SHAKE`는 분리한다.

```text
BODY_SHAKE
= 몸 전체를 털듯 흔드는 행동

HEAD_SHAKE
= 머리/귀 중심의 빠른 흔들기
```

사용:

```text
BODY_SHAKE
    ↓
Skin Behavior 보조 신호

HEAD_SHAKE
    ↓
Ear Behavior 보조 신호
```

---

## 5.2 POSTURE_CHANGE 유지

기존 서버가 수면 추정과 야간 뒤척임 계산에 사용하므로 이벤트 계약은 유지한다.

다만 V1에서는 별도 TinyML 클래스로 만들지 않는다.

권장:

```text
REST 상태
+
중력 방향 변화
+
Gyroscope 회전량
        ↓
POSTURE_CHANGE heuristic
```

즉:

```text
Model A
→ REST / WALK / RUN / VIGOROUS

Model B
→ SCRATCH / HEAD_SHAKE / BODY_SHAKE / OTHER

Heuristic
→ POSTURE_CHANGE
```

---

## 5.3 체중 입력 복원

체중은 장기 영양 보정에 반드시 필요하다.

V1 기본:

```text
앱에서 수동 체중 입력
```

선택 확장:

```text
식판 앞 전체 체중 발판
```

데이터:

```text
weight_kg
measured_at
source
```

`source`:

```text
MANUAL
PLATFORM_SCALE
```

---

## 5.4 Digital Twin Simulation 재정의

Digital Twin Simulation은 Safety Rule Engine을 반복하지 않는다.

### Safety Rule Engine

질문:

> **오늘 이 계획을 실행해도 안전한가?**

검사:

- 알레르기
- 금기
- 기존 사료 기여량
- 기존 영양제
- 단일/일일 영양 한도
- 배식 정수화 이후 한도

### Digital Twin Forward Simulation

질문:

> **이 계획을 앞으로 일정 기간 유지하면 목표 방향과 맞는가?**

검사:

- 에너지 방향
- 체중 목표 방향
- 급여량 변화폭
- 누적 영양제 exposure
- 계획 안정성

---

# 6. 스마트 목줄 V1

## 6.1 하드웨어

**Seeed Studio XIAO nRF52840 Sense**

센서:

```text
Accelerometer
Ax Ay Az

Gyroscope
Gx Gy Gz
```

V1은 IMU 기반 행동 분석에 집중한다.

제외:

- 심박
- HRV
- PVDF
- 체온
- ECG
- 생리학적 센서

---

# 7. 온디바이스 AI 구조

## 7.1 Model A — Activity Classifier

분류:

```text
REST
WALK
RUN
VIGOROUS
```

사용:

- Activity Load
- Energy OUT
- Mobility Trend
- Estimated Sleep 기초

권장 window:

```text
1~2초
```

---

## 7.2 Model B — Event Detector

분류:

```text
SCRATCH
HEAD_SHAKE
BODY_SHAKE
OTHER
```

사용:

```text
SCRATCH
→ Skin Behavior

BODY_SHAKE
→ Skin Behavior 보조

HEAD_SHAKE
→ Ear Behavior
```

권장 window:

```text
0.5~1초
```

---

## 7.3 Heuristic Event

TinyML 모델 외에 IMU 기반 규칙으로 계산:

```text
POSTURE_CHANGE
```

초기 구현 예:

```text
REST 상태 유지 중

중력벡터 방향 변화
+
Gyro 회전량 임계 초과

→ POSTURE_CHANGE
```

정확한 threshold는 실측 데이터로 조정한다.

---

# 8. Estimated Sleep

`SLEEP` 클래스를 별도로 학습하지 않는다.

서버에서:

```text
야간
+
장시간 REST
+
POSTURE_CHANGE
+
중간 Activity Event
    ↓
Estimated Sleep
```

계산:

- Estimated Sleep Time
- Longest Rest/Sleep Period
- Night Interruption Count
- Restlessness

---

# 9. 행동 모델 데이터 전략

## 9.1 공개 데이터

우선:

```text
REST
WALK
RUN
VIGOROUS
```

에 활용한다.

라벨 매핑 예:

```text
Sitting  ─┐
Standing ─┼→ REST
Lying    ─┘

Walking → WALK

Trotting → RUN

Playing → VIGOROUS
```

---

## 9.2 자체 데이터

V1에서 자체 데이터 수집 범위는 축소한다.

우선:

```text
SCRATCH
HEAD_SHAKE
BODY_SHAKE
OTHER
```

Prototype feasibility를 확인한다.

권장:

```text
3~5마리 정도로 시작
```

시간이 허용되면 확대한다.

V1에서 다음을 주장하지 않는다.

```text
모든 견종에 완전 일반화
```

대신:

```text
목줄 IMU 기반 특이 행동 탐지 가능성을 프로토타입 환경에서 입증
```

을 목표로 한다.

---

# 10. Train / Validation / Test

같은 강아지가 Train/Test에 동시에 들어가면 안 된다.

```text
Train Dogs
Validation Dogs
Test Dogs
```

단위로 분리한다.

권장:

```text
Leave-One-Dog-Out
```

또는 Dog-ID split.

---

# 11. Smart Feeder

## 역할

- BLE Gateway
- Wi-Fi Gateway
- 사료 정량 배식
- 영양제 정량 배식
- Local Cache
- Bowl Load Cell
- 실제 섭취량 측정

---

# 12. Bowl Load Cell

측정:

```text
배식량
잔량
실제 섭취량
섭취율
식사 시작 시각
식사 종료 시각
식사시간
섭취속도
식사를 건너뜀
```

핵심:

```text
목줄
→ Energy OUT

밥통
→ Energy IN
```

Appetite 관련 지표는 IMU 추론보다 Load Cell 직접측정을 우선한다.

---

# 13. 체중 데이터

## 13.1 V1 필수

앱 수동입력.

예:

```text
9/01 4.30 kg
9/08 4.27 kg
9/15 4.24 kg
```

주 1회 정도를 권장한다.

## 13.2 Optional

식판 앞에 전체 체중 발판을 둔다.

```text
        식판
         🥣

  ┌───────────────┐
  │    체중 발판   │
  │   Load Cells   │
  └───────────────┘
```

정확도를 위해 가능한 경우 네 발이 모두 올라오도록 설계한다.

---

# 14. Pet Wellness Digital Twin

## 14.1 정의

본 프로젝트의 Digital Twin은 의료/생리학적 Twin이 아니다.

정의:

> **강아지의 행동, 수면 추정, 실제 섭취량, 체중, 영양 이력을 지속적으로 동기화하고, 현재 웰니스 상태를 유지하며, 새로운 영양계획을 실제 적용 전에 방향성 수준으로 forward simulation하는 Behavioral & Nutritional Digital Twin**

---

# 15. Digital Twin이 갖는 상태

## 15.1 Profile

```text
dog_id
breed
age
sex
neutered
weight
target_weight
BCS
```

## 15.2 Baseline

```text
rest
walk
run
vigorous
scratch
body_shake
head_shake
posture_change
estimated_sleep
intake
weight_trend
```

## 15.3 Current State

```text
activity_load
mobility
skin
ear
sleep_recovery
appetite
```

## 15.4 Nutrition

```text
food
kcal_per_g
supplements
intake_history
prescription_history
```

## 15.5 Guardian Context

```text
recent_food_change
recent_shampoo_change
recent_environment_change
guardian_answers
notes
```

---

# 16. Digital Twin 상태 구조 개선

`Activity Load`와 `Mobility Trend`는 완전히 독립된 축으로 보지 않는다.

## Daily Metric

```text
Activity Load
```

당일 에너지 계산에 사용.

## Wellness State 5개

```text
1. Mobility
2. Skin
3. Ear
4. Sleep / Recovery
5. Appetite
```

즉 대시보드 예:

```text
몽이 Digital Twin

Today's Activity   +18%

Wellness
────────────────
Mobility           NORMAL
Skin               ATTENTION
Ear                NORMAL
Sleep / Recovery   SLIGHT_CHANGE
Appetite           NORMAL
```

---

# 17. 개인 Baseline

권장:

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

초기 기간에는 영양제 자동조정을 제한한다.

---

# 18. Wellness Engine

우선 기존 방식 유지:

- rolling median
- MAD
- robust z-score
- persistence
- hysteresis
- weighted axis score

V1에서 별도 딥러닝 서버 모델은 필수가 아니다.

출력:

```text
Mobility
Skin
Ear
Sleep / Recovery
Appetite
```

---

# 19. Skin State

입력 후보:

```text
Night Scratch
BODY_SHAKE
Night Restlessness
Estimated Sleep disruption
```

예:

```text
Scratch 증가
+
Body Shake 증가
+
Night Restlessness 증가
        ↓
Skin ATTENTION
```

주의:

```text
Skin ATTENTION ≠ 피부염
```

---

# 20. Ear State

입력:

```text
HEAD_SHAKE
```

향후:

```text
Ear Scratch
Ear Rub
```

가능.

Ear State에는 **자동 영양 처방권을 주지 않는다.**

기본:

```text
Ear ATTENTION
    ↓
보호자 질문 / 알림
```

---

# 21. Mobility State

입력:

```text
Walk 감소
Run 감소
Vigorous 감소
Night Restlessness 증가
```

14일 이상 추세 중심으로 본다.

---

# 22. Sleep / Recovery State

입력:

```text
Estimated Sleep
Night Interruptions
POSTURE_CHANGE
Night Activity
```

피부/귀 상태가 함께 악화된 경우,
수면 단독 문제로 중복 처방하지 않는다.

---

# 23. Appetite State

입력:

```text
Bowl Load Cell
```

주요 값:

```text
intake_ratio
meal_duration
meal_skip
intake_speed
```

Appetite 급락 시:

```text
Supplement 자동 변경 금지
+
보호자 알림
```

---

# 24. AI Wellness Agent

역할:

> 센서가 직접 알 수 없는 맥락을 보호자에게 질문한다.

예:

```text
Skin ATTENTION
+
Sleep 감소
+
Ear 정상
+
Appetite 정상
```

질문:

```text
최근 샴푸나 목욕 제품을 변경했나요?
최근 사료나 간식을 변경했나요?
피부가 붉거나 털 빠짐이 보이나요?
```

보호자 답변은 `DogTwin.context`에 저장한다.

---

# 25. Agent 권한 제한

Agent는:

- 상태 설명
- 질문 생성
- 맥락 수집

만 수행한다.

금지:

```text
LLM 답변
→ 바로 배식
```

반드시:

```text
Agent
→ structured context
→ Nutrition Engine
→ Safety Rule Engine
→ Simulation
→ Dispense
```

순서를 지킨다.

---

# 26. Nutrition Engine

입력:

```text
DogTwin
+
Dog Profile
+
현재 사료
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

Nutrition Engine은 **후보 생성기**다.

---

# 27. 영양제 V1

하드웨어:

```text
최대 8슬롯 확장 가능
```

실제 V1:

```text
4슬롯
```

추천:

1. Omega-3 EPA/DHA
2. Joint Support
3. Prebiotic / Fiber
4. Calm Support

---

# 28. 상태 → 영양 연결

## Activity Load

```text
Activity 증가/감소
```

주 액션:

```text
사료량 / Energy Budget 조정
```

---

## Skin

장기:

```text
Scratch 증가
+
BODY_SHAKE 증가
+
Sleep disruption
```

주 액션:

```text
Skin Wellness 관리 후보
```

영양 후보:

```text
Omega-3 / EFA 계열
```

하루 이벤트로 즉시 변경 금지.

---

## Mobility

장기:

```text
Walk 감소
Run 감소
Vigorous 감소
```

후보:

```text
Omega-3
Joint Support
```

---

## Ear

```text
HEAD_SHAKE 증가
```

자동 영양 변경 금지.

```text
Alert
+
Guardian Question
```

---

## Sleep

수면 저하만으로 Calm Support를 자동 배식하지 않는다.

먼저:

```text
Skin?
Ear?
Activity?
```

원인을 확인한다.

---

## Appetite

급격한 섭취 감소:

```text
Nutrition change blocked
+
Alert
```

---

# 29. Safety Rule Engine

기존 Safety 로직을 **단일 source of truth**로 유지한다.

중복 구현하지 않는다.

권장 순서:

```text
1. Acute / Emergency Gate
2. State Inference
3. Food Amount Calculation
4. Nutrition Candidate Generation
5. Contraindication Check
6. Allergy Check
7. Existing Food Contribution
8. Existing Supplement Check
9. Nutrient Limit Check
10. Dispensing Quantization
11. Final Safety Validation
```

---

# 30. Digital Twin Forward Simulation

## 30.1 목적

Safety와 역할이 다르다.

Safety:

> 오늘 먹여도 되는가?

Simulation:

> 이 계획을 일정 기간 유지하면 목표 방향과 맞는가?

---

## 30.2 V1 Simulation Horizon

권장:

```text
14일
```

Optional:

```text
30일
```

---

## 30.3 Simulation 입력

```text
current_weight
target_weight
BCS
current_activity
recent_weight_trend
actual_intake
food_kcal_per_g
current_food_amount
candidate_food_amount
current_supplements
candidate_supplements
individual_energy_coefficient
```

---

## 30.4 Simulation 출력

정밀 숫자 예측보다 **방향성** 중심.

```text
energy_direction
weight_direction
supplement_exposure
plan_stability
```

예:

```text
14-Day Simulation

Energy Direction       MILD_DEFICIT
Weight Direction       TOWARD_TARGET
Omega-3 Exposure       ACCEPTABLE
Food Change            -4.5%
Plan Stability         STABLE
```

---

## 30.5 하지 않을 것

```text
"14일 뒤 정확히 4.213kg"
```

같은 가짜 정밀 예측은 하지 않는다.

대신:

```text
GAIN
MAINTAIN
LOSS

TOWARD_TARGET
AWAY_FROM_TARGET
STABLE
```

정도의 방향성을 사용한다.

---

# 31. Digital Twin Feedback

실제 결과:

```text
섭취량
체중
행동 변화
```

를 다시 Twin에 넣는다.

예:

```text
Simulation:
체중 유지 방향

Actual:
2주간 체중 증가

→ prediction error
→ individual energy coefficient 보정
```

이 feedback loop가 Digital Twin의 핵심 개인화다.

---

# 32. Closed Loop

```text
REAL DOG
   ↓
AI Collar
   ↓
Behavior Data
   ↓
Pet Digital Twin
   ↓
Wellness Engine
   ↓
AI Wellness Agent
   ↓
Guardian Context
   ↓
Nutrition Candidate
   ↓
Safety Rule Engine
   ↓
14-Day Forward Simulation
   ↓
Smart Feeder
   ↓
Bowl Load Cell
   ↓
Actual Intake
   ↓
Weight Input / Platform Scale
   ↓
Pet Digital Twin Update
```

---

# 33. 시간축

## Fast Loop — Daily

입력:

```text
Activity Load
Actual Intake
Weight / BCS
```

출력:

```text
사료량 미세조정
```

## Slow Loop — 7~30일

입력:

```text
Skin Trend
Mobility Trend
Sleep Trend
Weight Trend
```

출력:

```text
Supplement Plan
```

## Safety Loop — Immediate

예:

```text
Appetite 급락
Head Shake 급증
행동 전체 급변
착용률 부족
confidence 부족
```

출력:

```text
Supplement change blocked
+
Guardian notification
```

---

# 34. V1 구현 범위 축소

## 반드시 구현

### Collar
- REST / WALK / RUN / VIGOROUS
- SCRATCH / HEAD_SHAKE / BODY_SHAKE / OTHER
- POSTURE_CHANGE heuristic

### Feeder
- 사료 배식
- 영양제 4슬롯
- Bowl Load Cell
- BLE Gateway
- Wi-Fi
- Local Cache

### Cloud
- 개인 baseline
- Mobility / Skin / Ear / Sleep / Appetite
- DogTwin
- Nutrition Candidate
- Safety
- 14-day forward simulation

### App
- 상태 표시
- 체중 입력
- Guardian 질문/응답
- Simulation 결과 표시

---

## 선택 구현

- 식판 앞 자동 체중계
- Active Learning
- Self-Lick
- SmartThings
- 비지도 anomaly detection
- 8슬롯 전체 구현

---

# 35. SmartThings

V1에서는 제외.

단:

```text
External Integration API
```

를 남긴다.

향후:

```text
SmartThings
→ Temperature
→ Humidity
→ Home/Away
→ Home Device State
```

를 Pet Digital Twin의 Home Context로 연결 가능.

---

# 36. 서버 구조 개선

기존 `core/` 구조를 유지한다.

새 파일을 불필요하게 많이 만들지 않는다.

## 새로 추가할 핵심 파일

```text
core/twin.py
core/wellness.py
core/simulation.py
agent/wellness_agent.py
```

기존 기능은 기존 파일을 확장한다.

예:

```text
sleep
→ aggregate.py 유지

baseline
→ signal.py 유지

event aggregation
→ aggregate.py 유지
```

---

# 37. 권장 서버 구조

```text
petcare/
├─ core/
│  ├─ telemetry.py
│  ├─ aggregate.py
│  ├─ signal.py
│  ├─ inference.py
│  ├─ energy.py
│  ├─ nutrition.py
│  ├─ safety.py
│  ├─ twin.py
│  ├─ wellness.py
│  ├─ simulation.py
│  ├─ dispense.py
│  ├─ prescribe.py
│  ├─ constants.py
│  └─ models.py
│
├─ agent/
│  └─ wellness_agent.py
│
├─ store/
│  └─ db.py
│
├─ jobs/
│  └─ pipeline.py
│
├─ api/
│  ├─ main.py
│  └─ ingest.py
│
├─ mock/
│  └─ generator.py
│
└─ tests/
```

---

# 38. 데이터 모델 추가

기존:

```text
dogs
collars
events
wear
daily_metrics
prescriptions
dispense_commands
```

추가:

```text
dog_twin_state
guardian_context
meal_intake
weight_measurements
simulation_runs
agent_questions
agent_answers
food_catalog
supplement_catalog
```

---

# 39. 개발 우선순위

## Phase 1

기존 코드 유지 + 문서/모델 정리

## Phase 2

Activity TinyML

```text
REST
WALK
RUN
VIGOROUS
```

## Phase 3

Bowl Load Cell + 실제 섭취량

## Phase 4

DogTwin + Wellness State

## Phase 5

체중 수동입력 + weight feedback

## Phase 6

14-day Forward Simulation

## Phase 7

Event Detector Prototype

```text
SCRATCH
HEAD_SHAKE
BODY_SHAKE
OTHER
```

## Phase 8

Guardian Agent

## Phase 9

4슬롯 Dispenser 통합

## Phase 10 — Optional

- 자동 체중계
- SmartThings
- Active Learning

---

# 40. 데모 시나리오

```text
1. 몽이 정상 baseline 존재

2. Recorded / Mock Data
   Scratch ↑
   BODY_SHAKE ↑
   Estimated Sleep ↓

3. DogTwin
   Skin → ATTENTION

4. Wellness Agent
   "최근 샴푸를 변경했나요?"

5. 보호자
   "아니요"

6. Nutrition Candidate
   Food 유지
   Omega-3 후보

7. Safety Rule Engine
   → PASS

8. 14-Day Twin Simulation

   Energy Direction     BALANCED
   Weight Direction     STABLE
   Supplement Exposure  ACCEPTABLE

9. PLAN ACCEPTED

10. Smart Feeder 배식

11. Bowl Load Cell
    실제 섭취량 측정

12. 주간 체중 입력

13. Twin Update
```

---

# 41. Digital Twin이라고 부를 수 있는 이유

본 시스템은 단순 프로필 DB가 아니다.

다음 네 가지를 가진다.

## 1. Virtual Representation

```text
DogTwin
```

## 2. Continuous Synchronization

```text
Behavior
Intake
Weight
Nutrition History
```

로 실제 강아지와 지속 동기화.

## 3. Forward Simulation

```text
새로운 Nutrition Plan
→ 14-Day What-if
```

## 4. Feedback

```text
Simulation
→ Actual Feeding
→ Actual Intake / Weight
→ Twin Calibration
```

따라서 정확한 표현은:

> **Behavioral & Nutritional Digital Twin Prototype**

이다.

의료/생리학적 Digital Twin이라고 주장하지 않는다.

---

# 42. 최종 제품 구조

```text
                 Physical Dog
                      │
       ┌──────────────┴──────────────┐
       ▼                             ▼
 AI Smart Collar                Smart Feeder
       │                             │
 Behavior AI                    Actual Intake
       │                             │
       └──────────────┬──────────────┘
                      ▼
               Pet Digital Twin
                      │
               Personal Baseline
                      │
                Wellness State
                      │
               Wellness Agent
                      │
              Guardian Context
                      │
              Nutrition Engine
                      │
              Safety Rule Engine
                      │
            14-Day Forward Simulation
                      │
                Actual Dispense
                      │
             Intake / Weight Feedback
                      │
                      └──────────────→ Twin Update
```

---

# 43. Claude Code 구현 원칙

1. 기존 서버 구조를 최대한 유지한다.
2. 기존 `aggregate.py`, `signal.py`, `safety.py`를 중복 구현하지 않는다.
3. `BODY_SHAKE`를 복원한다.
4. `HEAD_SHAKE`와 `BODY_SHAKE`를 구분한다.
5. `POSTURE_CHANGE` 이벤트 계약을 유지한다.
6. V1에서는 `POSTURE_CHANGE`를 heuristic으로 먼저 구현한다.
7. 체중 데이터는 `MANUAL` 입력을 기본으로 지원한다.
8. Optional로 `PLATFORM_SCALE` source를 지원한다.
9. `Safety`와 `Simulation`의 역할을 분리한다.
10. `Safety`는 단일 source of truth다.
11. `Simulation`은 미래 계획의 방향성을 평가한다.
12. AI/LLM 자연어 결과를 actuator 명령에 직접 연결하지 않는다.
13. Agent 결과는 structured context로 변환한 뒤 Nutrition Engine에 전달한다.
14. 모든 simulation 결과를 저장한다.
15. 모든 실제 처방/배식에는 `algo_version`을 남긴다.
16. SmartThings는 V1 dependency가 아니다.
17. 하드웨어 미완성 상태에서도 mock generator로 end-to-end 검증 가능하게 유지한다.

---

# 44. 최종 한 문장

> **본 프로젝트는 강아지의 행동·수면·섭취·체중 데이터를 지속적으로 동기화해 Behavioral & Nutritional Digital Twin을 구성하고, 각 강아지의 개인 baseline을 기준으로 웰니스 변화를 이해한다. 새로운 영양 계획은 실제 적용 전에 14일 방향성 시뮬레이션을 수행하고, 안전 규칙을 통과한 경우에만 자동 급식기로 실행하며, 실제 섭취량과 체중 결과를 다시 Twin에 반영해 개인화를 반복한다.**
