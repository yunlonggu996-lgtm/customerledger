#!/usr/bin/env python3
"""检查记录ID表与主表的同步状态。"""

import json
import os
import sys

from feishu_client import FeishuClient, FeishuError


def load_main_table(client, app_token, main_table_id):
    """返回 {(org_name, record_id), ...}"""
    records = set()
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{main_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            org = fields.get("组织名称", "")
            rid = item.get("record_id", "")
            if org and rid:
                records.add((org, rid))
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    return records


def load_record_id_table(client, app_token, table_id):
    """返回 {org_name: record_id, ...}"""
    mapping = {}
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            org = fields.get("客户名称", "")
            rid = fields.get("记录id", "")
            if org and rid:
                mapping[org] = rid
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    return mapping


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    client = FeishuClient(config["app_id"], config["app_secret"])
    client.get_tenant_access_token()

    main_records = load_main_table(client, config["app_token"], config["table_id"])
    rid_mapping = load_record_id_table(client, config["app_token"], config["record_id_table_id"])

    main_orgs = {org for org, _ in main_records}
    rid_orgs = set(rid_mapping.keys())

    print(f"主表浙江客户数:     {len(main_orgs)}")
    print(f"记录ID表映射数:     {len(rid_orgs)}")
    print(f"两边都有映射:       {len(main_orgs & rid_orgs)}")
    print(f"主表有但记录ID表无: {len(main_orgs - rid_orgs)}")
    print(f"记录ID表有但主表无: {len(rid_orgs - main_orgs)}")

    missing = main_orgs - rid_orgs
    extra = rid_orgs - main_orgs

    if missing:
        print(f"\n主表有、记录ID表缺失的 {len(missing)} 个客户：")
        for i, org in enumerate(sorted(missing), 1):
            print(f"  {i:3d}. {org}")

    if extra:
        print(f"\n记录ID表有、主表无的 {len(extra)} 个客户：")
        for i, org in enumerate(sorted(extra), 1):
            print(f"  {i:3d}. {org}")

    if not missing and not extra:
        print("\n✓ 两表完全同步")


if __name__ == "__main__":
    main()
