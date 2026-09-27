import numpy as np
import pandas as pd
from sklearn.metrics import ndcg_score

truth = pd.read_parquet(
    "shopping_queries_dataset/shopping_queries_dataset_examples.parquet"
)
truth = truth[
    (truth["small_version"] == 1)
    & (truth["split"] == "test")
    & (truth["product_locale"] == "us")
][["query_id", "product_id", "esci_label"]]

pred = pd.read_csv(
    "ranking/hypothesis/task_1_us_title_bullet.csv"
)

# 官方预测文件的行序就是排名，越靠前分数越高
pred["score"] = -pred.groupby("query_id").cumcount()

# 对照独立的官方测试集，确认候选没有遗漏或多出
data = truth.merge(
    pred,
    on=["query_id", "product_id"],
    how="outer",
    validate="one_to_one",
    indicator=True,
)
assert data["_merge"].eq("both").all(), "预测候选与测试集不一致"

# 按论文定义：E > S > C > I
gain = {"E": 1.0, "S": 0.1, "C": 0.01, "I": 0.0}
data["gain"] = data["esci_label"].map(gain)
assert data["gain"].notna().all()

scores = []
for _, group in data.groupby("query_id"):
    y_true = group["gain"].to_numpy()
    y_score = group["score"].to_numpy()

    if len(group) == 1:
        score = float(y_true[0] > 0)
    else:
        score = ndcg_score(
            y_true[None, :],
            y_score[None, :],
            ignore_ties=True,
        )
    scores.append(score)

print(f"测试 query 数：{len(scores)}")
print(f"US nDCG（全列表）：{np.mean(scores):.6f}")