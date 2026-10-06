# 배포

서버를 클라우드에 올리는 방법. **코드는 이미 준비돼 있다.**

```
DATABASE_URL 있음  →  PostgreSQL
DATABASE_URL 없음  →  로컬 SQLite (개발용)
```

같은 코드가 양쪽에서 돈다. `store/dialect.py` 가 문법 차이를 흡수한다.

---

## 1. Render (가장 쉬움)

1. https://render.com 가입 (GitHub 계정으로)
2. **New → Blueprint** → 이 저장소 선택
3. `render.yaml` 을 읽어 웹 서비스와 Postgres 를 자동 생성
4. 배포되면 **Environment** 탭에서 토큰 두 개를 확인한다

```
PEBBLE_DEVICE_TOKEN   밥통(게이트웨이)이 쓴다
PEBBLE_APP_TOKEN      앱이 쓴다
```

5. 첫 데이터 넣기 — **Shell** 탭에서

```bash
python -m seed_remote
```

이미 데이터가 있으면 건너뛴다. 재배포해도 안전하다.

---

## 2. 앱 연결

앱 → **설정** → 서버 연결

```
주소       https://pebble-api-xxxx.onrender.com
개체 ID    dog_choco
앱 토큰    (PEBBLE_APP_TOKEN 값)
```

비워두면 기기 안의 데모 데이터로 돈다. 발표 중 네트워크가 끊겨도
화면이 비지 않되, 어느 쪽인지 배지로 표시된다.

---

## 3. 게이트웨이 연결

라즈베리파이에서:

```bash
export PEBBLE_SERVER=https://pebble-api-xxxx.onrender.com
export PEBBLE_TOKEN=<PEBBLE_DEVICE_TOKEN>
python -m gateway.agent --mock
```

---

## 콜드스타트

무료 플랜은 **15분 놀면 잠들고, 깨는 데 30~60초**가 걸린다.

대응은 이미 들어가 있다.

| | |
|---|---|
| 앱 | 읽기 타임아웃 70초 + "서버를 깨우는 중입니다" 표시 |
| 밥통 | 캐시된 계획으로 계속 급여. 서버가 없어도 밥은 나간다 |

**발표 직전에 브라우저로 `/health` 를 한 번 열어 두면** 깨어 있는 상태로 시작한다.

---

## 보안

공개 인터넷에 올라가므로 토큰이 **반드시** 필요하다.
없으면 누구나 남의 목줄로 가짜 이벤트를 밀어넣을 수 있고,
그러면 엉뚱한 영양제가 처방된다.

토큰을 하나도 설정하지 않으면 서버가 시작할 때 경고를 찍는다.

```
[pebble] ⚠ 인증이 꺼져 있습니다.
```

권한은 둘로 나뉜다. 기기 토큰으로 앱 API 를 부르면 401 이다.

| | 쓰는 곳 |
|---|---|
| `PEBBLE_DEVICE_TOKEN` | `/v1/ingest` `/v1/feeder/*` `/v1/jobs/*` |
| `PEBBLE_APP_TOKEN` | `/v1/dogs/*` |
| (없음) | `/health` — 헬스체크는 공개 |

> 이건 캡스톤 수준의 최소 방어다. 상용이라면 기기별 발급·회전·폐기와
> 사용자 계정(OAuth)이 필요하다. `MVP_SCOPE.md` 참조.

---

## 로컬에서 Postgres 로 확인하려면

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16
export DATABASE_URL=postgresql://postgres:pw@localhost:5432/postgres
cd petcare && python -m seed_remote && uvicorn api.main:app
```

> 아직 실제 PostgreSQL 에서 돌려보지는 않았다.
> 모든 쿼리가 Postgres 파서를 통과하는 것은 확인했지만(26개 쿼리 + 18개 DDL),
> **첫 배포가 곧 첫 실행 검증**이다.

---

## 다른 플랫폼

`Dockerfile` 과 환경변수는 동일하다. `render.yaml` 만 플랫폼 설정으로 바꾸면 된다.

| | 특징 |
|---|---|
| Fly.io | 콜드스타트가 짧고 도쿄 리전이 있다. 카드 등록 필요 |
| Railway | 설정이 간단하지만 무료 크레딧 소진 후 유료 |
| Oracle Cloud 무료 VM | 잠들지 않지만 VM 을 직접 세팅해야 한다 |

---

## LLM 해석 켜기 (선택)

자유 텍스트 해석은 **규칙 사전**으로 먼저 돈다. 키를 넣으면 LLM 으로 올라간다.

```
규칙 사전   키 없이 돈다. 사전에 있는 표현만 알아듣는다
LLM        아무 문장이나 알아듣는다. 키가 필요하다
```

환경변수 세 개다. 없으면 자동으로 규칙 사전을 쓴다.

```bash
PEBBLE_LLM_PROVIDER=gemini          # 또는 anthropic
PEBBLE_LLM_KEY=<API 키>
PEBBLE_LLM_MODEL=gemini-2.0-flash   # 선택. 비우면 기본값
```

### 키를 어디에 두나

> **이 저장소는 Public 이다.** 키가 한 번 커밋되면 지워도 히스토리에 남고,
> GitHub 에 올라간 키는 봇이 몇 분 안에 긁어간다.
> **코드에 절대 적지 마라.** 환경변수로만 넘긴다.

**배포(Render)** — Environment 탭에 넣는다. 저장소를 거치지 않는다.

```
Dashboard → 서비스 선택 → Environment → Add Environment Variable
→ 저장하면 자동 재배포
```

**로컬 개발** — 둘 중 하나.

```bash
# ① 셸에서 (그 터미널에서만 유효. 제일 안전하다)
export PEBBLE_LLM_PROVIDER=gemini
export PEBBLE_LLM_KEY=...

# ② .env 파일 (매번 입력하기 번거로우면)
cp .env.example .env     # 값을 채운다
```

`.env` 는 `.gitignore` 가 막는다. `.env.example`(값이 빈 보기)만 커밋된다.
서버가 시작할 때 `.env` 를 자동으로 읽고, **이미 설정된 환경변수는 덮지 않는다** —
실수로 커밋된 .env 가 프로덕션 키를 덮는 것을 막기 위해서다.

### 키가 새지 않도록 해 둔 것

| | |
|---|---|
| URL 이 아니라 **헤더**로 보낸다 | `?key=` 로 보내면 URL 에 키가 박히고, URL 은 예외 메시지·프록시 로그로 샌다 |
| 예외 **메시지를 안 찍는다** | 타입 이름만 남긴다. HTTP 예외는 요청 정보를 문자열에 담는다 |
| 시작 로그에 **값이 없다** | `LLM 해석: gemini / gemini-2.0-flash` — 켜졌는지만 말한다 |
| 응답에 설정이 안 섞인다 | 앱으로 나가는 JSON 에 `engine` 이름만 들어간다 |

테스트가 이걸 지킨다 (`test_시작_로그에_키가_없다` 외 4개).

### 키가 샜다면

1. **먼저 폐기한다.** [AI Studio](https://aistudio.google.com/apikey) 에서 삭제 →
   새로 발급. 코드를 고치는 것보다 이게 먼저다.
2. 새 키를 Render Environment 에 넣는다.
3. 커밋에 들어갔다면 히스토리에서도 지워야 한다. 다만 **이미 공개된 키는
   지워도 소용없다** — 폐기가 유일한 대응이다.

Gemini 무료 티어 키라 금전 피해는 없지만, 할당량을 남이 쓰면 우리 요청이
막힌다. 그러면 규칙 사전으로 떨어져서 서비스는 계속 돈다.

| | 키 받는 곳 | 기본 모델 |
|---|---|---|
| `gemini` | https://aistudio.google.com/apikey | `gemini-2.0-flash` |
| `anthropic` | https://console.anthropic.com | `claude-haiku-4-5-20251001` |

Render 에 넣으려면 **Environment** 탭에 추가하고 재배포하면 된다.
로컬은 그냥 셸에서 내보낸다.

```bash
export PEBBLE_LLM_PROVIDER=gemini
export PEBBLE_LLM_KEY=...
cd petcare && uvicorn api.main:app
```

### 왜 이 자리에만 LLM 을 쓰나

이 경로가 내놓는 것은 **정해진 key 15개뿐**이고, 그 key 들이 할 수 있는 일은
`interpret()` 에서 **보류 · 차단 · 진료 권고**가 전부다.
**용량을 올리는 경로가 없다.**

그래서 추출이 틀려도 최악의 결과가 "영양제를 안 준다"다. 과다 급여가 아니다.
`test_LLM_로도_용량을_올릴_수_없다` 가 이걸 못 박는다 — LLM 이 모든 항목에
'예'를 뱉어도 기준선을 못 넘는다.

```
자유 텍스트 → [LLM] → 정해진 key/value → interpret() → 보류·차단
                       ^^^^^^^^^^^^^^^
                       여기서 자연어가 끊긴다
```

모르는 key 는 `sanitize()` 에서 **전부 버린다.** 환각이든 프롬프트
인젝션이든 시스템 안으로 못 들어온다.

사료 그램 수, 알갱이 개수, 안전 차단, 긴급정지에는 LLM 이 닿지 않는다.
거기서 환각이 나면 실제로 잘못된 영양제가 나간다.

### 실패하면

키가 없든, 네트워크가 죽든, 응답이 이상하든 **규칙 사전으로 떨어진다.**
발표 중 외부 API 하나 때문에 화면이 멈추면 안 된다. 6초 안에 응답이
없으면 포기하고 규칙을 쓴다.

어느 쪽이 썼는지는 응답의 `engine` 필드에 담긴다 (`rules` / `llm`).
