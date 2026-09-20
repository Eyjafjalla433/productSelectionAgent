from pathlib import Path

import bm25s
import pandas as pd

from .preprocessing import build_product_text


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "shopping_queries_dataset"
    / "shopping_queries_dataset_products.parquet"
)
ARTIFACTS = ROOT / "search_tool" / "artifacts"


def main():
    # 防止误覆盖已经生成的商品表和索引
    if ARTIFACTS.exists():
        raise FileExistsError(
            f"{ARTIFACTS} 已存在。若要重建，请先将旧目录改名备份。"
        )

    print("读取美国站商品……")
    products = pd.read_parquet(
        SOURCE,
        filters=[("product_locale", "==", "us")],
        columns=[
            "product_id",
            "product_locale",
            "product_title",
            "product_bullet_point",
            "product_brand",
            "product_color",
        ],
    )

    products = (
        products.dropna(subset=["product_id"])
        .drop_duplicates(["product_locale", "product_id"])
        .reset_index(drop=True)
    )

    print("生成与训练一致的商品文本……")
    products["product_text"] = products.apply(
        build_product_text, axis=1
    )

    products = products[
        products["product_text"].str.strip().ne("")
    ].reset_index(drop=True)

    if products.empty:
        raise ValueError("没有可建立索引的美国站商品")

    print(f"商品数量：{len(products):,}")

    # 不删除停用词，保留 no / without 等词。
    # BM25本身仍然只是词匹配，不会理解否定关系。
    tokens = bm25s.tokenize(
        products["product_text"].tolist(),
        stopwords=[],
    )

    print("建立 BM25 索引……")
    retriever = bm25s.BM25(method="lucene")
    retriever.index(tokens)

    ARTIFACTS.mkdir(parents=True)

    # 行号是索引与商品表的对应关系；保存后不要单独重排这张表
    products.to_parquet(
        ARTIFACTS / "products.parquet",
        index=False,
    )
    retriever.save(str(ARTIFACTS / "retrieval_index"))

    print(f"完成，保存至：{ARTIFACTS}")


if __name__ == "__main__":
    main()