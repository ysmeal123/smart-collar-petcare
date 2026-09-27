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
