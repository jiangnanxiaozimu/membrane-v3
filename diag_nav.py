#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断：连接千牛页，打印导航坐标，测试点首页(dom_idx=0)是否切换 URL。"""
import sys, os, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import crawl_qianniu_lv as C

def main():
    # 找到 myseller 千牛页（排除 edge:// 的 tab）
    page = C.find_qianniu_tab()
    if not page:
        print("❌ 没找到 myseller 千牛页")
        return 1
    ws = C.connect(page)
    print(f"当前页: {page.get('title','')[:30]} | {page.get('url','')[:70]}", flush=True)

    # 1. 导航项坐标
    navs = C.get_nav_items(ws)
    print(f"\n导航项 {len(navs)} 个:", flush=True)
    for n in navs:
        print(f"  [{n['i']}] {n['text']!r:16} cx={n['cx']:5} cy={n['cy']:5}", flush=True)

    # 2. viewport 尺寸
    vp = C.js_val(ws, "JSON.stringify({w:innerWidth,h:innerHeight,dpr:devicePixelRatio})")
    print(f"\nviewport: {vp}", flush=True)

    # 3. 点首页 dom_idx=0
    before = C.get_url(ws)
    print(f"\n点前 URL: {before.split('/home.htm/')[-1][:50]}", flush=True)
    C.click_nav(ws, 0)
    after = C.get_url(ws)
    print(f"点后 URL: {after.split('/home.htm/')[-1][:50]}", flush=True)
    print(f"切换: {'✅ 是' if after != before else '❌ 否'}", flush=True)

    # 4. 点后子菜单
    subs = C.detect_sub_menu(ws)
    print(f"\n点后 detect_sub_menu {len(subs)} 个: {[s['text'] for s in subs][:15]}", flush=True)

    ws.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
