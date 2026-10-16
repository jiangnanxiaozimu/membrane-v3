"""
operator.py — 操控员（大脑 · DeepSeek Flash）

职责：每一步执行前，用云端 DeepSeek Flash（deepseek-chat）判断「这一步对不对 / 该不该点」。
      —— 文本校验，不做视觉定位（定位是 VL / DOM / 匹配的活）。

设计（对齐 REPLAN.md §⑤）：
  工作流说「点 A」→ 扫描/匹配说「A 在 (x,y)」→ 操控员判断「这个 (x,y) 合理吗、要不要点」。
  输出 JSON 决策：{approved, confidence, reason, correction}。

纯逻辑模块，无 UI，可独立 import / 单测（喂假 step，断言返回 JSON 决策）。

模型：DeepSeek Flash（deepseek-chat，云端 API），不再依赖本地 Ollama qwen3:14b。
密钥：环境变量 MEMBRANE_DEEPSEEK_KEY，不硬编码。
"""
import json
import os
import re


DEFAULT_MODEL = os.environ.get("MEMBRANE_DEEPSEEK_MODEL", "deepseek-chat")
DEFAULT_API_BASE = os.environ.get("MEMBRANE_DEEPSEEK_BASE", "https://api.deepseek.com")


def _resolve_base_url():
    """解析 DeepSeek API 地址（保留旧名字以兼容历史 import）。"""
    base = DEFAULT_API_BASE.strip()
    return base if base.startswith("http") else "https://" + base


def _extract_json(text):
    """从模型输出里抠出 JSON（容忍前后有多余文字 / 代码块围栏）。"""
    if not text:
        raise ValueError("模型返回空")
    text = text.strip()
    # 去掉 markdown 代码块围栏（```json ... ```）
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 抠第一个 { ... } 块（贪心到最后一个 }）
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法解析 JSON: {text[:200]}")


def _deepseek_chat(messages, model=DEFAULT_MODEL, temperature=0.0, response_json=False,
                   api_base=None, api_key=None):
    """调 DeepSeek /v1/chat/completions，返回 {"message": {"content": ...}}（对齐 ollama 返回结构）。"""
    import requests
    api_key = api_key or os.environ.get("MEMBRANE_DEEPSEEK_KEY", "")
    if not api_key:
        raise ValueError("缺少 DeepSeek API key：请设置环境变量 MEMBRANE_DEEPSEEK_KEY")
    base = (api_base or DEFAULT_API_BASE).rstrip("/")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}
    resp = requests.post(
        f"{base}/v1/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return {"message": {"content": content}}


class DeepSeekClient:
    """模拟 ollama.Client 接口的 DeepSeek Flash 客户端。

    operator_verify.py 原本用 ollama.Client(host=...).chat(model=..., messages=[...],
    format="json", options={"temperature":...})，本类提供同名 .chat() 接口，
    内部转成 DeepSeek /v1/chat/completions，返回 {"message": {"content": ...}}。
    """

    def __init__(self, model=DEFAULT_MODEL, api_base=DEFAULT_API_BASE, api_key=None):
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key or os.environ.get("MEMBRANE_DEEPSEEK_KEY", "")

    def chat(self, model=None, messages=None, format=None, options=None, **kwargs):
        options = options or {}
        return _deepseek_chat(
            messages=messages or [],
            model=model or self.model,
            temperature=options.get("temperature", 0.0),
            response_json=(format == "json"),
            api_base=self.api_base,
            api_key=self.api_key,
        )


class Operator:
    """DeepSeek Flash 操控员：执行前校验决策。"""

    def __init__(self, model=DEFAULT_MODEL, api_base=DEFAULT_API_BASE, api_key=None):
        self.model = model
        self.api_base = api_base
        self.api_key = api_key

    # ---- 底层：调模型出 JSON ----
    def chat_json(self, system, user, temperature=0.0):
        """调 DeepSeek Flash，强制 JSON 输出，解析成 dict。temperature=0 求稳定。"""
        resp = _deepseek_chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=self.model,
            temperature=temperature,
            response_json=True,
            api_base=self.api_base,
            api_key=self.api_key,
        )
        return _extract_json(resp["message"]["content"])

    # ---- 决策：这一步该不该做 ----
    def decide(self, intent, candidate, context=None):
        """校验一步操作。

        intent: str —— 工作流想干什么，如 '点击「发布」按钮'
        candidate: dict —— 匹配到的候选元素 {type, name, cx, cy, ...}
        context: dict —— 可选，{state, previous_steps, ...}

        返回 dict：
          {approved: bool, confidence: float, reason: str, correction: str|None}
        """
        system = (
            "你是膜的操控员，负责在执行前校验每一步操作是否合理。"
            "你只输出一个 JSON 对象，字段固定为："
            '{"approved": true/false, "confidence": 0到1之间的数, '
            '"reason": "一句话理由", "correction": "需要修正时给修正建议，否则 null"}。'
            "判断标准：候选元素语义与意图是否匹配、坐标是否在合理范围、是否存在明显风险。"
        )
        ctx = json.dumps(context, ensure_ascii=False) if context else "无"
        user = (
            f"意图：{intent}\n"
            f"候选元素：{json.dumps(candidate, ensure_ascii=False)}\n"
            f"上下文：{ctx}\n"
            f"请判断这一步是否应该执行。"
        )
        decision = self.chat_json(system, user)
        # 兜底补全字段，防模型漏字段
        decision.setdefault("approved", False)
        decision.setdefault("confidence", 0.0)
        decision.setdefault("reason", "")
        decision.setdefault("correction", None)
        return decision
