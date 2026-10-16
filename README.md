# 膜 3.0（Membrane 3.0）— 骨架仓库

外骨骼式桌面自动化 RPA Agent 的骨架。走「看 → 认 → 判 → 点 → 验」闭环，让 AI 操作浏览器和桌面应用。当前以淘宝「千牛」卖家后台为示范场景。

> **这是骨架仓库**：只含代码与接口规格，**不含模型权重、不含业务数据、不含任何 API key / 账号 / Cookie / 登录态**。模型只给下载/接入方式（见 [docs/MODELS.md](docs/MODELS.md)）。

---

## 膜是什么

膜 = 套在现有桌面软件外面的「外骨骼」。它不改动软件本身，而是像一层膜覆盖上去：

1. **看** — 扫描屏幕，框出可交互元素（含坐标）
2. **认** — 把「工作流想点的东西」和「扫到的元素」对上
3. **判** — 大脑校验这一步该不该点
4. **点** — 真实鼠标去点（不是模拟 JS）
5. **验** — 确认点完生效没有（页面跳了 / 元素出现了）

## 四块零件

| 零件 | 职责 | 文件 | 依赖 |
|---|---|---|---|
| ① 眼睛 | 扫描屏幕（UIA + CDP + 视觉 qwen3-vl），返回元素坐标 | `membrane_vision.py` | 本地 Ollama `qwen3-vl:8b` |
| ② 大脑 | 执行前校验每一步，输出 JSON 决策 | `membrane_operator.py` | 云端 DeepSeek Flash API |
| ③ 手 | 物理鼠标点击/输入/按键 | `exec/executor.py` | 纯 Windows API，无外部依赖 |
| ④ 记忆 | 记住页面长啥样，下次直接对上，不用重扫 | `store_db.py`（SQLite）+ `workflow/` + `memory/` | 无外部依赖 |

**数据流**：`membrane.py`（入口）→ `run_workflow.py`（组装）→ `workflow/engine.py`（主循环）→ 注入 ①②③④。

## 目录结构

```
membrane-v3/
├── README.md                  # 本文件
├── docs/
│   ├── ARCHITECTURE.md        # 架构与文件说明
│   ├── MODELS.md              # 模型下载/接入
│   └── API.md                 # API 接入位置（前端 → 后端 → 模型）
├── interfaces/                # 接口规格（插座标准，对接新组件看这里）
│   ├── scan.md                # ① 眼睛接口
│   ├── brain.md               # ② 大脑接口
│   ├── hand.md                # ③ 手接口
│   └── memory.md              # ④ 记忆接口
├── memory/                    # 膜自身的知识（理论/设计决策，不含用户数据）
├── frontend-react/            # 前端（React 总控台，开副屏）
├── server/                    # Flask API 后端（api_server.py + requirements.txt）
├── workflow/                  # 执行引擎（engine/model/layout/path_tree）
├── exec/                      # ③ 手（物理鼠标）
├── scripts/                   # 数据采集脚本（爬千牛页面 → 填数据团）
├── proto/                     # 3D 导出（可选）
├── tests/                     # 单测
│
├── membrane.py                # 统一入口（status/list/gen/run）
├── run_workflow.py            # 引擎组装（注入 scan/match/execute/operator）
├── membrane_vision.py         # ① 眼睛（扫描总组件）
├── membrane_operator.py       # ② 大脑（DeepSeek Flash 操控员）
├── store_db.py                # ④ 记忆读写（SQLite 数据团）
├── membrane_virtual_display.py# 虚拟屏（副屏）
├── actor_library.py           # 演员库（页面骨架匹配，加速「认」）
├── coordinator.py             # 统筹编排
├── popup_clear.py             # 弹窗清障（截屏→识别→点击→确认）
├── crawl_*.py                 # 页面爬取（数据团原料）
├── launch_*.py                # 启动（虚拟屏/CDP/客户端）
├── migrate_*.py               # 数据迁移
├── operator_verify.py         # 操控员校验（真实鼠标走闭环）
└── diag_*.py                  # 诊断
```

## 闭环与降级

正常闭环（四段流水线）：

```
看 scan → 认 match → 判 operator → 点 execute → 验 verify_migration
```

降级链（蓝图见 `WORKFLOW_LOGIC_V2.md`，未完全实施）：

```
① CDP 后门 → ② 物理真手 → ③ GPT 视觉支招（仅想法）→ ④ 停步喊人
```

## 快速开始

```bash
# 1. 装模型（眼睛 + 大脑），见 docs/MODELS.md
ollama pull qwen3-vl:8b                    # 眼睛（本地）
export MEMBRANE_DEEPSEEK_KEY=sk-xxx        # 大脑（DeepSeek Flash，走环境变量）

# 2. 起后端（Flask，端口 8931）
python server/api_server.py --port 8931

# 3. 起前端（Vite dev，端口 5173）
cd frontend-react && pnpm install && pnpm dev
# 浏览器打开 http://localhost:5173 → 副屏画面 + 后端状态

# 4. 激活副屏 + CDP（真实点击前需要）
python launch_qianniu.py                   # 虚拟屏 + Edge CDP 9222
```

依赖安装见 `requirements.txt`（后端，Windows 专用）与 `frontend-react/package.json`（前端）。

## 未完工部分（占位，见 interfaces/）

| 功能 | 状态 | 占位 |
|---|---|---|
| 前端「渲染图」（3D 渲染面板） | 未完工，已移除 | 后端 `/api/v2/render/status` 端点保留 |
| 前端「数据团展示」（3D 导航图） | 未完工，已移除 | 后端 `/api/v2/navmap` 端点保留 |
| 前端「工作流可视化编辑」 | 未完工，已移除 | 工作流引擎（`workflow/`）可用，缺可视化编辑 |

这些模块接口规格已在 `interfaces/` 预留，后续调试好的组件放进对应目录即可使用。

## 隐私

本仓库**不含**任何 API key、账号、Cookie、浏览器登录态、个人数据、业务数据（商品/店铺/截图）。密钥一律走环境变量注入；数据库、爬取快照、截图、日志均被 `.gitignore` 排除。
