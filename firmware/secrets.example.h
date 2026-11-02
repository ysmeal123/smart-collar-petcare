/*
 * 비밀 값 **보기**. 이 파일은 커밋된다. 값을 적지 마라.
 *
 *     cp firmware/secrets.example.h firmware/esp32_spin/secrets.h
 *
 * 그리고 secrets.h 에 진짜 값을 채운다. .gitignore 가 secrets.h 를 막는다.
 *
 * **저장소가 Public 이다.** .ino 에 토큰을 적으면 그대로 올라가고,
 * 지워도 히스토리에 남는다. GitHub 에 올라간 비밀은 봇이 몇 분 안에
 * 긁어간다.
 */

#pragma once

// Wi-Fi. 아이폰 핫스팟이면 SSID 가 폰 이름이다.
#define WIFI_SSID     ""
#define WIFI_PASSWORD ""

// 서버. 배포 주소는 공개돼 있어도 상관없다 - 토큰이 없으면 401 이다.
#define PEBBLE_SERVER "https://pebble-api-0ro8.onrender.com"

// 기기 토큰. 서버의 PEBBLE_DEVICE_TOKEN 과 같은 값이어야 한다.
// (Render -> pebble-api -> Environment)
//
// **앱 토큰이 아니다.** 둘은 권한이 다르다.
//   APP     대시보드 읽기, 문진 보내기
//   DEVICE  센서 올리기, 사출 명령 받고 ack
#define PEBBLE_TOKEN  ""

// 이 밥통이 담당하는 개체와 목줄.
#define PEBBLE_DOG    "dog_choco"
#define PEBBLE_COLLAR "PBL-0001"
