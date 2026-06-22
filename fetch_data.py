#!/usr/bin/env python3
"""客户台账数据拉取工具 - 输出 JSON 供写入飞书多维表使用。"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta

from api_client import ApiClient, ApiError
from config import DEFAULT_PAGE_SIZE


# 日期字段名（包含这些关键字的字段会被转换为毫秒时间戳）
DATE_FIELD_KEYWORDS = ("日期", "时间")


def _parse_datetime_to_ms(s):
    """将日期时间字符串转为 13 位毫秒时间戳。

    支持格式：
    - "2027-03-24 23:59:59+08"
    - "2026-05-15 10:30:15.769343"
    - "2026-05-15 10:30:15"
    - "2027-03-24 23:59:59"
    """
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    try:
        # 处理 +08 / -08 时区
        m = re.match(r"^(.+?)([+-]\d{1,2})$", s)
        if m and "T" not in s and len(m.group(1)) <= 19:
            # 时区偏移在末尾，例如 "2027-03-24 23:59:59+08"
            dt_str, tz = m.group(1), int(m.group(2))
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            tz_offset = timezone(timedelta(hours=tz))
            dt = dt.replace(tzinfo=tz_offset)
            return int(dt.timestamp() * 1000)
        # 处理微秒
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
        return None
    except Exception:
        return None


def _to_ms_timestamp(v):
    """将可能为字符串列表的值转为毫秒时间戳。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        # 已经是数字，若是 10 位秒级则转 13 位毫秒级
        n = int(v)
        if n < 10**12:  # 秒级
            return n * 1000
        return n
    if isinstance(v, list):
        if not v:
            return None
        # 取第一个非空值
        for item in v:
            ms = _to_ms_timestamp(item)
            if ms:
                return ms
        return None
    if isinstance(v, str):
        return _parse_datetime_to_ms(v)
    return None


def _normalize_value(v):
    """归一化为飞书多维表兼容的字段类型。"""
    if v is None:
        return ""
    if isinstance(v, list):
        if not v:
            return ""
        return ", ".join(str(x) for x in v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, bool):
        return str(v)
    return str(v)


def _star_to_number(v):
    """将星级 emoji 字符串转为数字。'⭐️⭐️⭐️' -> 3, '⭐️⭐️' -> 2 等。"""
    if v is None or v == "":
        return None
    s = str(v)
    # 计算白色中等五角星 emoji 的数量（U+2B50，可能后跟变体选择器 U+FE0F）
    count = 0
    for i, ch in enumerate(s):
        if ch == "\u2b50":
            count += 1
        elif ch == "\ufe0f":
            # 变体选择器，不计数
            continue
    if count > 0:
        return count
    # 如果是纯数字字符串
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _is_date_field(name):
    return any(kw in name for kw in DATE_FIELD_KEYWORDS)


def fetch_all(output_file=None, progress=True):
    """拉取全部数据，返回列表与列名。"""
    client = ApiClient()
    print("开始拉取数据...")

    def _progress(done, total):
        if progress:
            pct = int(done * 100 / max(total, 1))
            print(f"\r进度: {done}/{total} ({pct}%)", end="", flush=True)

    records, columns, total = client.fetch_all(
        keyword="", page_size=DEFAULT_PAGE_SIZE, progress_cb=_progress
    )

    if progress:
        print()

    print(f"拉取完成，共 {len(records)} 条记录，{len(columns)} 个字段")

    normalized = []
    for rec in records:
        row = {}
        for k in columns:
            raw = rec.get(k)
            if k == "客户星级":
                # 客户星级 -> 数字 (主表字段名为"星级"，type=2 数字)
                num = _star_to_number(raw)
                row["客户星级"] = num if num is not None else ""
            elif _is_date_field(k):
                ms = _to_ms_timestamp(raw)
                row[k] = ms if ms is not None else ""
            else:
                row[k] = _normalize_value(raw)
        normalized.append(row)

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(
                {"columns": columns, "total": total, "data": normalized, "fetched_at": datetime.now().isoformat()},
                f, ensure_ascii=False, indent=2,
            )
        print(f"已保存到: {output_file}")

    return normalized, columns


if __name__ == "__main__":
    output = "customer_ledger_data.json"
    try:
        data, cols = fetch_all(output_file=output)
        print("\n字段列表（共", len(cols), "个）:")
        print(", ".join(cols[:20]), "...")
        print("\n示例数据（第一条）:")
        print(json.dumps(data[0], ensure_ascii=False, indent=2)[:1500])
    except ApiError as e:
        print(f"API 错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"错误: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
