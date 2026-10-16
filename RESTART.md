# 膜后端 — 启动 / 停止手册

> 2026-10-09 停服前记录。下次开机后按「启动步骤」从上到下跑一遍即可，不会丢。

膜后端 = 4 个组件：

| # | 组件 | 端口 | 作用 |
|---|------|------|------|
| 1 | Ollama | 11435 | 本地 LLM：qwen3:14b 操控员 / qwen3-vl:8b 视觉 / qwen2.5:7b |
| 2 | 虚拟屏 + Edge CDP | 9222 | **核心**：副屏(-1920,0) + Edge 千牛页面，膜的眼睛和手 |
| 3 | 3D 界面服务器 | 8899 | 3D 全站导航图（可选，不影响核心） |

---

## 启动步骤（从零开始）

```bash
# ── 1. Ollama（若没自启）────────────────────────
#    环境变量 OLLAMA_HOST=0.0.0.0:11435 已设（用户级，默认是 11434）
#    通常 Ollama 桌面应用开机自启，不用手动管。
#    验证：ollama list  应看到 qwen3:14b / qwen3-vl:8b / qwen2.5:7b

# ── 2. 虚拟屏 + Edge CDP（核心，必须起）──────────
cd D:/AI/membrane
python launch_qianniu.py
#   它做三件事：① 创建虚拟屏(Parsec VDD) ② 启动 Edge(千牛, CDP 9222, 隔离 profile) ③ 心跳保活
#   状态写 launch_state.json
#   验证：python launch_qianniu.py --status  应看到两块屏 {-1920} 和 {0}

# ── 3. 3D 界面（可选）────────────────────────────
cd D:/AI/membrane
python -m http.server 8899 --directory proto --bind 127.0.0.1
```

## 停止

```bash
# 1. 停 launch_qianniu.py → 心跳停 → 虚拟屏 100ms 内自动消失（Parsec VDD 特性）
# 2. 关闭副屏 Edge（独立 profile，CDP 9222，正常关闭窗口即可）
# 3. 停 3D 服务器
# 4. 停 Ollama：ollama stop   # 释放模型显存
```

## 关键配置（别改错）

- **Ollama 端口**：环境变量 `OLLAMA_HOST=0.0.0.0:11435`（用户级）
- **Edge CDP**：`--remote-debugging-port=9222 --user-data-dir=D:\AI\membrane\_edge_cdp_profile`（隔离 profile，不影响日常 Edge）
- **虚拟屏**：Parsec VDD 驱动，`membrane_virtual_display.py`，心跳 100ms 保活 → **杀进程即消失，无需手动清理**
- **旧方式**：`start_edge_cdp.bat`（只开 Edge CDP 不建虚拟屏，打开 deepseek 页，已弃用）
- **模型**：qwen3:14b（操控员，执行前校验）/ qwen3-vl:8b（视觉，弹窗识别）/ qwen2.5:7b
