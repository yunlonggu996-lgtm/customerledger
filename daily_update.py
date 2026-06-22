#!/usr/bin/env python3
"""每日增量更新：根据组织名称查记录ID，更新主表对应记录的字段。"""

import json
import sys
import os

from feishu_client import FeishuClient, FeishuError


def clear_table(client, app_token, table_id):
    """清空表中所有记录。"""
    record_ids = []
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        try:
            resp = client._request(url)
        except FeishuError:
            break
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            record_ids.append(item.get("record_id"))
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    if record_ids:
        client.delete_records(app_token, table_id, record_ids, chunk_size=50)


def fetch_latest_data(output_file=None):
    """从 boss-api 拉取最新数据。"""
    from api_client import ApiClient
    from config import DEFAULT_PAGE_SIZE

    client = ApiClient()
    print("正在从 boss-api 拉取最新数据...")
    records, columns, total = client.fetch_all(keyword="", page_size=DEFAULT_PAGE_SIZE)
    print(f"✓ 拉取完成：{len(records)} 条记录")

    normalized = []
    for rec in records:
        row = {}
        for k in columns:
            v = rec.get(k)
            if v is None:
                row[k] = ""
            elif isinstance(v, list):
                row[k] = ", ".join(str(x) for x in v) if v else ""
            elif isinstance(v, bool):
                row[k] = str(v)
            else:
                row[k] = v
        normalized.append(row)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({"columns": columns, "total": total, "data": normalized},
                      f, ensure_ascii=False, indent=2)
        print(f"✓ 已保存到 {output_file}")
    return normalized, columns


def load_user_mapping(client, app_token, user_table_id):
    """读取人员姓名→unionid 映射。"""
    print(f"\n读取人员映射表 {user_table_id}...")
    mapping = {}
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{user_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            person_info = fields.get("人员")
            unionid = fields.get("unionid")
            if isinstance(person_info, list) and person_info:
                name = person_info[0].get("name")
                if name and unionid:
                    mapping[name] = unionid
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    print(f"✓ 读取到 {len(mapping)} 个人员映射")
    return mapping


def load_record_id_mapping(client, app_token, record_id_table_id):
    """读取 客户名称→主表 record_id 映射。"""
    print(f"\n读取记录ID表 {record_id_table_id}...")
    mapping = {}
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{record_id_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", []) or []
        for item in items:
            fields = item.get("fields", {})
            org_name = fields.get("客户名称")
            main_record_id = fields.get("记录id")
            if org_name and main_record_id:
                if org_name in mapping:
                    print(f"  ⚠️ 客户名称重复: {org_name}")
                else:
                    mapping[org_name] = main_record_id
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    print(f"✓ 读取到 {len(mapping)} 条记录ID映射")
    return mapping


def build_payload(data, columns, user_mapping, record_id_mapping,
                   app_token, table_id, client):
    """构造需要更新的记录列表。返回 (updates, inserts, exceptions)

    updates: 已存在客户，需更新字段
    inserts: 新客户，需新增到主表
    exceptions: 人员映射缺失的客户
    """
    print("\n构造数据...")

    bitable_fields = client.get_table_fields(app_token, table_id)
    field_map = {f["field_name"]: f["field_id"] for f in bitable_fields}
    field_name_map = {f["field_id"]: f["field_name"] for f in bitable_fields}
    field_type_map = {f["field_name"]: f["type"] for f in bitable_fields}

    mapping = {}
    user_field_names = []
    date_field_names = []
    # JSON 字段名 -> 主表字段名 的别名映射
    alias_map = {
        "客户星级": "星级",
    }
    for col in columns:
        # 优先按原字段名匹配，否则尝试别名
        target_col = alias_map.get(col, col)
        if target_col not in field_map:
            continue
        ftype = field_type_map[target_col]
        if ftype == 11:
            user_field_names.append(col)
        elif ftype == 5:
            date_field_names.append(col)
            mapping[col] = field_map[target_col]
        else:
            mapping[col] = field_map[target_col]

    updates = []
    inserts = []
    exceptions = []

    def _build_fields(row):
        """构造单条记录的 fields 字典。"""
        fields = {}
        for col, field_id in mapping.items():
            val = row.get(col)
            if val is None or val == "":
                continue
            fields[field_name_map.get(field_id, field_id)] = val

        for col in user_field_names:
            val = row.get(col)
            if not val or val == "":
                continue
            names = val.split(", ") if ", " in val else [val]
            unionids = []
            missing = []
            for name in names:
                name = name.strip()
                if name in user_mapping:
                    unionids.append(user_mapping[name])
                else:
                    missing.append(name)
            if unionids:
                fields[col] = [{"id": uid} for uid in unionids]
            if missing:
                err_type = "CSM异常" if col == "客户成功" else "RPA教练异常"
                exceptions.append({"组织名称": row.get("组织名称", ""), "异常信息": err_type})
                print(f"  ⚠️ {err_type}: {row.get('组织名称', '')}")
        return fields

    for row in data:
        org_name = row.get("组织名称", "")
        if not org_name:
            continue

        record_id = record_id_mapping.get(org_name)
        fields = _build_fields(row)

        if not fields:
            continue

        if record_id:
            updates.append({"record_id": record_id, "fields": fields})
        else:
            inserts.append({"org_name": org_name, "fields": fields})

    return updates, inserts, exceptions


def update_records_batch(client, app_token, table_id, updates, chunk_size=200):
    """批量更新记录。"""
    total = len(updates)
    success = 0
    failed_ids = []
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update"
    for i in range(0, total, chunk_size):
        chunk = updates[i:i + chunk_size]
        data = {"records": chunk}
        try:
            resp = client._request(url, method="POST", data=data)
            if resp.get("code") == 0:
                success += len(resp.get("data", {}).get("records", []))
            else:
                # 整批失败，全部计入失败
                for item in chunk:
                    failed_ids.append(item["record_id"])
        except FeishuError:
            for item in chunk:
                failed_ids.append(item["record_id"])
        print(f"  进度: {min(i+chunk_size, total)}/{total}", flush=True)
    return {"total": total, "success": success, "failed": failed_ids}


def write_exceptions(client, app_token, exception_table_id, exceptions):
    """写入异常记录：先清空，再合并相同企业后写入。"""
    print(f"\n清空异常表 {exception_table_id}...")
    clear_table(client, app_token, exception_table_id)

    if not exceptions:
        print("✓ 无异常记录")
        return

    merged = {}
    for exc in exceptions:
        org = exc["组织名称"]
        err = exc["异常信息"]
        if org not in merged:
            merged[org] = []
        if err not in merged[org]:
            merged[org].append(err)

    records = []
    for org, errors in merged.items():
        records.append({
            "组织名称": org,
            "异常信息": "; ".join(errors),
        })

    print(f"写入 {len(records)} 条合并后的异常记录...")
    result = client.add_records(app_token, exception_table_id, records, chunk_size=50)
    print(f"✓ 异常记录写入: {result['success']}/{result['total']}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="客户台账每日增量更新")
    parser.add_argument("--app-id", help="飞书应用 app_id")
    parser.add_argument("--app-secret", help="飞书应用 app_secret")
    parser.add_argument("--app-token", help="多维表应用 token")
    parser.add_argument("--table-id", help="主数据表 ID")
    parser.add_argument("--user-table-id", help="人员映射表 ID")
    parser.add_argument("--record-id-table-id", help="记录ID表 ID")
    parser.add_argument("--exception-table-id", help="异常记录表 ID")
    parser.add_argument("--data-file", default=None, help="本地数据文件（跳过拉取）")
    parser.add_argument("--no-fetch", action="store_true", help="不重新拉取数据")
    args = parser.parse_args()

    defaults = {}
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            defaults = json.load(f)
        print(f"✓ 加载配置文件: {config_path}")

    def get_arg(name, key=None):
        key = key or name.replace("--", "").replace("-", "_")
        val = getattr(args, key)
        if val is not None:
            return val
        return defaults.get(key.replace("_", "-"), defaults.get(key))

    args.app_id = get_arg("app-id")
    args.app_secret = get_arg("app-secret")
    args.app_token = get_arg("app-token")
    args.table_id = get_arg("table-id")
    args.user_table_id = get_arg("user-table-id") or "tblPpg5oWLDTmMnn"
    args.record_id_table_id = get_arg("record-id-table-id") or "tblq1pNSJ7H9KNpI"
    args.exception_table_id = get_arg("exception-table-id") or "tblLvuZPY6yzPssh"

    if not args.app_id or not args.app_secret or not args.app_token or not args.table_id:
        print("✗ 缺少必要参数，请通过配置文件或命令行提供", file=sys.stderr)
        sys.exit(1)

    try:
        client = FeishuClient(args.app_id, args.app_secret)
        token = client.get_tenant_access_token()
        print(f"✓ 获取 token 成功: {token[:20]}...")

        if args.data_file and os.path.exists(args.data_file):
            with open(args.data_file, "r", encoding="utf-8") as f:
                obj = json.load(f)
            data = obj["data"]
            columns = obj["columns"]
            print(f"✓ 从本地加载 {len(data)} 条记录")
        elif args.no_fetch:
            print("✗ --no-fetch 需要配合 --data-file 使用")
            sys.exit(1)
        else:
            data, columns = fetch_latest_data("customer_ledger_data.json")

        user_mapping = load_user_mapping(client, args.app_token, args.user_table_id)
        record_id_mapping = load_record_id_mapping(client, args.app_token, args.record_id_table_id)

        updates, inserts, exceptions = build_payload(
            data, columns, user_mapping, record_id_mapping,
            args.app_token, args.table_id, client
        )

        print(f"\n待更新记录: {len(updates)}")
        print(f"待新增客户: {len(inserts)}")
        print(f"新增异常: {len(exceptions)}")

        if inserts:
            print("\n新客户列表:")
            for item in inserts[:20]:
                print(f"  - {item['org_name']}")
            if len(inserts) > 20:
                print(f"  ... 等共 {len(inserts)} 个")

        if updates:
            result = update_records_batch(client, args.app_token, args.table_id, updates)
            print(f"\n更新完成: {result['success']}/{result['total']} 条成功")
            if result.get("failed"):
                print(f"  失败 {len(result['failed'])} 条: {result['failed'][:10]}")

        new_record_id_mapping = {}
        if inserts:
            print(f"\n开始新增 {len(inserts)} 个新客户到主表...")
            new_records_payload = [item["fields"] for item in inserts]
            add_result = client.add_records(args.app_token, args.table_id,
                                             new_records_payload, chunk_size=50)
            new_record_ids = add_result.get("record_ids", [])
            print(f"✓ 新增完成: {add_result['success']}/{add_result['total']} 条")

            # 记录映射关系
            for item, rid in zip(inserts, new_record_ids):
                if rid:
                    new_record_id_mapping[item["org_name"]] = rid

            # 写入记录ID表
            if new_record_id_mapping:
                print(f"\n将 {len(new_record_id_mapping)} 个新客户的 record_id 写入记录ID表...")
                record_id_records = [
                    {"客户名称": name, "记录id": rid}
                    for name, rid in new_record_id_mapping.items()
                ]
                rid_result = client.add_records(args.app_token, args.record_id_table_id,
                                                record_id_records, chunk_size=50)
                print(f"✓ 记录ID写入: {rid_result['success']}/{rid_result['total']} 条")

        write_exceptions(client, args.app_token, args.exception_table_id, exceptions)

        print("\n=== 增量更新完成 ===")

    except FeishuError as e:
        print(f"飞书 API 错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
