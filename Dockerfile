# 배포용 이미지.
#
# numpy 가 ARM/x86 양쪽 휠을 제공하므로 slim 이미지에서 바로 설치된다.
# 빌드 도구를 넣지 않아 이미지가 작고 빌드가 빠르다.

FROM python:3.12-slim

WORKDIR /app

# 의존성을 먼저 깔아 레이어 캐시를 살린다
COPY petcare/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY petcare/ /app/

# 플랫폼이 주는 포트를 쓴다. 없으면 8000.
ENV PORT=8000
EXPOSE 8000

# 컨테이너가 재시작해도 스키마는 startup 훅이 확인한다.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
