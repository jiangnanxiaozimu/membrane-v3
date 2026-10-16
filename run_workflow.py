"""
run_workflow.py — 工作流执行 runner（集成层）

把三件真东西接进执行引擎，让膜能自己跑完「读 → 认 → 判断 → 点 → 迁移」：
  - 眼睛（scan）  ：membrane_vision.detect_ui_elements() 真扫描
  - 手（execute） ：exec.executor 真鼠标 / 键盘
  - 裁判（operator）：membrane_operator.Operator（qwen3:14b 执行前校验）

用法：
  python run_workflow.py --graph sample.json            # 干跑：真扫描+真判断，但不真点
  python run_workflow.py --graph sample.json --execute  # 真点（需虚拟屏已激活 + 你盯着）
  python run_workflow.py                                # 用内置样例图干跑

干跑是默认 —— 这是刻意的：第一次集成先证明「眼睛认对人、裁判判得对、手知道往哪点」，
真点等你在场看着再开。
"""
import argparse
import json
import os

# ---- 类型词表归一化 ----
# 图/演员用小写（button/a/textarea），UIA 扫描用大写（Button/Edit/Hyperlink）。
# 匹配前统一到规范小写，避免「图写 button、扫到 Button」对不上。
_TYPE_ALIAS = {
    "button": "button", "Button": "button", "SplitButton": "button",
    "edit": "input", "Edit": "input", "input": "input",
    "textarea": "input", "TextArea": "input",
    "text": "text", "Text": "text",
    "hyperlink": "a", "Hyperlink": "a", "a": "a",
    "checkbox": "checkbox", "CheckBox": "checkbox",
    "radio": "radio", "RadioButton": "radio",
    "combobox": "select", "ComboBox": "select", "select": "select",
    "tabitem": "tab", "TabItem": "tab",
    "menuitem": "menu", "MenuItem": "menu",
    "treeitem": "tree", "TreeItem": "tree",
    "listitem": "list", "ListItem": "list",
    "slider": "slider", "Slider": "slider",
}


def normalize_type(t):
    t = str(t or "").strip()
    return _TYPE_ALIAS.get(t, t.lower())


# ---- 眼睛：真扫描 ----
def make_scan():
    def scan(state):
        import membrane_vision
        result = membrane_vision.detect_ui_elements()
        elements = result.get('elements', []) if isinstance(result, dict) else []
        # 补中心点 cx/cy（匹配和点击都要用）
        for el in elements:
            if 'cx' not in el or 'cy' not in el:
                if 'x1' in el:
                    el['cx'] = (el['x1'] + el['x2']) // 2
                    el['cy'] = (el['y1'] + el['y2']) // 2
                elif 'x' in el:
                    el['cx'] = el['x'] + el.get('w', 0) // 2
                    el['cy'] = el['y'] + el.get('h', 0) // 2
        return elements
    return scan


# ---- 认：类型归一化后的匹配 ----
def make_match():
    def match(intent_element, live_elements):
        if not intent_element or not live_elements:
            return None
        t = normalize_type(intent_element.get("type", ""))
        n = (intent_element.get("name", "") or "").strip()
        # 1) 类型 + 名字都匹配（最严格）
        for el in live_elements:
            if normalize_type(el.get("type", "")) == t and (el.get("name", "") or "").strip() == n:
                return el
        # 2) 名字匹配（类型宽松）
        if n:
            for el in live_elements:
                if (el.get("name", "") or "").strip() == n:
                    return el
        # 2.5) 名字子串匹配（"扫码登录" 能匹配 "扫码登录更便捷"）
        if n:
            for el in live_elements:
                en = (el.get("name", "") or "").strip()
                if en and (n in en or en in n):
                    return el
        # 3) 类型匹配（名字宽松，取第一个）
        for el in live_elements:
            if normalize_type(el.get("type", "")) == t:
                return el
        return None
    return match


# ---- 手：真执行（干跑默认） ----
def make_execute(dry_run=True):
    def execute(action, target, edge):
        atype = (action or {}).get("type", "click")
        if not target:
            return {"ok": False, "error": "无可执行目标元素"}
        # 目标中心点（优先 cx/cy，否则从 x1/y1/x2/y2 或 x/y/w/h 算）
        if 'cx' in target and 'cy' in target:
            cx, cy = target['cx'], target['cy']
        elif 'x1' in target:
            cx, cy = (target['x1'] + target['x2']) // 2, (target['y1'] + target['y2']) // 2
        else:
            cx, cy = target.get('x', 0), target.get('y', 0)
        name = target.get('name') or '?'

        if dry_run:
            return {"ok": True, "dry_run": True, "action": atype,
                    "target": name, "x": cx, "y": cy}

        from exec import executor as ex
        if atype == "click":
            return ex.click(cx, cy)
        if atype == "input":
            return ex.type_text(cx, cy, edge.get("input") or "")
        if atype == "hover":
            return ex.hover(cx, cy)
        if atype == "scroll":
            try:
                amt = int(edge.get("input") or -120)
            except (TypeError, ValueError):
                amt = -120
            return ex.scroll(cx, cy, amount=amt)
        if atype == "wait":
            try:
                secs = float(edge.get("input") or 1)
            except (TypeError, ValueError):
                secs = 1.0
            return ex.wait(secs)
        return ex.click(cx, cy)
    return execute


# ---- 迁移验证：点完确认页面真跳转了（纠错闭环） ----
def _get_qianniu_url():
    """读当前千牛 tab 的 URL（CDP 只读）。失败返回 None。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        for t in data:
            if t.get("type") == "page" and "myseller" in (t.get("url") or ""):
                return t["url"]
    except Exception:
        pass
    return None


def _url_path(url):
    """URL 完整 path（去尾斜杠，忽略 query/hash）。用于区分同导航下的不同子页。

    千牛同导航下子页共享模块段（财务的 总览/账户明细/收支账单 模块段都是
    whale-accountant），靠模块段区分不了子页切换，必须比完整 path。
    """
    from urllib.parse import urlparse
    if not url:
        return None
    try:
        return urlparse(url).path.rstrip("/") or "/"
    except Exception:
        return url


def make_verify(graph):
    """迁移验证函数：state.url_pattern 存目标页 page_url，点完读当前 URL 的完整 path 对比。

    返回 verify(edge) -> (ok: bool, got: 实际 path)。
    """
    state_path = {}
    for s in graph.get("states", []):
        up = s.get("url_pattern")
        if up:
            state_path[s["id"]] = _url_path(up)

    def verify(edge):
        expected = state_path.get(edge.get("to_state"))
        if not expected:
            return True, None  # 该 state 无 url_pattern，跳过验证
        # 点完后 SPA 路由切换有延迟，等待并重试（最多 ~5 秒）
        import time
        got = None
        for _ in range(5):
            got = _url_path(_get_qianniu_url())
            if got == expected:
                return True, got
            time.sleep(1)
        return False, got
    return verify


# ---- 状态自检：定位当前在哪个 state（URL path 匹配） ----
def make_match_state(graph):
    """读当前千牛 URL 的完整 path，匹配各 state 的 url_pattern，返回当前 state_id（或 None）。

    供引擎两处用：点边前判断「已在目标页」跳过点击；迁移失败时判定「现在实际在哪」。
    """
    from urllib.parse import urlparse
    state_path = {}
    for s in graph.get("states", []):
        up = s.get("url_pattern")
        if up:
            state_path[s["id"]] = urlparse(up).path.rstrip("/")

    def match_state():
        cur = _get_qianniu_url()
        if not cur:
            return None
        cur_path = urlparse(cur).path.rstrip("/")
        for sid, p in state_path.items():
            if p == cur_path:
                return sid
        return None
    return match_state


# ---- 组装引擎 ----
def build_engine(graph, dry_run=True, verify=None):
    from workflow.engine import Engine
    from membrane_operator import Operator
    if verify is None:
        verify = not dry_run  # 默认：真点才验证迁移（干跑没点，URL 不会变）
    return Engine(
        graph,
        operator=Operator(),
        scan=make_scan(),
        match=make_match(),
        execute=make_execute(dry_run=dry_run),
        verify_migration=make_verify(graph) if verify else None,
        match_state=make_match_state(graph),
    )


# ---- 读图 ----
def load_graph(path=None):
    if path:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    return SAMPLE_GRAPH


# ---- 定扫描边界 ----
# set_display_bounds 是内存态，不跨进程。每个 runner 进程都必须自己把边界指到副屏，
# 否则 membrane_vision 用模块默认常量（LEFT=1920），会扫到一块不存在的区域。
# 副屏真实位置由 launch_qianniu.py 写在 launch_state.json，优先读它。
def apply_display_bounds(bounds=None):
    import membrane_vision as mv
    if bounds:
        left, top, width, height = bounds
    else:
        state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_state.json")
        if os.path.exists(state_file):
            with open(state_file, encoding='utf-8') as f:
                st = json.load(f)
            left, top, width, height = st['left'], st['top'], st['width'], st['height']
        else:
            return None
    mv.set_display_bounds(int(left), int(top), int(width), int(height))
    return {'left': int(left), 'top': int(top), 'width': int(width), 'height': int(height)}


# ---- 内置样例图（干跑用） ----
SAMPLE_GRAPH = {
    "version": 1,
    "app_id": "sample",
    "states": [
        {"id": "s_home", "name": "首页", "layer": 0,
         "elements": [
             {"id": "el_1", "type": "button", "name": "待发货"}
         ]},
        {"id": "s_pending", "name": "待发货列表", "layer": 1,
         "elements": [
             {"id": "el_2", "type": "button", "name": "第一个订单"}
         ]},
    ],
    "edges": [
        {"id": "e_1", "from_state": "s_home", "from_element": "el_1",
         "action": {"type": "click"}, "input": None, "to_state": "s_pending"},
    ],
}


def _fmt_log(log):
    lines = []
    for r in log:
        t = r.get('type', '?')
        if t == 'terminal':
            lines.append(f"[终点] 状态={r.get('state')}")
        elif t == 'max_steps':
            lines.append(f"[超步] 状态={r.get('state')}")
        elif t == 'rejected':
            d = r.get('decision', {})
            lines.append(f"[拒绝] {r.get('intent')} — 原因: {d.get('reason')} (置信{d.get('confidence')})")
        elif t == 'not_found':
            lines.append(f"[找不到] {r.get('intent')} — 屏幕上没扫到这个元素，停步")
        else:
            d = r.get('decision', {})
            cand = r.get('candidate') or {}
            pos = f"({cand.get('cx')},{cand.get('cy')})" if cand.get('cx') is not None else "(无候选)"
            lines.append(
                f"[{t}] {r.get('intent')} — 候选{pos} — 裁判: {d.get('reason')} (置信{d.get('confidence')})"
            )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="膜工作流执行 runner")
    ap.add_argument("--graph", help="工作流图 JSON 路径（缺省用内置样例）")
    ap.add_argument("--execute", action="store_true", help="真点（默认干跑，不动鼠标）")
    ap.add_argument("--bounds", help="扫描边界 left,top,width,height（缺省读 launch_state.json）")
    args = ap.parse_args()

    bounds = None
    if args.bounds:
        bounds = [int(x) for x in args.bounds.split(",")]
    b = apply_display_bounds(bounds)
    if b:
        print(f"== 扫描边界: 副屏 left={b['left']} top={b['top']} {b['width']}x{b['height']} ==")

    graph = load_graph(args.graph)
    engine = build_engine(graph, dry_run=not args.execute)
    print(f"== 膜工作流 runner {'（干跑 dry-run，不真点）' if not args.execute else '（真执行！）'} ==")
    log = engine.run()
    print(_fmt_log(log))


if __name__ == "__main__":
    main()
