# 팀원 시작하기

**읽을 것은 이 문서 하나다.** 각 폴더의 README 는 그 부분을 깊게
들어갈 때 본다.

```
app-android/   앱     (Kotlin + Compose)
petcare/       서버   (FastAPI)
firmware/      밥통   (ESP32)
gateway/       구버전 게이트웨이 (라즈베리파이 — 폐기, 참고용)
```

---

## 비밀은 저장소에 없다

**이 저장소는 Public 이다.** 키가 한 번 커밋되면 지워도 히스토리에
남고, GitHub 에 올라간 키는 봇이 몇 분 안에 긁어간다.

| 값 | 어디에 있나 |
|---|---|
| `PEBBLE_APP_TOKEN` | 팀장이 직접 전달 (DM) |
| `PEBBLE_DEVICE_TOKEN` | 같음 |
| Gemini API 키 | **아무도 받을 필요 없다** (아래 참고) |

`.env.example` 에 **빈 칸만** 들어 있다. `.env` 는 `.gitignore` 가 막는다.

---

## 앱만 할 사람 — 설정할 게 없다

앱은 `app/src/main/assets/demo_*.json` 을 읽는다. **백엔드를 안 띄워도
앱이 뜬다.** 발표 시연에서 서버 의존성을 없애려고 그렇게 만들었고,
덕분에 앱 작업에 서버가 필요 없다.

```bash
git clone https://github.com/ysmeal123/smart-collar-petcare
# Android Studio 로 app-android 폴더 열기 → Run
```

`local.properties`(SDK 경로)는 Android Studio 가 알아서 만든다.
PC 마다 달라서 커밋하지 않는다.

```bash
cd app-android
gradlew :app:testDebugUnitTest   # 단위 테스트
gradlew :app:assembleDebug       # APK
gradlew :app:installDebug        # 에뮬레이터/기기에 설치
```

UI·온보딩·차트 작업은 전부 이 상태로 할 수 있다.

### 실제 서버를 붙일 때

**코드를 고치지 않는다.** 앱 안에 입력란이 있다 (`ui/SettingsSheet.kt`).

| 칸 | 값 |
|---|---|
| 서버 주소 | `https://pebble-api-0ro8.onrender.com` |
| 개체 ID | `dog_choco` |
| 토큰 | `PEBBLE_APP_TOKEN` — 팀장에게 받는다 |

비워두면 데모 데이터로 돌아간다. 서버가 안 뜨거나 토큰이 틀려도
앱이 죽지 않고 데모로 내려간다.

> **무료 호스팅은 15분 놀면 잠든다.** 깨는 데 30~60초가 걸려서 첫
> 호출이 느리다. 고장이 아니다. 그래서 읽기 타임아웃이 70초다.

---

## 서버를 직접 띄울 사람 — 키가 하나도 필요 없다

```bash
cd petcare
pip install -r requirements.txt
python seed_live.py              # 4마리 44일치 — 이벤트부터 사출 명령까지
uvicorn api.main:app --reload
```

`.env` 가 없어도 돈다. 빈 값마다 안전한 기본으로 떨어진다.

```
DATABASE_URL 비움    로컬 SQLite (petcare.db)
PEBBLE_LLM_KEY 비움  규칙 사전으로 해석 — 서비스가 그대로 동작한다
토큰 비움            인증 꺼짐 (로컬에서만. 배포에는 반드시 넣는다)
```

**Gemini 키를 나눠줄 이유가 없다.** LLM 은 문진 텍스트에서 증상을
뽑는 한 곳(`agent/llm.py`)에만 쓰이고, 키가 없으면 규칙 사전이
그 일을 한다. 실측 정확도가 80% 에서 98% 로 올라가는 차이일 뿐,
없으면 안 도는 기능이 아니다.

테스트:

```bash
cd petcare && python -m pytest tests -q
```

### 에뮬레이터에서 로컬 서버를 보려면

```
http://10.0.2.2:8000      에뮬레이터에서 본 호스트 PC
http://localhost:8000      에뮬레이터 자기 자신 — 안 된다
```

실기기면 PC 의 LAN IP 를 넣는다. 같은 Wi-Fi 여야 한다.

---

## 밥통(ESP32)을 만질 사람

`firmware/README.md` 를 본다. 핀 배정, 검증된 펄스 모양, 드라이버가
죽는 이유가 거기 있다.

**서버는 한 줄도 안 바뀐다.** 계약이 HTTP + JSON 이라 밥통이 파이든
ESP32 든 상관없다. `gateway/` 는 라즈베리파이용 구버전이고, 파이가
죽어서 ESP32 로 옮겼다. 로직 참고용으로 남겨둔다.

---

## 서로 뭘 건드리면 안 되나

**판정 권한은 서버에 있다.** 앱이 가진 안전 규칙(`data/Profile.kt`)은
온보딩 중에 "이 입력이 처방을 어떻게 바꾸는지" 보여주기 위한
**표시용 사본**이다. 진짜 판정은 `petcare/core/constants.py` 가 한다.

```
앱에서 규칙을 고치면 화면만 바뀌고 처방은 안 바뀐다.
반드시 서버를 고치고 앱 사본을 맞춘다.
```

**문진은 브레이크지 액셀이 아니다.** `interpret()` 에는 보류·차단·
진료권고만 있고 용량을 올리는 경로가 없다. 사용자가 "영양제 늘려줘"
라고 써도 늘지 않는다. 이해상충을 구조로 막은 것이라 이 방향을
뒤집는 변경은 하지 않는다.

---

## 막히면

| 증상 | 먼저 볼 곳 |
|---|---|
| 앱이 데모 데이터만 보인다 | 설정의 주소·토큰. 서버가 자고 있을 수도 (60초 기다려본다) |
| 서버가 401 을 준다 | 토큰이 Render 의 값과 다르다 |
| 앱 빌드가 깨진다 | `local.properties` 를 지우고 Android Studio 재시작 |
| 시드 데이터가 비어 있다 | `python seed_live.py` 를 안 돌렸다 |
