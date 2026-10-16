"""
coordinator.py — 膜缓存命中闭环（Coordinator 调度中心）v1 页面识别

实现用户定义的缓存命中机制（和 CPU 缓存一个道理）：
  扫当前界面 → 去数据团做「结构匹配」→ 命中 = 见过这个页面 → 复用库里的结构
  未命中 = 没见过 → 探索入库

v1 只实现「页面识别」这一环：
  - 扫描：CDP 网页可交互元素（a / button / input / select / textarea）
  - 匹配：结构指纹（type+name 集合）Jaccard 相似度，不看坐标
  - 命中判定：Jaccard ≥ 阈值（默认 0.5）

关键认知（v1 修正，来自实跑诊断）：
  1. 缓存命中判的是「结构」不是「坐标」——坐标是命中后读出来点击用的；
     动态页（商品列表/促销/时间戳）天天变，坐标不能用来判命中。
  2. 扫描口径必须对齐——数据团是全量 DOM（含 div/span），运行时只扫可交互标签；
     匹配时两边都过滤成「可交互标签」才公平。
  3. 数据团 url_key 是「论文式编号」（1.4_3）不是 URL，且 skeleton 没存 URL，
     所以页面识别只能靠结构指纹，兄弟页面区分度有限（后续要补 URL 元数据）。

用法：
  python coordinator.py               # 扫一次，报告识别出的页面 + 匹配度
  python coordinator.py --top 10     # 看前 10 名候选
  python coordinator.py --threshold 0.5
"""
import argparse
import json
import os

INTERACTIVE = {'a', 'button', 'input', 'select', 'textarea'}

# L1 顶部导航（千牛左侧导航栏一级项，所有模块共享，白名单稳定）
L1_NAV = {'首页', '常用', '交易', '商品', '营销', '推广', '客服',
          '店铺', '私域', '财务', '金融', '数据', '服务'}

# 通用站点「结构」元素类型（crawl_site.py 结构扫描产出，千牛 L1/L2 之外的第二条识别通道）
STRUCT_TYPES = {'tab', 'title', 'nav', 'sku'}


def apply_bounds():
    """扫描边界读 launch_state.json（副屏真实位置 -1920，不能靠模块默认常量）。"""
    import membrane_vision as mv
    sf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_state.json")
    if os.path.exists(sf):
        with open(sf, encoding='utf-8') as f:
            st = json.load(f)
        mv.set_display_bounds(int(st['left']), int(st['top']),
                              int(st['width']), int(st['height']))
        return st
    return None


def get_tab_ws_url(substr='myseller'):
    """按 URL 子串找 CDP page tab，返回 (webSocketDebuggerUrl, url)（只读，不操作）。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
        for t in data:
            if t.get('type') == 'page' and substr in (t.get('url') or ''):
                return t['webSocketDebuggerUrl'], t['url']
    except Exception:
        pass
    return None, None


def get_myseller_ws_url():
    """拿当前千牛 tab 的 CDP WebSocket URL（只读，不操作）。"""
    return get_tab_ws_url('myseller')


def focus_tab(substr='myseller'):
    """把焦点切回含 substr 的 tab（点了 target=_blank 外链后新 tab 抢焦点，需切回）。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
        for t in data:
            if t.get('type') == 'page' and substr in (t.get('url') or ''):
                req = urllib.request.Request(
                    f"http://127.0.0.1:9222/json/activate/{t['id']}", method='GET')
                with urllib.request.urlopen(req, timeout=5) as rr:
                    return rr.read().decode('utf-8')
    except Exception:
        pass
    return None


def focus_myseller():
    """把焦点切回 myseller tab（点了 target=_blank 外链后新 tab 抢焦点，需切回）。"""
    return focus_tab('myseller')


def list_page_tabs():
    """列出 CDP 所有 page 类型 tab 的 (url, title, targetId)。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
        return [(t.get('url'), t.get('title'), t.get('id')) for t in data if t.get('type') == 'page']
    except Exception:
        return []


def scan_interactive():
    """扫副屏当前网页的可交互元素（CDP 通道，口径和数据团对齐）。

    返回 (elements, url)。elements 里每个含 type/name/x1/y1/x2/y2，
    坐标是「副屏本地坐标」（已含视口偏移，和 exec.executor 一致）。
    """
    import membrane_vision as mv
    ws_url, url = get_myseller_ws_url()
    if not ws_url:
        return [], url
    # 视口真实屏幕位置（副屏左上角 + 边框/工具栏偏移），不能直接传 VIRTUAL_SCREEN_LEFT
    vp_x, vp_y = mv._get_viewport_screen_pos(ws_url)
    els = mv.scan_web_elements(ws_url, vp_x, vp_y)
    els = [e for e in els if e.get('type') in INTERACTIVE]
    return els, url


def _fingerprint(els):
    """结构指纹：{(type, name)} 集合。"""
    return {(e.get('type', ''), (e.get('name') or '').strip()) for e in els}


def structure_match(scan_fp, actor_els):
    """结构匹配：Jaccard 相似度（type+name 集合，不看坐标）。

    返回 (jaccard, inter, actor_interactive_count)。
    """
    actor_fp = _fingerprint(e for e in actor_els if e.get('type', '') in INTERACTIVE)
    if not scan_fp or not actor_fp:
        return 0.0, 0, len(actor_fp)
    inter = len(scan_fp & actor_fp)
    union = len(scan_fp | actor_fp)
    return (inter / union if union else 0.0), inter, len(actor_fp)


def identify(scan_fp):
    """全库结构匹配，返回按 Jaccard 降序的 (app_id, url_key, jaccard, inter, actor_n)。"""
    from store_db import StoreDB
    db = StoreDB()
    try:
        rows = []
        for page in db.list_pages(None):
            els = db.list_elements(page['id'])
            j, inter, an = structure_match(scan_fp, els)
            rows.append((page['app_id'], page['url_key'], j, inter, an))
    finally:
        db.close()
    rows.sort(key=lambda r: -r[2])
    return rows


def _el_x(e):
    """统一取元素 x 坐标（扫描用 x1，数据团用 x）。"""
    if e.get('x1') is not None:
        return e['x1']
    return e.get('x', 0) or 0


def skeleton_fingerprint(els):
    """骨架指纹：L1 导航（白名单）+ L2 左侧菜单（100≤x<250），剔除 L3 内容区。

    骨架 = 页面里不随数据变化的稳定结构，用它判「是不是同一个页面」，
    命中率能到 98%（全量结构匹配被动态内容稀释到 30~40%）。
    """
    fp = set()
    for e in els:
        if e.get('type', '') not in INTERACTIVE:
            continue
        name = (e.get('name') or '').strip()
        x = _el_x(e)
        if name in L1_NAV:
            fp.add(('a', name))
        elif 100 <= x < 250 and name:
            fp.add((e['type'], name))
    return fp


def identify_skeleton(scan_els):
    """骨架结构匹配，返回按 Jaccard 降序的 (app_id, url_key, jaccard, inter, actor_skel_n)。"""
    from store_db import StoreDB
    scan_fp = skeleton_fingerprint(scan_els)
    db = StoreDB()
    try:
        rows = []
        for page in db.list_pages(None):
            fp = skeleton_fingerprint(db.list_elements(page['id']))
            inter = len(scan_fp & fp)
            union = len(scan_fp | fp)
            j = inter / union if union else 0.0
            rows.append((page['app_id'], page['url_key'], j, inter, len(fp)))
    finally:
        db.close()
    rows.sort(key=lambda r: -r[2])
    return rows


def _find_in_scan(els, target_name, target_type=None):
    """在扫描元素里按 (type, name) 找目标，返回中心点坐标（副屏本地）。"""
    for e in els:
        if (e.get('name') or '').strip() == target_name:
            if target_type and e.get('type') != target_type:
                continue
            return ((e['x1'] + e['x2']) // 2, (e['y1'] + e['y2']) // 2, e)
    return None


def _read_from_lib(app_id, url_key, target_name, target_type=None):
    """命中页面的库里读目标坐标（视口坐标 → +视口偏移 转副屏本地）。

    数据团是「模块级聚合」，同 name 可能有多个副本，这里先返回第一个
    （多副本消歧 / 锚点纠偏是 v3 的事）。
    """
    import membrane_vision as mv
    from store_db import StoreDB
    db = StoreDB()
    try:
        page = None
        for p in db.list_pages(None):
            if p['app_id'] == app_id and p['url_key'] == url_key:
                page = p
                break
        if not page:
            return None
        bl, ct = mv.get_viewport_offset()
        for e in db.list_elements(page['id']):
            if (e.get('name') or '').strip() == target_name:
                if target_type and e.get('type') != target_type:
                    continue
                return (int(e.get('cx', e.get('x', 0)) + bl),
                        int(e.get('cy', e.get('y', 0)) + ct), e)
    finally:
        db.close()
    return None


def derive_url_key(url):
    """URL 派生数据团 url_key（千牛主页 /home.htm 前缀固定，去掉后取路径）。"""
    from urllib.parse import urlparse
    if not url:
        return 'unknown'
    p = urlparse(url).path.strip('/')
    if p.startswith('home.htm'):
        p = p[len('home.htm'):].strip('/')
    return p or 'home'


def explore_write_back(els, url, app_id='qianniu', url_key=None):
    """未命中 → 把当前扫描的页面 + 元素写回数据团（缓存长出这个页面）。

    返回 (page_id, url_key, n_elements)。元素坐标从扫描的 x1/y1/x2/y2
    转成数据团的 x/y/w/h（视口坐标，读出来点击时再加视口偏移）。
    """
    from store_db import StoreDB
    url_key = url_key or derive_url_key(url)
    elems = []
    for e in els:
        d = dict(e)
        d['x'] = e.get('x1', 0)
        d['y'] = e.get('y1', 0)
        d['w'] = e.get('x2', 0) - e.get('x1', 0)
        d['h'] = e.get('y2', 0) - e.get('y1', 0)
        elems.append(d)

    db = StoreDB()
    try:
        fp = sorted(_fingerprint(elems))
        pid = db.upsert_page(app_id, url_key, skeleton={'url': url},
                             fingerprint=fp)
        db.replace_elements(pid, elems)
        return pid, url_key, len(elems)
    finally:
        db.close()


def incremental_update(app_id, url_key, scan_els):
    """基本命中(90~98%)时，把扫描里新出现的骨架元素增量追加到命中页面。

    让缓存跟上页面迭代（如营销页新增「海外增长」菜单）。返回新增元素数。
    坐标：扫描是副屏本地(x1/y1)，数据团是视口坐标(x/y)，差视口偏移(border_left, chrome_top)。
    """
    import membrane_vision as mv
    from store_db import StoreDB
    db = StoreDB()
    try:
        page = db.get_page_by_url_key(app_id, url_key)
        if not page:
            return 0
        lib_fp = skeleton_fingerprint(db.list_elements(page['id']))
        scan_fp = skeleton_fingerprint(scan_els)
        new_fp = scan_fp - lib_fp
        if not new_fp:
            return 0
        bl, ct = mv.get_viewport_offset()
        added = 0
        for e in scan_els:
            if e.get('type', '') not in INTERACTIVE:
                continue
            name = (e.get('name') or '').strip()
            if (e['type'], name) not in new_fp or not name:
                continue
            d = dict(e)
            d['x'] = int(e.get('x1', 0)) - bl
            d['y'] = int(e.get('y1', 0)) - ct
            d['w'] = int(e.get('x2', 0)) - int(e.get('x1', 0))
            d['h'] = int(e.get('y2', 0)) - int(e.get('y1', 0))
            db.upsert_element(page['id'], d)
            added += 1
        return added
    finally:
        db.close()


def struct_fingerprint(els):
    """结构指纹：{(type, name)}，type ∈ {tab,title,nav,sku}。通用站点（淘宝/1688）识别用。"""
    return {(e.get('type', ''), (e.get('name') or '').strip())
            for e in els if e.get('type', '') in STRUCT_TYPES}


def scan_struct_current(tab_substr='myseller'):
    """运行时结构扫描：复用 crawl_site 逐屏滚动扫结构，转副屏本地坐标。

    tab_substr 指定目标 tab（URL 子串），默认千牛；淘宝/1688 传对应子串。
    返回 (elements, url)。elements 含 type(tab/title/nav/sku)/name/x1/y1/x2/y2。
    """
    import membrane_vision as mv
    import websocket
    ws_url, url = get_tab_ws_url(tab_substr)
    if not ws_url:
        return [], url
    bl, ct = mv.get_viewport_offset()
    ws = websocket.create_connection(ws_url, timeout=15)
    try:
        from crawl_site import scan_structure  # 逐屏滚动，返回 viewport 坐标
        raw = scan_structure(ws)
    finally:
        ws.close()
    els = []
    for e in raw:
        els.append({
            'type': e.get('tag', ''),        # tab/title/nav/sku
            'name': e.get('text', ''),
            'x1': e['x'] + bl, 'y1': e['y'] + ct,
            'x2': e['x'] + bl + e['w'], 'y2': e['y'] + ct + e['h'],
        })
    return els, url


def identify_struct(scan_els):
    """结构识别：全库结构指纹匹配，返回按 Jaccard 降序的 (app_id, url_key, jaccard, inter, 库结构n)。

    千牛页面元素 type 是 DOM tag(a/button/...)，不属 STRUCT_TYPES → 空指纹 → 0 分，
    天然只匹配淘宝/1688 这类有结构元素的页面。
    """
    from store_db import StoreDB
    scan_fp = struct_fingerprint(scan_els)
    db = StoreDB()
    try:
        rows = []
        for page in db.list_pages(None):
            fp = struct_fingerprint(db.list_elements(page['id']))
            inter = len(scan_fp & fp)
            union = len(scan_fp | fp)
            j = inter / union if union else 0.0
            rows.append((page['app_id'], page['url_key'], j, inter, len(fp)))
    finally:
        db.close()
    rows.sort(key=lambda r: -r[2])
    return rows


STRUCT_SELS_JS = """const sels = {
    tab: '[role=tab],[class*=tabs-tab],[class*=tab-item],[class*=tab-nav],[class*=Tab-item]',
    title: 'h1,h2,h3,h4,h5,[class*=common-title],[class*=block-header-title],[class*=navigation-bar-nav-title]',
    nav: 'nav a, header a',
    sku: '[class*=sku],[class*=Sku]',
  };
  const sel = sels[__TYPE__] || sels.tab;
  const nodes = document.querySelectorAll(sel);
  for (const el of nodes) {
    const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (t === __NAME__) {"""

STRUCT_SCROLL_JS = """(() => {
""" + STRUCT_SELS_JS + """
      el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
      return 'ok';
    }
  }
  return 'null';
})()"""

STRUCT_RECT_JS = """(() => {
""" + STRUCT_SELS_JS + """
      const r = el.getBoundingClientRect();
      return JSON.stringify({x: Math.round(r.left), y: Math.round(r.top),
          w: Math.round(r.width), h: Math.round(r.height), text: t, tag: el.tagName});
    }
  }
  return 'null';
})()"""


def locate_struct_element(tab_substr, target_name, target_type=None):
    """点击前实时定位：CDP 在目标 tab 里找结构元素 → scrollIntoView → 等滚动稳定 → 读实时 rect → 转副屏本地。

    数据团里的坐标是「扫描那一刻滚动位置」的快照，点击时页面已滚回顶部会错位，
    所以点击坐标必须实时读。分两阶段：先 scrollIntoView（可能触发平滑滚动），
    sleep 等滚动结束，再重新读 getBoundingClientRect，避免读到动画中间态坐标。
    返回 (cx, cy, el_dict)，找不到返回 None。
    """
    import time
    import membrane_vision as mv
    ws_url, _ = get_tab_ws_url(tab_substr)
    if not ws_url:
        return None
    from crawl_qianniu_lv import connect, js_val
    ws = connect({'webSocketDebuggerUrl': ws_url})
    if not ws:
        return None
    try:
        bl, ct = mv.get_viewport_offset(ws_url)
        t = json.dumps(target_type or 'tab')
        n = json.dumps(target_name)
        # 先滚回顶部并等稳定：识别扫描（scan_struct_current）逐屏滚动后可能残留平滑滚动
        # 或 affix 吸顶状态，若不归零，scrollIntoView 会从中间态开始、读到错位坐标。
        js_val(ws, "window.scrollTo({top:0, behavior:'instant'}); 'ok'")
        for _ in range(30):  # 轮询直到 scrollY 真正归零（affix 取消吸顶 + 布局稳定）
            if js_val(ws, "window.scrollY") == 0:
                break
            time.sleep(0.1)
        time.sleep(0.3)
        # 阶段1：scrollIntoView（behavior:instant 尽量即时，但页面 CSS 可能强制 smooth）
        scroll_expr = (STRUCT_SCROLL_JS.replace('__TYPE__', t).replace('__NAME__', n))
        js_val(ws, scroll_expr)
        time.sleep(0.6)  # 等滚动彻底稳定（smooth 动画 0.5s 左右）
        # 阶段2：重新读实时 rect（页面已稳定）
        rect_expr = STRUCT_RECT_JS.replace('__TYPE__', t).replace('__NAME__', n)
        v = js_val(ws, rect_expr)
        if not isinstance(v, dict):
            return None
        cx = v['x'] + bl + v['w'] // 2
        cy = v['y'] + ct + v['h'] // 2
        return cx, cy, v
    finally:
        ws.close()


def act_struct(target_name, target_type=None, dry_run=False, tab_substr='myseller'):
    """结构通道的缓存命中点击：扫描结构 → 识别 → 定位 → 点 → 验证。

    针对淘宝/1688 等非千牛站点（识别靠 tab/title/nav/sku 结构指纹）。
    tab_substr 指定目标 tab（URL 子串，如 'item.upload.taobao.com' / 'detail.1688.com'）。
    """
    apply_bounds()
    els, url = scan_struct_current(tab_substr)
    if not els:
        return {"ok": False, "error": "没扫到页面结构元素"}

    rows = identify_struct(els)
    best_app, best_key, best_j, _, _ = rows[0]
    hit = best_j >= 0.98

    found = locate_struct_element(tab_substr, target_name, target_type)
    if not found:
        return {"ok": False, "hit": hit,
                "page": f"{best_app}:{best_key} ({best_j:.0%})",
                "error": f"找不到目标「{target_name}」"}

    cx, cy, _el = found
    if dry_run:
        return {"ok": True, "dry_run": True, "hit": hit,
                "target": target_name, "x": cx, "y": cy,
                "page": f"{best_app}:{best_key} ({best_j:.0%})"}

    from exec import executor as ex
    import time
    focus_tab(tab_substr)
    time.sleep(0.5)
    tabs_before = {tid for _, _, tid in list_page_tabs()}
    ex.click(cx, cy)
    time.sleep(2)
    _, after = get_tab_ws_url(tab_substr)
    after_tabs = list_page_tabs()
    new_tab_urls = sorted(u for u, _, tid in after_tabs if tid not in tabs_before)
    if new_tab_urls:
        focus_tab(tab_substr)
    return {"ok": True, "hit": hit, "target": target_name,
            "page": f"{best_app}:{best_key} ({best_j:.0%})",
            "x": cx, "y": cy, "url_changed": after != url,
            "new_tab": bool(new_tab_urls), "new_tab_urls": new_tab_urls}


def act(target_name, target_type=None, dry_run=False):
    """缓存命中优先执行一次点击：扫描 → 识别 → 定位(实时优先/库兜底) → 点 → 验证。

    返回 dict，含 hit(是否命中页面)/url_changed(点击是否跳转)。
    """
    apply_bounds()
    els, url = scan_interactive()
    if not els:
        return {"ok": False, "error": "没扫到网页可交互元素"}

    # 1. 识别页面（骨架结构匹配，稳定骨架命中率到 98%）
    rows = identify_skeleton(els)
    best_app, best_key, best_j, _, _ = rows[0]
    hit = best_j >= 0.98

    # 1b. 未命中 → 写回，再识别应命中
    #     基本命中(90~98%) = 页面迭代（新增/删除菜单）→ 增量追加新骨架元素
    #     真未命中(<90%) = 全新页面 → 全量回写
    wrote_back = None
    if not hit and not dry_run:
        if best_j >= 0.90:
            n = incremental_update(best_app, best_key, els)
            wrote_back = ('incremental', best_key, n) if n else None
        else:
            wrote_back = explore_write_back(els, url)
        rows = identify_skeleton(els)
        best_app, best_key, best_j, _, _ = rows[0]
        hit = best_j >= 0.98

    # 2. 定位目标：实时扫描优先，库兜底
    found = _find_in_scan(els, target_name, target_type)
    source = 'scan'
    if not found and hit:
        found = _read_from_lib(best_app, best_key, target_name, target_type)
        source = 'lib'
    if not found:
        return {"ok": False, "hit": hit, "wrote_back": wrote_back,
                "error": f"找不到目标「{target_name}」"}

    cx, cy, _el = found
    if dry_run:
        return {"ok": True, "dry_run": True, "hit": hit, "source": source,
                "target": target_name, "x": cx, "y": cy, "page": f"{best_app}:{best_key}"}

    # 3. 点击（副屏本地坐标），先快照 tab 集合（用 targetId，不是 URL——外链 spm 参数每次都变）
    from exec import executor as ex
    tabs_before = {tid for _, _, tid in list_page_tabs()}
    ex.click(cx, cy)

    # 4. 验证：当前 tab URL 变化（内部跳转）或 新 tab 出现（target=_blank 外链/新窗）
    import time
    time.sleep(2)
    _, after = get_myseller_ws_url()
    after_tabs = list_page_tabs()
    new_tab_urls = sorted(u for u, _, tid in after_tabs if tid not in tabs_before)
    if new_tab_urls:
        focus_myseller()  # 切回 myseller，别让新 tab 抢走后续点击焦点
    return {"ok": True, "hit": hit, "source": source, "target": target_name,
            "page": f"{best_app}:{best_key} ({best_j:.0%})",
            "x": cx, "y": cy, "wrote_back": wrote_back,
            "url_changed": after != url, "new_tab": bool(new_tab_urls),
            "new_tab_urls": new_tab_urls}


def main():
    ap = argparse.ArgumentParser(description="膜缓存命中闭环 v1 页面识别")
    ap.add_argument('--top', type=int, default=8)
    ap.add_argument('--threshold', type=float, default=0.5)
    args = ap.parse_args()

    apply_bounds()
    els, url = scan_interactive()
    print(f"== 当前千牛 URL: {url} ==")
    if not els:
        print("!! 没扫到网页可交互元素 —— 副屏千牛可能没在前台")
        return

    scan_fp = _fingerprint(els)
    print(f"== 扫到 {len(els)} 个可交互元素，去重指纹 {len(scan_fp)} 个 ==")

    rows = identify(scan_fp)
    print(f"== 数据团 top{args.top} 结构匹配（Jaccard，不看坐标）==")
    for app, key, j, inter, an in rows[:args.top]:
        print(f"  {app}:{key:14s} J={j:6.2%}  交{inter:3d}  库可交互{an:4d}")

    best_app, best_key, best_j, _, _ = rows[0]
    if best_j >= args.threshold:
        print(f"=> [命中] 结构相似度 {best_j:.2%} ≥ {args.threshold:.0%}，"
              f"识别为「{best_app}:{best_key}」，可复用库里的结构")
    else:
        print(f"=> [未命中] 最高结构相似度 {best_j:.2%} < {args.threshold:.0%}，"
              f"当前页面不在数据团，需探索入库")

    # TODO(v2): 命中后「读坐标点击」——在命中页面里用 (type,name) 查目标元素的坐标，
    #          读到就点，读不到（元素是新的）就探索回写。


if __name__ == "__main__":
    main()
