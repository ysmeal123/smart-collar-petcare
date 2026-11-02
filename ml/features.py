"""
행동 판정용 특징 — **XIAO 펌웨어(firmware/xiao_collar)와 1:1 로 같아야 한다.**

여기를 바꾸면 펌웨어의 computeFeatures() 도 똑같이 바꿔야 한다.
어긋나면 학습 때와 다른 숫자가 모델에 들어가서 판정이 엉망이 된다.

입력: 2초 창 (50Hz × 100샘플)
    acc  [g]     (N, 3)
    gyro [deg/s] (N, 3)

**목줄 방향에 무관하게** 만든다. 공개 데이터셋 센서와 XIAO 는 축 방향이
다르고, 목줄도 돌아간다. 그래서 축별 값 대신
    - 중력 방향(창 평균 가속도)을 기준으로 한 수직/수평 성분
    - 크기(norm)
만 쓴다.
"""

import numpy as np

FS = 50          # Hz
WIN = 100        # 2초
NAMES = [
    "am_std", "am_min", "am_max", "am_jerk",
    "v_std", "v_min", "v_max", "v_zcr",
    "h_mean", "h_std", "h_max",
    "gm_mean", "gm_std", "gm_max",
]


def features(acc: np.ndarray, gyro: np.ndarray) -> np.ndarray:
    acc = acc.astype(np.float32)
    gyro = gyro.astype(np.float32)

    g = acc.mean(axis=0)
    gn = float(np.linalg.norm(g))
    u = g / gn if gn > 1e-6 else np.array([0, 0, 1], np.float32)

    dyn = acc - g
    v = dyn @ u                                   # 수직 흔들림
    h = np.linalg.norm(dyn - np.outer(v, u), axis=1)  # 수평 흔들림 크기
    am = np.linalg.norm(acc, axis=1)
    gm = np.linalg.norm(gyro, axis=1)

    zcr = float(np.count_nonzero(np.signbit(v[1:]) != np.signbit(v[:-1])))
    jerk = float(np.mean(np.abs(np.diff(am))))

    return np.array([
        am.std(), am.min(), am.max(), jerk,
        v.std(), v.min(), v.max(), zcr,
        h.mean(), h.std(), h.max(),
        gm.mean(), gm.std(), gm.max(),
    ], dtype=np.float32)
