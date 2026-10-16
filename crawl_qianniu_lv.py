#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
crawl_qianniu_lv.py — 千牛层级自动爬取器（直接更新 membrane.db 数据团）

按用户要求「先爬一级菜单，再爬二级菜单，一点一点往后更新」：

  用法：
    python crawl_qianniu_lv.py --level 1            # 只爬 13 个一级导航（url_key 1.1~1.13）
    python crawl_qianniu_lv.py --level 2            # 只爬二级子菜单（url_key 1.x_y）
    python crawl_qianniu_lv.py --level all          # 一级 + 二级
    python crawl_qianniu_lv.py --only 1.4           # 只爬指定模块（调试）
    python crawl_qianniu_lv.py --resume             # 断点续爬
    python crawl_qianniu_lv.py --dry-run            # 只扫不写库
    python crawl_qianniu_lv.py --list-state         # 打印进度

闭环（吸取「批量跑被广告弹窗连锁污染 6 个模块」的教训）：
  关残留弹窗 → 点导航(JS click，绕过遮罩 hit-test) → 关新弹窗 → URL 确认切换 → 深扫 → 写库。
  每一步都独立闭环，一个模块失败不影响下一个。

复用家底：
  - SCAN_JS / COLLAPSE_JS / STRUCT_JS / 滚动 / 折叠展开 —— 来自 scripts/crawl_qianniu_deep.py
  - normalize_element —— migrate_store_to_sqlite（DOM → 演员格式，与现有数据团一致）
  - store_db.StoreDB —— upsert_page + replace_elements 整页替换（幂等）

新增（老深爬没有的）：
  - 弹窗关闭 close_overlays（阿里 next-dialog 广告遮罩 + 引导按钮）
  - URL 切换确认（判断点击是否真的生效）
  - 生意参谋(数据 1.12)开新 tab 的处理（扫 sycm → 关 tab 回千牛）
  - Edge 原生权限弹窗「允许」兜底（UIA 扫 + executor 真点）
  - 直接写 SQLite（老深爬写 JSON，这里一步到位）
"""
import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import websocket
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "websocket-client"], check=True)
    import websocket

from store_db import StoreDB                              # noqa: E402
from migrate_store_to_sqlite import normalize_element     # noqa: E402

CDP_BASE = "http://127.0.0.1:9222"
STATE_FILE = BASE / "crawl_lv_state.json"
APP_SLUG = "qianniu"

# 左侧导航顺序（对应数据团 url_key 1.1~1.13）
NAV_ITEMS = [
    ("1.1", "首页"), ("1.2", "常用"), ("1.3", "交易"), ("1.4", "商品"),
    ("1.5", "营销"), ("1.6", "推广"), ("1.7", "客服"), ("1.8", "店铺"),
    ("1.9", "私域"), ("1.10", "财务"), ("1.11", "金融"), ("1.12", "数据"),
    ("1.13", "服务"),
]
# 数据(1.12) 是生意参谋，点导航会在新 tab 打开 sycm.taobao.com（不是本 tab 跳转）
SYCM_KEY = "1.12"

# ============================================================
# 元素捕获 / 折叠检测 / 结构统计 JS（复用 scripts/crawl_qianniu_deep.py）
# ============================================================
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
        if (r.top < 0 || r.left < 0 || r.top > window.innerHeight || r.left > window.innerWidth) return;
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const hasOnclick = el.hasAttribute && el.hasAttribute('onclick');
        const isRealInteractive = ['a','button','input','select','textarea'].includes(tag)
            || role || hasOnclick || el.hasAttribute('tabindex');
        let text = ownText(el);
        if (!text && isRealInteractive) {
            text = (el.innerText || el.textContent || '').replace(/\\s+/g,' ').trim().substring(0, 80);
        }
        if (!text && !isRealInteractive) return;
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

STRUCT_JS = """
(() => {
    return JSON.stringify({
        tabs: document.querySelectorAll('[role="tab"]').length,
        modal: document.querySelectorAll('[class*="modal"],[class*="Modal"],[class*="dialog"],[class*="Dialog"],[class*="drawer"],[class*="Drawer"],[role="dialog"]').length,
        dropdown: document.querySelectorAll('[class*="select"],[class*="Select"],[class*="picker"],[class*="Picker"],[class*="dropdown"],[class*="Dropdown"]').length,
    });
})()
"""

# 关阿里 next-dialog 弹窗（广告遮罩）+ 引导按钮（不碰「确定/取消」以免误提交）
CLOSE_OVERLAYS_JS = """
(function(){
    var closed = 0; var seen = new Set();
    function clickEl(c){ if (!c) return; if (seen.has(c)) return;
        if (c.getBoundingClientRect().width > 0) { seen.add(c); c.click(); closed++; } }
    document.querySelectorAll('.next-dialog-close-icon, .next-dialog-close, .next-icon-close, [class*=dialog-close]').forEach(clickEl);
    document.querySelectorAll('button, a').forEach(function(b){
        var t = (b.textContent || '').trim();
        if (/^(知道了|关闭|我知道了|稍后再说|跳过|不再提醒|下次再说)$/.test(t)) clickEl(b);
    });
    // 移除全屏遮罩（next-overlay-backdrop 盖住导航，物理点击会被挡）——广告弹窗残留的元凶
    document.querySelectorAll('.next-overlay-backdrop').forEach(function(o){
        if (o.parentElement) { o.parentElement.removeChild(o); closed++; }
    });
    // next-dialog 弹窗本体的关闭按钮（多套一层选择器兜底）
    document.querySelectorAll('.next-overlay-wrapper.opened .next-dialog [class*=close], .next-overlay-wrapper.opened .next-dialog [class*=Close]').forEach(clickEl);
    return closed;
})()
"""

# 折叠展开时不碰这些词（避免误点成动作）
EXPAND_UNSAFE = {"删除", "发货", "退款", "提交", "发布", "下架", "确认", "复制",
                 "打印", "导出", "撤回", "同意", "拒绝", "关闭", "停用", "支付",
                 "提现", "转账", "签约", "开通", "申请"}


# ============================================================
# CDP 基础
# ============================================================
def cdp_list_pages():
    try:
        return json.loads(urllib.request.urlopen(f"{CDP_BASE}/json", timeout=5).read().decode())
    except Exception:
        return []


def find_tab_by_url(substr):
    for p in cdp_list_pages():
        if p.get("type") == "page" and substr in p.get("url", ""):
            return p
    return None


def find_qianniu_tab():
    return find_tab_by_url("myseller.taobao.com") or find_tab_by_url("qn.taobao.com")


def connect(page):
    if not page:
        return None
    return websocket.create_connection(page["webSocketDebuggerUrl"], timeout=20)


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
    v = cdp_eval(ws, expr, timeout)
    if isinstance(v, dict) and "_error" in v:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def get_url(ws):
    return js_val(ws, "location.href") or ""


# ============================================================
# 弹窗关闭 / 导航 / 深扫
# ============================================================
def close_overlays(ws):
    v = js_val(ws, CLOSE_OVERLAYS_JS, timeout=8)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def get_nav_items(ws):
    v = js_val(ws, """
        JSON.stringify(Array.from(document.querySelectorAll('a[class*="navItem--"],a[class*="NavItem--"]'))
            .map((el,i)=>{
                const r = el.getBoundingClientRect();
                return {i, text:(el.textContent||'').trim().substring(0,12),
                        cx:Math.round(r.left+r.width/2), cy:Math.round(r.top+r.height/2)};
            }))
    """)
    return v if isinstance(v, list) else []


def click_at(ws, cx, cy):
    """物理派发点击（真实事件管线，能触发 window.open/target=_blank 新 tab）。"""
    cdp_raw(ws, "Input.dispatchMouseEvent",
            {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1}, timeout=3)
    cdp_raw(ws, "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1}, timeout=3)


def click_nav(ws, dom_idx):
    """点左侧导航：先滚入可视区→物理点击；坐标无效则 JS click 兜底。

    踩坑：二级菜单展开会把一级导航向上挤压，「首页」坐标 cy 变负(-6)，
    物理点击负坐标无效 → 页面没切换 → detect_sub_menu 读到旧页残留子菜单。
    """
    # 1. 把目标导航项滚进可视区，并把可滚的一级导航容器滚到顶
    cdp_eval(ws, f"""
        (() => {{
            const els = document.querySelectorAll('a[class*="navItem--"],a[class*="NavItem--"]');
            const el = els[{dom_idx}];
            if (!el) return;
            el.scrollIntoView({{block:'center', inline:'nearest'}});
            let p = el.parentElement;
            while (p) {{
                if (p.scrollHeight > p.clientHeight + 5) {{ p.scrollTop = 0; break; }}
                p = p.parentElement;
            }}
        }})()
    """, timeout=5)
    time.sleep(0.6)
    # 2. 重新取坐标，仅当坐标落在可视区才物理点击（真实点击语义，能触发新 tab）
    navs = get_nav_items(ws)
    if (dom_idx < len(navs) and navs[dom_idx].get("cx")
            and 0 <= navs[dom_idx].get("cy", -1) <= 2000):
        click_at(ws, navs[dom_idx]["cx"], navs[dom_idx]["cy"])
    else:
        # 3. 兜底 JS click（对普通页面切换有效，仅 window.open 类不生效）
        cdp_eval(ws, f"document.querySelectorAll('a[class*=\"navItem--\"],a[class*=\"NavItem--\"]')[{dom_idx}].click()", timeout=5)
    time.sleep(3.0)


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


def click_sub(ws, idx):
    """点子菜单：物理点击（真实事件），失败（URL 没变，可能被遮罩挡）则 JS click 兜底。"""
    before = get_url(ws)
    subs = detect_sub_menu(ws)
    if idx < len(subs) and subs[idx].get("cx"):
        click_at(ws, subs[idx]["cx"], subs[idx]["cy"])
    else:
        cdp_eval(ws, f"""
            (() => {{
                const items = document.querySelectorAll('.tbd-menu-item[class*="only-child"],[class*="secondMenuNavItem"],[class*="menuItem--"],[class*="MenuItem--"]');
                if ({idx} < items.length) items[{idx}].click();
            }})()
        """, timeout=5)
    time.sleep(2.5)
    if get_url(ws) != before:
        return
    # 物理点击没生效 → JS click 兜底
    cdp_eval(ws, f"""
        (() => {{
            const items = document.querySelectorAll('.tbd-menu-item[class*="only-child"],[class*="secondMenuNavItem"],[class*="menuItem--"],[class*="MenuItem--"]');
            if ({idx} < items.length) items[{idx}].click();
        }})()
    """, timeout=5)
    time.sleep(2.5)


def scan_dom(ws):
    v = js_val(ws, SCAN_JS, timeout=20)
    return v if isinstance(v, list) else []


def merge_elements(acc, new):
    for el in new:
        key = (el.get("tag"), el.get("x"), el.get("y"), el.get("w"), el.get("h"),
               (el.get("text") or "")[:40])
        if key not in acc:
            acc[key] = el
    return acc


def scroll_all_containers(ws, capture_cb, settle=0.9, max_rounds=40):
    """滚动 window + 内层可滚容器到底，逐步回调 capture_cb() 抓元素。"""
    acc = {}
    rounds = 0
    last_y = -1
    while rounds < max_rounds:
        capture_cb(acc)
        info = js_val(ws, "JSON.stringify({y:window.scrollY, sh:document.documentElement.scrollHeight, ih:window.innerHeight})")
        if not isinstance(info, dict):
            break
        if info["sh"] <= info["ih"] + 2:
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
                        return el.scrollTop === before;
                    }})()
                """)
                time.sleep(settle)
                if done:
                    break
            n_containers += 1
    return acc, n_containers


def expand_collapsibles(ws, acc, settle=0.7):
    heads = js_val(ws, COLLAPSE_JS)
    if not isinstance(heads, list):
        return 0
    n = 0
    for h in heads:
        if h.get("expanded"):
            continue
        txt = h.get("text", "")
        if any(w in txt for w in EXPAND_UNSAFE):
            continue
        cdp_raw(ws, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": h["cx"], "y": h["cy"], "button": "left", "clickCount": 1}, timeout=3)
        cdp_raw(ws, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": h["cx"], "y": h["cy"], "button": "left", "clickCount": 1}, timeout=3)
        time.sleep(settle)
        merge_elements(acc, scan_dom(ws))
        n += 1
    return n


def deep_scan_page(ws, label):
    """深扫一页：首屏 + 滚动 + 折叠展开，返回元素列表 + 结构统计。"""
    acc = {}

    def cb(a):
        merge_elements(a, scan_dom(ws))

    cb(acc)
    _, n_cont = scroll_all_containers(ws, cb)
    n_expand = expand_collapsibles(ws, acc)
    struct = js_val(ws, STRUCT_JS) or {}
    elems = list(acc.values())
    print(f"    📄 {label}: {len(elems)} 元素 (滚动容器{n_cont} 折叠展开{n_expand} "
          f"tab={struct.get('tabs')} modal={struct.get('modal')} dropdown={struct.get('dropdown')})", flush=True)
    return elems, struct


# ============================================================
# 权限弹窗兜底（Edge 原生，非 DOM，UIA 扫 + executor 真点「允许」）
# ============================================================
def allow_permission_dialog():
    """当前若有 Edge 原生权限弹窗（含「允许/阻止」），点「允许」。返回是否处理。"""
    try:
        import membrane_vision as mv
        res = mv.detect_ui_elements()
        elems = res.get("elements", []) if isinstance(res, dict) else []
    except Exception:
        return False
    allow = None
    for e in elems:
        if (e.get("name") or "").strip() == "允许":
            allow = e
            break
    if not allow:
        return False
    cx, cy = allow.get("cx"), allow.get("cy")
    if cx is None:
        cx = (allow.get("x1", 0) + allow.get("x2", 0)) // 2
        cy = (allow.get("y1", 0) + allow.get("y2", 0)) // 2
    from exec import executor as ex
    ex.click(cx, cy)
    print(f"    🔓 权限弹窗 → 点「允许」@({cx},{cy})", flush=True)
    time.sleep(2)
    return True


def handle_permission_if_any():
    """若有 edge:// 原生弹窗（权限/推广）挡点击，点「允许」，找不到则直接关 tab。"""
    try:
        pages = cdp_list_pages()
    except Exception:
        return False
    handled = False
    for p in pages:
        u = p.get("url", "") or ""
        if p.get("type") == "page" and u.startswith("edge://"):
            if allow_permission_dialog():
                handled = True
                continue
            try:
                ws = connect(p)
                cdp_raw(ws, "Page.close", timeout=5)
                ws.close()
                print(f"    🚪 关闭 edge:// 弹窗 tab: {u[:40]}", flush=True)
                handled = True
            except Exception:
                pass
    return handled


# ============================================================
# 写库
# ============================================================
def save_page(url_key, nav_id, nav_text, url, title, elems, struct, dry_run, sub_text=None, sub_index=None):
    """归一化元素 + 整页替换写入 SQLite。返回元素数。"""
    norms = [normalize_element(e) for e in elems]
    # 过滤：名字为空的元素不要（匹配靠名字）
    norms = [n for n in norms if (n.get("name") or "").strip()]
    if dry_run:
        print(f"    [dry-run] 不写库，{url_key} 拟写 {len(norms)} 元素", flush=True)
        return len(norms)

    db = StoreDB()
    skeleton = {
        "nav_id": nav_id, "nav_text": nav_text,
        "page_title": title, "page_url": url,
        "element_count": len(norms), "structure": struct,
        "crawl": "lv", "scanned_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }
    if sub_index is not None:
        skeleton.update({"parent_nav_id": nav_id, "sub_index": sub_index, "sub_text": sub_text})
    fingerprint = {"crawl": "lv", "raw_element_count": len(elems), "kept": len(norms)}
    pid = db.upsert_page(APP_SLUG, url_key, skeleton=skeleton, fingerprint=fingerprint, tenant_id=None)
    db.replace_elements(pid, norms)
    db.close()
    print(f"    💾 已写库 {url_key}: {len(norms)} 元素", flush=True)
    return len(norms)


# ============================================================
# 进度状态（断点续爬）
# ============================================================
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {"done": {}}
    return {"done": {}}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 主流程
# ============================================================
def crawl_first_level(ws, level, only, resume, dry_run):
    state = load_state()
    done = state.get("done", {})
    summary = []

    for nav_id, nav_text in NAV_ITEMS:
        if only and nav_id != only:
            continue
        key = nav_id
        if resume and key in done:
            print(f"⏭️ [{nav_id}] {nav_text} 已爬过，跳过", flush=True)
            continue

        print(f"\n{'='*52}\n📄 [{nav_id}] {nav_text}", flush=True)
        dom_idx = int(nav_id.split(".")[1]) - 1

        # 1. 关残留弹窗（广告遮罩会盖导航，先清）
        c0 = close_overlays(ws)
        if c0:
            print(f"  [闭环] 关残留弹窗 {c0} 个", flush=True)
            time.sleep(1)
        before_url = get_url(ws)

        # 2. 点导航（JS click，绕过遮罩 hit-test）
        click_nav(ws, dom_idx)
        time.sleep(2)

        # 3. 点后关新弹的广告
        c1 = close_overlays(ws)
        if c1:
            print(f"  [闭环] 点后关弹窗 {c1} 个", flush=True)
            time.sleep(1)

        # 4. 生意参谋(数据 1.12)特殊：新 tab 打开
        if nav_id == SYCM_KEY:
            ok, url, title, elems, struct = crawl_sycm_tab(ws, nav_id, nav_text, dry_run)
        else:
            after_url = get_url(ws)
            b_tail = before_url.split("/home.htm/")[-1][:30]
            a_tail = after_url.split("/home.htm/")[-1][:30]
            if after_url != before_url:
                print(f"  ✅ 已切换: {a_tail}", flush=True)
            else:
                print(f"  ⚠️ URL 未变 (before={b_tail} after={a_tail})，可能同页模块或点击未生效，仍记录实际状态", flush=True)
            elems, struct = deep_scan_page(ws, f"{nav_id} {nav_text}")
            url, title = after_url, js_val(ws, "document.title")
            ok = True

        if ok:
            n = save_page(nav_id, nav_id, nav_text, url, title, elems, struct, dry_run)
            done[key] = n
            save_state(state)
            summary.append((nav_id, nav_text, n))
        else:
            print(f"  ❌ {nav_id} 爬取失败，跳过（不影响后续模块）", flush=True)

    return summary


def crawl_sycm_tab(ws_main, nav_id, nav_text, dry_run):
    """数据(1.12)：点导航后新 tab 打开 sycm 生意参谋，扫它，再关 tab 回千牛。"""
    sycm = None
    for _ in range(4):  # 最多等 ~8s
        sycm = find_tab_by_url("sycm.taobao.com")
        if sycm:
            break
        time.sleep(2)
    if not sycm:
        print(f"  ❌ 没找到 sycm 新 tab（生意参谋未打开）", flush=True)
        return False, "", "", [], {}

    # 权限弹窗兜底（首次访问 sycm 可能弹 edge://permission-request-dialog）
    allow_permission_dialog()
    time.sleep(1)
    # 若 sycm tab 正停在权限弹窗页，允许后会自动前进到 sycm
    for _ in range(4):
        u = None
        try:
            ws_sycm = connect(sycm)
            u = get_url(ws_sycm)
            if u and "sycm.taobao.com" in u:
                break
            ws_sycm.close()
        except Exception:
            pass
        time.sleep(2)
    else:
        print(f"  ❌ sycm tab 未进入生意参谋页", flush=True)
        return False, "", "", [], {}

    print(f"  ✅ 生意参谋已打开: {u.split('?')[0]}", flush=True)
    elems, struct = deep_scan_page(ws_sycm, f"{nav_id} {nav_text}(sycm)")
    title = js_val(ws_sycm, "document.title")
    ws_sycm.close()

    # 关 sycm tab，回到千牛
    try:
        ws_close = connect(sycm)
        cdp_raw(ws_close, "Page.close", timeout=5)
        ws_close.close()
        print(f"  🚪 已关 sycm tab，回千牛", flush=True)
    except Exception:
        pass
    time.sleep(1.5)
    return True, u, title, elems, struct


def crawl_sub_level(ws, only, resume, dry_run):
    """二级菜单：先点一级导航，再逐个点子菜单，扫每个子状态。

    闭环：点一级导航后必须确认 URL 切换了才 detect_sub_menu，否则会读到
    上一页残留的二级菜单（已踩坑：点首页 cy 负坐标没切页，把交易的子菜单
    当成首页的子菜单写库）。
    """
    state = load_state()
    done = state.get("done", {})
    summary = []

    for nav_id, nav_text in NAV_ITEMS:
        if only and not nav_id.startswith(only):
            continue
        dom_idx = int(nav_id.split(".")[1]) - 1
        print(f"\n{'='*52}\n📄 [{nav_id}] {nav_text} 子菜单", flush=True)

        if nav_id == SYCM_KEY:
            print("  ⚠️ 数据(1.12)是新 tab，二级爬取暂跳过", flush=True)
            continue

        # 点一级导航，闭环验证 URL 切换（没切 = 点击没生效，读到的会是旧页子菜单）
        # 物理点击可能被 next-dialog 广告弹窗遮罩挡住，失败则 JS click 兜底（已验证 JS click 能切）
        before_url = get_url(ws)
        switched = False
        for attempt in range(3):
            close_overlays(ws)
            handle_permission_if_any()
            click_nav(ws, dom_idx)
            close_overlays(ws)
            handle_permission_if_any()
            after_url = get_url(ws)
            if after_url != before_url:
                a = after_url.split("/home.htm/")[-1][:40]
                print(f"  ✅ 已切换: {a}", flush=True)
                switched = True
                break
            # 物理点击没生效（很可能被遮罩挡）→ JS click 兜底
            cdp_eval(ws, f"document.querySelectorAll('a[class*=\"navItem--\"],a[class*=\"NavItem--\"]')[{dom_idx}].click()", timeout=5)
            time.sleep(2.5)
            close_overlays(ws)
            handle_permission_if_any()
            after_url = get_url(ws)
            if after_url != before_url:
                a = after_url.split("/home.htm/")[-1][:40]
                print(f"  ✅ 已切换(JS兜底): {a}", flush=True)
                switched = True
                break
            b = before_url.split("/home.htm/")[-1][:30]
            print(f"  [第{attempt+1}次] URL 未变 ({b})，重试", flush=True)
            before_url = after_url
            time.sleep(1)
        if not switched:
            print(f"  ⚠️ 3 次未切换，跳过 [{nav_id}] {nav_text}", flush=True)
            continue

        subs = detect_sub_menu(ws)
        if not subs:
            print("  → 无子菜单", flush=True)
            continue
        print(f"  → {len(subs)} 个子菜单", flush=True)
        for si, s in enumerate(subs):
            key = f"{nav_id}_{si}"
            if resume and key in done:
                print(f"    ⏭️ [{si}] {s['text']} 已爬过，跳过", flush=True)
                continue
            print(f"    ── [{si}] {s['text']}", flush=True)
            close_overlays(ws)
            handle_permission_if_any()
            click_sub(ws, si)
            close_overlays(ws)
            handle_permission_if_any()
            elems, struct = deep_scan_page(ws, f"{nav_id}_{si} {s['text']}")
            url = get_url(ws)
            title = js_val(ws, "document.title")
            n = save_page(key, nav_id, nav_text, url, title, elems, struct, dry_run,
                          sub_text=s["text"], sub_index=si)
            if not dry_run:
                done[key] = n
                save_state(state)
            summary.append((key, s["text"], n))
            # 点子菜单后要回到一级，才能点下一个子菜单
            close_overlays(ws)
            handle_permission_if_any()
            click_nav(ws, dom_idx)
            close_overlays(ws)
            handle_permission_if_any()
            time.sleep(1)
    return summary


def main():
    args = sys.argv[1:]
    level = "1"
    if "--level" in args:
        level = args[args.index("--level") + 1]
    only = None
    if "--only" in args:
        only = args[args.index("--only") + 1]
    resume = "--resume" in args
    dry_run = "--dry-run" in args
    if "--list-state" in args:
        st = load_state()
        done = st.get("done", {})
        print("已爬页面:")
        for k in sorted(done):
            print(f"  {k} → {done[k]} 元素")
        return 0

    # 定扫描边界（权限弹窗 UIA + executor 要用副屏真实位置）
    try:
        import run_workflow
        run_workflow.apply_display_bounds()
    except Exception as e:
        print(f"[warn] 定边界失败: {e}", flush=True)

    page = find_qianniu_tab()
    if not page:
        print("❌ 没找到千牛页面（CDP 9222），先跑 launch_qianniu.py", flush=True)
        return 1
    ws = connect(page)
    print(f"已连接: {page.get('title', '?')[:40]} | {page.get('url', '')[:60]}", flush=True)

    # 启动时校验导航检测器（先验证再下结论）
    navs = get_nav_items(ws)
    print(f"检测到 {len(navs)} 个导航项: {[n['text'] for n in navs]}", flush=True)
    if len(navs) < 13:
        print("⚠️ 导航项不足 13 个，检测器选择器可能失效，请先检查", flush=True)

    t0 = time.time()
    if level in ("1", "all"):
        summary = crawl_first_level(ws, level, only, resume, dry_run)
    elif level == "2":
        summary = crawl_sub_level(ws, only, resume, dry_run)
    else:
        print(f"❌ 未知 --level {level}（可选 1/2/all）", flush=True)
        ws.close()
        return 1

    print(f"\n\n===== 爬取汇总（{level}）=====", flush=True)
    total = 0
    for key, name, n in summary:
        print(f"  {key} {name}: {n} 元素", flush=True)
        total += n
    print(f"  共 {len(summary)} 页 / {total} 元素，耗时 {time.time()-t0:.0f}s", flush=True)

    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
