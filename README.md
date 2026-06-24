# 客户台账数据抓取与飞书推送工具

通过 API 抓取售后场景客户数据，支持本地 SQLite 存储及飞书多维表格写入，并提供每日增量更新与飞书群通知功能。

## 功能特性

- 📡 **数据抓取**：从 Boss API 拉取客户台账数据，支持分页获取
- 💾 **本地存储**：使用 SQLite 缓存数据，支持本地编辑
- 📊 **多维表同步**：将数据写入飞书多维表格，支持增量更新
- 🔄 **每日更新**：自动对比新旧数据，更新已存在客户，新增未记录客户
- 📱 **飞书通知**：更新完成后推送飞书群消息，包含新增客户列表
- ⚠️ **异常记录**：记录人员映射缺失等异常情况到专用表格

## 文件结构

```
customerledger/
├── api_client.py       # API 客户端：调用 Boss API 抓取数据
├── config.py           # 全局配置：API 端点、字段映射等
├── config.example.json # 配置模板（复制为 config.json 使用）
├── daily_update.py     # 每日增量更新主程序
├── db.py               # SQLite 本地数据库操作
├── feishu_client.py    # 飞书 API 客户端：多维表操作
├── fetch_data.py       # 数据抓取与格式化工具
└── write_to_bitable.py # 数据写入多维表
```

## 环境要求

- Python 3.7+

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置文件

复制 `config.example.json` 为 `config.json`，并填入相关配置：

```json
{
  "bearer_token": "boss的 Bear token",
  "app_id": "飞书自建应用appid",
  "app_secret": "飞书自建应用secret",
  "app_token": "多维表token",
  "table_id": "主表ID",
  "exception_table_id": "异常表ID",
  "record_id_table_id": "记录ID表ID",
  "user_table_id": "用户表ID",
  "feishu_webhook": "飞书机器人webhook",
  "feishu_sign": "飞书机器人sign签名"
}
```

### 3. 运行每日更新

```bash
python daily_update.py
```

### 可选参数

| 参数 | 说明 |
|------|------|
| `--app-id` | 飞书应用 app_id |
| `--app-secret` | 飞书应用 app_secret |
| `--app-token` | 多维表应用 token |
| `--table-id` | 主数据表 ID |
| `--user-table-id` | 人员映射表 ID |
| `--record-id-table-id` | 记录ID表 ID |
| `--exception-table-id` | 异常记录表 ID |
| `--data-file` | 使用本地数据文件（跳过拉取） |
| `--no-fetch` | 不重新拉取数据（需配合 `--data-file`） |

## 使用说明

### 每日增量更新流程

1. **拉取最新数据**：从 Boss API 获取所有客户记录
2. **加载映射关系**：读取人员姓名→unionid 映射、客户名称→record_id 映射
3. **构造更新数据**：对比已有记录，生成更新/新增列表
4. **批量更新**：更新已存在客户的字段
5. **批量新增**：新增未记录的客户到主表
6. **同步记录ID**：将新客户的 record_id 写入记录ID表
7. **记录异常**：将人员映射缺失等异常写入异常表
8. **发送通知**：推送飞书群消息，包含更新统计和新增客户列表

### 飞书通知卡片

更新成功时，飞书群会收到如下格式的通知：

```
✅ 浙江客户信息更新成功

新增客户：
• 杭州XX科技有限公司
• 宁波XX贸易有限公司

更新记录条数  103 条
更新时间      2026-06-23 15:30:00
```

## 注意事项

- `config.json` 包含敏感信息（API Token、飞书密钥等），已加入 `.gitignore`，**请勿提交到仓库**
- 首次运行时需要确保多维表中已创建相关表格和字段
- 建议通过定时任务（如 cron）每日执行 `daily_update.py`

## License

MIT
