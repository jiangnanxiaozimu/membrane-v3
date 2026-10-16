"""
verify_random_pages.py — 随机抽查 20 个爬取页面，导航 + 截屏验证

从 crawl_deep_state.json 随机选 20 个 url_key，让膜逐个导航过去，
CDP Page.captureScreenshot 截屏保存，PIL 检查像素（尺寸/亮度/颜色数），
确认爬取数据对应的页面真实存在、真实渲染（非黑屏/白屏/空白）。

用法：python scripts/verify_random_pages.py [数量]   # 默认 20
输出：verify_shots/shot_{url_key}.png + 控制台汇总报告
"""
import sys, json, io, time, os, random, base64, urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import websocket
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "websocket-client"], check=True)
    import websocket

CDP_BASE = "http://127.0.0.1:9222"
SHOT_DIR = Path("D:/AI/membrane/verify_shots")
STATE_PATH = Path("D:/AI/membrane/crawl_deep_state.json")


# ---------------- CDP 基础 ----------------
def cdp_list_pages():
    try:
        return json.loads(urllib.request.urlopen(f"{CDP_BASE}/json", timeout=5).read().decode())
    except Exception:
        return []


def find_main_tab():
    """主工作台 = myseller.taobao.com（千牛卖家中心），排除选货市场/发布页/跨域 frame。"""
    for p in cdp_list_pages():
        if "myseller.taobao.com" in p.get("url", ""):
            return p
    return None


def connect():
    page = find_main_tab()
    if not page:
        return None, None
    return websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20), page


def cdp_raw(ws, method, params=None, timeout=15):
    mid = int(time.time() * 1000) % 100000
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        ws.settimeout(max(0.1, deadline - time.time()))
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except Exception:
            return {"_error": "recv"}
        try:
            r = json.loads(raw)
        except Exception:
            continue
        if r.get("id") == mid:
            if "error" in r:
                return {"_error": str(r["error"])}
            return r.get("result", {})
    return {"_error": "timeout"}


def cdp_eval(ws, expr, timeout=15):
    r = cdp_raw(ws, "Runtime.evaluate", {"expression": expr, "returnByValue": True}, timeout)
    if "_error" in r:
        return r
    if "exceptionDetails" in r:
        return {"_error": r["exceptionDetails"].get("text", "?")[:120]}
    return r.get("result", {}).get("value")


def js_val(ws, expr, timeout=15):
    v = cdp_eval(ws, expr, timeout)
    if isinstance(v, dict) and "_error" in v:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


# ---------------- 导航 ----------------
def click_nav(ws, idx):
    cdp_eval(ws, f"document.querySelectorAll('a[class*=\"navItem--\"],a[class*=\"NavItem--\"]')[{idx}].click()", timeout=5)
    time.sleep(2.8)


def click_sub(ws, idx):
    cdp_eval(ws, f"""
        (() => {{
            const items = document.querySelectorAll('.tbd-menu-item[class*="only-child"],[class*="secondMenuNavItem"],[class*="menuItem--"],[class*="MenuItem--"]');
            if ({idx} < items.length) items[{idx}].click();
        }})()
    """, timeout=5)
    time.sleep(2.2)


def nav_to(ws, url_key):
    """url_key 形如 '1.4'（主页）或 '1.4_0'（子菜单）→ 点导航。"""
    parts = url_key.split("_")
    nav_id = parts[0]                       # "1.4"
    idx = int(nav_id.split(".")[1]) - 1     # "1.4" → 3（0-indexed）
    click_nav(ws, idx)
    if len(parts) > 1:
        click_sub(ws, int(parts[1]))
    time.sleep(1.0)


# ---------------- 截屏 + 校验 ----------------
def capture(ws, path):
    """CDP 截屏 → PNG 字节数（0 表示失败）。"""
    r = cdp_raw(ws, "Page.captureScreenshot", {"format": "png"}, timeout=20)
    if "data" in r:
        data = base64.b64decode(r["data"])
        Path(path).write_bytes(data)
        return len(data)
    return 0


def check_image(path):
    """PIL 检查：尺寸 / 平均亮度 / 颜色数（判断是否黑屏/白屏/空白）。"""
    try:
        from PIL import Image
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "Pillow"], check=True)
        from PIL import Image
    img = Image.open(path).convert("RGB")
    w, h = img.size
    px = list(img.getdata())
    step = max(1, len(px) // 4000)
    sample = px[::step]
    avg = sum(sum(p) for p in sample) / (len(sample) * 3)
    colors = len(set(sample[:2000]))
    return w, h, round(avg, 1), colors


# ---------------- 主逻辑 ----------------
def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))["done"]
    keys = list(state.keys())
    if n > len(keys):
        n = len(keys)
    sample = random.sample(keys, n)

    ws, page = connect()
    if not ws:
        print("❌ 没找到千牛主工作台（myseller.taobao.com）")
        return 1
    print(f"已连接主工作台: {page.get('title', '?')[:40]}")
    print(f"随机抽查 {n} 个页面（共 {len(keys)} 个 url_key）\n")

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    cdp_raw(ws, "Page.enable")  # 启用 Page domain 以便截屏

    rows = []
    ok = 0
    for url_key in sample:
        try:
            nav_to(ws, url_key)
            url_now = js_val(ws, "location.href")
            fname = f"shot_{url_key}.png"
            fpath = SHOT_DIR / fname
            nbytes = capture(ws, fpath)
            if nbytes > 0:
                w, h, avg, colors = check_image(fpath)
                # 判定：非黑（avg>15）非白（avg<245）且有内容（colors>3）
                good = (15 < avg < 245) and colors > 3
                status = "✅" if good else "⚠️"
                if good:
                    ok += 1
            else:
                w = h = avg = colors = 0
                status = "❌ 截屏失败"
            rows.append((url_key, status, nbytes, w, h, avg, colors, str(url_now)[:60]))
            print(f"  {status} {url_key:8s} | {nbytes:>6}B | {w}x{h} | 亮度{avg} 色{colors} | {str(url_now)[:45]}")
        except Exception as e:
            rows.append((url_key, "❌ 异常", 0, 0, 0, 0, 0, str(e)[:50]))
            print(f"  ❌ {url_key} 异常: {e}")

    ws.close()
    print(f"\n{'='*60}")
    print(f"抽查完成：{ok}/{n} 个页面截屏正常（非空白/黑屏/白屏）")
    print(f"截图目录：{SHOT_DIR}")
    # 覆盖统计：抽查覆盖了几个 nav 区
    navs = sorted({k.split('_')[0] for k, *_ in rows})
    print(f"覆盖导航区：{navs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
