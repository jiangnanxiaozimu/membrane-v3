# 模型下载与接入

膜用到**两个模型**，一个本地（眼睛）、一个云端（大脑）。**本仓库不包含任何模型权重文件**，按下面的方式获取即可。

| 零件 | 模型 | 位置 | 获取方式 |
|---|---|---|---|
| ① 眼睛 | `qwen3-vl:8b`（视觉语言模型） | 本地 Ollama | `ollama pull qwen3-vl:8b` |
| ② 大脑 | `deepseek-chat`（DeepSeek Flash） | 云端 API | 注册拿 key，走环境变量 |

> 旧版的本地大脑 `qwen3:14b` 已移除 —— 大脑统一用云端 DeepSeek Flash，本地只保留视觉模型 `qwen3-vl:8b`。

---

## ① 眼睛：qwen3-vl:8b（本地 Ollama）

用途：截屏后识别「页面上有没有弹窗 / 关闭按钮在哪」等视觉判断（`popup_clear.py` 等）。

```bash
# 安装 Ollama（如未装）：https://ollama.com/download
# 拉取视觉模型（约 5GB）
ollama pull qwen3-vl:8b

# 确认已就位
ollama list
```

默认连接地址 `http://127.0.0.1:11434`；本项目的约定端口是 **11435**，通过环境变量 `OLLAMA_HOST` 覆盖：

```bash
export OLLAMA_HOST=127.0.0.1:11435
```

代码里的读取位置：
- `membrane_vision.py` — 视觉扫描（UIA + CDP 为主，VL 为辅）
- `popup_clear.py` — `VLM_MODEL = "qwen3-vl:8b"`（弹窗识别）

---

## ② 大脑：DeepSeek Flash（deepseek-chat，云端 API）

用途：执行前校验每一步「该不该点」，输出 JSON 决策（`membrane_operator.py` 的 `Operator.decide()`）。

接入三步：

```bash
# 1. 注册并创建 API key：https://platform.deepseek.com
# 2. 写入环境变量（不硬编码到代码）
export MEMBRANE_DEEPSEEK_KEY=sk-xxxxxxxxxxxxxxxx

# 3.（可选）覆盖默认地址/模型名
export MEMBRANE_DEEPSEEK_BASE=https://api.deepseek.com
export MEMBRANE_DEEPSEEK_MODEL=deepseek-chat
```

代码读取位置（`membrane_operator.py`）：

```python
DEFAULT_MODEL   = os.environ.get("MEMBRANE_DEEPSEEK_MODEL", "deepseek-chat")
DEFAULT_API_BASE = os.environ.get("MEMBRANE_DEEPSEEK_BASE", "https://api.deepseek.com")
# key 在调用时读：os.environ.get("MEMBRANE_DEEPSEEK_KEY", "")
```

调用接口：OpenAI 兼容的 `POST {base}/v1/chat/completions`，`response_format={"type":"json_object"}`。

---

## 不用装的

- `qwen3:14b`（旧版本地大脑）— 已砍，无需安装。
- ComfyUI（`127.0.0.1:8188`）— 图像生成用，与膜的扫描/决策闭环无关，可选。
