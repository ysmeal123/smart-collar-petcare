"""
시험용 행동 분류 모델 학습 → XIAO 용 C 헤더로 내보낸다.

    python ml/train.py <DogMoveData.csv> <출력 model.h>

데이터: Vehkaoja et al. Movement Sensor Dataset (CC BY 4.0), 목(Neck) 센서만.
클래스: 0 쉬기 / 1 걷기 / 2 뛰기 / 3 털기

평가는 **개 단위로 나눠서** 한다 (GroupKFold). 같은 개가 학습과 평가에 섞이면
정확도가 부풀려진다 — 우리 개는 학습 데이터에 없는 새 개이기 때문이다.
"""

import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import GroupKFold

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from features import FS, NAMES, WIN, features  # noqa: E402

CLASSES = ["rest", "walk", "run", "shake"]
CLASSES_KO = ["쉬기", "걷기", "뛰기", "털기"]
LABEL_MAP = {
    "Lying chest": 0, "Sitting": 0, "Standing": 0,
    "Walking": 1, "Pacing": 1,
    "Trotting": 2, "Galloping": 2,
    "Shaking": 3,
}
HOP = {0: 50, 1: 50, 2: 50, 3: 5}       # 털기는 드물어서 촘촘히 자른다
PURITY = {0: 0.8, 1: 0.8, 2: 0.8, 3: 0.5}  # 털기는 1~2초라 창의 절반이면 인정


def load(path: str) -> pd.DataFrame:
    cols = ["DogID", "TestNum", "ANeck_x", "ANeck_y", "ANeck_z",
            "GNeck_x", "GNeck_y", "GNeck_z", "Behavior_1"]
    dt = {c: np.float32 for c in cols[2:8]}
    dt.update(DogID=np.int32, TestNum=np.int8, Behavior_1="category")
    return pd.read_csv(path, usecols=cols, dtype=dt)


def windows(df: pd.DataFrame):
    X, y, groups = [], [], []
    for (dog, test), seg in df.groupby(["DogID", "TestNum"], sort=False):
        a = seg[["ANeck_x", "ANeck_y", "ANeck_z"]].to_numpy()
        g = seg[["GNeck_x", "GNeck_y", "GNeck_z"]].to_numpy()
        lab = seg["Behavior_1"].map(LABEL_MAP).fillna(-1).to_numpy(np.int8)

        # 100Hz → 50Hz: 두 샘플 평균
        n = len(a) // 2 * 2
        a = a[:n].reshape(-1, 2, 3).mean(axis=1)
        g = g[:n].reshape(-1, 2, 3).mean(axis=1)
        lab = lab[:n:2]

        for cls in range(len(CLASSES)):
            hop = HOP[cls]
            hit = (lab == cls).astype(np.float32)
            # 창마다 해당 라벨 비율 (누적합으로 빠르게)
            cs = np.concatenate([[0], np.cumsum(hit)])
            for s in range(0, len(lab) - WIN + 1, hop):
                if (cs[s + WIN] - cs[s]) / WIN < PURITY[cls]:
                    continue
                # 다른 클래스가 더 많은 창은 버린다
                w = lab[s:s + WIN]
                counts = np.bincount(w[w >= 0], minlength=len(CLASSES))
                if counts.argmax() != cls and cls != 3:
                    continue
                X.append(features(a[s:s + WIN], g[s:s + WIN]))
                y.append(cls)
                groups.append(dog)
    return np.array(X), np.array(y), np.array(groups)


def main():
    csv, out_h = sys.argv[1], sys.argv[2]
    print("데이터 읽는 중…", flush=True)
    df = load(csv)
    print(f"  {len(df):,} 행", flush=True)

    X, y, groups = windows(df)
    print("창 개수:", {CLASSES_KO[c]: int((y == c).sum()) for c in range(len(CLASSES))}, flush=True)

    def make():
        return RandomForestClassifier(
            n_estimators=15, max_depth=10, min_samples_leaf=5,
            class_weight="balanced", n_jobs=-1, random_state=0,
        )

    # 개 단위 교차검증
    pred = np.zeros_like(y)
    for tr, te in GroupKFold(n_splits=5).split(X, y, groups):
        m = make().fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
    print("\n== 처음 보는 개에 대한 정확도 (5-fold, 개 단위) ==")
    print(classification_report(y, pred, target_names=CLASSES_KO, digits=3))
    print("혼동행렬 (행=정답, 열=예측):", CLASSES_KO)
    print(confusion_matrix(y, pred))

    # 전체로 최종 학습 → C 헤더
    model = make().fit(X, y)
    imp = sorted(zip(model.feature_importances_, NAMES), reverse=True)
    print("\n중요 특징:", ", ".join(f"{n} {v:.2f}" for v, n in imp[:6]))

    import emlearn
    c = emlearn.convert(model, method="inline", dtype="float")
    c.save(file=out_h, name="dogmodel")
    print(f"\nC 헤더 저장: {out_h}")


if __name__ == "__main__":
    main()
