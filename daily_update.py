#!/usr/bin/env python3
"""每日增量更新：根据组织名称查记录ID，更新主表对应记录的字段。"""

import json
import sys
import os
import urllib.request
from datetime import datetime

from feishu_client import FeishuClient, FeishuError
from api_client import ApiError


def send_feishu_notification(webhook_url, success, update_count, error_msg=None, sign_secret=None, new_customers=None, exceptions=None):
    """发送飞书群机器人消息通知。"""
    if not webhook_url:
        return

    import hashlib
    import base64
    import hmac
    import time

    if success:
        content = {
            "更新记录条数": f"{update_count} 条",
            "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        title = "浙江客户信息更新成功"
    else:
        content = {
            "错误信息": error_msg or "未知错误",
            "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        title = "浙江客户信息更新失败"

    elements = [
        {
            "tag": "div",
            "fields": [
                {"is_short": True, "text": {"tag": "lark_md", "content": f"**{k}**\n{v}"}}
                for k, v in content.items()
            ]
        }
    ]

    # 如果有新增客户，在卡片中显示
    if success and new_customers:
        names = new_customers[:20]
        customer_text = "\n".join(f"• {name}" for name in names)
        if len(new_customers) > 20:
            customer_text += f"\n...等共 {len(new_customers)} 个"
        elements.insert(0, {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**新增客户：**\n{customer_text}"}
        })

    # 如果有异常客户，在卡片中显示
    if exceptions:
        exc_texts = []
        for exc in exceptions[:20]:
            exc_texts.append(f"• {exc.get('组织名称', '')}（{exc.get('异常信息', '')}）")
        exc_content = "\n".join(exc_texts)
        if len(exceptions) > 20:
            exc_content += f"\n...等共 {len(exceptions)} 个"
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**服务人员异常客户：**\n{exc_content}"}
        })

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "green" if success else "red"
            },
            "elements": elements
        }
    }

    # 添加签名（放在请求体中）
    if sign_secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{sign_secret}"
        sign = base64.b64encode(hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()).decode("utf-8")
        payload["timestamp"] = timestamp
        payload["sign"] = sign

    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                print("\n[通知] 飞书群消息推送成功")
            else:
                print(f"\n[通知] 飞书群消息推送失败: {result.get('msg')}")
    except Exception as e:
        print(f"\n[通知] 飞书群消息推送异常: {e}")



def fetch_latest_data(output_file=None):
    """从 boss-api 拉取最新数据。"""
    from api_client import ApiClient
    from config import DEFAULT_PAGE_SIZE
    from fetch_data import _normalize_value, _star_to_number, _to_ms_timestamp, _is_date_field

    client = ApiClient()
    print("正在从 boss-api 拉取最新数据...")
    records, columns, total = client.fetch_all(keyword="", page_size=DEFAULT_PAGE_SIZE)
    print(f"✓ 拉取完成：{len(records)} 条记录")

    normalized = []
    for rec in records:
        row = {}
        for k in columns:
            raw = rec.get(k)
            if k == "客户星级":
                # 客户星级 -> 数字
                num = _star_to_number(raw)
                row[k] = num if num is not None else ""
            elif _is_date_field(k):
                # 日期字段 -> 毫秒时间戳
                ms = _to_ms_timestamp(raw)
                row[k] = ms if ms is not None else ""
            else:
                row[k] = _normalize_value(raw)
        normalized.append(row)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({"columns": columns, "total": total, "data": normalized},
                      f, ensure_ascii=False, indent=2)
        print(f"✓ 已保存到 {output_file}")
    return normalized, columns


def load_user_mapping(client, app_token, user_table_id, table_name_map=None):
    """读取人员姓名→unionid 映射，以及在职状态。"""
    name = table_name_map.get(user_table_id, user_table_id) if table_name_map else user_table_id
    print(f"\n读取人员映射表 {name}...")
    mapping = {}  # name -> unionid
    status_map = {}  # name -> 在职状态
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
            status = fields.get("在职状态", "")
            if isinstance(person_info, list) and person_info:
                name = person_info[0].get("name")
                if name:
                    if unionid:
                        mapping[name] = unionid
                    status_map[name] = status
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    print(f"✓ 读取到 {len(mapping)} 个人员映射，{len(status_map)} 个在职状态")
    return mapping, status_map


def load_record_id_mapping(client, app_token, record_id_table_id, table_name_map=None):
    """读取 客户名称→主表 record_id 映射。"""
    name = table_name_map.get(record_id_table_id, record_id_table_id) if table_name_map else record_id_table_id
    print(f"\n读取记录ID表 {name}...")
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


def build_payload(data, columns, user_mapping, status_map, record_id_mapping,
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
    select_field_names = set()  # 需要转换为选项ID的字段
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
        elif ftype in [3, 4]:
            # 单选或多选字段，记录下来后面处理
            select_field_names.add(col)
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
            field_name = field_name_map.get(field_id, field_id)
            # 处理多选/单选字段：直接传文本值（不转选项ID）
            if col in select_field_names:
                ftype = field_type_map.get(field_name, 0)
                if isinstance(val, str):
                    if ftype == 3:
                        # 单选字段：单个值
                        fields[field_name] = val
                    elif ftype == 4:
                        # 多选字段：需要是列表格式
                        if ", " in val:
                            fields[field_name] = [v.strip() for v in val.split(", ")]
                        else:
                            fields[field_name] = [val]
                elif isinstance(val, list):
                    fields[field_name] = val
            else:
                fields[field_name] = val

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
                # 根据在职状态区分异常原因
                for miss_name in missing:
                    role = "CSM" if col == "客户成功" else "RPA教练"
                    if miss_name in status_map:
                        status = status_map[miss_name]
                        if status and status != "在职":
                            err_type = f"{role}离职"
                        else:
                            err_type = f"{role}异常"
                    else:
                        err_type = f"{role}不存在"
                    exceptions.append({"组织名称": row.get("组织名称", ""), "异常信息": err_type})
                    print(f"  ⚠️ {err_type}: {row.get('组织名称', '')} - {miss_name}")
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
            # 非浙江业务组的客户，跳过新增
            biz_area = row.get("业务区域名称", "")
            if biz_area and biz_area.strip() != "浙江业务组":
                print(f"  ⏭ 跳过非浙江业务组客户: {org_name}（{biz_area}）")
                continue
            inserts.append({"org_name": org_name, "fields": fields})

    return updates, inserts, exceptions


def update_records_batch(client, app_token, table_id, updates, chunk_size=150):
    """批量更新记录。"""
    total = len(updates)
    success = 0
    failed_ids = []
    for i in range(0, total, chunk_size):
        chunk = updates[i:i + chunk_size]
        try:
            result = client.update_records(app_token, table_id, chunk)
            success += result.get("success", 0)
        except FeishuError as e:
            print(f"  更新批次失败: {e}")
            for item in chunk:
                failed_ids.append(item["record_id"])
        print(f"  进度: {min(i+chunk_size, total)}/{total}", flush=True)
    return {"total": total, "success": success, "failed": failed_ids}


def load_existing_exception_orgs(client, app_token, table_id):
    """读取异常表中已存在的组织名称集合。"""
    orgs = set()
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
            org_name = item.get("fields", {}).get("组织名称")
            if org_name:
                orgs.add(org_name)
        page_token = resp.get("data", {}).get("page_token")
        if not page_token:
            break
    return orgs


def write_exceptions(client, app_token, exception_table_id, exceptions, table_name_map=None):
    """写入异常记录：对比已有记录，只新增不存在的。返回新增的异常列表用于通知。"""
    name = table_name_map.get(exception_table_id, exception_table_id) if table_name_map else exception_table_id

    if not exceptions:
        print("✓ 无异常记录")
        return []

    # 读取已存在的组织名称
    print(f"\n读取异常表 {name} 已有记录...")
    existing_orgs = load_existing_exception_orgs(client, app_token, exception_table_id)
    print(f"✓ 已有 {len(existing_orgs)} 条异常记录")

    # 合并相同企业的异常信息
    merged = {}
    for exc in exceptions:
        org = exc["组织名称"]
        err = exc["异常信息"]
        if org not in merged:
            merged[org] = []
        if err not in merged[org]:
            merged[org].append(err)

    # 过滤出新增的异常
    new_records = []
    new_exceptions = []
    for org, errors in merged.items():
        if org in existing_orgs:
            print(f"  - 跳过已存在的异常: {org}")
            continue
        error_info = "; ".join(errors)
        new_records.append({"组织名称": org, "异常信息": error_info})
        new_exceptions.append({"组织名称": org, "异常信息": error_info})

    if not new_records:
        print("✓ 无新增异常记录")
        return []

    print(f"写入 {len(new_records)} 条新增异常记录...")
    result = client.add_records(app_token, exception_table_id, new_records, chunk_size=50)
    print(f"✓ 异常记录写入: {result['success']}/{result['total']}")
    return new_exceptions


def prompt_new_bearer_token(config_path):
    """提示用户输入新的 bearer_token，更新 config.json 并重载运行时配置。"""
    from config import reload_bearer_token

    print("\n⚠️  Boss API 鉴权失败，Bearer Token 可能已失效")
    print("请在下方输入新的 Bearer Token：")
    try:
        new_token = input("新的 Bearer Token: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n✗ 用户取消输入")
        return False

    if not new_token:
        print("✗ 未输入有效 Token，跳过更新")
        return False

    # 读取现有配置，更新 token
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            conf = json.load(f)
        conf["bearer_token"] = new_token
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(conf, f, ensure_ascii=False, indent=2)
        print("✓ config.json 已更新")
    except Exception as e:
        print(f"✗ 写入 config.json 失败: {e}")
        return False

    # 重载运行时配置
    reload_bearer_token()
    print("✓ Bearer Token 已生效，正在重试...")
    return True


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

        # 获取所有数据表名称映射
        tables = client.list_tables(args.app_token)
        table_name_map = {t["table_id"]: t["name"] for t in tables}

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
            try:
                data, columns = fetch_latest_data("customer_ledger_data.json")
            except ApiError as e:
                print(f"✗ 拉取数据失败: {e}")
                # 判断是否为鉴权相关错误，提示用户输入新 token
                err_msg = str(e).lower()
                is_auth_error = (
                    "401" in err_msg or "403" in err_msg or
                    "unauthorized" in err_msg or "token" in err_msg or
                    "鉴权" in err_msg or "auth" in err_msg
                )
                if is_auth_error and prompt_new_bearer_token(config_path):
                    data, columns = fetch_latest_data("customer_ledger_data.json")
                else:
                    raise

        user_mapping, status_map = load_user_mapping(client, args.app_token, args.user_table_id, table_name_map)
        record_id_mapping = load_record_id_mapping(client, args.app_token, args.record_id_table_id, table_name_map)

        updates, inserts, exceptions = build_payload(
            data, columns, user_mapping, status_map, record_id_mapping,
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
                                             new_records_payload, chunk_size=150)
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
                                                record_id_records, chunk_size=150)
                print(f"✓ 记录ID写入: {rid_result['success']}/{rid_result['total']} 条")

        new_exceptions = write_exceptions(client, args.app_token, args.exception_table_id, exceptions, table_name_map)

        # 计算总更新条数（更新 + 新增）
        total_updated = len(updates) + len(inserts)
        
        # 检查是否有更新失败
        update_failed_count = 0
        if updates:
            update_failed_count = result.get("failed", [])
            update_failed_count = len(update_failed_count) if isinstance(update_failed_count, list) else 0
        
        print("\n=== 增量更新完成 ===")
        
        # 发送通知（成功或失败）
        webhook_url = defaults.get("feishu-webhook") or defaults.get("feishu_webhook")
        sign_secret = defaults.get("feishu_sign")
        new_customer_names = [item["org_name"] for item in inserts] if inserts else []
        if update_failed_count > 0:
            error_msg = f"更新失败 {update_failed_count} 条"
            send_feishu_notification(webhook_url, success=False, update_count=total_updated, error_msg=error_msg, sign_secret=sign_secret, new_customers=new_customer_names, exceptions=new_exceptions)
        else:
            send_feishu_notification(webhook_url, success=True, update_count=total_updated, sign_secret=sign_secret, new_customers=new_customer_names, exceptions=new_exceptions)

    except FeishuError as e:
        error_msg = f"飞书 API 错误: {e}"
        print(error_msg, file=sys.stderr)
        # 发送失败通知
        webhook_url = defaults.get("feishu-webhook") or defaults.get("feishu_webhook")
        sign_secret = defaults.get("feishu_sign")
        send_feishu_notification(webhook_url, success=False, update_count=0, error_msg=error_msg, sign_secret=sign_secret)
        sys.exit(1)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"错误: {error_msg}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        # 发送失败通知
        webhook_url = defaults.get("feishu-webhook") or defaults.get("feishu_webhook")
        sign_secret = defaults.get("feishu_sign")
        send_feishu_notification(webhook_url, success=False, update_count=0, error_msg=error_msg, sign_secret=sign_secret)
        sys.exit(1)


if __name__ == "__main__":
    main()
