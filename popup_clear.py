# -*- coding: utf-8 -*-
"""
膜 — 弹窗识别 + 点掉（清障脚本）

目的：浏览器里总会弹出乱七八糟的东西（登录框、跨境专供提示、广告浮层、
      「是否允许通知」等），这些弹窗会挡住后续的点击。本脚本在每次操作前
      先把屏幕清干净。

流程（严格「扫描→点击→再扫描」闭环，铁律 automation_stepwise_not_batch）：
    1. 截副屏 → qwen3-vl 视觉识别有没有弹窗、关闭按钮在哪
    2. 有弹窗 → 真实点击（CDP Input.dispatchMouseEvent，不是 JS .click()）
    3. 再截图 → 确认弹窗没了，没清干净或又弹新的就再来一轮
    4. 直到无弹窗或到达最大轮数

用法：
    python popup_clear.py              # 清到干净为止（最多 4 轮）
    python popup_clear.py --once       # 只清一轮
    python popup_clear.py --max 6      # 最多 6 轮
    python popup_clear.py --watch 15   # 持续监控，每 15 秒清一次（Ctrl+C 退出）

不读 DOM、不用 Runtime.evaluate 操作、不用 JS .click() —— 纯视觉 + 真实点击。
"""
import base64
import io
import json
import os
import sys
import time
import urllib.request

import requests
import websocket
from PIL import Image, ImageGrab

# ---- 配置 ----
CDP = "http://127.0.0.1:9222"
OLLAMA = "http://127.0.0.1:11435"
VLM_MODEL = "qwen3-vl:8b"

_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(_DIR, "launch_state.json")
SHOT_PATH = os.path.join(_DIR, "popup_shot.jpg")  # 最近一次截图（调试用）


def load_bounds():
    """从 launch_state.json 读副屏边界。"""
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        left, top = d.get("left", -1920), d.get("top", 0)
        w, h = d.get("width", 1920), d.get("height", 1080)
        if w > 0 and h > 0:
            return int(left), int(top), int(w), int(h)
    return -1920, 0, 1920, 1080


LEFT, TOP, W, H = load_bounds()

# ---- 截图 ----
def screenshot():
    """截副屏，返回 PIL Image (RGB)。"""
    img = ImageGrab.grab(bbox=(LEFT, TOP, LEFT + W, TOP + H), all_screens=True)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def to_b64(img, max_px=786432):
    """PIL → base64 JPEG，过大则缩到 ~1024x768 以内。"""
    w, h = img.size
    if w * h > max_px:
        s = (max_px / (w * h)) ** 0.5
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---- JSON 解析（容忍模型多说话 / 代码块围栏）----
def _extract_json(text):
    text = (text or "").strip()
    # 去 ```json ... ``` 围栏
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法解析 JSON: {text[:200]}")


# ---- 视觉识别弹窗（qwen3-vl:8b，本地 Ollama）----
POPUP_PROMPT = """你是弹窗检测器。看这张浏览器截图，判断屏幕上有没有「弹窗/对话框/浮层/广告」遮挡了主页面内容。

需要点掉的弹窗：登录/注册框、跨境专供或代发提示、广告浮层、是否允许通知、翻译提示、各种模态框。

只输出一个 JSON，不要输出任何其他文字：
{
  "has_popup": true 或 false,
  "popup_desc": "弹窗简短描述，无弹窗则空字符串",
  "close_buttons": [
    {"label": "按钮文字，如 关闭/我知道了/取消/跳过/不再提醒/X", "x": 中心x, "y": 中心y}
  ],
  "advice": "建议点哪个按钮关掉它"
}

规则：
1. 坐标 0-1000 归一化（0=最左/最上，1000=最右/最下），x y 是按钮中心点。
2. close_buttons 只放「能安全关掉弹窗」的按钮：右上角 X、关闭、我知道了、暂不、跳过、不再提醒、以后再说、取消。绝对不要放「登录」「确定」「同意」「立即购买」这类会触发动作的按钮。
3. 优先选按钮文字明确的（比小 X 更容易点准），把最该点的放第一个。
4. 没有弹窗时 has_popup=false，close_buttons 为空数组。
5. 只输出 JSON。"""


def detect_popup(img):
    """截图 → qwen3-vl 识别，返回 dict。识别失败返回 None。"""
    b64 = to_b64(img)
    try:
        resp = requests.post(
            OLLAMA + "/api/chat",
            json={
                "model": VLM_MODEL,
                "messages": [{"role": "user", "content": POPUP_PROMPT, "images": [b64]}],
                "stream": False,
                "options": {"temperature": 0.0},
            },
            timeout=120,
        )
        resp.raise_for_status()
        text = resp.json()["message"]["content"]
        return _extract_json(text)
    except Exception as e:
        print(f"[popup_clear] 视觉识别失败: {e}")
        return None


# ---- CDP：真实点击（Input.dispatchMouseEvent，不是 JS .click()）----
def get_page():
    """拿 CDP 业务页面 target（优先淘宝/1688，回退第一个 http 页）。"""
    try:
        pages = json.loads(urllib.request.urlopen(CDP + "/json", timeout=6).read().decode())
    except Exception:
        return None
    pages = [p for p in pages
             if p.get("type") == "page" and p.get("webSocketDebuggerUrl")
             and p.get("url", "").startswith("http")]
    if not pages:
        return None
    for p in pages:
        if any(k in p.get("url", "") for k in ("myseller.taobao.com", "1688.com")):
            return p
    return pages[0]


def _cdp_raw(ws, method, params=None, timeout=10):
    mid = int(time.time() * 1000) % 100000
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    dl = time.time() + timeout
    while time.time() < dl:
        ws.settimeout(max(0.1, dl - time.time()))
        try:
            r = json.loads(ws.recv())
        except Exception:
            continue
        if r.get("id") == mid:
            return r.get("result", {})
    return {}


def viewport_screen_pos(ws):
    """算 viewport 左上角在屏幕上的绝对坐标（复用膜的老办法）。"""
    _cdp_raw(ws, "Runtime.enable")
    js = ("JSON.stringify({sx: window.screenX || window.screenLeft || 0, "
          "sy: window.screenY || window.screenTop || 0, ow: window.outerWidth, "
          "iw: window.innerWidth, oh: window.outerHeight, ih: window.innerHeight})")
    r = _cdp_raw(ws, "Runtime.evaluate", {"expression": js, "returnByValue": True})
    try:
        d = json.loads(r["result"]["result"]["value"])
    except Exception:
        return LEFT, TOP
    chrome_top = d["oh"] - d["ih"]
    border_left = (d["ow"] - d["iw"]) // 2
    return d["sx"] + border_left, d["sy"] + chrome_top


def real_click(ws, vp_x, vp_y):
    """CDP 真实点击（物理事件管线，能触发真实跳转）。"""
    _cdp_raw(ws, "Input.dispatchMouseEvent",
             {"type": "mousePressed", "x": int(vp_x), "y": int(vp_y),
              "button": "left", "clickCount": 1}, timeout=3)
    _cdp_raw(ws, "Input.dispatchMouseEvent",
             {"type": "mouseReleased", "x": int(vp_x), "y": int(vp_y),
              "button": "left", "clickCount": 1}, timeout=3)


def os_click(screen_x, screen_y):
    """兜底：操作系统级真实点击（能点到浏览器 chrome 区的弹窗）。"""
    import ctypes
    ctypes.windll.user32.SetCursorPos(int(screen_x), int(screen_y))
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFT DOWN
    time.sleep(0.03)
    ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFT UP


# ---- 坐标换算 ----
def img_to_screen(nx, ny):
    """0-1000 归一化 → 屏幕绝对坐标。"""
    return LEFT + nx / 1000.0 * W, TOP + ny / 1000.0 * H


def screen_to_viewport(sx, sy, vp_left, vp_top):
    """屏幕绝对坐标 → viewport 坐标。"""
    return sx - vp_left, sy - vp_top


# ---- 单轮清理 ----
def clear_round(ws, vp_left, vp_top, shot_path=SHOT_PATH):
    """截→识→点→再截 的一轮。返回 (点了没, 结果描述)。"""
    img = screenshot()
    try:
        img.save(shot_path, "JPEG", quality=80)
    except Exception:
        pass

    det = detect_popup(img)
    if det is None:
        return False, "识别失败"

    has = bool(det.get("has_popup"))
    desc = str(det.get("popup_desc", ""))[:60]
    btns = det.get("close_buttons") or []

    if not has or not btns:
        return False, "无弹窗" + (f"（{desc}）" if desc else "")

    # 取第一个（模型按「最该点」排序）安全关闭按钮
    b = btns[0]
    nx = float(b.get("x", 0))
    ny = float(b.get("y", 0))
    label = str(b.get("label", ""))[:12]

    sx, sy = img_to_screen(nx, ny)
    vpx, vpy = screen_to_viewport(sx, sy, vp_left, vp_top)

    # viewport 内 → CDP 真实点击；越界（浏览器 chrome 弹窗）→ OS 级点击
    if 0 <= vpx <= W and 0 <= vpy <= H:
        real_click(ws, vpx, vpy)
        how = f"CDP点击({int(vpx)},{int(vpy)})"
    else:
        os_click(sx, sy)
        how = f"OS点击({int(sx)},{int(sy)})"

    print(f"[popup_clear] 弹窗「{desc}」→ 点「{label}」{how}")
    time.sleep(1.2)  # 等弹窗消失
    return True, f"点了「{label}」"


# ---- 主流程 ----
def main():
    max_rounds = 4
    watch_interval = None

    args = sys.argv[1:]
    if "--once" in args:
        max_rounds = 1
    if "--watch" in args:
        try:
            watch_interval = float(args[args.index("--watch") + 1])
        except (IndexError, ValueError):
            watch_interval = 10.0
    if "--max" in args:
        try:
            max_rounds = int(args[args.index("--max") + 1])
        except (IndexError, ValueError):
            pass

    print(f"[popup_clear] 副屏边界=({LEFT},{TOP}) {W}x{H} | VLM={VLM_MODEL}")

    def clear_all():
        page = get_page()
        if page is None:
            print("[popup_clear] 没有打开的 CDP 页面，跳过")
            return
        try:
            ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15)
        except Exception as e:
            print(f"[popup_clear] 连不上 CDP: {e}")
            return
        try:
            vp_left, vp_top = viewport_screen_pos(ws)
            for i in range(max_rounds):
                clicked, msg = clear_round(ws, vp_left, vp_top)
                if not clicked:
                    print(f"[popup_clear] 第{i+1}轮：{msg}，清障结束")
                    break
                print(f"[popup_clear] 第{i+1}轮：{msg}")
            else:
                print(f"[popup_clear] 已清 {max_rounds} 轮仍有弹窗，停止（可能卡住）")
        finally:
            try:
                ws.close()
            except Exception:
                pass

    if watch_interval is not None:
        print(f"[popup_clear] 进入监控模式，每 {watch_interval}s 清一次（Ctrl+C 退出）")
        try:
            while True:
                clear_all()
                time.sleep(watch_interval)
        except KeyboardInterrupt:
            print("\n[popup_clear] 监控停止")
    else:
        clear_all()


if __name__ == "__main__":
    main()
