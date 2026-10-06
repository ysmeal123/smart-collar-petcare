"""
추출기 성능 측정.

    python -m bench_extract

키가 없으면 규칙 사전을, 있으면 LLM 을 잰다. **같은 문장으로 잰다.**
그래야 "키를 넣으면 뭐가 나아지나"에 숫자로 답할 수 있다.

측정하는 것:

    재현율   잡아야 할 것 중 몇 개를 잡았나      (놓치면 처방이 안 멈춘다)
    정밀도   잡은 것 중 몇 개가 맞았나          (틀리면 엉뚱하게 멈춘다)

**둘의 무게가 다르다.** 이 경로는 처방을 멈추는 쪽으로만 작동하므로,
놓치면 "평소대로 처방" 이고 잘못 잡으면 "필요한 영양제를 안 준다" 다.
둘 다 나쁘지만 어느 쪽도 과다 급여로는 가지 않는다.

문장은 보호자가 실제로 쓸 법한 말로 골랐다. 사전에 있는 단어를 그대로
쓴 문장만 모으면 규칙 사전이 100% 가 나오고, 그건 아무것도 말해주지 않는다.
"""

from __future__ import annotations

import sys

from agent.extract import extract

#: (문장, 정답 key 집합)
#:
#: 정답은 "이 문장을 읽은 사람이 당연히 체크할 항목" 이다.
#: 애매하면 넣지 않았다 - 사람도 갈리는 문장으로 기계를 재면 안 된다.
CASES: list[tuple[str, set[str]]] = [
    # --- 사전 단어를 그대로 쓴 문장 (쉬움) ---
    ("샴푸를 바꿨어요", {"shampoo_changed"}),
    ("어제 토했어요", {"vomit"}),
    ("다리를 절어요", {"limping"}),
    ("풀밭에서 놀았어요", {"env_exposure"}),
    ("이사했어요", {"env_changed"}),
    ("목욕시켰어요", {"recent_bath"}),
    ("귀에서 냄새가 나요", {"ear_smell"}),
    ("간식을 많이 줬어요", {"treats"}),

    # --- 같은 뜻, 다른 말 (보통) ---
    ("바디워시를 새 걸로 교체했어요", {"shampoo_changed"}),
    ("사료 브랜드를 다른 걸로 시도해봤어요", {"food_changed"}),
    ("뒷다리를 살짝 끄는 것 같아요", {"limping"}),
    ("통 입에 안 대요", {"eating_less"}),
    ("밥그릇을 반이나 남겼어요", {"eating_less"}),
    ("윗집이 밤새 쿵쿵거렸어요", {"noise"}),
    ("소파에 올라오려다 포기하더라고요", {"stairs_avoid"}),
    ("동물병원에서 미용하고 왔어요", {"recent_bath"}),
    ("배를 자꾸 핥아서 털이 벗겨졌어요", {"skin_visible"}),
    ("요 며칠 장마라 밖에 못 나갔어요", {"weather_cold"}),
    ("귓속이 거뭇거뭇해요", {"ear_discharge"}),
    ("새벽에 자꾸 깨서 낑낑거려요", set()),          # 수면 문제지만 묻는 항목이 아니다

    # --- 부정문 (어려움) ---
    ("토는 안 했어요", set()),
    ("설사는 없었어요", set()),
    ("사료는 그대로예요", set()),
    ("샴푸 바꾼 적 없어요", set()),
    ("절지는 않아요", set()),
    ("계단은 잘 올라가요", set()),
    ("밥은 잘 먹어요", set()),

    # --- 부정형이 곧 증상 (제일 어려움) ---
    ("계단을 안 올라가려고 해요", {"stairs_avoid"}),
    ("요즘 산책을 못 시켰어요", {"weather_cold"}),
    ("밥을 잘 안 먹어요", {"eating_less"}),
    ("침대에 못 뛰어올라요", {"stairs_avoid"}),

    # --- 여러 건이 섞인 문장 ---
    ("목욕은 시켰는데 토는 안 했어요", {"recent_bath"}),
    ("샴푸도 바꾸고 사료도 바꿨어요", {"shampoo_changed", "food_changed"}),
    ("3일 전에 풀밭에서 뒹굴었는데 그 뒤부터 목을 긁어요", {"env_exposure"}),
    ("어제부터 통 입에 안 대고, 윗집이 밤새 쿵쿵거렸어요",
     {"eating_less", "noise"}),
    ("비 와서 산책을 줄였더니 계단도 안 올라가요",
     {"weather_cold", "stairs_avoid"}),

    # --- 해당 없음 (오탐 확인) ---
    ("오늘 기분이 좋아 보여요", set()),
    ("산책하면서 사진 많이 찍었어요", set()),
    ("새 장난감을 사줬어요", set()),
    ("영양제를 더 주세요", set()),
    ("오늘 처음 써봅니다", set()),
]


def main() -> int:
    from agent import llm

    engine = "LLM" if llm.enabled() else "규칙 사전"
    print(f"\n추출기: {engine}")
    if llm.enabled():
        print(f"모델:   {llm._provider()} / {llm._model()}")
    print("=" * 72)

    tp = fp = fn = 0
    wrong: list[tuple[str, set, set]] = []

    for text, want in CASES:
        got = set(extract(text).answers)
        hit = got & want
        tp += len(hit)
        fp += len(got - want)
        fn += len(want - got)
        if got != want:
            wrong.append((text, want, got))

    exact = len(CASES) - len(wrong)
    recall = tp / (tp + fn) if tp + fn else 1.0
    precision = tp / (tp + fp) if tp + fp else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    if wrong:
        print("\n틀린 문장\n" + "-" * 72)
        for text, want, got in wrong:
            print(f"  {text}")
            missed, extra = want - got, got - want
            if missed:
                print(f"      놓침: {sorted(missed)}")
            if extra:
                print(f"      오탐: {sorted(extra)}")

    print("\n" + "=" * 72)
    print(f"  문장 단위 정확도   {exact}/{len(CASES)}  ({exact / len(CASES):.0%})")
    print(f"  재현율             {recall:.0%}   (잡아야 할 {tp + fn}개 중 {tp}개)")
    print(f"  정밀도             {precision:.0%}   (잡은 {tp + fp}개 중 {tp}개)")
    print(f"  F1                 {f1:.2f}")
    print()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    sys.exit(main())
