import json
from feishu_client import FeishuClient

# 读取 config.json
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

client = FeishuClient(config["app_id"], config["app_secret"])

# 获取多维表字段
bitable_fields = client.get_table_fields(config["app_token"], config["table_id"])

# 构建选项映射
select_options_map = {}
for f in bitable_fields:
    if f["type"] in [3, 4]:  # 单选、多选
        opts = f.get("property", {}).get("options", [])
        select_options_map[f["field_name"]] = {opt["name"]: opt["id"] for opt in opts}

print("多选/单选字段选项映射:")
for field_name, opt_map in select_options_map.items():
    print(f"\n{field_name}:")
    for name, opt_id in opt_map.items():
        print(f"  {name} -> {opt_id}")

# 测试一个示例值
test_value = "公有云, 私有云"  # 示例值
if "RPA部署类型" in select_options_map:
    opt_map = select_options_map["RPA部署类型"]
    if ", " in test_value:
        selected = []
        for v in test_value.split(", "):
            if v.strip() in opt_map:
                selected.append(opt_map[v.strip()])
        print(f"\n测试转换: '{test_value}' -> {selected}")
