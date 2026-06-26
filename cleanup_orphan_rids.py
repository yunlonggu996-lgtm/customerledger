#!/usr/bin/env python3
"""清理记录ID表中已在主表被删除的孤儿映射。"""

import json
import os
import sys

from feishu_client import FeishuClient, FeishuError


def load_main_table_orgs(client, app_token, main_table_id):
    """读取主表中所有组织的名称集合。"""
    orgs = set()
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{main_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            org = item.get("fields", {}).get("组织名称", "")
            if org:
                orgs.add(org)
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    return orgs


def load_record_id_table(client, app_token, table_id):
    """读取记录ID表，返回 [(record_id, org_name), ...]"""
    records = []
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            rid = item.get("record_id", "")
            org = fields.get("客户名称", "")
            if rid and org:
                records.append((rid, org))
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    return records


def delete_records(client, app_token, table_id, record_ids, chunk_size=50):
    """批量删除记录。"""
    total = len(record_ids)
    success = 0
    for i in range(0, total, chunk_size):
        chunk = record_ids[i:i + chunk_size]
        data = {"records": chunk}
        resp = client._request(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
            method="POST", data=data
        )
        if resp.get("code") == 0:
            success += len(chunk)
        else:
            print(f"  批次 {i//chunk_size} 失败: {resp.get('msg')}")
        print(f"  进度: {min(i + chunk_size, total)}/{total}", flush=True)
    return success


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    client = FeishuClient(config["app_id"], config["app_secret"])
    client.get_tenant_access_token()
    print("✓ 飞书 Token 获取成功")

    # 读取主表所有组织
    print("\n读取主表组织名称...")
    main_orgs = load_main_table_orgs(client, config["app_token"], config["table_id"])
    print(f"✓ 主表组织数: {len(main_orgs)}")

    # 读取记录ID表所有记录
    print("读取记录ID表...")
    rid_records = load_record_id_table(client, config["app_token"], config["record_id_table_id"])
    print(f"✓ 记录ID表记录数: {len(rid_records)}")

    # 找出孤儿记录（在记录ID表但不在主表）
    orphans = [(rid, org) for rid, org in rid_records if org not in main_orgs]

    print(f"\n记录ID表中孤儿映射（主表已无此客户）: {len(orphans)} 条")

    if not orphans:
        print("✓ 无孤儿映射，无需清理")
        return

    print("\n待删除的孤儿映射：")
    for i, (rid, org) in enumerate(sorted(orphans, key=lambda x: x[1]), 1):
        print(f"  {i:3d}. {org}")

    confirm = input(f"\n确认删除这 {len(orphans)} 条孤儿映射？(输入 'yes' 确认): ").strip()
    if confirm.lower() != "yes":
        print("✗ 用户取消")
        sys.exit(0)

    orphan_rids = [rid for rid, _ in orphans]
    print(f"\n删除 {len(orphan_rids)} 条记录...")
    deleted = delete_records(client, config["app_token"], config["record_id_table_id"], orphan_rids)
    print(f"✓ 删除完成: {deleted}/{len(orphan_rids)} 条")

    print(f"\n=== 清理完成 ===")
    print(f"记录ID表原有: {len(rid_records)} 条")
    print(f"删除孤儿:     {deleted} 条")
    print(f"记录ID表现有: {len(rid_records) - deleted} 条")


if __name__ == "__main__":
    main()
