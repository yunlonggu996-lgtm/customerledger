"""全局配置：API端点、鉴权、分页参数。"""

import json
import os

# 加载运行时配置
_config = {}
_config_path = os.path.join(os.path.dirname(__file__), "config.json")
if os.path.exists(_config_path):
    with open(_config_path, "r", encoding="utf-8") as f:
        _config = json.load(f)

# API 基础配置
API_BASE_URL = "https://boss-api.shadow-rpa.net"
API_PATH = "/boss/api/v3/aftersales/scene/query"
API_URL = API_BASE_URL + API_PATH

# Bearer Token
BEARER_TOKEN = _config.get("bearer_token", "")

# 默认请求头
DEFAULT_HEADERS = {
    "Authorization": f"Bearer {BEARER_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Origin": "https://boss.shadow-rpa.net",
    "Referer": "https://boss.shadow-rpa.net/",
}

# 显示列：domainColumnId -> 字段中文名
SHOW_COLUMN_IDS = [
    1, 2, 52, 43, 64, 32, 14, 33, 62, 63, 44, 45, 46, 53, 12, 13, 15, 38, 57,
    7, 55, 16, 24, 29, 3, 4, 5, 66, 67, 68, 69, 70, 71, 72, 76, 77, 78, 11,
    17, 18, 19, 20, 21, 22, 23, 25, 26, 27, 28, 30, 31, 34, 35, 36, 37, 39,
    40, 41, 42, 47, 48, 49, 50, 51, 58, 59, 60, 61, 73, 74, 75, 79, 80,
]

# 分页与排序
DEFAULT_PAGE_SIZE = 100
DEFAULT_SORT = {"domainColumnId": 11, "isAsc": False}

# HTTP 超时（秒）
HTTP_TIMEOUT = 30

# 本地数据库
DB_PATH = "customer_ledger.db"

# 表格中显示的列（仅在UI中默认显示的列；所有列在详情中可见）
UI_DISPLAY_COLUMNS = [
    "客户编号",
    "组织名称",
    "组织简称",
    "业务区域名称",
    "客户成功",
    "RPA教练",
    "RPA合作状态",
    "RPA到期日期",
    "RPA剩余天数",
    "RPA续费状态",
    "健康度",
    "客户分层",
    "客户星级",
]
