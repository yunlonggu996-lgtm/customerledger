"""HTTP 客户端：调用 boss-api 售后场景查询接口。"""

import json
import ssl
import urllib.request
import urllib.error

from config import API_URL, DEFAULT_HEADERS, HTTP_TIMEOUT


def _make_ssl_context():
    """构造 SSL 上下文，macOS 系统 Python 证书缺失时自动回退到不验证。"""
    ctx = ssl.create_default_context()
    try:
        # 触发证书加载
        import socket
        socket.create_connection(("www.apple.com", 443), timeout=3)
    except Exception:
        pass
    # 失败时回退：跳过证书校验
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class ApiClient:
    """简单的 boss-api 客户端。"""

    def __init__(self, base_url=API_URL, headers=None, timeout=HTTP_TIMEOUT):
        self.base_url = base_url
        self.headers = dict(headers or DEFAULT_HEADERS)
        self.timeout = timeout
        self._ssl_ctx = _make_ssl_context()

    def query_scene(self, keyword="", conditions=None, page=1, size=100,
                    sort_condition=None, show_column_ids=None):
        """调用 /aftersales/scene/query 接口。

        返回 dict: {code, success, msg, data: {dataList, columnList, total}, page}
        """
        from config import SHOW_COLUMN_IDS, DEFAULT_SORT

        payload = {
            "keyword": keyword or "",
            "conditions": conditions or [],
            "showColumnIds": list(show_column_ids or SHOW_COLUMN_IDS),
            "page": int(page),
            "size": int(size),
            "sortCondition": sort_condition or dict(DEFAULT_SORT),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url, data=data, headers=self.headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=self._ssl_ctx) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise ApiError(f"HTTP {e.code}: {err_body[:500]}", code=e.code)
        except urllib.error.URLError as e:
            raise ApiError(f"网络错误: {e.reason}")
        except Exception as e:
            raise ApiError(f"请求异常: {e}")

        try:
            obj = json.loads(body)
        except json.JSONDecodeError as e:
            raise ApiError(f"返回非 JSON: {body[:300]}")

        if not obj.get("success"):
            raise ApiError(
                f"接口返回失败: code={obj.get('code')} msg={obj.get('msg')}"
            )
        return obj

    def fetch_all(self, keyword="", page_size=100, progress_cb=None,
                  stop_cb=None):
        """自动分页拉取所有数据。返回 list[dict] 与 columns(list[str])。"""
        first = self.query_scene(keyword=keyword, page=1, size=page_size)
        data = first.get("data") or {}
        total = int(data.get("total") or 0)
        columns = list(data.get("columnList") or [])
        all_records = list(data.get("dataList") or [])

        if progress_cb:
            progress_cb(min(len(all_records), total), total)

        page = 2
        while len(all_records) < total:
            if stop_cb and stop_cb():
                break
            obj = self.query_scene(keyword=keyword, page=page, size=page_size)
            d = obj.get("data") or {}
            recs = d.get("dataList") or []
            if not recs:
                break
            all_records.extend(recs)
            if progress_cb:
                progress_cb(min(len(all_records), total), total)
            page += 1

        return all_records, columns, total


class ApiError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code
