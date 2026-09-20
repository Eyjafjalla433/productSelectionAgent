from functools import lru_cache
from pathlib import Path

import bm25s
import numpy as np
import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


PACKAGE_DIR = Path(__file__).resolve().parent
ARTIFACTS = PACKAGE_DIR / "artifacts"
MODEL_DIR = PACKAGE_DIR / "model"


class ProductSearchTool:
    def __init__(self):
        if not MODEL_DIR.is_dir():
            raise FileNotFoundError(f"找不到模型目录：{MODEL_DIR}")

        self.products = pd.read_parquet(
            ARTIFACTS / "products.parquet"
        )
        self.retriever = bm25s.BM25.load(
            str(ARTIFACTS / "retrieval_index"),
            mmap=True,
        )

        self.device = torch.device(
            "cuda" if torch.cuda.is_available() else "cpu"
        )

        # 只读取你训练后保存的本地模型
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(MODEL_DIR),
            local_files_only=True,
        )
        self.model = AutoModelForSequenceClassification.from_pretrained(
            str(MODEL_DIR),
            local_files_only=True,
        ).to(self.device)

        self.model.eval()

        self.id_to_row = {
            str(product_id): i
            for i, product_id in enumerate(self.products["product_id"])
        }

    def search(self, query, top_k=10, candidate_k=100):
        """英文美国站商品搜索，返回按相关性降序排列的ID和分数。"""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 必须是非空字符串")

        if not 1 <= top_k <= candidate_k <= 1000:
            raise ValueError(
                "参数必须满足 1 <= top_k <= candidate_k <= 1000"
            )

        query = query.strip()

        query_tokens = bm25s.tokenize(
            [query],
            stopwords=[],
            return_ids=False,
        )
        if not query_tokens[0]:
            return []

        # 返回索引中的商品行号
        row_ids, bm25_scores = self.retriever.retrieve(
            query_tokens,
            k=min(candidate_k, len(self.products)),
        )

        # 没有词匹配的商品不进入重排
        matched = bm25_scores[0] > 0
        row_ids = row_ids[0][matched]

        if len(row_ids) == 0:
            return []

        candidates = self.products.iloc[row_ids].copy()
        texts = candidates["product_text"].tolist()

        # 沿用官方美国站推理方式：读取原始logit作为分数
        scores = []
        batch_size = 16

        with torch.inference_mode():
            for start in range(0, len(texts), batch_size):
                batch_texts = texts[start:start + batch_size]

                inputs = self.tokenizer(
                    [query] * len(batch_texts),
                    batch_texts,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                ).to(self.device)

                logits = self.model(**inputs).logits
                scores.extend(
                    logits.reshape(-1).cpu().tolist()
                )

        if not np.isfinite(scores).all():
            raise RuntimeError("模型输出包含非有限分数")

        candidates["score"] = scores
        candidates = candidates.sort_values(
            ["score", "product_id"],
            ascending=[False, True],
        ).head(top_k)

        return [
            {
                "product_id": str(row.product_id),
                "score": float(row.score),
            }
            for row in candidates.itertuples()
        ]

    def get_details(self, product_ids):
        """按给定ID顺序返回商品资料，供Agent解释或前端展示。"""
        results = []

        for product_id in product_ids:
            product_id = str(product_id)
            row_id = self.id_to_row.get(product_id)

            if row_id is None:
                results.append({
                    "product_id": product_id,
                    "found": False,
                })
                continue

            row = self.products.iloc[row_id]

            def text(field):
                value = row[field]
                return "" if pd.isna(value) else str(value)

            results.append({
                "product_id": product_id,
                "found": True,
                "product_locale": "us",
                "title": text("product_title"),
                "brand": text("product_brand"),
                "color": text("product_color"),
                "bullet_point": text("product_bullet_point"),
                "product_url": f"https://www.amazon.com/dp/{product_id}",
                "url_verified": False,
            })

        return results


# 同一Python进程内复用模型和索引，不重复加载
@lru_cache(maxsize=1)
def _get_tool():
    return ProductSearchTool()


def search_products(query: str, top_k: int = 10) -> list:
    """搜索美国站商品。query应为英文，返回product_id和相关性分数。"""
    return _get_tool().search(
        query=query,
        top_k=top_k,
        candidate_k=max(100, top_k),
    )


def get_product_details(product_ids: list[str]) -> list:
    """查询商品标题、品牌、卖点和未经验证的Amazon推导链接。"""
    return _get_tool().get_details(product_ids)