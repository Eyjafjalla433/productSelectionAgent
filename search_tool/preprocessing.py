import pandas as pd


def clean_text(value):
    """处理缺失值，保持与训练时的文本处理一致。"""
    if pd.isna(value):
        return ""
    return str(value).strip()


def build_product_text(row):
    """商品标题 + 卖点，与 title_bullet 实验的输入保持一致。"""
    title = clean_text(row["product_title"])
    bullet = clean_text(row["product_bullet_point"])

    if not bullet:
        return title

    return f"{title}\nFeatures: {bullet}"