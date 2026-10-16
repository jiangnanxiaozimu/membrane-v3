"""
crawl_qianniu_modal.py — 千牛弹窗/抽屉/Tab 深爬（Phase 2）

Phase 1（crawl_qianniu_deep.py）抓的是「页面主视图」。这里补抓首屏看不见的 UI 态：
  1. Tab 切换：点每个 tab → 扫切换后的内容
  2. 弹窗/抽屉：点「查看/设置/编辑/新增/筛选…」这类触发按钮 → 扫弹窗内容 → ESC/关闭按钮关掉

安全铁律（比 Phase 1 更严）：
  - 只点「打开弹窗」类按钮（白名单关键词），绝不点 发货/删除/退款/提交/保存/确认/下架/发布/复制/打印/导出
  - 弹窗内只扫不点；扫完立即 ESC + 点关闭按钮
  - 每次点击后校验：若 URL 变了（点进详情页）→ 立即导航回当前 nav

用法：
  python crawl_qianniu_modal.py                # 全量：所有 base 页的 tab + modal
  python crawl_qianniu_modal.py --only 1.4_1   # 只爬某个页面状态（调试）
  python crawl_qianniu_modal.py --resume       # 断点继续

输出：store/apps/qianniu/pages/ 写 *_modal.json；进度 crawl_modal_state.json
"""
import sys, json, io, time, os, urllib.request
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import websocket
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "websocket-client"], check=True)
    import websocket

CDP_BASE = "http://127.0.0.1:9222"
OUTPUT_DIR = Path("D:/AI/membrane/store/apps/qianniu/pages")
STATE_FILE = Path("D:/AI/membrane/crawl_modal_state.json")

NAV_ITEMS = [
    ("1.1", "首页"), ("1.2", "常用"), ("1.3", "交易"), ("1.4", "商品"),
    ("1.5", "营销"), ("1.6", "推广"), ("1.7", "客服"), ("1.8", "店铺"),
    ("1.9", "私域"), ("1.10", "财务"), ("1.11", "金融"), ("1.12", "数据"),
    ("1.13", "服务"),
]

# 打开弹窗类按钮的白名单关键词（点这些按钮只会开弹窗，不改数据）
MODAL_TRIGGERS = ["查看", "设置", "编辑", "新增", "添加", "筛选", "管理", "配置",
                  "详情", "规则", "模板", "类目", "品牌", "标签", "上传", "导入",
                  "绑定", "关联", "选择", "新建", "创建", "设计", "装修", "诊断",
                  "分析", "预览", "更多", "展开", "帮助", "说明", "教程", "消息",
                  "通知", "反馈", "客服", "协议", "下载", "发布", "去看看", "规则中心"]

# 危险动作词：子串包含即拦（无论前缀后缀，"批量删除""一键发货"都会被拦）
DANGER_ACTIONS = ["删除", "发货", "退款", "下架", "提交", "保存", "确认", "复制",
                  "打印", "导出", "撤回", "同意", "拒绝", "关闭", "停用", "支付",
                  "提现", "转账", "签约", "开通", "申请", "评价", "投诉", "举报",
                  "屏蔽", "拉黑", "确定", "取消", "重置", "清空"]

# "发布"特例：这些是"打开发布表单/入口"，安全可点；其余含"发布"的（发布/立即发布）是动作，危险
PUBLISH_SAFE = ["发布商品", "发布宝贝", "去发布", "发布攻略", "发布教程", "发布活动",
                "发布作品", "发布内容", "发布新", "发布直播"]


def is_danger(text):
    t = (text or "").replace(" ", "")
    if "发布" in t:
        return not any(s in t for s in PUBLISH_SAFE)
    return any(a in t for a in DANGER_ACTIONS)

SCAN_JS = open(Path(__file__).resolve().parent / "crawl_qianniu_deep.py", encoding="utf-8").read()
# 从 deep 脚本里抠出 SCAN_JS（含噪声过滤）复用
import re
m = re.search(r'SCAN_JS = """.*?"""', SCAN_JS, re.S)
SCAN_JS = m.group(0).split('"""', 2)[1] if m else None


def cdp_list_pages():
    try:
        return json.loads(urllib.request.urlopen(f"{CDP_BASE}/json", timeout=5).read().decode())
    except Exception:
        return []


def find_qianniu_tab():
    for p in cdp_list_pages():
        if "myseller.taobao.com" in p.get("url", "") or "qn.taobao.com" in p.get("url", ""):
            return p
    return None


def connect():
    page = find_qianniu_tab()
    if not page:
        return None, None
    return websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20), page


def cdp_raw(ws, method, params=None, timeout=10):
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


def cdp_eval(ws, expr, timeout=10):
    r = cdp_raw(ws, "Runtime.evaluate", {"expression": expr, "returnByValue": True}, timeout)
    if "_error" in r:
        return r
    if "exceptionDetails" in r:
        return {"_error": r["exceptionDetails"].get("text", "?")[:100]}
    return r.get("result", {}).get("value")


def js_val(ws, expr, timeout=10):
    v = cdp_eval(ws, expr, timeout)
    if isinstance(v, dict) and "_error" in v:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def click_at(ws, cx, cy):
    cdp_raw(ws, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy,
                                             "button": "left", "clickCount": 1}, timeout=3)
    cdp_raw(ws, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy,
                                             "button": "left", "clickCount": 1}, timeout=3)


def scan_dom(ws):
    if not SCAN_JS:
        return []
    v = js_val(ws, SCAN_JS, timeout=20)
    return v if isinstance(v, list) else []


def merge(acc, elems):
    for e in elems:
        k = (e.get("tag"), e.get("x"), e.get("y"), e.get("w"), e.get("h"), (e.get("text") or "")[:40])
        acc.setdefault(k, e)
    return acc


def get_modal_triggers(ws):
    """找当前页里能点开弹窗的按钮（白名单关键词 + a/button）。"""
    v = js_val(ws, """
        JSON.stringify(Array.from(document.querySelectorAll('a,button,[role="button"],[role="link"],[class*="btn"],[class*="Btn"]'))
            .map(el => {
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 12) return null;
                if (r.width > 400 || r.height > 80) return null;
                if (r.top < 0 || r.left < 0 || r.top > innerHeight || r.left > innerWidth) return null;
                const s = getComputedStyle(el);
                if (s.display === 'none' || s.visibility === 'hidden') return null;
                return {text:(el.innerText||el.textContent||'').trim().substring(0,30),
                        cx:Math.round(r.left+r.width/2), cy:Math.round(r.top+r.height/2)};
            })
            .filter(Boolean))
    """)
    return v if isinstance(v, list) else []


def get_tabs(ws):
    v = js_val(ws, """
        JSON.stringify(Array.from(document.querySelectorAll('[role="tab"]'))
            .map(el => {
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 10) return null;
                if (r.top < 0 || r.left < 0 || r.top > innerHeight || r.left > innerWidth) return null;
                return {text:(el.innerText||el.textContent||'').trim().substring(0,20),
                        cx:Math.round(r.left+r.width/2), cy:Math.round(r.top+r.height/2)};
            })
            .filter(Boolean))
    """)
    return v if isinstance(v, list) else []


def modal_open(ws):
    v = js_val(ws, """
        JSON.stringify(Array.from(document.querySelectorAll('[role="dialog"],[class*="modal"],[class*="Modal"],[class*="drawer"],[class*="Drawer"],[class*="dialog"],[class*="Dialog"],[class*="overlay"],[class*="Overlay"]'))
            .filter(el => { const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
                return r.width>100 && r.height>100 && s.display!=='none' && s.visibility!=='hidden' && s.opacity!=='0'; }).length)
    """)
    return (v or 0) if isinstance(v, int) else 0


def close_modal(ws):
    cdp_raw(ws, "Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27}, timeout=3)
    cdp_raw(ws, "Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27}, timeout=3)
    time.sleep(0.6)
    v = js_val(ws, """
        JSON.stringify((() => {
            const btn = document.querySelector('[class*="dialog-close"],[class*="drawer-close"],[class*="closeBtn"],[class*="CloseBtn"],[aria-label="关闭"],[aria-label="Close"]');
            if (!btn) return null;
            const r = btn.getBoundingClientRect();
            return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)};
        })())
    """)
    if isinstance(v, dict) and v.get("x") is not None:
        click_at(ws, v["x"], v["y"])
        time.sleep(0.6)


def click_nav(ws, idx):
    cdp_eval(ws, f"document.querySelectorAll('a[class*=\"navItem--\"],a[class*=\"NavItem--\"]')[{idx}].click()", timeout=5)
    time.sleep(3.0)


def click_sub(ws, idx):
    cdp_eval(ws, f"""
        (() => {{
            const items = document.querySelectorAll('.tbd-menu-item[class*="only-child"],[class*="secondMenuNavItem"],[class*="menuItem--"],[class*="MenuItem--"]');
            if ({idx} < items.length) items[{idx}].click();
        }})()
    """, timeout=5)
    time.sleep(2.5)


def detect_sub_menu(ws):
    v = js_val(ws, """
        (() => {
            const menuItems = document.querySelectorAll('.tbd-menu-item[class*="only-child"],[class*="secondMenuNavItem"],[class*="menuItem--"],[class*="MenuItem--"]');
            const seenY = new Set(); const out = [];
            menuItems.forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width < 30 || r.height < 10) return;
                const t = (el.querySelector('[class*="title"]') || el).textContent.trim().substring(0,30);
                if (!t) return;
                const y = Math.round(r.top);
                if (seenY.has(y)) return; seenY.add(y);
                out.push({text:t, cx:Math.round(r.left+r.width/2), cy:Math.round(r.top+r.height/2)});
            });
            return JSON.stringify(out);
        })()
    """)
    return v if isinstance(v, list) else []


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {"done": {}}


def save_state(st):
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def crawl_tabs(ws, acc):
    """点每个 tab，扫内容。返回新增 tab 状态数。"""
    tabs = get_tabs(ws)
    n = 0
    for t in tabs:
        if not t.get("text"):
            continue
        click_at(ws, t["cx"], t["cy"])
        time.sleep(1.2)
        merge(acc, scan_dom(ws))
        n += 1
        if n >= 12:
            break
    return n


def crawl_modals(ws, acc, page_url_before):
    """点白名单弹窗按钮，扫弹窗，关掉。返回打开的弹窗数。"""
    triggers = get_modal_triggers(ws)
    opened = 0
    seen_texts = set()
    for tr in triggers:
        text = tr.get("text", "")
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        # 白名单过滤：必须含触发关键词，且不是危险动作
        if not any(k in text for k in MODAL_TRIGGERS):
            continue
        if is_danger(text):
            continue
        click_at(ws, tr["cx"], tr["cy"])
        time.sleep(1.5)
        # 校验：URL 变了 = 点进详情页了，不是弹窗 → 导航回来
        url_now = js_val(ws, "location.href")
        if url_now and page_url_before and url_now != page_url_before:
            print(f"      ↪️ [{text}] URL 变了（详情页），导航回…")
            cdp_eval(ws, "history.back()", timeout=5)
            time.sleep(2.5)
            continue
        if modal_open(ws):
            merge(acc, scan_dom(ws))
            opened += 1
            close_modal(ws)
        # 弹窗没开（可能是跳转/无操作），继续
    return opened


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    resume = "--resume" in sys.argv
    if "--list-state" in sys.argv:
        st = load_state()
        print("已完成页面:", list(st.get("done", {}).keys()))
        return 0

    ws, page = connect()
    if not ws:
        print("❌ 没找到千牛页面")
        return 1
    print(f"已连接: {page.get('title', '?')[:50]}")
    st = load_state()
    done = st.get("done", {})

    for nav_id, nav_text in NAV_ITEMS:
        idx = int(nav_id.split(".")[1])
        if only and only.split("_")[0] != nav_id:
            continue
        print(f"\n{'='*50}\n📄 [{nav_id}] {nav_text}")
        click_nav(ws, idx - 1)

        subs = detect_sub_menu(ws) or []
        targets = [(nav_id, None, nav_text)] + \
                  [(nav_id, si, s["text"]) for si, s in enumerate(subs)]

        for tid, sub_idx, label in targets:
            key = tid if sub_idx is None else f"{tid}_{sub_idx}"
            if only and key != only:
                continue
            if resume and key in done:
                print(f"  ⏭️ {key} {label} 已爬过，跳过")
                continue
            # 导航到该页
            if sub_idx is None:
                click_nav(ws, idx - 1)
            else:
                # 确保在正确 nav 下，再点子菜单
                if detect_sub_menu(ws):
                    pass
                # 重新点 nav 再点子菜单
                click_nav(ws, idx - 1)
                subs2 = detect_sub_menu(ws) or []
                if sub_idx < len(subs2):
                    click_sub(ws, sub_idx)

            url_before = js_val(ws, "location.href")
            acc = {}
            merge(acc, scan_dom(ws))
            nt = crawl_tabs(ws, acc)
            nm = crawl_modals(ws, acc, url_before)

            elems = list(acc.values())
            out_name = f"page_{tid.replace('.','_')}" + (f"_sub_{sub_idx:02d}" if sub_idx is not None else "") + "_modal.json"
            save_record = {
                "scanned_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "app_slug": "qianniu", "crawl": "modal",
                "parent_nav_id": tid, "sub_index": sub_idx, "sub_text": label,
                "page_url": str(url_before), "element_count": len(elems),
                "tabs_clicked": nt, "modals_opened": nm, "elements": elems,
            }
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            with open(OUTPUT_DIR / out_name, "w", encoding="utf-8") as f:
                json.dump(save_record, f, ensure_ascii=False, indent=2)
            done[key] = {"elements": len(elems), "tabs": nt, "modals": nm}
            save_state(st)
            print(f"  ✅ {key} {label}: {len(elems)} 元素 (tab{nt} 弹窗{nm})")

    ws.close()
    total = sum(v.get("elements", 0) for v in done.values())
    print(f"\n✅ Phase2 完成。{len(done)} 页面状态，总 {total} 元素。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
