#!/usr/bin/env python3
"""客户台账数据写入飞书多维表（完整版：清空重写 + 记录ID关联）。"""

import json
import sys

from feishu_client import FeishuClient, FeishuError


def load_local_data(file_path="customer_ledger_data.json"):
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_user_mapping(client, app_token, user_table_id):
    print(f"\n读取人员映射表 {user_table_id}...")
    mapping = {}
    page_token = ""
    while True:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{user_table_id}/records"
        url += f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
        resp = client._request(url)
        items = resp.get("data", {}).get("items", [])
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


def clear_table(client, app_token, table_id):
    """清空表中所有记录。"""
    print(f"\n清空表 {table_id}...")
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
        result = client.delete_records(app_token, table_id, record_ids, chunk_size=50)
        print(f"✓ 已删除 {result['total']} 条记录")
    else:
        print("✓ 表为空，无需删除")


def sync_to_bitable(client, app_token, table_id, data, columns, user_mapping=None):
    print(f"\n开始同步 {len(data)} 条记录到多维表...")

    bitable_fields = client.get_table_fields(app_token, table_id)
    field_map = {f["field_name"]: f["field_id"] for f in bitable_fields}
    field_name_map = {f["field_id"]: f["field_name"] for f in bitable_fields}
    field_type_map = {f["field_name"]: f["type"] for f in bitable_fields}

    mapping = {}
    skipped_user_fields = []
    skipped_date_fields = []
    for col in columns:
        if col not in field_map:
            continue
        ftype = field_type_map[col]
        if ftype == 11:
            skipped_user_fields.append(col)
        elif ftype == 5:
            skipped_date_fields.append(col)
        else:
            mapping[col] = field_map[col]

    if skipped_user_fields:
        print("\n⚠️ 人员字段（type=11）将通过姓名映射写入:")
        print("  ", ", ".join(skipped_user_fields))

    if skipped_date_fields:
        print("\n⚠️ 日期字段（type=5）暂不写入:")
        print("  ", ", ".join(skipped_date_fields))

    records = []
    exceptions = []
    org_names = []

    for row in data:
        fields = {}
        for col, field_id in mapping.items():
            val = row.get(col)
            if val is None or val == "":
                continue
            fields[field_name_map.get(field_id, field_id)] = val

        org_name = row.get("组织名称", "")
        org_names.append(org_name)

        if user_mapping:
            for col in skipped_user_fields:
                val = row.get(col)
                if not val or val == "":
                    continue
                names = val.split(", ") if ", " in val else [val]
                unionids = []
                missing_names = []
                for name in names:
                    name = name.strip()
                    if name in user_mapping:
                        unionids.append(user_mapping[name])
                    else:
                        missing_names.append(name)
                if unionids:
                    fields[col] = [{"id": uid} for uid in unionids]
                if missing_names:
                    err_type = "CSM异常" if col == "客户成功" else "RPA教练异常"
                    exceptions.append({
                        "组织名称": org_name,
                        "异常信息": err_type,
                    })
                    print(f"  ⚠️ {err_type}: {org_name}")

        if fields:
            records.append(fields)

    print(f"\n有效记录: {len(records)} / {len(data)}")

    result = client.add_records(app_token, table_id, records, chunk_size=50)
    print(f"\n写入完成: {result['success']}/{result['total']} 条成功")
    return result, exceptions, org_names


def write_record_ids(client, app_token, record_id_table_id, record_ids, org_names):
    """将记录ID与组织名称对应写入记录ID表。"""
    print(f"\n写入 {len(record_ids)} 条记录ID映射到 {record_id_table_id}...")
    records = []
    for rid, name in zip(record_ids, org_names):
        records.append({
            "客户名称": name,
            "记录id": rid,
        })
    result = client.add_records(app_token, record_id_table_id, records, chunk_size=150)
    print(f"✓ 记录ID写入完成: {result['success']}/{result['total']} 条成功")
    return result


def write_exceptions(client, app_token, exception_table_id, exceptions):
    if not exceptions:
        print("\n✓ 无异常记录")
        return

    print(f"\n写入 {len(exceptions)} 条异常记录到异常表 {exception_table_id}...")
    records = []
    for exc in exceptions:
        records.append({
            "组织名称": exc["组织名称"],
            "异常信息": exc["异常信息"],
        })
    result = client.add_records(app_token, exception_table_id, records, chunk_size=150)
    print(f"✓ 异常记录写入完成: {result['success']}/{result['total']} 条成功")
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="客户台账数据写入飞书多维表")
    parser.add_argument("--app-id", required=True, help="飞书应用 app_id")
    parser.add_argument("--app-secret", required=True, help="飞书应用 app_secret")
    parser.add_argument("--app-token", required=True, help="多维表应用 token")
    parser.add_argument("--table-id", required=True, help="主数据表 ID")
    parser.add_argument("--data", default="customer_ledger_data.json", help="数据文件路径")
    parser.add_argument("--user-table-id", default="tblPpg5oWLDTmMnn", help="人员映射表 ID")
    parser.add_argument("--exception-table-id", default="tblLvuZPY6yzPssh", help="异常记录表 ID")
    parser.add_argument("--record-id-table-id", default="tblq1pNSJ7H9KNpI", help="记录ID表 ID")
    args = parser.parse_args()

    try:
        client = FeishuClient(args.app_id, args.app_secret)
        token = client.get_tenant_access_token()
        print(f"✓ 获取 token 成功: {token[:20]}...")

        user_mapping = load_user_mapping(client, args.app_token, args.user_table_id)

        data_obj = load_local_data(args.data)
        columns = data_obj["columns"]
        data = data_obj["data"]
        print(f"\n✓ 加载 {len(data)} 条记录，{len(columns)} 个字段")

        clear_table(client, args.app_token, args.table_id)
        clear_table(client, args.app_token, args.exception_table_id)
        clear_table(client, args.app_token, args.record_id_table_id)

        result, exceptions, org_names = sync_to_bitable(
            client, args.app_token, args.table_id, data, columns, user_mapping
        )

        if result.get("record_ids"):
            write_record_ids(client, args.app_token, args.record_id_table_id,
                             result["record_ids"], org_names)

        write_exceptions(client, args.app_token, args.exception_table_id, exceptions)

        print("\n=== 全部完成 ===")

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
