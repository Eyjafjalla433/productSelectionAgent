import json

from .tool import search_products, get_product_details


def main():
    print("第一次搜索会加载模型和索引，请稍等。")

    while True:
        query = input("\n输入英文 query，输入 exit 退出：").strip()

        if query.lower() == "exit":
            break
        if not query:
            continue

        results = search_products(query, top_k=5)

        print("\n排序结果：")
        print(json.dumps(results, ensure_ascii=False, indent=2))

        details = get_product_details(
            [item["product_id"] for item in results]
        )

        print("\n对应商品：")
        for rank, item in enumerate(details, start=1):
            print(f"{rank}. {item.get('title', '商品不存在')}")
            print(f"   {item.get('product_url', '')}")


if __name__ == "__main__":
    main()