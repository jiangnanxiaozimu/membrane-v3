# 架构

## 一句话

膜是套在桌面软件外面的「外骨骼」：**眼睛**看屏幕 → **大脑**判断 → **手**点 → **记忆**记住，形成闭环。它不改动被操作的软件本身。

## 四块零件

```
┌─────────┐   ┌─────────┐   ┌─────────┐
│ ① 眼睛   │ → │ ② 大脑   │ → │ ③ 手     │
│ 扫描屏幕  │   │ 校验决策  │   │ 物理鼠标  │
└─────────┘   └─────────┘   └─────────┘
      ↑                          │
      └────────── ④ 记忆 ←───────┘
              （数据团 + 工作流）
```

### ① 眼睛 — `membrane_vision.py`

扫描当前屏幕，返回可交互元素列表（每个元素含 `type` / `name` / 坐标 `cx,cy`）。

三路扫描（按需组合）：
- **UIA**（Windows UI Automation）— 拿桌面应用原生控件
- **CDP**（Chrome DevTools Protocol）— 拿浏览器 DOM 元素（Edge 端口 9222）
- **视觉**（qwen3-vl）— 兜底：截图让模型看「弹窗/关闭按钮在哪」

核心接口：`detect_ui_elements(frame=None) -> list[dict]`（元素带 `cx/cy/name/type`）。

### ② 大脑 — `membrane_operator.py`

执行前校验「这一步该不该点」。**只做文本决策，不做视觉定位**（定位是眼睛的活）。

核心接口：

```python
Operator().decide(intent, candidate, context) -> dict
# 返回 {"approved": bool, "confidence": float, "reason": str, "correction": str|None}
```

模型：DeepSeek Flash（`deepseek-chat`，云端 API）。密钥走 `MEMBRANE_DEEPSEEK_KEY` 环境变量。

### ③ 手 — `exec/executor.py`

真实物理鼠标（Windows `SetCursorPos` + `mouse_event`），不是 JS 模拟点击。接口：

```python
click(x, y)  hover(x, y)  type_text(x, y, text)
press_key(key)  hotkey(keys)  scroll(x, y, amount)  wait(seconds)
```

### ④ 记忆 — `store_db.py` + `workflow/` + `memory/`

- **数据团**（`store_db.py`，SQLite `membrane.db`）：记住每个页面长啥样（页面骨架 + 元素坐标），下次对上直接点，不用重扫。
- **工作流**（`workflow/`）：要执行的动作序列，存在数据团的 `workflows` 表里（`graph_json`）。
- **膜自身知识**（`memory/`）：设计决策、术语、已知坑 —— **只放理论/膜自身记忆，不放用户数据**。

## 数据流（闭环）

```
membrane.py（入口 CLI）
  └─ run_workflow.py（组装引擎）
       └─ workflow/engine.py（主循环：四段流水线）
            注入 →
            scan = make_scan()            # ① 眼睛
            match = make_match()          # 认（骨架匹配）
            operator = Operator()         # ② 大脑
            execute = make_execute()      # ③ 手
            verify_migration = ...        # 验（点完生效没）
```

每步动作：`看 → 认 → 判 → 点 → 验`，任何一环失败按降级链换招（`WORKFLOW_LOGIC_V2.md`）。

## 前后端连接

```
frontend-react（React 总控台，端口 5173）
   │  fetch http://127.0.0.1:8931/api/v2/*
   ▼
server/api_server.py（Flask，端口 8931）
   │  import membrane / run_workflow / membrane_vision / store_db
   ▼
膜四块零件（①眼睛 ②大脑 ③手 ④记忆）
```

前端只做展示与控制，逻辑全在后端。端点清单见 [API.md](API.md)。

## 关键设计决策

1. **真实鼠标，不模拟 JS** —— 点完页面真的跳，测出来的才是真效果（JS `.click()` 会绕过弹窗障碍，测不出「被拦」）。
2. **大脑与眼睛分离** —— 文本决策（DeepSeek Flash）和视觉定位（qwen3-vl）是两个模型，职责不交叉。
3. **数据团 = 记忆** —— 扫过一次的页面存进 SQLite，下次按骨架指纹直接匹配，省去重扫。
4. **密钥走环境变量** —— 任何 key 都不硬编码、不进 git。
