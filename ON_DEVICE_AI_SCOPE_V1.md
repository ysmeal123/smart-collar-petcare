# 온디바이스 AI 담당 범위

## 1. 목적

우리 프로젝트에서 온디바이스 AI 목줄의 역할은 다음 한 문장으로 정리한다.

> **6축 IMU 데이터를 이용해 강아지의 행동을 실시간으로 분류하고, 원시 센서 데이터 대신 행동 결과만 서버로 전달한다.**

목줄은 질병을 진단하거나 영양제를 결정하지 않는다.

---

# 2. 하드웨어

## 기준 보드

**Seeed Studio XIAO nRF52840 Sense**

사용 센서:

- 3축 Accelerometer: `Ax, Ay, Az`
- 3축 Gyroscope: `Gx, Gy, Gz`

즉 총 **6축 IMU 데이터**를 사용한다.

---

# 3. V1에서 추론해야 하는 행동

온디바이스 AI는 두 가지 역할로 나눈다.

---

## 3.1 Model A — Activity Classification

지속적으로 발생하는 활동 상태를 분류한다.

### 클래스

```text
REST
WALK
RUN
VIGOROUS
```

### 의미

- `REST`
  - 가만히 있음
  - 휴식
  - 비활동 상태

- `WALK`
  - 일반적인 걷기

- `RUN`
  - 뛰기
  - Trot 포함

- `VIGOROUS`
  - 격한 놀이
  - 빠르고 강한 움직임
  - 고강도 활동

### 서버에서 사용하는 목적

이 결과는 서버에서 다음 계산에 사용한다.

- 일일 활동량
- 활동 강도
- Energy OUT
- Mobility Trend
- Estimated Sleep 계산의 기초

### 출력 예

```text
REST      0.03
WALK      0.92
RUN       0.04
VIGOROUS  0.01

→ WALK
```

### 권장 Window

초기 기준:

```text
약 1~2초
```

실제 학습 결과에 따라 조정한다.

---

# 4. Model B — Health Behavior Event Detection

짧게 발생하는 특이 행동을 탐지한다.

### 클래스

```text
SCRATCH
HEAD_SHAKE
OTHER
```

### 의미

- `SCRATCH`
  - 몸을 긁는 행동

- `HEAD_SHAKE`
  - 머리 / 귀를 빠르게 흔드는 행동

- `OTHER`
  - 그 외 움직임

### 서버에서 사용하는 목적

```text
SCRATCH
    ↓
Skin Behavior Index

HEAD_SHAKE
    ↓
Ear Behavior Index
```

중요:

```text
SCRATCH → 피부염 진단 X

HEAD_SHAKE → 외이도염 진단 X
```

목줄은 **행동만 인식**한다.

질병이나 웰니스 상태 해석은 서버가 담당한다.

### 권장 Window

Scratch / Head Shake는 짧은 이벤트이므로:

```text
약 0.5~1초
```

수준에서 시작해 실제 데이터로 튜닝한다.

---

# 5. 목줄 AI가 하지 않는 것

온디바이스 AI에서는 다음 기능을 수행하지 않는다.

- 피부염 진단
- 외이도염 진단
- 관절 이상 판단
- 영양제 선택
- 영양제 용량 계산
- 사료량 계산
- Pet Digital Twin 상태 계산
- Wellness State 판단
- LLM 실행
- 수면을 직접 확정

즉 목줄의 책임 범위는:

```text
IMU
 ↓
행동 인식
 ↓
행동 결과 생성
 ↓
BLE 전송
```

까지다.

---

# 6. Sleep 처리

V1에서는 `SLEEP`을 별도의 TinyML 클래스로 만들지 않는다.

목줄에서는 `REST`를 최대한 안정적으로 추론한다.

서버가 다음 정보를 종합한다.

```text
야간 시간대
+
장시간 REST
+
중간 움직임
+
활동 패턴
    ↓
Estimated Sleep
```

따라서 수면은 서버에서 계산한다.

표현은:

```text
Estimated Sleep
Probable Sleep
```

을 사용한다.

---

# 7. 서버로 전송해야 할 정보

## 7.1 Activity Summary

예:

```json
{
  "window_sec": 60,
  "rest_sec": 31,
  "walk_sec": 19,
  "run_sec": 6,
  "vigorous_sec": 4,
  "worn_sec": 60,
  "battery": 82
}
```

목줄에서 일정 시간 동안의 Activity 결과를 집계해서 보낸다.

---

## 7.2 Event

Scratch / Head Shake는 이벤트가 발생할 때 전달한다.

예:

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

---

# 8. 필수 전송 필드

목줄에서 Gateway로 보내야 하는 주요 정보:

```text
seq
boot_id
t_ms
event_type / activity_class
confidence
duration
worn_sec
battery
activity_summary
```

목적:

- 중복 데이터 방지
- 재부팅 구분
- 시간 복원
- 낮은 confidence 필터링
- 착용 여부 판단

---

# 9. Confidence 처리

모델이 확신하지 못하는 경우 억지로 행동을 확정하지 않는다.

예:

```text
WALK      0.37
RUN       0.31
REST      0.25
VIGOROUS  0.07
```

이 경우:

```text
UNKNOWN
```

처리 가능.

Event Model에서는:

```text
OTHER
```

처리한다.

초기 confidence threshold는 예를 들어:

```text
0.60
```

수준에서 시작하고, 실제 데이터로 조정한다.

---

# 10. Activity 모델 학습 데이터

Activity 모델은 공개 Dog IMU Dataset을 우선 활용한다.

공개 데이터 라벨을 다음과 같이 재구성한다.

```text
Sitting
Standing
Lying
    ↓
REST

Walking
    ↓
WALK

Trotting
    ↓
RUN

Playing
    ↓
VIGOROUS
```

목표:

```text
REST
WALK
RUN
VIGOROUS
```

4-class classifier를 먼저 만든다.

---

# 11. Scratch / Head Shake 학습 데이터

이 두 행동은 공개 RAW IMU 데이터가 충분하지 않을 가능성이 높으므로 직접 데이터를 수집한다.

## 수집 방식

```text
XIAO nRF52840 Sense
+
스마트폰 영상
```

동시에 기록한다.

영상 timestamp를 기준으로 IMU에 라벨을 붙인다.

예:

```text
13:02:11.2 ~ 13:02:13.0
SCRATCH

13:04:05.1 ~ 13:04:05.9
HEAD_SHAKE
```

---

# 12. 자체 데이터 수집 목표

권장:

```text
약 8~15마리
```

가능하면:

- 소형견
- 중형견
- 대형견

을 섞는다.

특히 공개 데이터에서 상대적으로 부족할 수 있는 소형견 데이터를 우선 확보한다.

예:

- 말티즈
- 포메라니안
- 치와와
- 토이푸들
- 요크셔테리어

---

# 13. 학습 목표를 잘못 잡으면 안 됨

우리가 학습하는 것은:

```text
피부염 X
외이도염 X
```

가 아니다.

학습하는 것은:

```text
SCRATCH 행동
HEAD_SHAKE 행동
```

이다.

즉 건강 상태 해석과 행동 분류를 분리한다.

---

# 14. Train / Validation / Test 원칙

같은 강아지의 데이터를 Train과 Test에 동시에 넣지 않는다.

잘못된 방식:

```text
Dog A 데이터

80% → Train
20% → Test
```

권장:

```text
Train
Dog 1 ~ Dog 35

Validation
Dog 36 ~ Dog 40

Test
Dog 41 ~ Dog 45
```

또는:

```text
Leave-One-Dog-Out Cross Validation
```

사용.

목표는:

> **처음 보는 강아지에서도 행동을 인식할 수 있는가?**

를 검증하는 것이다.

---

# 15. 학습 파이프라인

## Activity Model

```text
Open Dog IMU Dataset
    ↓
Label Mapping
    ↓
Sampling Rate 통일
    ↓
Windowing
    ↓
Dog ID 기준 Train/Val/Test
    ↓
Model Training
    ↓
Quantization
    ↓
XIAO nRF52840 배포
```

---

## Event Model

```text
직접 수집한 IMU + 영상
    ↓
영상 기반 Ground Truth
    ↓
SCRATCH / HEAD_SHAKE / OTHER
    ↓
짧은 Window 생성
    ↓
Dog ID 기준 Train/Val/Test
    ↓
Model Training
    ↓
Quantization
    ↓
XIAO nRF52840 배포
```

---

# 16. 모델 경량화

목표 보드가 nRF52840이므로 모델은 MCU에서 실행 가능해야 한다.

권장:

- 작은 Neural Network
- INT8 Quantization
- TensorFlow Lite Micro / Edge Impulse 사용 가능
- RAM / Flash 사용량 측정
- inference latency 측정

GPU는 필수 아님.

학습은 PC나 Cloud에서 하고:

```text
목줄에서는 추론만 수행
```

한다.

---

# 17. 개발 우선순위

## Phase 1

공개 데이터로:

```text
REST
WALK
RUN
VIGOROUS
```

4-class 모델 제작.

## Phase 2

PC에서 모델 평가.

확인:

- Accuracy
- Precision
- Recall
- F1-score
- Confusion Matrix

## Phase 3

INT8 등으로 경량화.

## Phase 4

XIAO nRF52840 Sense에 올려 실시간 추론.

## Phase 5

실제 강아지에게 착용해서 검증.

## Phase 6

SCRATCH / HEAD_SHAKE 데이터 직접 수집.

## Phase 7

```text
SCRATCH
HEAD_SHAKE
OTHER
```

Event Detector 개발.

## Phase 8

Activity + Event 모델을 실제 firmware에 통합.

---

# 18. 성공 기준

V1 목표는:

```text
Activity
────────────
REST
WALK
RUN
VIGOROUS

Event
────────────
SCRATCH
HEAD_SHAKE
```

를 **처음 보는 강아지에서도 의미 있게 구분**하는 것이다.

단순 전체 accuracy보다 클래스별 성능을 확인한다.

특히 중요:

- Scratch Recall
- Scratch False Positive
- Head Shake Recall
- Head Shake False Positive
- Walk / Run confusion
- Rest / Vigorous confusion

---

# 19. 서버와의 역할 분담

## 목줄

질문:

> **“강아지가 지금 무엇을 하고 있는가?”**

출력:

```text
REST
WALK
RUN
VIGOROUS

SCRATCH
HEAD_SHAKE
```

---

## 서버

질문:

> **“이 행동들이 이 강아지의 평소와 비교해서 어떤 의미인가?”**

계산:

```text
Activity
Mobility
Skin
Ear
Sleep
Appetite
```

---

# 20. 최종 책임 범위

온디바이스 AI 담당자는 최종적으로 아래 파이프라인을 완성하면 된다.

```text
6-axis IMU
   ↓
Preprocessing
   ↓
Windowing
   ↓
TinyML Inference
   ↓
Confidence Filtering
   ↓
Activity / Event Result
   ↓
BLE Packet
```

최종 출력:

```text
Activity:
REST / WALK / RUN / VIGOROUS

Event:
SCRATCH / HEAD_SHAKE
```

---

# 21. 가장 중요한 한 문장

> **온디바이스 AI의 목표는 질병을 판단하는 것이 아니라, 강아지의 행동을 정확하고 저전력으로 인식하여 서버가 상태를 이해할 수 있는 신뢰도 높은 입력 데이터를 만드는 것이다.**
