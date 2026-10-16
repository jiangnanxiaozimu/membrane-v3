# API 接入位置

膜有三层 API，接入新组件时按需对接：

```
前端（React） → 后端（Flask） → 模型（DeepSeek / Ollama）
```

---

## 第一层：前端 → 后端（Flask HTTP API）

- 后端地址：`http://127.0.0.1:8931`
- 前端封装：`frontend-react/src/api.ts`（`API_BASE = 'http://127.0.0.1:8931'`）
- 后端定义：`server/api_server.py`（Flask 路由）

### v2 端点（当前使用）

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/v2/health` | 健康检查（数据团库 + 统计） |
| GET | `/api/v2/backend/status` | 后端 5 组件状态（虚拟屏/CDP/数据团/DeepSeek/视觉） |
| GET | `/api/v2/workflows` | 列出工作流 |
| GET | `/api/v2/workflows/<wf_id>` | 单条工作流（含 graph） |
| POST | `/api/v2/workflows/<wf_id>/run` | 执行工作流，body `{execute: bool}`（false=干跑，true=真点） |
| GET | `/api/v2/scan/frame` | 副屏截图 base64 PNG + 元素标注列表 |
| GET | `/api/v2/scan/stream` | 副屏实时流（MJPEG） |
| GET | `/api/v2/apps` | 列出应用（数据团） |
| GET | `/api/v2/pages?app_id=` | 列出页面 |
| GET | `/api/v2/elements?page_id=` | 列出元素 |
| GET | `/api/v2/navmap` | 导航拓扑（3D 导航图数据，前端未完工） |
| GET | `/api/v2/render/status` | 渲染状态（前端未完工） |

### v1 端点（旧，兼容保留）

`/api/v1/health`、`/api/v1/elements`、`/api/v1/pages`、`/api/v1/navigate`、`/api/v1/stats` 等。

---

## 第二层：后端 → 模型

### 大脑 DeepSeek Flash（`membrane_operator.py`）

```python
# 环境变量
MEMBRANE_DEEPSEEK_KEY    # 必填，API key
MEMBRANE_DEEPSEEK_BASE   # 可选，默认 https://api.deepseek.com
MEMBRANE_DEEPSEEK_MODEL  # 可选，默认 deepseek-chat

# 调用
Operator().decide(intent, candidate, context) -> dict
DeepSeekClient().chat(model=..., messages=[...], format="json", options={...})  # 兼容 ollama 接口
```

底层走 OpenAI 兼容的 `POST {base}/v1/chat/completions`。

### 眼睛 Ollama（`membrane_vision.py` / `popup_clear.py`）

```python
OLLAMA_HOST            # 可选，默认 127.0.0.1:11435
VLM_MODEL = "qwen3-vl:8b"   # popup_clear.py 里写死
```

底层走 Ollama 的 `/api/chat` 或 `ollama` Python 包。

---

## 第三层：模型 → 外部

- DeepSeek 官方 API：`https://api.deepseek.com`（OpenAI 兼容）
- Ollama 官方库：`https://ollama.com/library/qwen3-vl`（本地推理）

## 环境变量汇总

| 变量 | 用途 | 必填 |
|---|---|---|
| `MEMBRANE_DEEPSEEK_KEY` | DeepSeek 大脑 API key | 是（大脑决策需要） |
| `MEMBRANE_DEEPSEEK_BASE` | DeepSeek API 地址 | 否（默认官方） |
| `MEMBRANE_DEEPSEEK_MODEL` | DeepSeek 模型名 | 否（默认 deepseek-chat） |
| `OLLAMA_HOST` | Ollama 地址 | 否（默认 127.0.0.1:11435） |
