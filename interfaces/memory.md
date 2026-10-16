# ④ 记忆接口 — 数据团 + 工作流 + 膜自身知识

**文件**：`store_db.py`（SQLite 数据团）+ `workflow/`（执行引擎）+ `memory/`（膜自身知识）

## 数据团（`store_db.py`）

SQLite 库 `membrane.db`，5 张表：`tenants` / `apps` / `pages` / `elements` / `workflows`。

```python
from store_db import StoreDB
db = StoreDB()   # 默认 membrane.db

# 应用 → 页面 → 元素 三级
db.list_apps(tenant_id=None) -> list
db.list_pages(app_id=..., tenant_id=None) -> list
db.list_elements(page_id) -> list

# 按 url_key 直查（「记住页面长啥样」的关键）
db.get_page_by_url_key(app_id, url_key)
db.list_elements_by_url_key(app_id, url_key)

# 工作流（动作序列，存 graph_json）
db.list_workflows() / db.get_workflow(wf_id) / db.upsert_workflow(...)

db.close()
```

## 工作流（`workflow/engine.py`）

```python
from workflow.engine import Engine

Engine(graph, operator=None, scan=None, match=None, execute=None, verify_migration=None, ...)
engine.run(root_state_id=None, max_steps=100) -> 日志
```

引擎是四段流水线主循环：`看 → 认 → 判 → 点 → 验`，注入眼睛/大脑/手后运行（`run_workflow.build_engine` 负责组装）。

## 膜自身知识（`memory/` 目录）

存放**理论 / 设计决策 / 术语 / 已知坑**，供后续对接和迭代参考。

## 铁律（记忆边界）

本层**只放理论知识和膜自身的记忆**，绝不放：

- 用户数据、操作记录（如「点了千牛某按钮」「上了某商品」）
- 业务数据（商品 / 店铺 / 截图）
- 任何 API key / 账号 / Cookie

数据团里的页面/元素是**运行时生成物**，由爬取脚本重建，不入库（见 `.gitignore`）。
