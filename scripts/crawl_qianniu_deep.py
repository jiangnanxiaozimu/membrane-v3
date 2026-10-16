"""
crawl_qianniu_deep.py — 千牛深爬（第二组记忆团，全量）

对比旧 scan_all_pages.py（只扫首屏 + 限选器，覆盖 ~10%）：
  1. 滚动所有可滚容器（懒加载内容全抓）
  2. 全量交互元素捕获（a/button/input/role/onclick/tabindex + 常见类名）
  3. 折叠面板展开后重扫
  4. 记录 tab / modal / dropdown 的存在（为下一步 modal 爬取打底）

安全铁律：
  - 只点导航（nav）+ 子菜单 + 折叠头，绝不点「发货/删除/退款/提交/发布/下架」等动作按钮
  - 每页只滚 + 扫 + 展开折叠，不点进详情、不开弹窗（弹窗/详情留给 phase 2）

用法：
  python crawl_qianniu_deep.py                # 全量：13 导航 + 全部子菜单
  python crawl_qianniu_deep.py --only 1.4     # 只爬某个导航（调试用）
  python crawl_qianniu_deep.py --resume       # 从断点继续
  python crawl_qianniu_deep.py --list-state   # 打印当前进度

输出：store/apps/qianniu/pages/ 下写 *_deep.json（与旧 *_raw.json 并存，不覆盖）
      进度落盘 crawl_deep_state.json
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
STATE_FILE = Path("D:/AI/membrane/crawl_deep_state.json")

NAV_ITEMS = [
    ("1.1", "首页"), ("1.2", "常用"), ("1.3", "交易"), ("1.4", "商品"),
    ("1.5", "营销"), ("1.6", "推广"), ("1.7", "客服"), ("1.8", "店铺"),
    ("1.9", "私域"), ("1.10", "财务"), ("1.11", "金融"), ("1.12", "数据"),
    ("1.13", "服务"),
]

# 折叠面板展开时不碰这些词（避免误点成动作）
EXPAND_UNSAFE = {"删除", "发货", "退款", "提交", "发布", "下架", "确认", "复制",
                 "打印", "导出", "撤回", "同意", "拒绝", "关闭", "停用", "支付",
                 "提现", "转账", "签约", "开通", "申请"}

# ---- 元素捕获 JS：全量交互元素 + 常见可点类名，取「直接文本」过滤容器噪声 ----
SCAN_JS = """
(() => {
    const SEL = 'a,button,input,select,textarea,' +
        '[role="button"],[role="link"],[role="tab"],[role="menuitem"],[role="listitem"],' +
        '[role="radio"],[role="checkbox"],[role="combobox"],[role="treeitem"],[role="switch"],' +
        '[onclick],[tabindex],' +
        '[class*="btn"],[class*="Btn"],[class*="menuItem"],[class*="navItem"],[class*="NavItem"],' +
        '[class*="tab"],[class*="Tab"],[class*="menu"],[class*="Menu"],' +
        '[class*="link"],[class*="Link"],[class*="select"],[class*="Select"],' +
        '[class*="picker"],[class*="Picker"],[class*="collapse"],[class*="Collapse"],' +
        '[class*="fold"],[class*="Fold"],[class*="switch"],[class*="Switch"],' +
        '[class*="item"],[class*="Item"],[class*="card"],[class*="Card"]';
    const INLINE = new Set(['SPAN','B','I','EM','STRONG','SMALL','SUB','SUP']);
    function ownText(el) {
        // 只取元素自己的文本节点（+ 行内子元素），不含块级子元素文本
        let t = '';
        el.childNodes.forEach(n => {
            if (n.nodeType === 3) t += n.nodeValue;
            else if (n.nodeType === 1 && INLINE.has(n.tagName)) t += n.textContent;
        });
        return t.replace(/\\s+/g, ' ').trim();
    }
    const seen = new Set();
    const out = [];
    document.querySelectorAll(SEL).forEach(el => {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        if (r.width === 0 || r.height === 0) return;
        if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return;
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const hasOnclick = el.hasAttribute && el.hasAttribute('onclick');
        const isRealInteractive = ['a','button','input','select','textarea'].includes(tag)
            || role || hasOnclick || el.hasAttribute('tabindex');
        let text = ownText(el);
        if (!text && isRealInteractive) {
            // 交互元素无直接文本时，兜底取 innerText（截断）
            text = (el.innerText || el.textContent || '').replace(/\\s+/g,' ').trim().substring(0, 80);
        }
        // 噪声过滤：既非真交互、又没直接文本，或块太大（是容器）→ 跳过
        if (!text && !isRealInteractive) return;
        if (!isRealInteractive && !text) return;
        if (!isRealInteractive && (r.width > 500 || r.height > 100)) return;
        text = text.substring(0, 150);
        const key = tag + '|' + Math.round(r.left) + '|' + Math.round(r.top) +
                    '|' + Math.round(r.width) + '|' + Math.round(r.height) + '|' + text.substring(0, 40);
        if (seen.has(key)) return;
        seen.add(key);
        const rec = {
            tag, cls: (typeof el.className === 'string') ? el.className.substring(0, 200) : '',
            id: el.id || '', role, text,
            x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height),
        };
        if (tag === 'input') {
            rec.input_type = el.type || ''; rec.placeholder = el.placeholder || '';
            rec.value = (el.value || '').substring(0, 100);
        }
        if (tag === 'a') rec.href = el.getAttribute('href') || '';
        out.push(rec);
    });
    return JSON.stringify(out);
})()
"""

# ---- 折叠面板检测 JS ----
COLLAPSE_JS = """
(() => {
    const heads = document.querySelectorAll('[class*="collapse-header"],[class*="CollapseHeader"],[class*="collapse-title"],[class*="panel-title"],[class*="PanelTitle"],[class*="fold-title"],[class*="section-title"],[aria-expanded]');
    const out = [];
    heads.forEach((el, i) => {
        const r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 8) return;
        const expanded = el.getAttribute('aria-expanded');
        out.push({
            i, text: (el.innerText || el.textContent || '').trim().substring(0, 30),
            cx: Math.round(r.left + r.width/2), cy: Math.round(r.top + r.height/2),
            expanded: expanded === 'true',
        });
    });
    return JSON.stringify(out);
})()
"""

# ---- 结构统计 JS（tab/modal/dropdown 数量，打底用） ----
STRUCT_JS = """
(() => {
    return JSON.stringify({
        tabs: document.querySelectorAll('[role="tab"]').length,
        modal: document.querySelectorAll('[class*="modal"],[class*="Modal"],[class*="dialog"],[class*="Dialog"],[class*="drawer"],[class*="Drawer"],[role="dialog"]').length,
        dropdown: document.querySelectorAll('[class*="select"],[class*="Select"],[class*="picker"],[class*="Picker"],[class*="dropdown"],[class*="Dropdown"]').length,
    });
})()
"""


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
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20)
    return ws, page


def cdp_raw(ws, method, params=None, timeout=12):
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


def cdp_eval(ws, expr, timeout=12):
    r = cdp_raw(ws, "Runtime.evaluate", {"expression": expr, "returnByValue": True}, timeout)
    if "_error" in r:
        return r
    if "exceptionDetails" in r:
        return {"_error": r["exceptionDetails"].get("text", "?")[:100]}
    return r.get("result", {}).get("value")


def js_val(ws, expr, timeout=12):
    """执行 JS，返回解析后的值（str→json，否则原样）。"""
    v = cdp_eval(ws, expr, timeout)
    if isinstance(v, dict) and "_error" in v:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def scan_dom(ws):
    v = js_val(ws, SCAN_JS, timeout=20)
    return v if isinstance(v, list) else []


def merge_elements(acc, new):
    """按 位置+tag+文本前40 去重合并。"""
    for el in new:
        key = (el.get("tag"), el.get("x"), el.get("y"), el.get("w"), el.get("h"),
               (el.get("text") or "")[:40])
        if key not in acc:
            acc[key] = el
    return acc


def scroll_all_containers(ws, capture_cb, settle=0.9, max_rounds=40):
    """滚动 window + 所有内层可滚容器到底，逐步回调 capture_cb() 抓元素。

    返回 (合并元素 dict, 滚动的容器数)。"""
    acc = {}
    # 1) window 滚动
    rounds = 0
    last_y = -1
    while rounds < max_rounds:
        capture_cb(acc)
        info = js_val(ws, "JSON.stringify({y:window.scrollY, sh:document.documentElement.scrollHeight, ih:window.innerHeight})")
        if not isinstance(info, dict):
            break
        if info["sh"] <= info["ih"] + 2:  # 无 window 滚动
            break
        if info["y"] >= info["sh"] - info["ih"] - 2:
            break
        cdp_eval(ws, f"window.scrollBy(0, {info['ih']})")
        time.sleep(settle)
        new_y = js_val(ws, "window.scrollY")
        if new_y == last_y:
            break
        last_y = new_y
        rounds += 1

    # 2) 内层可滚容器
    containers = js_val(ws, """
        JSON.stringify(Array.from(document.querySelectorAll('div,ul,section,main,article'))
            .filter(el => el.scrollHeight > el.clientHeight + 10)
            .map(el => ({sh: el.scrollHeight, ch: el.clientHeight}))
            .sort((a,b)=>b.sh-a.sh).slice(0,8))
    """)
    n_containers = 0
    if isinstance(containers, list):
        for ci in range(len(containers)):
            for _ in range(max_rounds):
                capture_cb(acc)
                done = js_val(ws, f"""
                    (() => {{
                        const el = Array.from(document.querySelectorAll('div,ul,section,main,article'))
                            .filter(e => e.scrollHeight > e.clientHeight + 10)
                            .sort((a,b)=>b.scrollHeight-a.scrollHeight)[{ci}];
                        if (!el) return true;
                        const before = el.scrollTop;
                        el.scrollTop = el.scrollHeight;
                        return el.scrollTop === before;  // 滚不动了 = true
                    }})()
                """)
                time.sleep(settle)
                if done:
                    break
            n_containers += 1
    return acc, n_containers


def expand_collapsibles(ws, acc, settle=0.7):
    """展开所有折叠面板（aria-expanded=false 或折叠头），展开后重扫。"""
    heads = js_val(ws, COLLAPSE_JS)
    if not isinstance(heads, list):
        return 0
    n = 0
    for h in heads:
        if h.get("expanded"):
            continue  # 已展开
        txt = h.get("text", "")
        if any(w in txt for w in EXPAND_UNSAFE):
            continue
        # 点击折叠头展开
        cdp_raw(ws, "Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": h["cx"], "y": h["cy"],
            "button": "left", "clickCount": 1}, timeout=3)
        cdp_raw(ws, "Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": h["cx"], "y": h["cy"],
            "button": "left", "clickCount": 1}, timeout=3)
        time.sleep(settle)
        merge_elements(acc, scan_dom(ws))
        n += 1
    return n


def get_nav_items(ws):
    v = js_val(ws, """
        JSON.stringify(Array.from(document.querySelectorAll('a[class*="navItem--"],a[class*="NavItem--"]'))
            .map((el,i)=>({i,text:(el.textContent||'').trim().substring(0,12)})))
    """)
    return v if isinstance(v, list) else []


def detect_sub_menu(ws):
    """返回子菜单项 [{text, cx, cy}]。复用旧脚本的检测器。"""
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


def save_record(record, fname):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_DIR / fname, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"done": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def deep_scan_page(ws, page_label):
    """对一个页面做深扫：滚动 + 折叠展开，返回元素列表 + 结构统计。"""
    acc = {}

    def cb(a):
        merge_elements(a, scan_dom(ws))

    cb(acc)
    _, n_cont = scroll_all_containers(ws, cb)
    n_expand = expand_collapsibles(ws, acc)
    struct = js_val(ws, STRUCT_JS) or {}
    elems = list(acc.values())
    print(f"    📄 {page_label}: {len(elems)} 元素 (滚动容器{n_cont} 折叠展开{n_expand} "
          f"tab={struct.get('tabs')} modal={struct.get('modal')} dropdown={struct.get('dropdown')})")
    return elems, struct


def main():
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    resume = "--resume" in sys.argv
    if "--list-state" in sys.argv:
        st = load_state()
        done = st.get("done", {})
        print("已完成页面:")
        for k in sorted(done):
            print(f"  {k} → {done[k]} 元素")
        return 0

    ws, page = connect()
    if not ws:
        print("❌ 没找到千牛页面（CDP 9222）")
        return 1
    print(f"已连接: {page.get('title', '?')[:50]}")
    state = load_state()
    done = state.get("done", {})

    # 首页也纳入
    for nav_id, nav_text in NAV_ITEMS:
        idx = int(nav_id.split(".")[1])
        if only and nav_id != only:
            continue
        if resume and f"{nav_id}" in done:
            print(f"⏭️ [{nav_id}] {nav_text} 已爬过，跳过")
            continue

        print(f"\n{'='*50}\n📄 [{nav_id}] {nav_text}")
        click_nav(ws, idx - 1)
        elems, struct = deep_scan_page(ws, f"{nav_id} {nav_text}")
        url = js_val(ws, "location.href")
        title = js_val(ws, "document.title")
        save_record({
            "scanned_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "app_slug": "qianniu", "crawl": "deep",
            "nav_id": nav_id, "nav_text": nav_text,
            "page_title": str(title), "page_url": str(url),
            "element_count": len(elems), "structure": struct, "elements": elems,
        }, f"page_{nav_id.replace('.', '_')}_deep.json")
        done[f"{nav_id}"] = len(elems)
        save_state(state)

        # 子菜单
        subs = detect_sub_menu(ws)
        if not subs:
            print(f"    → 无子菜单")
            continue
        print(f"    → {len(subs)} 个子菜单")
        for si, s in enumerate(subs):
            key = f"{nav_id}_{si}"
            if resume and key in done:
                print(f"      ⏭️ 子菜单[{si}] {s['text']} 已爬过，跳过")
                continue
            click_sub(ws, si)
            elems, struct = deep_scan_page(ws, f"{nav_id}_{si} {s['text']}")
            url = js_val(ws, "location.href")
            title = js_val(ws, "document.title")
            save_record({
                "scanned_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "app_slug": "qianniu", "crawl": "deep",
                "parent_nav_id": nav_id, "parent_nav_text": nav_text,
                "sub_index": si, "sub_text": s["text"],
                "page_title": str(title), "page_url": str(url),
                "element_count": len(elems), "structure": struct, "elements": elems,
            }, f"page_{nav_id.replace('.', '_')}_sub_{si:02d}_deep.json")
            done[key] = len(elems)
            save_state(state)

    ws.close()
    print(f"\n✅ 深爬完成。共 {len(done)} 个页面状态。汇总：")
    total = sum(done.values())
    print(f"   总元素: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
