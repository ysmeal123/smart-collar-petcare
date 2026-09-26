"""
신호 정제.

목줄에서 올라온 일별 요약을 알고리즘이 쓸 수 있는 z-score로 바꾼다.

    ① confidence 게이팅   (생성/수집 단계에서 이미 적용됨)
    ② 착용률 미달일 제외
    ③ 로버스트 z-score

여기서 가장 중요한 건 z의 분모다. 자세한 이유는 robust_z 주석 참조.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from core.constants import (
    AXIS_PERSISTENCE,
    AXIS_WINDOW_DAYS,
    BASELINE_DAYS,
    MAD_TO_SIGMA,
    MEDIAN_SE_FACTOR,
    AXIS_MIN_MAD,
    MIN_MAD,
    NIGHT_HOURS,
    SLEEP_HOURS,
)
from core.models import DailySummary, HealthAxis


@dataclass
class ZResult:
    z: float
    baseline: float
    recent: float

    @property
    def ratio(self) -> float:
        """평소 대비 몇 배인가. 사용자에게 보여줄 문장을 만들 때 쓴다."""
        if self.baseline <= 0:
            return 1.0
        return self.recent / self.baseline


def robust_z(
    baseline: list[float], window: list[float], min_mad: float = MIN_MAD
) -> ZResult:
    """
        MAD_σ = 1.4826 × median(|x − baseline중앙값|)
        SE    = MAD_σ × 1.253 / √n
        z     = (관찰 창 중앙값 − baseline 중앙값) / SE

    분모의 표본 수 보정(1.253/√n)이 핵심이다.

    분자는 '창 전체의 중앙값'인데 분모를 '일별 MAD'로 두면 분모가 과대평가되어
    신호를 절반 가까이 깎아먹는다. 실제로 활동량이 45% 감소한 케이스에서
    보정 없이는 z=-0.72가 나와 전혀 발화하지 않았다. 보정 후 z=-1.53.
    """
    if not baseline or not window:
        return ZResult(0.0, 0.0, 0.0)

    base = np.asarray(baseline, dtype=float)
    base_med = float(np.median(base))
    win_med = float(np.median(window))

    mad = float(np.median(np.abs(base - base_med))) * MAD_TO_SIGMA
    se = max(mad, min_mad) * MEDIAN_SE_FACTOR / np.sqrt(len(window))

    return ZResult((win_med - base_med) / se, base_med, win_med)


def valid_mask(days: list[DailySummary]) -> list[bool]:
    """착용률 미달인 날은 baseline·관찰 양쪽에서 모두 뺀다."""
    return [d.valid for d in days]


def extract_metrics(days: list[DailySummary]) -> dict[str, list[float]]:
    """AXIS_WEIGHTS가 참조하는 지표들을 일별 시계열로 뽑아낸다."""
    return {
        # --- 피부 ---
        "scratch_night":  [d.summary.scratch_night for d in days],
        "body_shake":     [d.summary.body_shake_total for d in days],
        # --- 귀 ---
        "head_shake":     [d.summary.head_shake_total for d in days],
        # --- 이동성 ---
        # 활동은 반드시 초 단위. 정수 '분'으로 자르면 하루 2~5분 뛰는 개의
        # 신호가 통째로 사라진다.
        "run_sec":        [d.summary.run_sec for d in days],
        "walk_sec":       [d.summary.walk_sec for d in days],
        "vigorous_sec":   [d.summary.vigorous_sec for d in days],
        "activity_sec":   [d.summary.run_sec + d.summary.walk_sec
                           + d.summary.vigorous_sec for d in days],
        # --- 수면 ---
        "restless":       [d.sleep.restless_count for d in days],
        # 깊은 밤(22~04시)의 뒤척임. 통증성 각성에 더 민감하다.
        "restless_night": [sum(d.hourly.posture_change[h] for h in NIGHT_HOURS)
                           for d in days],
        "sleep_min":      [d.sleep.total_min for d in days],
        "night_activity": [sum(d.hourly.activity_sec[h] for h in SLEEP_HOURS)
                           for d in days],
        # --- 식욕 ---
        # 로드셀 직접 측정. IMU 추론이 아니라 근거가 가장 강하다.
        "intake_ratio":   [d.intake_ratio for d in days],
    }


def _slice(vals: list[float], valid: list[bool], lo: int, hi: int) -> list[float]:
    return [v for v, ok in zip(vals[lo:hi], valid[lo:hi]) if ok]


def baseline_slice(vals: list[float], valid: list[bool], n_days: int) -> list[float]:
    """
    baseline 구간.

    처방 중에는 이 구간을 동결해야 한다. 그러지 않으면 영양제 효과로
    지표가 좋아질 때 baseline이 따라 내려가 '정상화됐다'고 오판하고
    처방을 끊었다가 재발하는 발진이 생긴다.
    (동결은 inference.evaluate_axes 에서 state로 처리한다)
    """
    end = min(BASELINE_DAYS, n_days)
    return _slice(vals, valid, 0, end)


def axis_windows(axis: HealthAxis, n_days: int) -> list[tuple[int, int]]:
    """
    축이 판정에 쓸 관찰 창들. 최근 것이 먼저 온다.

    축마다 신호 특성이 달라 창 길이가 다르다.
      - 피부: 7일 x 2회 (가려움은 며칠 만에 급증)
      - 관절: 14일 x 1회 (활동량은 일별 변동이 커서 7일로는 노이즈에 묻힘)
    어느 쪽이든 '창 x 횟수 >= 14일'이 되어 최소 2주는 관찰한 뒤 처방한다.
    """
    win = AXIS_WINDOW_DAYS[axis]
    need = AXIS_PERSISTENCE[axis]
    return [(n_days - (k + 1) * win, n_days - k * win) for k in range(need)]


def metric_z(
    metrics: dict[str, list[float]], valid: list[bool],
    key: str, lo: int, hi: int, n_days: int,
    min_mad: float = MIN_MAD,
) -> ZResult:
    vals = metrics[key]
    return robust_z(
        baseline_slice(vals, valid, n_days), _slice(vals, valid, lo, hi), min_mad
    )


def acute_z(
    metrics: dict[str, list[float]], valid: list[bool],
    key: str, window_days: int, ref_days: int,
) -> ZResult:
    """
    급성 판정용 z.

    참조 구간이 baseline이 아니라 '최근 window_days를 제외한 직전 ref_days'다.
    급성은 '평소보다 나쁨'이 아니라 '갑자기 나빠짐'이기 때문이다.

    baseline 기준으로 하면 두 방향으로 다 깨진다.
      - 만성이 충분히 진행되면 절대 z가 커져 급성으로 오판한다
      - 급성 직후엔 긴 창에 정상일이 섞여 신호가 희석된다
    """
    vals = metrics[key]
    w, r = window_days, ref_days
    ref = _slice(vals, valid, -(w + r), -w)
    win = _slice(vals, valid, len(vals) - w, len(vals))
    return robust_z(ref, win)
