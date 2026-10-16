#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断：点客服(dom_idx=6)前，dump 一级导航坐标；点后 dump 导航坐标 + URL。"""
import sys, os, time, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import crawl_qianniu_lv as C

def dump_navs(ws, label):
    navs = C.get_nav_items(ws)
    print(f"\n=== {label}: 一级导航 {len(navs)} 项 ===", flush=True)
    for n in navs:
        print(f"  [{n['i']}] {n['text']!r:14} cx={n['cx']:4} cy={n['cy']:4}", flush=True)

def main():
    page = C.find_qianniu_tab()
    if not page:
        print("❌ 没找到千牛页")
        return 1
    ws = C.connect(page)
    print(f"当前页: {page.get('title','')[:30]} | {page.get('url','')[:60]}", flush=True)

    dump_navs(ws, "点客服前")

    before = C.get_url(ws)
    C.click_nav(ws, 6)   # 客服 dom_idx=6
    after = C.get_url(ws)
    print(f"\n点客服后 URL: {after.split('/home.htm/')[-1][:50]}  (变化={'✅' if after!=before else '❌'})", flush=True)

    dump_navs(ws, "点客服后")

    # 客服导航项(dom_idx=6)的 DOM 信息
    info = C.js_val(ws, """
        (() => {
            const els = document.querySelectorAll('a[class*="navItem--"],a[class*="NavItem--"]');
            const el = els[6];
            if (!el) return JSON.stringify({err:'no el[6]'});
            const r = el.getBoundingClientRect();
            const cs = getComputedStyle(el);
            return JSON.stringify({
                text: el.textContent.trim(),
                cls: el.className,
                href: el.getAttribute('href'),
                rect: {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
                visibility: cs.visibility, display: cs.display,
                pointerEvents: cs.pointerEvents,
            });
        })()
    """)
    print(f"\n客服导航项 DOM: {info}", flush=True)

    ws.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
