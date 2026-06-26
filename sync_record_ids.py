#!/usr/bin/env python3
"""同步记录ID表：确保主表中每条浙江业务组客户都在记录ID表中有映射。"""

import json
import os
import sys

from feishu_client import FeishuClient, FeishuError


def load_main_table_records(client, app_token, main_table_id):
    """读取主表中所有浙江业务组客户的 record_id 和组织名称。"""
    records = []
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{main_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            biz_area = fields.get("业务区域名称", "")
            # 只处理浙江业务组
            if biz_area and biz_area.strip() == "浙江业务组":
                records.append({
                    "record_id": item.get("record_id"),
                    "org_name": fields.get("组织名称", ""),
                })
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    return records


def load_record_id_mapping(client, app_token, record_id_table_id):
    """读取记录ID表中所有映射。返回 {org_name: record_id}"""
    mapping = {}
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{record_id_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            org_name = fields.get("客户名称", "")
            rid = fields.get("记录id", "")
            if org_name and rid:
                mapping[org_name] = rid
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    return mapping


def add_record_id_mappings(client, app_token, record_id_table_id, records, chunk_size=50):
    """批量新增记录ID映射。"""
    total = len(records)
    success = 0
    record_ids = []
    for i in range(0, total, chunk_size):
        chunk = records[i:i + chunk_size]
        payload = [{"fields": {"客户名称": r["org_name"], "记录id": r["record_id"]}} for r in chunk]
        resp = client._request(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{record_id_table_id}/records/batch_create",
            method="POST", data={"records": payload}
        )
        if resp.get("code") == 0:
            recs = resp.get("data", {}).get("records", [])
            success += len(recs)
            record_ids.extend([r.get("record_id") for r in recs if r.get("record_id")])
        else:
            print(f"  批次 {i//chunk_size} 失败: {resp.get('msg')}")
        print(f"  进度: {min(i + chunk_size, total)}/{total}", flush=True)
    return {"total": total, "success": success, "record_ids": record_ids}


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if not os.path.exists(config_path):
        print(f"✗ 未找到配置文件: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    app_id = config.get("app_id")
    app_secret = config.get("app_secret")
    app_token = config.get("app_token")
    main_table_id = config.get("table_id")
    record_id_table_id = config.get("record_id_table_id")

    if not all([app_id, app_secret, app_token, main_table_id, record_id_table_id]):
        print("✗ 配置不完整", file=sys.stderr)
        sys.exit(1)

    try:
        client = FeishuClient(app_id, app_secret)
        client.get_tenant_access_token()
        print("✓ 飞书 Token 获取成功")

        # Step 1: 读取主表中浙江业务组的客户
        print("\n读取主表中浙江业务组客户...")
        main_records = load_main_table_records(client, app_token, main_table_id)
        print(f"✓ 主表浙江业务组客户: {len(main_records)} 条")

        # Step 2: 读取记录ID表现有映射
        print("读取记录ID表...")
        rid_mapping = load_record_id_mapping(client, app_token, record_id_table_id)
        print(f"✓ 记录ID表已有映射: {len(rid_mapping)} 条")

        # Step 3: 找出需要新增的映射
        main_orgs = {r["org_name"]: r["record_id"] for r in main_records}
        to_add = [(org, rid) for org, rid in main_orgs.items() if org not in rid_mapping]

        print(f"\n需要新增的映射: {len(to_add)} 条")

        if not to_add:
            print("✓ 记录ID表已是最新，无需同步")
            return

        # 列出需要新增的
        print("\n待新增映射的客户：")
        for i, (org, rid) in enumerate(sorted(to_add), 1):
            print(f"  {i:3d}. {org}")

        # Step 4: 确认执行
        confirm = input(f"\n确认新增 {len(to_add)} 条映射到记录ID表？(输入 'yes' 确认): ").strip()
        if confirm.lower() != "yes":
            print("✗ 用户取消")
            sys.exit(0)

        # Step 5: 写入
        records_to_add = [{"org_name": org, "record_id": rid} for org, rid in to_add]
        print("\n写入记录ID表...")
        result = add_record_id_mappings(client, app_token, record_id_table_id, records_to_add)
        print(f"✓ 写入完成: {result['success']}/{result['total']} 条")

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
