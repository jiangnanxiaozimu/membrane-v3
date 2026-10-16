#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""crawl_site.py — 通用站点「结构」爬取器。

按用户要求：不逐个 SKU 深爬，只爬页面「结构栏目」（tab 栏 + 栏目标题 + 导航），
SKU 只取 1-2 个示例作代表。写库到指定 app。

用法：
  python crawl_site.py --app taobao --key item_publish --tab item.upload.taobao.com
  python crawl_site.py --app 1688   --key offer_detail  --tab detail.1688.com
  python crawl_site.py --dry-run    # 只扫不写库

结构元素 type 用语义标签：tab（标签页）/ title（栏目标题）/ nav（导航）/ sku（规格示例）。
这些是页面「骨架」，不随商品数据变化；SKU 列表、推荐商品等动态内容不爬。
"""
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from crawl_qianniu_lv import (  # noqa: E402
    close_overlays, connect, find_tab_by_url,
    cdp_eval, js_val, get_url, handle_permission_if_any,
)
from store_db import StoreDB                              # noqa: E402
from migrate_store_to_sqlite import normalize_element     # noqa: E402

APP_NAMES = {
    "taobao": "淘宝卖家后台",
    "1688": "1688采购",
}

# 结构扫描 JS：只提取 tab 栏 + 栏目标题 + 顶部导航 + 1-2 个 SKU 示例。
# 不深滚动、不抓商品卡片/推荐列表等动态内容。
# 选择器基于实测 DOM：淘宝 next-tabs-tab/block-header-title/navigation-bar-nav-title，
# 1688 tab-item/tab-nav/common-title。
STRUCT_SCAN_JS = """
(() => {
  const out = [];
  const seen = new Set();
  function rec(tag, text, r, extra) {
    text = (text || '').replace(/\\s+/g, ' ').trim();
    if (!text) return;
    if (r.width < 16 || r.height < 8) return;
    if (r.top < -5 || r.left < -5 || r.top > window.innerHeight + 5 || r.left > window.innerWidth + 5) return;
    const key = tag + '|' + text.substring(0, 30);
    if (seen.has(key)) return;
    seen.add(key);
    out.push(Object.assign({tag, text, x: Math.round(r.left), y: Math.round(r.top),
        w: Math.round(r.width), h: Math.round(r.height)}, extra || {}));
  }
  // 1. tab 结构（淘宝 next-tabs-tab / 1688 tab-item + tab-nav / role=tab）
  //    tab 名 2~12 字：过滤 tab 展开的错误内容（如「物流服务1提取方式必填项未填」）
  document.querySelectorAll('[role=tab],[class*=tabs-tab],[class*=tab-item],[class*=tab-nav],[class*=Tab-item]').forEach(el => {
    const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (t.length < 2 || t.length > 12) return;
    rec('tab', t, el.getBoundingClientRect(), {role: el.getAttribute('role') || ''});
  });
  // 2. 栏目标题（淘宝 block-header-title/navigation-bar-nav-title / 1688 common-title / h1-h5）
  document.querySelectorAll('h1,h2,h3,h4,h5,[class*=common-title],[class*=block-header-title],[class*=navigation-bar-nav-title]').forEach(el => {
    rec('title', el.textContent, el.getBoundingClientRect(), {tag2: el.tagName});
  });
  // 3. 顶部/主导航
  document.querySelectorAll('nav a, header a').forEach(el => {
    rec('nav', el.textContent, el.getBoundingClientRect(), {href: (el.getAttribute('href') || '').substring(0, 120)});
  });
  // 4. SKU/规格示例（只取 1-2 个，代表动态内容，不逐个爬）
  let n = 0;
  document.querySelectorAll('[class*=sku],[class*=Sku]').forEach(el => {
    if (n >= 2) return;
    const r = el.getBoundingClientRect();
    if (r.width < 10 || r.height < 10) return;
    const t = (el.textContent || '').replace(/\\s+/g, ' ').trim().substring(0, 24);
    if (t) { rec('sku', t, r); n++; }
  });
  return JSON.stringify(out);
})()
"""


def scan_structure_once(ws):
    v = js_val(ws, STRUCT_SCAN_JS, timeout=20)
    return v if isinstance(v, list) else []


def scan_structure(ws):
    """逐屏滚动采样：每滚一屏扫一次结构，合并去重（不漏中间位置的结构，如 1688 主 tab）。"""
    acc = {}
    cdp_eval(ws, "window.scrollTo(0, 0)")
    time.sleep(0.6)
    last_y = -1
    while True:
        for e in scan_structure_once(ws):
            key = (e.get('tag'), (e.get('text') or '')[:30])
            acc.setdefault(key, e)
        info = js_val(ws, "JSON.stringify({y:window.scrollY, sh:document.documentElement.scrollHeight, ih:window.innerHeight})")
        if not isinstance(info, dict):
            break
        if info["y"] >= info["sh"] - info["ih"] - 2:
            break
        cdp_eval(ws, f"window.scrollBy(0, {info['ih']})")
        time.sleep(0.5)
        new_y = js_val(ws, "window.scrollY")
        if new_y == last_y:
            break
        last_y = new_y
    cdp_eval(ws, "window.scrollTo(0, 0)")
    return list(acc.values())


def save_to_app(app_slug, url_key, url, title, elems, dry_run):
    norms = [normalize_element(e) for e in elems]
    norms = [n for n in norms if (n.get("name") or "").strip()]
    if dry_run:
        print(f"    [dry-run] {app_slug}:{url_key} 拟写 {len(norms)} 元素")
        return len(norms)

    db = StoreDB()
    db.upsert_app(app_slug, APP_NAMES.get(app_slug, app_slug))
    skeleton = {
        "page_title": title, "page_url": url,
        "element_count": len(norms), "crawl": "site-struct",
    }
    pid = db.upsert_page(app_slug, url_key, skeleton=skeleton, fingerprint=None)
    db.replace_elements(pid, norms)
    db.close()
    print(f"    💾 {app_slug}:{url_key} 已写库 {len(norms)} 元素")
    return len(norms)


def main():
    args = sys.argv[1:]

    def opt(name, default=None):
        if name in args:
            return args[args.index(name) + 1]
        return default

    app = opt("--app", "site")
    key = opt("--key")
    tab = opt("--tab")
    dry_run = "--dry-run" in args
    if not key or not tab:
        print("用法: crawl_site.py --app <slug> --key <url_key> --tab <url子串> [--dry-run]")
        return 1

    page = find_tab_by_url(tab)
    if not page:
        print(f"❌ 没找到含 {tab} 的 tab（CDP 9222），先打开对应页面")
        return 1
    ws = connect(page)
    print(f"已连接: {page.get('title', '?')[:40]} | {page.get('url', '')[:80]}")

    close_overlays(ws)
    handle_permission_if_any()
    url = get_url(ws)
    title = js_val(ws, "document.title")

    # 首屏扫结构（不滚动，避免抓 SKU 列表/推荐商品）
    elems = scan_structure(ws)
    print(f"    📐 结构扫描: {len(elems)} 元素 "
          f"(tab={sum(1 for e in elems if e.get('tag')=='tab')} "
          f"title={sum(1 for e in elems if e.get('tag')=='title')} "
          f"nav={sum(1 for e in elems if e.get('tag')=='nav')} "
          f"sku={sum(1 for e in elems if e.get('tag')=='sku')})")
    save_to_app(app, key, url, title, elems, dry_run)

    try:
        cdp_eval(ws, "window.scrollTo(0, 0)")
    except Exception:
        pass
    ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
