#!/usr/bin/env python3
"""清理飞书多维表中业务区域名称≠浙江业务组的记录。

执行步骤：
1. 读取主表中所有业务区域≠浙江业务组的记录
2. 删除主表中对应的记录
3. 删除记录ID表中对应的记录
"""

import json
import os
import sys

from feishu_client import FeishuClient, FeishuError


def load_main_records_non_zhejiang(client, app_token, main_table_id):
    """读取主表中业务区域名称≠浙江业务组的所有记录。返回 [(record_id, 组织名称), ...]"""
    records = []
    page_token = ""
    total = 0
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{main_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            biz_area = fields.get("业务区域名称", "")
            if biz_area and biz_area.strip() != "浙江业务组":
                records.append({
                    "record_id": item.get("record_id"),
                    "org_name": fields.get("组织名称", ""),
                    "biz_area": biz_area,
                })
        total += len(items)
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    return records, total


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
            print(f"  删除失败: {resp.get('msg')}")
        print(f"  进度: {min(i + chunk_size, total)}/{total}", flush=True)
    return success


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
        print("✗ 配置不完整，需要 app_id, app_secret, app_token, table_id, record_id_table_id", file=sys.stderr)
        sys.exit(1)

    try:
        client = FeishuClient(app_id, app_secret)
        client.get_tenant_access_token()
        print("✓ 飞书 Token 获取成功")

        # Step 1: 读取主表中业务区域≠浙江业务组的记录
        print(f"\n读取主表 {main_table_id} 中业务区域≠浙江业务组的记录...")
        to_delete, total = load_main_records_non_zhejiang(client, app_token, main_table_id)
        print(f"  主表共 {total} 条记录，其中 {len(to_delete)} 条业务区域≠浙江业务组")

        if not to_delete:
            print("\n✓ 无需清理，记录已符合要求")
            return

        print("\n待删除的记录：")
        for i, r in enumerate(to_delete, 1):
            print(f"  {i:3d}. {r['org_name']} （业务区域：{r['biz_area']}）")

        # Step 2: 确认删除
        confirm = input("\n确认删除以上所有记录？(输入 'yes' 确认): ").strip()
        if confirm.lower() != "yes":
            print("✗ 用户取消删除")
            sys.exit(0)

        # Step 3: 删除主表记录
        main_record_ids = [r["record_id"] for r in to_delete]
        org_names_to_delete = {r["org_name"] for r in to_delete}

        print(f"\n删除主表中 {len(main_record_ids)} 条记录...")
        deleted_main = delete_records(client, app_token, main_table_id, main_record_ids)
        print(f"✓ 主表删除完成：{deleted_main}/{len(main_record_ids)} 条")

        # Step 4: 删除记录ID表中对应的映射
        print(f"\n读取记录ID表 {record_id_table_id} ...")
        rid_mapping = load_record_id_mapping(client, app_token, record_id_table_id)
        print(f"  记录ID表共 {len(rid_mapping)} 条映射")

        rid_to_delete = [rid for org, rid in rid_mapping.items() if org in org_names_to_delete]
        org_to_delete = [org for org in org_names_to_delete if org in rid_mapping]

        if rid_to_delete:
            print(f"\n删除记录ID表中 {len(rid_to_delete)} 条对应映射...")
            deleted_rid = delete_records(client, app_token, record_id_table_id, rid_to_delete)
            print(f"✓ 记录ID表删除完成：{deleted_rid}/{len(rid_to_delete)} 条")
            print(f"  删除映射的客户：")
            for i, org in enumerate(sorted(org_to_delete), 1):
                print(f"    {i}. {org}")
        else:
            print("\n✓ 记录ID表中无对应映射需要删除")

        print(f"\n=== 清理完成 ===")
        print(f"主表删除:     {deleted_main} 条")
        print(f"记录ID删除:   {len(rid_to_delete)} 条")

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
