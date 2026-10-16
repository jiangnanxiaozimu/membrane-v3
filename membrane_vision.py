"""
膜 — 视觉识别引擎 v1.0
owllxc 自动化进程管理 第一版 Demo
全桌面 UI 标注引擎 — UIA 全树遍历 + CDP 网页 DOM，零 AI 依赖
UIA 坐标由 Windows 提供，网页坐标由浏览器 DOM 引擎提供
"""

import json
import os
import re
import uiautomation as auto

# ---- 虚拟屏配置（运行时动态更新） ----
VIRTUAL_SCREEN_LEFT = 1920
VIRTUAL_SCREEN_TOP = 0
VIRTUAL_SCREEN_WIDTH = 1920
VIRTUAL_SCREEN_HEIGHT = 1080

# Edge 视口偏移缓存（border_left, chrome_top）。见 get_viewport_offset()。
_viewport_offset_cache = None


def set_display_bounds(left, top, width, height):
    """由 membrane_app.pyw 调用来更新目标屏幕坐标"""
    global VIRTUAL_SCREEN_LEFT, VIRTUAL_SCREEN_TOP, VIRTUAL_SCREEN_WIDTH, VIRTUAL_SCREEN_HEIGHT
    VIRTUAL_SCREEN_LEFT = left
    VIRTUAL_SCREEN_TOP = top
    VIRTUAL_SCREEN_WIDTH = width
    VIRTUAL_SCREEN_HEIGHT = height


def _load_bounds_from_state():
    """从 launch_state.json 读目标屏边界作为默认值。

    独立进程直接 import membrane_vision 时没有 membrane_app.pyw 来调用
    set_display_bounds，会错用默认的右屏(1920)而漏掉副屏(-1920)。这里在模块
    加载时自动补读一次；membrane_app.pyw 后续显式调用 set_display_bounds 仍可覆盖。
    """
    try:
        sp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_state.json")
        if not os.path.exists(sp):
            return
        with open(sp, "r", encoding="utf-8") as f:
            d = json.load(f)
        left, top = d.get("left"), d.get("top")
        w, h = d.get("width"), d.get("height")
        if all(isinstance(v, (int, float)) for v in (left, top, w, h)) and w > 0 and h > 0:
            set_display_bounds(left, top, w, h)
    except Exception:
        pass


_load_bounds_from_state()


def get_display_bounds():
    """返回当前目标屏幕坐标"""
    return {
        'left': VIRTUAL_SCREEN_LEFT, 'top': VIRTUAL_SCREEN_TOP,
        'width': VIRTUAL_SCREEN_WIDTH, 'height': VIRTUAL_SCREEN_HEIGHT,
    }

# ---- 控件类型 → BGR 标注颜色 ----
TYPE_COLORS = {
    "ButtonControl":       (0, 200, 0),     # 绿 — 按钮
    "EditControl":         (0, 200, 200),   # 黄 — 输入框
    "TextControl":         (200, 80, 80),   # 蓝 — 文本标签
    "HyperlinkControl":    (200, 0, 200),   # 紫 — 超链接
    "ListItemControl":     (0, 140, 200),   # 橙 — 列表项
    "CheckBoxControl":     (200, 200, 0),   # 青 — 复选框
    "RadioButtonControl":  (140, 200, 0),   # 黄绿 — 单选框
    "ComboBoxControl":     (0, 100, 200),   # 深橙 — 下拉框
    "TabItemControl":      (200, 100, 0),   # 深蓝 — 标签页
    "MenuItemControl":     (100, 0, 200),   # 深紫 — 菜单项
    "TreeItemControl":     (0, 200, 100),   # 翠绿 — 树节点
    "SliderControl":       (200, 120, 0),   # 蓝绿 — 滑块
    "SplitButtonControl":  (0, 180, 180),   # 黄绿 — 拆分按钮
    "DataItemControl":     (180, 180, 0),   # 青黄 — 数据项
    "SpinnerControl":      (180, 0, 180),   # 品红 — 微调器
}

# 值得标注的可交互控件类型
INTERACTIVE_TYPES = set(TYPE_COLORS.keys())

# UIA 树遍历深度限制
MAX_DEPTH = 15
# 单次扫描最大元素数（防止某些应用 UIA 树爆炸）
MAX_ELEMENTS = 500


# ============================================================
# 全桌面 UI 扫描
# ============================================================

def _is_on_virtual_display(rect):
    """检查矩形是否在虚拟屏范围内"""
    if rect is None:
        return False
    return (
        rect.right > VIRTUAL_SCREEN_LEFT
        and rect.left < VIRTUAL_SCREEN_LEFT + VIRTUAL_SCREEN_WIDTH
        and rect.bottom > VIRTUAL_SCREEN_TOP
        and rect.top < VIRTUAL_SCREEN_TOP + VIRTUAL_SCREEN_HEIGHT
    )


def _to_local(rect):
    """屏幕坐标 → 虚拟屏本地坐标"""
    return (
        rect.left - VIRTUAL_SCREEN_LEFT,
        rect.top - VIRTUAL_SCREEN_TOP,
        rect.right - VIRTUAL_SCREEN_LEFT,
        rect.bottom - VIRTUAL_SCREEN_TOP,
    )


def scan_display_elements():
    """
    扫描虚拟屏上所有窗口的可交互 UI 元素。
    返回元素列表，坐标已转换为虚拟屏本地坐标。
    自动去重：相近坐标的重复元素只保留最深层级的。
    """
    elements = []
    seen_windows = 0

    desktop = auto.GetRootControl()

    try:
        top_windows = desktop.GetChildren()
    except Exception:
        top_windows = []

    for window in top_windows:
        if len(elements) >= MAX_ELEMENTS:
            break

        try:
            rect = window.BoundingRectangle
        except Exception:
            continue

        if not _is_on_virtual_display(rect):
            continue

        if rect.width() <= 0 or rect.height() <= 0:
            continue

        # 过滤：跳过任务栏自身（单独扫描）、隐藏/最小化窗口
        try:
            cls = window.ClassName or ""
        except Exception:
            cls = ""
        if "Shell" in cls and "Tray" in cls:
            continue

        try:
            is_offscreen = window.IsOffscreen
        except Exception:
            is_offscreen = False
        if is_offscreen:
            continue

        seen_windows += 1

        try:
            name = (window.Name or "").strip()
        except Exception:
            name = ""

        try:
            for control, depth in auto.WalkControl(window, maxDepth=MAX_DEPTH):
                if len(elements) >= MAX_ELEMENTS:
                    break

                try:
                    ctrl_type = control.ControlTypeName
                except Exception:
                    continue

                if ctrl_type not in INTERACTIVE_TYPES:
                    continue

                try:
                    ctrl_rect = control.BoundingRectangle
                except Exception:
                    continue

                if ctrl_rect.width() <= 0 or ctrl_rect.height() <= 0:
                    continue

                # 过滤不可见元素（折叠的书签栏等）
                try:
                    if control.IsOffscreen:
                        continue
                except Exception:
                    pass

                x1, y1, x2, y2 = _to_local(ctrl_rect)

                if x2 <= 0 or y2 <= 0 or x1 >= VIRTUAL_SCREEN_WIDTH or y2 >= VIRTUAL_SCREEN_HEIGHT:
                    continue

                try:
                    el_name = (control.Name or "").strip()
                except Exception:
                    el_name = ""

                try:
                    auto_id = control.AutomationId or ""
                except Exception:
                    auto_id = ""

                elements.append({
                    "type": ctrl_type.replace("Control", ""),
                    "name": el_name,
                    "auto_id": auto_id,
                    "window": name,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "depth": depth,
                })

        except Exception:
            continue

    # 去重：坐标高度重叠的元素只保留最深（最具体）的那个
    elements = _dedup_elements(elements)
    return elements


def _dedup_elements(elements):
    """去重：如果两个元素坐标几乎完全相同，保留 depth 更深 + 有名字的"""
    if len(elements) <= 1:
        return elements

    kept = []
    # 按坐标分组
    for el in elements:
        x1, y1, x2, y2 = el["x1"], el["y1"], el["x2"], el["y2"]
        merged = False
        for existing in kept:
            ex1, ey1, ex2, ey2 = existing["x1"], existing["y1"], existing["x2"], existing["y2"]
            # 两个矩形 IoU > 0.8 视为重复
            overlap_x1 = max(x1, ex1)
            overlap_y1 = max(y1, ey1)
            overlap_x2 = min(x2, ex2)
            overlap_y2 = min(y2, ey2)
            if overlap_x1 < overlap_x2 and overlap_y1 < overlap_y2:
                overlap_area = (overlap_x2 - overlap_x1) * (overlap_y2 - overlap_y1)
                area1 = (x2 - x1) * (y2 - y1)
                area2 = (ex2 - ex1) * (ey2 - ey1)
                min_area = min(area1, area2)
                if min_area > 0 and overlap_area / min_area > 0.8:
                    # 保留更深层 + 有名字的
                    score_el = (el.get("depth", 0), 1 if el.get("name") else 0)
                    score_ex = (existing.get("depth", 0), 1 if existing.get("name") else 0)
                    if score_el > score_ex:
                        kept.remove(existing)
                        kept.append(el)
                    merged = True
                    break
        if not merged:
            kept.append(el)

    return kept


def element_type_summary(elements):
    """按类型分组统计"""
    counts = {}
    for el in elements:
        t = el["type"]
        counts[t] = counts.get(t, 0) + 1
    # 按数量降序
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def detect_ui_elements(frame=None):
    """
    全屏 UI 扫描 — UIA + CDP 双通道合并。
    frame 参数保留以兼容旧接口，实际不使用。
    """
    all_elements = []
    windows = []

    # ---- 通道 1: UIA 桌面控件 ----
    try:
        uia_elements = scan_display_elements()
    except Exception as e:
        return {"error": f"UIA扫描失败: {str(e)[:120]}"}

    all_elements.extend(uia_elements)
    windows = list(set(e["window"] for e in uia_elements if e.get("window")))

    # ---- 通道 2: CDP 网页内容 ----
    web_elements = []
    try:
        cdp_targets = _get_cdp_targets()
    except Exception:
        cdp_targets = []

    for target in cdp_targets:
        try:
            ws_url = target.get("webSocketDebuggerUrl", "")
            if not ws_url:
                continue
            # 获取 viewport 在屏幕上的位置
            vp_x, vp_y = _get_viewport_screen_pos(ws_url)
            # 扫描主页面元素
            web_els = scan_web_elements(ws_url, vp_x, vp_y)
            if web_els:
                web_elements.extend(web_els)
                page_url = target.get("url", "")[:60]
                if page_url and page_url not in windows:
                    windows.append("[WEB] " + page_url)

            # 扫描 iframe 子帧（跨域也能穿透）
            iframe_els = _scan_iframes(ws_url, vp_x, vp_y)
            if iframe_els:
                web_elements.extend(iframe_els)
        except Exception:
            continue

    # ---- 始终过滤 UIA 垃圾（不依赖 CDP 是否成功）----
    all_elements = _dedup_elements(all_elements)
    all_elements = _filter_tiny(all_elements)
    all_elements = _filter_containers(all_elements)
    all_elements = _filter_background_windows(all_elements)
    all_elements = _filter_bookmarks_bar(all_elements)

    # ---- CDP 网页元素合并 ----
    if web_elements:
        all_elements.extend(web_elements)
        all_elements = _dedup_uia_cdp(all_elements)

    if not all_elements:
        return {"error": "虚拟屏上未找到任何可交互 UI 元素 — 请先打开应用窗口"}

    buttons = [e for e in all_elements if e["type"] in ("Button", "button")]
    summary = element_type_summary(all_elements)

    import time
    return {
        "elements": all_elements,
        "buttons": buttons,
        "count": len(all_elements),
        "button_count": len(buttons),
        "window_count": len(windows),
        "windows": windows,
        "type_summary": summary,
        "web_count": len(web_elements),
        "scan_time": time.strftime("%H:%M:%S"),
    }


def _get_cdp_targets():
    """获取 Edge CDP 调试页面列表"""
    import urllib.request
    try:
        resp = urllib.request.urlopen("http://localhost:9222/json", timeout=8)
        data = json.loads(resp.read().decode())
    except Exception:
        return []
    # 过滤出业务网页标签页（排除扩展页 / devtools / Edge 内部页）
    pages = [t for t in data
             if t.get("type") == "page"
             and "chrome-extension://" not in t.get("url", "")
             and "devtools://" not in t.get("url", "")
             and not (t.get("url", "") or "").startswith("edge://")]
    if not pages:
        return []
    # 优先千牛(myseller)主战场页面——否则第一个 page 可能是 edge 推广弹窗
    for p in pages:
        if "myseller.taobao.com" in (p.get("url", "") or ""):
            return [p]
    # 回退：第一个 http/https 业务页面
    for p in pages:
        if (p.get("url", "") or "").startswith("http"):
            return [p]
    return pages[:1]


def _find_page_hwnd(ws_url=None):
    """按页面标题定位 Edge 窗口 hwnd（通用：任意 CDP 页面，不再写死千牛）。

    Edge 窗口标题形如「<页面title> - 个人 - Microsoft Edge」。从 CDP /json 读 ws_url
    对应 page 的真实 title，枚举可见窗口找「标题含该 title 且是 Edge 窗口」的（排除
    VS Code 等同样含该标题的非浏览器窗口）；兜底：落在副屏范围内的 Edge 窗口；再兜底
    千牛旧关键词（兼容历史）。找不到返回 None。
    """
    import ctypes
    import ctypes.wintypes as wt
    u = ctypes.windll.user32

    titles = []
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
        for t in data:
            if t.get('type') != 'page' or not t.get('title'):
                continue
            if ws_url and t.get('webSocketDebuggerUrl') == ws_url:
                titles.insert(0, t['title'])      # 目标页面 title 优先
            elif 'myseller' in (t.get('url') or ''):
                titles.append(t['title'])         # 千牛作为候选之一（不再是唯一）
    except Exception:
        pass

    _WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    by_title = []
    edge_on_virtual = []

    @_WNDENUMPROC
    def _cb(hwnd, lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_string_buffer(256)
        u.GetWindowTextA(hwnd, buf, 255)
        title = buf.value.decode('gbk', errors='ignore')
        if not title:
            return True
        is_edge = 'Edge' in title or 'edge' in title
        # 1) 标题含目标页面 title 且是 Edge 窗口（避免 VS Code 等同名非浏览器窗口）
        if is_edge and any(t and t in title for t in titles):
            by_title.append(hwnd)
        # 2) 兼容：千牛旧关键词
        elif '商家工作台' in title or '千牛商家' in title:
            by_title.append(hwnd)
        # 3) 兜底：落在副屏范围内的 Edge 窗口（title 匹配失败时的保底）
        if is_edge:
            try:
                r = wt.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                if VIRTUAL_SCREEN_LEFT - 8 <= r.left < VIRTUAL_SCREEN_LEFT + VIRTUAL_SCREEN_WIDTH:
                    edge_on_virtual.append(hwnd)
            except Exception:
                pass
        return True
    u.EnumWindows(_cb, 0)
    if by_title:
        return by_title[0]
    if edge_on_virtual:
        return edge_on_virtual[0]
    return None


def get_viewport_offset(ws_url=None):
    """读 Edge 视口偏移 (border_left, chrome_top) = 视口左上角相对副屏原点的偏移。

    旧算法用 inner/outer 差值猜（(outer-inner)/2, outer-inner），在非最大化窗口下错：
    实测 (12, 92) vs 实际 (8, 84)，点小 tab 会偏到相邻元素。

    正确算法（Win32 读前台窗口客户区）：
      视口左上屏幕 = 客户区左上屏幕 + (0, 客户区高 - innerHeight)   # 滚动条在右边，顶部 chrome 在上方
      bl = 视口左上x - 副屏left；ct = 视口左上y - 副屏top
    前提：目标 tab 所在窗口是前台窗口（点击前 focus 保证）。
    """
    # 读 innerWidth/innerHeight（CDP，从目标 tab）
    if not ws_url:
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
                data = json.loads(r.read().decode('utf-8'))
            for t in data:
                if t.get('type') == 'page' and t.get('webSocketDebuggerUrl'):
                    ws_url = t['webSocketDebuggerUrl']
                    break
        except Exception:
            return (0, 0)
        if not ws_url:
            return (0, 0)
    try:
        import websocket
        ws = websocket.create_connection(ws_url, timeout=10)
        _cdp_send(ws, {"id": 1, "method": "Runtime.enable"})
        r = _cdp_send(ws, {"id": 2, "method": "Runtime.evaluate",
                           "params": {"expression": "JSON.stringify({iw:innerWidth,ih:innerHeight})",
                                      "returnByValue": True}})
        v = json.loads(r["result"]["result"]["value"])
        ih = v["ih"]
        ws.close()
    except Exception:
        return (0, 0)
    # 读前台窗口客户区（Win32）
    import ctypes
    import ctypes.wintypes as wt
    try:
        u = ctypes.windll.user32
        hwnd = _find_page_hwnd(ws_url) or u.GetForegroundWindow()
        class RECT(ctypes.Structure):
            _fields_ = [('l', ctypes.c_long), ('t', ctypes.c_long),
                        ('r', ctypes.c_long), ('b', ctypes.c_long)]
        cr = RECT()
        u.GetClientRect(hwnd, ctypes.byref(cr))
        pt = wt.POINT(0, 0)
        u.ClientToScreen(hwnd, ctypes.byref(pt))
        b = get_display_bounds()
        bl = pt.x - b['left']                    # 视口左 = 客户区左（滚动条在右）
        ct = (pt.y + cr.b - ih) - b['top']       # 视口顶 = 客户区顶 + 顶部 chrome
        return bl, ct
    except Exception:
        return (0, 0)


def _get_viewport_screen_pos(ws_url):
    """视口在屏幕上的真实位置 = 副屏左上角 + 视口偏移（边框 + 工具栏）。

    这样 scan_web_elements 的 local_x = screen_x - VIRTUAL_SCREEN_LEFT
                            = (副屏left + bl + el.x) - 副屏left = bl + el.x
    得到的是「副屏本地坐标」（和 UIA 扫描、exec.executor 一致）。
    """
    bl, ct = get_viewport_offset(ws_url)
    return VIRTUAL_SCREEN_LEFT + bl, VIRTUAL_SCREEN_TOP + ct


def _filter_bookmarks_bar(elements):
    """过滤 Edge 书签栏区域（y=60-100），无论可见与否都跳过"""
    return [e for e in elements
            if not (e.get("depth", 0) < 99  # 只过滤 UIA，不动 CDP 网页元素
                    and 60 <= e.get("y1", 0) <= 100
                    and e.get("type", "") in ("ListItem", "Button"))]


def _filter_background_windows(elements):
    """
    多窗口去重：只保留面积最大的主窗口 + 所有 CDP 网页元素。
    CDP 元素(depth=99)不受窗口过滤影响。
    """
    if len(elements) <= 3:
        return elements

    uia_els = [e for e in elements if e.get("depth", 0) < 99]
    web_els = [e for e in elements if e.get("depth", 0) == 99]

    if len(uia_els) <= 3:
        return elements

    windows = {}
    for el in uia_els:
        win = el.get("window", "")
        if win not in windows:
            windows[win] = []
        windows[win].append(el)

    if len(windows) <= 1:
        return elements

    def win_area(els):
        if not els: return 0
        xs = [e["x1"] for e in els] + [e["x2"] for e in els]
        ys = [e["y1"] for e in els] + [e["y2"] for e in els]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))

    sorted_wins = sorted(windows.items(), key=lambda kv: win_area(kv[1]), reverse=True)
    main_win = sorted_wins[0][0]
    uia_kept = [e for e in uia_els if e.get("window", "") == main_win]
    return uia_kept + web_els


def _dedup_uia_cdp(elements):
    """CDP 网页元素优先，过滤容器和无意义的碎片元素"""
    uia_elements = [e for e in elements if e.get("depth", 0) < 99]
    web_elements = [e for e in elements if e.get("depth", 0) == 99]

    # UIA 去重 → 过滤碎片 → 过滤容器
    uia_kept = _dedup_elements(uia_elements)
    uia_kept = _filter_tiny(uia_kept)
    uia_kept = _filter_containers(uia_kept)

    # CDP 元素：去重 + 过滤
    web_filtered = _filter_tiny(web_elements)
    web_filtered = _filter_containers(web_filtered)
    web_filtered = _dedup_web(web_filtered)

    return uia_kept + web_filtered


def _dedup_web(elements):
    """CDP 网页元素去重：同坐标+同标签去重（主页面和 iframe 可能扫到同一个元素）"""
    seen = {}
    kept = []
    for el in elements:
        key = f"{el['type']}|{el['x1']},{el['y1']}"
        if key in seen:
            # 保留有名字的
            if el.get("name") and not seen[key].get("name"):
                kept.remove(seen[key])
                kept.append(el)
                seen[key] = el
        else:
            seen[key] = el
            kept.append(el)
    return kept


def _filter_tiny(elements):
    """过滤尺寸过小的碎片元素（4px 文本节点等）"""
    return [e for e in elements
            if (e["x2"] - e["x1"]) >= 8 and (e["y2"] - e["y1"]) >= 8]


def _filter_containers(elements):
    """
    过滤大容器元素：
    1. 面积 > 200000 且无名字 → 肯定是背景容器
    2. 面积 > 50000 且包含 3+ 个小元素 → 父容器
    """
    if len(elements) <= 3:
        return elements

    kept = []
    for i, el in enumerate(elements):
        area = (el["x2"] - el["x1"]) * (el["y2"] - el["y1"])

        # 巨型无名字元素 = 背景容器
        if area > 200000 and not el.get("name"):
            continue

        if area > 50000:
            contained = 0
            for j, other in enumerate(elements):
                if i == j:
                    continue
                if (other["x1"] >= el["x1"] and other["y1"] >= el["y1"]
                        and other["x2"] <= el["x2"] and other["y2"] <= el["y2"]):
                    contained += 1
            if contained >= 3:
                continue
        kept.append(el)
    return kept


# ============================================================
# 任务栏按钮扫描（向后兼容 v6）
# ============================================================

def _find_secondary_taskbar():
    """在 UIA 树中找到虚拟屏上的 Shell_SecondaryTrayWnd"""
    desktop = auto.GetRootControl()
    for w in desktop.GetChildren():
        if w.ClassName == "Shell_SecondaryTrayWnd":
            return w
    return None


def _clean_name(name: str) -> str:
    """清理 UIA 按钮名称"""
    name = name.split("\n")[0]
    name = name.replace(" 已固定", "")
    name = re.sub(r" - \d+ 个运行窗口", "", name)
    name = re.sub(r" \d{1,2}:\d{2}$", "", name)
    return name


def _get_taskbar_buttons():
    """从 UIA 读取虚拟屏任务栏按钮"""
    tray = _find_secondary_taskbar()
    if tray is None:
        return None, "找不到虚拟屏任务栏 Shell_SecondaryTrayWnd"

    tray_rect = tray.BoundingRectangle
    tray_screen_left = tray_rect.left
    tray_screen_top = tray_rect.top
    tray_h = tray_rect.bottom - tray_screen_top
    virtual_h_estimate = tray_screen_top + tray_h
    crop_top = virtual_h_estimate - 50

    buttons = []

    def find_buttons(ctrl, depth=0):
        if depth > 12:
            return
        try:
            children = ctrl.GetChildren()
        except Exception:
            return
        for child in children:
            try:
                ctrl_type = child.ControlTypeName
            except Exception:
                ctrl_type = ""
            try:
                name = child.Name or ""
            except Exception:
                name = ""
            try:
                cls = child.ClassName or ""
            except Exception:
                cls = ""
            if ctrl_type == "ButtonControl" and name:
                try:
                    rect = child.BoundingRectangle
                except Exception:
                    find_buttons(child, depth + 1)
                    continue
                if rect.bottom <= tray_screen_top or rect.top >= tray_rect.bottom:
                    find_buttons(child, depth + 1)
                    continue
                local_x1 = rect.left - tray_screen_left
                local_y1 = rect.top - crop_top
                local_x2 = rect.right - tray_screen_left
                local_y2 = rect.bottom - crop_top
                buttons.append({
                    "name": _clean_name(name),
                    "x1": local_x1, "y1": local_y1,
                    "x2": local_x2, "y2": local_y2,
                    "class": cls,
                })
                continue
            find_buttons(child, depth + 1)

    find_buttons(tray)
    if not buttons:
        return None, "UIA 未在虚拟屏任务栏中找到任何按钮"
    return buttons, None


def _scan_iframes(ws_url, vp_base_x, vp_base_y):
    """
    扫描页面内所有 iframe 的子文档元素。
    从父页面测量 iframe 位置（绕过跨域 frameElement=null），
    然后用 createIsolatedWorld 穿透 iframe 扫描。
    """
    import websocket
    try:
        ws = websocket.create_connection(ws_url, timeout=15)
    except Exception:
        return []

    try:
        _cdp_send(ws, {"id": 1, "method": "Page.enable"})
        _cdp_send(ws, {"id": 2, "method": "Runtime.enable"})
        r = _cdp_send(ws, {"id": 3, "method": "Page.getFrameTree"})

        # 第一步：在父页面上下文获取所有 iframe 的 src → 位置映射
        pos_js = """(function(){
            var map = {};
            document.querySelectorAll('iframe').forEach(function(iframe){
                var src = (iframe.src || '').split('?')[0];  // 忽略查询参数
                var rect = iframe.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    map[src] = {x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height)};
                }
            });
            return JSON.stringify(map);
        })()"""
        pr = _cdp_send(ws, {"id": 4, "method": "Runtime.evaluate",
                            "params": {"expression": pos_js, "returnByValue": True}})
        iframe_positions = json.loads(pr["result"]["result"]["value"])

        all_els = []

        def scan_frame(frame, offset_x, offset_y):
            frame_url = frame["frame"].get("url", "")
            frame_id = frame["frame"]["id"]
            main_frame = r["result"]["frameTree"]["frame"]

            # 主帧跳过（已由 scan_web_elements 扫描）
            if frame == main_frame:
                pass
            elif frame_url and "chrome-extension://" not in frame_url:
                # 从父页面获取的 iframe 位置表中匹配
                fx, fy = offset_x, offset_y
                frame_base = frame_url.split("?")[0]
                for src, pos in iframe_positions.items():
                    if src and src in frame_base:
                        fx = offset_x + pos["x"]
                        fy = offset_y + pos["y"]
                        break

                try:
                    # 穿透跨域
                    wr = _cdp_send(ws, {
                        "id": 10, "method": "Page.createIsolatedWorld",
                        "params": {"frameId": frame_id, "worldName": "membrane_scan"}
                    })
                    ctx_id = wr["result"]["executionContextId"]

                    scan_js = """(function(){
                        var results = []; var seen = {};
                        var tags = ['button','a','input','select','textarea','[class*=qrcode]','[class*=qr-code]','[class*=scan-code]'];
                        document.querySelectorAll(tags.join(',')).forEach(function(el){
                            var rect = el.getBoundingClientRect();
                            if (rect.width < 2 || rect.height < 2) return;
                            if (rect.top < 0 || rect.left < 0) return;
                            if (rect.top > window.innerHeight) return;
                            var key = el.tagName + '|' + Math.round(rect.left) + ',' + Math.round(rect.top);
                            if (seen[key]) return; seen[key] = true;
                            var text = (el.textContent || el.value || el.placeholder || el.title || '').trim().substring(0,40);
                            results.push({tag: el.tagName.toLowerCase(), text: text, id: el.id||'', name: el.name||'', type: el.type||'',
                                x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height)});
                        });
                        return JSON.stringify(results);
                    })()"""
                    sr = _cdp_send(ws, {
                        "id": 12, "method": "Runtime.evaluate",
                        "params": {"expression": scan_js, "returnByValue": True,
                                   "contextId": ctx_id}
                    })
                    raw = sr["result"]["result"]["value"]
                    iframe_els = json.loads(raw)

                    for el in iframe_els:
                        screen_x = vp_base_x + fx + el["x"]
                        screen_y = vp_base_y + fy + el["y"]
                        local_x = screen_x - VIRTUAL_SCREEN_LEFT
                        local_y = screen_y - VIRTUAL_SCREEN_TOP
                        if local_x < -50 or local_y < -50 or local_x > VIRTUAL_SCREEN_WIDTH + 50:
                            continue
                        all_els.append({
                            "type": el["tag"],
                            "name": el["text"],
                            "web_id": el["id"],
                            "web_name": el["name"],
                            "x1": local_x, "y1": local_y,
                            "x2": local_x + el["w"], "y2": local_y + el["h"],
                            "depth": 99,
                        })
                except Exception:
                    pass

            # 递归子帧（偏移继承）
            for child in frame.get("childFrames", []):
                scan_frame(child, offset_x, offset_y)

        scan_frame(r["result"]["frameTree"], 0, 0)
        return all_els

    except Exception:
        return []
    finally:
        try:
            ws.close()
        except Exception:
            pass


def detect_taskbar_buttons(frame=None):
    """检测任务栏按钮 — 兼容旧接口"""
    buttons, error = _get_taskbar_buttons()
    if error:
        return {"error": error}
    return {"buttons": buttons}


# ============================================================
# CDP 网页元素扫描
# ============================================================

# 网页可交互标签
WEB_INTERACTIVE_TAGS = {"button", "a", "input", "select", "textarea"}

# 网页元素 → 标注颜色 (BGR)
WEB_COLORS = {
    "button":   (0, 200, 0),     # 绿
    "a":        (200, 0, 200),   # 紫
    "input":    (0, 200, 200),   # 黄
    "select":   (0, 100, 200),   # 橙
    "textarea": (0, 160, 160),   # 黄绿
}


def _cdp_send(ws, msg):
    """发送 CDP 命令并等待响应（跳过事件）"""
    ws.send(json.dumps(msg))
    while True:
        r = json.loads(ws.recv())
        if "id" in r and r["id"] == msg["id"]:
            return r


def scan_web_elements(cdp_ws_url, viewport_screen_x, viewport_screen_y):
    """
    通过 CDP 扫描网页中的可交互元素（按钮、链接、输入框等）。
    返回元素列表，坐标已转换为虚拟屏本地坐标。

    cdp_ws_url: CDP WebSocket URL (如 ws://localhost:9222/devtools/page/xxx)
    viewport_screen_x/y: viewport 在屏幕上的位置
    返回的 x/y 为虚拟屏本地坐标（已减去 VIRTUAL_SCREEN_LEFT/TOP）
    """
    try:
        import websocket
        ws = websocket.create_connection(cdp_ws_url, timeout=15)
    except Exception:
        return []

    try:
        _cdp_send(ws, {"id": 1, "method": "Runtime.enable"})

        js = """(function(){
            var results = [];
            var tags = ['button','a','input','select','textarea','[class*=qrcode]','[class*=qr-code]','[class*=scan-code]',
                        '[onclick]','[role]',
                        'li[class*=item]','li[class*=tab]','li[class*=menu]','li[class*=catalogue]','li[class*=nav]','li[class*=entry]',
                        'span[class*=item]','span[class*=tab]','span[class*=menu]','span[class*=catalogue]','span[class*=nav]','span[class*=entry]',
                        'div[class*=menu-item]','div[class*=nav-item]','div[class*=tab-item]','div[class*=catalogue]'];
            var seen = {};
            document.querySelectorAll(tags.join(',')).forEach(function(el){
                var rect = el.getBoundingClientRect();
                if (rect.width < 2 || rect.height < 2) return;
                if (rect.top < 0 || rect.left < 0) return;
                if (rect.top > window.innerHeight || rect.left > window.innerWidth) return;
                // 可点击筛选：交互标签 / 带 role/onclick / 光标为 pointer，避免扫进纯文本 span/li 噪声
                var tag = el.tagName.toLowerCase();
                var isInteractive = ['button','a','input','select','textarea'].indexOf(tag) >= 0;
                var hasRole = el.getAttribute('role') !== null;
                var hasOnclick = el.getAttribute('onclick') !== null;
                var pointer = getComputedStyle(el).cursor === 'pointer';
                if (!(isInteractive || hasRole || hasOnclick || pointer)) return;
                // 去重：相同坐标+标签只保留一个
                var key = el.tagName + '|' + Math.round(rect.left) + ',' + Math.round(rect.top);
                if (seen[key]) return;
                seen[key] = true;
                var text = (el.textContent || el.value || el.placeholder || el.title || '').trim().substring(0,40);
                var href = el.href || '';
                if (href.length > 200) href = href.substring(0,200);
                results.push({
                    tag: el.tagName.toLowerCase(),
                    text: text,
                    id: el.id || '',
                    name: el.name || '',
                    type: el.type || '',
                    href: href,
                    x: Math.round(rect.left),
                    y: Math.round(rect.top),
                    w: Math.round(rect.width),
                    h: Math.round(rect.height)
                });
            });
            return JSON.stringify(results);
        })()"""

        r = _cdp_send(ws, {"id": 2, "method": "Runtime.evaluate",
                           "params": {"expression": js, "returnByValue": True}})
        try:
            raw = r["result"]["result"]["value"]
            web_els = json.loads(raw)
        except Exception:
            return []

        # 坐标转换：viewport 坐标 → 屏幕坐标 → 虚拟屏本地坐标
        elements = []
        for el in web_els:
            screen_x = viewport_screen_x + el["x"]
            screen_y = viewport_screen_y + el["y"]
            local_x = screen_x - VIRTUAL_SCREEN_LEFT
            local_y = screen_y - VIRTUAL_SCREEN_TOP

            if local_x < -50 or local_y < -50 or local_x > VIRTUAL_SCREEN_WIDTH + 50:
                continue

            tag = el["tag"]
            elements.append({
                "type": el["tag"],
                "name": el["text"],
                "web_id": el["id"],
                "web_name": el["name"],
                "href": el.get("href", ""),
                "x1": local_x,
                "y1": local_y,
                "x2": local_x + el["w"],
                "y2": local_y + el["h"],
                "depth": 99,  # 网页元素画在最上层
            })

        return elements

    except Exception:
        return []
    finally:
        try:
            ws.close()
        except Exception:
            pass


# ============================================================
# 标注绘制
# ============================================================

def draw_boxes(frame, elements):
    """在截图上绘制 UI 元素标注框（PIL 渲染中文 + 深度排序）"""
    import cv2
    from PIL import Image, ImageDraw, ImageFont

    h, w = frame.shape[:2]

    # 尝试加载中文字体
    _font = None
    for _fp in ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
                "C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/msyhbd.ttc"]:
        try:
            _font = ImageFont.truetype(_fp, 11)
            break
        except Exception:
            continue

    # 深度排序：先画大框/浅层，再画小框/深层（后者盖在上面）
    def _sort_key(el):
        depth = el.get("depth", 0)
        area = (el.get("x2", 0) - el.get("x1", 0)) * (el.get("y2", 0) - el.get("y1", 0))
        return (depth, -area)

    sorted_els = sorted(elements, key=_sort_key)

    # 转 PIL 画文字
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    drawn = 0

    for el in sorted_els:
        x1 = max(0, int(el.get("x1", 0)))
        y1 = max(0, int(el.get("y1", 0)))
        x2 = min(w, int(el.get("x2", 0)))
        y2 = min(h, int(el.get("y2", 0)))

        if x2 <= x1 + 4 or y2 <= y1 + 4:
            continue

        # 颜色
        ctrl_type = el.get("type", "") or "Button"
        color_key = ctrl_type + "Control"
        rgb_color = TYPE_COLORS.get(color_key)
        if rgb_color is None:
            rgb_color = WEB_COLORS.get(ctrl_type, (128, 128, 128))
        # BGR → RGB for PIL
        pil_color = (rgb_color[2], rgb_color[1], rgb_color[0])

        # 画矩形
        draw.rectangle([x1, y1, x2, y2], outline=pil_color, width=1)

        # 标签
        name = el.get("name", ctrl_type)[:12]
        if not name:
            name = ctrl_type[:8]
        label = f"{ctrl_type[:3]}:{name}" if name else ctrl_type[:6]

        # 文字位置：元素上方，如果太靠顶就放内部
        text_y = y1 - 13 if y1 > 14 else y1 + 2
        text_x = x1 + 2

        if _font:
            # 画文字阴影增强可读性
            draw.text((text_x + 1, text_y + 1), label, fill=(0, 0, 0), font=_font)
            draw.text((text_x, text_y), label, fill=pil_color, font=_font)
        else:
            # fallback ASCII
            draw.text((text_x, text_y), label, fill=pil_color)

        drawn += 1

    # 左下角统计
    draw.text((8, h - 16), f"UI: {drawn}", fill=(255, 255, 255))

    # 转回 cv2
    frame[:] = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    return frame

# 需要 numpy 在 draw_boxes 内
import numpy as np
