#!/usr/bin/env python3
"""对比 BOSS API 与飞书多维表主表中的组织名称差异。"""

import json
import os
import sys

from api_client import ApiClient, ApiError
from feishu_client import FeishuClient, FeishuError
from config import DEFAULT_PAGE_SIZE, _config, _config_path


def fetch_boss_orgs():
    """从 BOSS API 拉取所有客户的组织名称。"""
    from fetch_data import _normalize_value

    client = ApiClient()
    print("正在从 BOSS API 拉取数据...")
    records, columns, total = client.fetch_all(keyword="", page_size=DEFAULT_PAGE_SIZE)
    print(f"✓ BOSS API 拉取完成：{len(records)} 条记录")

    orgs = set()
    for rec in records:
        name = rec.get("组织名称")
        if name:
            orgs.add(_normalize_value(name))
    print(f"  → 提取到 {len(orgs)} 个组织名称")
    return orgs


def fetch_bitable_orgs(client, app_token, main_table_id, table_name_map):
    """从飞书多维表主表读取所有记录的组织名称。"""
    name = table_name_map.get(main_table_id, main_table_id)
    print(f"\n正在从飞书多维表「{name}」读取数据...")

    orgs = set()
    page_token = ""
    total_loaded = 0
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{main_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        try:
            resp = client._request(url)
        except FeishuError as e:
            print(f"✗ 读取失败: {e}")
            break
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            org_name = fields.get("组织名称") or fields.get("组织简称") or ""
            if org_name:
                orgs.add(str(org_name).strip())
        total_loaded += len(items)
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break

    print(f"✓ 飞书多维表读取完成：{total_loaded} 条记录，{len(orgs)} 个组织名称")
    return orgs


def compare_orgs(boss_orgs, bitable_orgs):
    """对比两个来源的组织名称，打印差异。"""
    only_in_boss = boss_orgs - bitable_orgs
    only_in_bitable = bitable_orgs - boss_orgs
    common = boss_orgs & bitable_orgs

    print("\n" + "=" * 60)
    print("对比结果")
    print("=" * 60)
    print(f"BOSS API 组织数量:     {len(boss_orgs)}")
    print(f"飞书多维表组织数量:     {len(bitable_orgs)}")
    print(f"两边都有:             {len(common)}")
    print(f"仅在 BOSS API 中:      {len(only_in_boss)}")
    print(f"仅在飞书多维表中:      {len(only_in_bitable)}")

    if only_in_boss:
        print("\n" + "-" * 60)
        print(f"以下 {len(only_in_boss)} 个组织存在于 BOSS API，但不存在于飞书多维表：")
        print("-" * 60)
        for i, name in enumerate(sorted(only_in_boss), 1):
            print(f"  {i:3d}. {name}")

    if only_in_bitable:
        print("\n" + "-" * 60)
        print(f"以下 {len(only_in_bitable)} 个组织存在于飞书多维表，但不存在于 BOSS API：")
        print("-" * 60)
        for i, name in enumerate(sorted(only_in_bitable), 1):
            print(f"  {i:3d}. {name}")

    if not only_in_boss and not only_in_bitable:
        print("\n✓ 两边数据完全一致，无差异！")

    return only_in_boss, only_in_bitable


def main():
    # 加载配置
    if not os.path.exists(_config_path):
        print(f"✗ 未找到配置文件: {_config_path}", file=sys.stderr)
        print("请先创建 config.json（参考 config.example.json）", file=sys.stderr)
        sys.exit(1)

    with open(_config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    app_id = config.get("app_id")
    app_secret = config.get("app_secret")
    app_token = config.get("app_token")
    main_table_id = config.get("table_id")

    if not all([app_id, app_secret, app_token, main_table_id]):
        print("✗ 配置不完整，需要 app_id, app_secret, app_token, table_id", file=sys.stderr)
        sys.exit(1)

    try:
        # 1. 从 BOSS API 拉取数据
        boss_orgs = fetch_boss_orgs()

        # 2. 从飞书多维表读取数据
        client = FeishuClient(app_id, app_secret)
        client.get_tenant_access_token()
        print(f"✓ 飞书 Token 获取成功")

        tables = client.list_tables(app_token)
        table_name_map = {t["table_id"]: t["name"] for t in tables}
        print(f"✓ 获取到 {len(tables)} 个数据表")

        bitable_orgs = fetch_bitable_orgs(client, app_token, main_table_id, table_name_map)

        # 3. 对比
        compare_orgs(boss_orgs, bitable_orgs)

    except ApiError as e:
        print(f"\n✗ BOSS API 错误: {e}", file=sys.stderr)
        sys.exit(1)
    except FeishuError as e:
        print(f"\n✗ 飞书 API 错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ 未知错误: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
