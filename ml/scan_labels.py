"""
공개 데이터셋(DogMoveData.csv)의 행동 라벨 분포를 센다.

    python ml/scan_labels.py C:\\Users\\ehdwn\\pebble-data\\raw\\csv\\DogMoveData.csv

1.9GB 라 한 번에 못 올린다. 라벨 열만 조각으로 읽는다.
100Hz 이므로 행 수 / 100 = 초.
"""

import sys
from collections import Counter

import pandas as pd

path = sys.argv[1]
cols = ["DogID", "Task", "Behavior_1", "Behavior_2", "Behavior_3"]

b1 = Counter()
any_b = Counter()
task = Counter()
dogs_per_label: dict[str, set] = {}
rows = 0

for chunk in pd.read_csv(path, usecols=cols, chunksize=1_000_000, dtype=str):
    rows += len(chunk)
    b1.update(chunk["Behavior_1"].tolist())
    task.update(chunk["Task"].tolist())
    for c in ["Behavior_1", "Behavior_2", "Behavior_3"]:
        sub = chunk[[c, "DogID"]]
        sub = sub[sub[c] != "<undefined>"]
        any_b.update(sub[c].tolist())
        for lab, dog in zip(sub[c], sub["DogID"]):
            dogs_per_label.setdefault(lab, set()).add(dog)

print(f"총 {rows:,} 행 = {rows / 100 / 3600:.1f} 시간 (100Hz)\n")

print("== Behavior_1 (주 라벨) ==")
for k, v in b1.most_common():
    print(f"  {k:28s} {v / 100 / 60:8.1f} 분")

print("\n== Behavior_1~3 어디든 등장 (분, 개 수) ==")
for k, v in any_b.most_common():
    print(f"  {k:28s} {v / 100 / 60:8.1f} 분   {len(dogs_per_label[k]):2d} 마리")

print("\n== Task ==")
for k, v in task.most_common():
    print(f"  {k:28s} {v / 100 / 60:8.1f} 분")
