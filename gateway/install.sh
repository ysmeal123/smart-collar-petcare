#!/usr/bin/env bash
#
# 라즈베리파이에 게이트웨이를 서비스로 등록한다.
#
#     sudo bash gateway/install.sh
#
# 제품에는 모니터도 키보드도 없다. 전원이 들어오면 알아서 떠야 하고,
# 죽으면 알아서 다시 떠야 한다. systemd 가 그 일을 한다.
#
# 한 번만 돌리면 된다. 그 뒤로는 코드를 받고 재시작만 하면 된다.
#
#     git pull && sudo systemctl restart pebble-gateway

set -euo pipefail

SERVICE=pebble-gateway
ENV_DIR=/etc/pebble
ENV_FILE="$ENV_DIR/gateway.env"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-pi}"

if [[ $EUID -ne 0 ]]; then
    echo "sudo 로 실행하세요:  sudo bash gateway/install.sh" >&2
    exit 1
fi

echo "저장소 : $REPO"
echo "사용자 : $RUN_USER"
echo

# --- 환경 파일 -------------------------------------------------------------
#
# 토큰이 들어가므로 600 으로 잠근다. 유닛 파일에 적으면
# /etc/systemd/system 에 평문으로 남고 누구나 읽을 수 있다.

mkdir -p "$ENV_DIR"
if [[ -f "$ENV_FILE" ]]; then
    echo "환경 파일이 이미 있습니다: $ENV_FILE  (덮어쓰지 않습니다)"
else
    cat > "$ENV_FILE" <<'EOF'
# 밥통 게이트웨이 설정.
#
# PEBBLE_TOKEN 은 서버의 PEBBLE_DEVICE_TOKEN 과 같은 값이어야 한다.
# (Render -> pebble-api -> Environment)

PEBBLE_SERVER=https://pebble-api-0ro8.onrender.com
PEBBLE_TOKEN=
PEBBLE_DOG=dog_choco
PEBBLE_COLLAR=PBL-0001

# 배출량 보정값. `python3 -m gateway.calibrate 1` 로 실측해서 채운다.
# 비워두면 추정치로 돈다 - 4알이 2알이나 9알이 될 수 있다.
#PEBBLE_STEPS_PER_PELLET=20
#PEBBLE_STEPS_PER_GRAM=8

# 장착한 로더 슬롯. 기본은 1,2,3,4 (오메가3/피부장벽/관절/장건강)
#PEBBLE_SLOTS=1,2,3,4
EOF
    chmod 600 "$ENV_FILE"
    echo "환경 파일을 만들었습니다: $ENV_FILE"
fi

# --- 유닛 파일 -------------------------------------------------------------
#
# 저장소 위치와 사용자가 사람마다 다르다. 템플릿을 치환해서 깐다.

sed -e "s|/home/pi/smart-collar-petcare|$REPO|g" \
    -e "s|^User=pi$|User=$RUN_USER|" \
    "$REPO/gateway/$SERVICE.service" > "/etc/systemd/system/$SERVICE.service"

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

echo
echo "등록 완료."
echo
if ! grep -q '^PEBBLE_TOKEN=.\+' "$ENV_FILE"; then
    echo "  ⚠ 아직 토큰이 비어 있습니다. 먼저 채우세요:"
    echo "      sudo nano $ENV_FILE"
    echo
fi
echo "  시작    sudo systemctl start $SERVICE"
echo "  상태    systemctl status $SERVICE"
echo "  로그    journalctl -u $SERVICE -f"
echo "  중지    sudo systemctl stop $SERVICE"
echo
echo "  코드 갱신 후:  git pull && sudo systemctl restart $SERVICE"
