"""飞书 API 客户端：获取 tenant_access_token，调用多维表接口。"""

import json
import ssl
import urllib.request
import urllib.error


class FeishuClient:
    def __init__(self, app_id, app_secret, timeout=30):
        self.app_id = app_id
        self.app_secret = app_secret
        self.timeout = timeout
        self._token = None
        self._token_expire_time = 0
        self._ssl_ctx = self._make_ssl_context()

    def _make_ssl_context(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _request(self, url, method="GET", headers=None, data=None):
        hdrs = dict(headers or {})
        hdrs.setdefault("Content-Type", "application/json")
        if self._token:
            hdrs.setdefault("Authorization", f"Bearer {self._token}")
        req_data = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=req_data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")[:500]
            raise FeishuError(f"HTTP {e.code}: {err_body}")
        except urllib.error.URLError as e:
            raise FeishuError(f"网络错误: {e.reason}")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise FeishuError(f"非 JSON 响应: {body[:300]}")

    def get_tenant_access_token(self):
        """获取 tenant_access_token。"""
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        data = {"app_id": self.app_id, "app_secret": self.app_secret}
        resp = self._request(url, method="POST", data=data)
        if resp.get("code") != 0:
            raise FeishuError(f"获取 token 失败: {resp.get('msg')}")
        self._token = resp["tenant_access_token"]
        self._token_expire_time = int(resp.get("expire", 0)) + int(resp.get("timestamp", 0))
        return self._token

    def ensure_token(self):
        """确保 token 有效，过期自动刷新。"""
        import time
        now = int(time.time())
        if not self._token or now >= self._token_expire_time - 60:
            self.get_tenant_access_token()
        return self._token

    # ========== 多维表 API ==========

    def list_tables(self, app_token):
        """列出多维表应用中的数据表。"""
        self.ensure_token()
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables"
        resp = self._request(url)
        if resp.get("code") != 0:
            raise FeishuError(f"list_tables: {resp.get('msg')}")
        return resp.get("data", {}).get("items", [])

    def get_table_fields(self, app_token, table_id):
        """获取数据表的字段列表。"""
        self.ensure_token()
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        resp = self._request(url)
        if resp.get("code") != 0:
            raise FeishuError(f"get_table_fields: {resp.get('msg')}")
        return resp.get("data", {}).get("items", [])

    def add_records(self, app_token, table_id, records, chunk_size=50):
        """批量添加记录。每次最多 50 条。返回 {total, success, record_ids}"""
        self.ensure_token()
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
        total = len(records)
        success = 0
        record_ids = []
        for i in range(0, total, chunk_size):
            chunk = records[i:i + chunk_size]
            data = {"records": [{"fields": r} for r in chunk]}
            resp = self._request(url, method="POST", data=data)
            if resp.get("code") != 0:
                raise FeishuError(f"add_records[{i//chunk_size}]: {resp.get('msg')}")
            recs = resp.get("data", {}).get("records", [])
            success += len(recs)
            record_ids.extend([r.get("record_id") for r in recs if r.get("record_id")])
        return {"total": total, "success": success, "record_ids": record_ids}

    def update_records(self, app_token, table_id, records, chunk_size=50):
        """批量更新记录（需提供 record_id）。"""
        self.ensure_token()
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update"
        total = len(records)
        success = 0
        for i in range(0, total, chunk_size):
            chunk = records[i:i + chunk_size]
            data = {"records": [{"record_id": r["record_id"], "fields": r["fields"]} for r in chunk]}
            resp = self._request(url, method="POST", data=data)
            if resp.get("code") != 0:
                raise FeishuError(f"update_records[{i//chunk_size}]: {resp.get('msg')}")
            success += len(resp.get("data", {}).get("records", []))
        return {"total": total, "success": success}

    def delete_records(self, app_token, table_id, record_ids, chunk_size=50):
        """批量删除记录。"""
        self.ensure_token()
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
        total = len(record_ids)
        for i in range(0, total, chunk_size):
            chunk = record_ids[i:i + chunk_size]
            data = {"records": chunk}
            resp = self._request(url, method="POST", data=data)
            if resp.get("code") != 0:
                raise FeishuError(f"delete_records[{i//chunk_size}]: {resp.get('msg')}")
        return {"total": total}

    def search_records(self, app_token, table_id, field_key, value):
        """搜索指定字段等于某个值的记录（用于查重）。"""
        self.ensure_token()
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search"
        data = {
            "field_key": field_key,
            "value": value,
            "page_size": 10
        }
        resp = self._request(url, method="POST", data=data)
        if resp.get("code") != 0:
            raise FeishuError(f"search_records: {resp.get('msg')}")
        return resp.get("data", {}).get("items", [])


class FeishuError(Exception):
    pass
