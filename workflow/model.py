"""
workflow/model.py — 工作流图（graph_json）schema 校验

职责：纯校验，无 IO、无副作用。输入 dict，输出错误列表（空列表 = 合法）。
不执行、不布局——那是 layout.py / engine.py 的活。

校验规则（对齐 WORKFLOW_3D_DESIGN.md §1）：
  - version == 1
  - states 非空，每个 state 有唯一 id + name
  - 元素 id 全局唯一（edge 引用不歧义）
  - edges 里 from_state / to_state 必须指向已存在的 state
  - from_element 必须指向 from_state.elements 里的 id
  - action.type 白名单：click / input / scroll / hover / wait
"""

ACTION_TYPES = {"click", "input", "scroll", "hover", "wait"}


def validate_graph(graph):
    """校验工作流图，返回错误列表（空列表 = 合法）。"""
    errors = []
    if not isinstance(graph, dict):
        return ["graph 必须是对象"]

    if graph.get("version") != 1:
        errors.append(f"version 必须是 1，得到 {graph.get('version')!r}")

    states = graph.get("states")
    if not isinstance(states, list) or not states:
        errors.append("states 必须是非空列表")
        states = []

    state_ids = set()
    elem_owner = {}  # element_id -> state_id（要求全局唯一）
    for s in states:
        if not isinstance(s, dict):
            errors.append("states 里有非对象元素")
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            errors.append("state 缺少合法 id")
            continue
        if sid in state_ids:
            errors.append(f"state id 重复: {sid}")
        state_ids.add(sid)
        if not s.get("name"):
            errors.append(f"state {sid} 缺少 name")

        elems = s.get("elements", [])
        if not isinstance(elems, list):
            errors.append(f"state {sid} 的 elements 必须是列表")
            elems = []
        seen_elem = set()
        for e in elems:
            if not isinstance(e, dict):
                errors.append(f"state {sid} 的 elements 里有非对象元素")
                continue
            eid = e.get("id")
            if not isinstance(eid, str) or not eid:
                errors.append(f"state {sid} 里有元素缺少 id")
                continue
            if eid in seen_elem:
                errors.append(f"state {sid} 内元素 id 重复: {eid}")
            seen_elem.add(eid)
            if eid in elem_owner:
                errors.append(f"元素 id 全局重复: {eid}（state {elem_owner[eid]} 与 {sid}）")
            else:
                elem_owner[eid] = sid

    edges = graph.get("edges", [])
    if not isinstance(edges, list):
        errors.append("edges 必须是列表")
        edges = []
    seen_edge = set()
    for ed in edges:
        if not isinstance(ed, dict):
            errors.append("edges 里有非对象元素")
            continue
        eid = ed.get("id")
        if not isinstance(eid, str) or not eid:
            errors.append("edge 缺少合法 id")
            continue
        if eid in seen_edge:
            errors.append(f"edge id 重复: {eid}")
        seen_edge.add(eid)

        fs = ed.get("from_state")
        ts = ed.get("to_state")
        if fs not in state_ids:
            errors.append(f"edge {eid} 的 from_state 不存在: {fs!r}")
        if ts not in state_ids:
            errors.append(f"edge {eid} 的 to_state 不存在: {ts!r}")

        fe = ed.get("from_element")
        if fe is not None:
            owner = elem_owner.get(fe)
            if owner is None:
                errors.append(f"edge {eid} 的 from_element 不存在: {fe!r}")
            elif owner != fs:
                errors.append(f"edge {eid} 的 from_element {fe!r} 属于 state {owner!r}，不属 from_state {fs!r}")

        action = ed.get("action")
        atype = action.get("type") if isinstance(action, dict) else None
        if atype not in ACTION_TYPES:
            errors.append(f"edge {eid} 的 action.type 非法: {atype!r}")

    return errors
