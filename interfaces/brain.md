# ② 大脑接口 — DeepSeek Flash 操控员

**文件**：`membrane_operator.py`

**职责**：执行前校验每一步「该不该点」，输出 JSON 决策。**只做文本决策，不做视觉定位**（定位是眼睛 `membrane_vision` 的活）。

## 核心接口

```python
from membrane_operator import Operator, DeepSeekClient

# 1. 操控员决策（执行引擎主用）
Operator().decide(intent, candidate, context=None) -> dict
#   intent    : str   工作流想干什么，如 '点击「发布」按钮'
#   candidate : dict  匹配到的候选元素 {type, name, cx, cy, ...}
#   context   : dict  可选 {state, previous_steps, ...}
#   返回      : {"approved": bool, "confidence": 0~1, "reason": str, "correction": str|None}

# 2. 兼容 ollama 接口的底层客户端（operator_verify.py 用）
DeepSeekClient().chat(model=..., messages=[...], format="json", options={"temperature": 0.0})
#   返回 {"message": {"content": "..."}}（对齐 ollama.Client 返回结构）
```

## 模型配置

```bash
export MEMBRANE_DEEPSEEK_KEY=sk-xxx        # 必填
export MEMBRANE_DEEPSEEK_BASE=...          # 可选，默认 https://api.deepseek.com
export MEMBRANE_DEEPSEEK_MODEL=...         # 可选，默认 deepseek-chat
```

底层走 OpenAI 兼容的 `POST {base}/v1/chat/completions`，`response_format={"type":"json_object"}`。

## 对接注意

- `decide()` 返回的 `approved=False` 时，执行引擎会停步（当前四段流水线逻辑），不会乱点。
- 无 key 时调用会抛 `ValueError("缺少 DeepSeek API key...")`，不会静默失败。
- 旧版本地大脑 `qwen3:14b` 已移除，不要再依赖它。
