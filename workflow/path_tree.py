"""
workflow/path_tree.py — 路径树生成

职责：纯计算，无 IO、无副作用。从根状态出发 DFS，把「状态迁移」整理成树。

规则（对齐 WORKFLOW_3D_DESIGN.md §3）：
  - 只按迁移边（from_state != to_state）展开
  - 环：已访问集合截断，标 loop=True，不展开
  - 深度上限 max_depth 截断，标 truncated=True
"""
from collections import defaultdict


def build_path_tree(graph, root_state_id, max_depth=6):
    """生成路径树，返回根节点 dict（嵌套 children）。"""
    states = {s["id"]: s for s in graph["states"]}
    if root_state_id not in states:
        raise ValueError(f"根状态不存在: {root_state_id}")

    elem_by_id = {}
    for s in graph["states"]:
        for e in s.get("elements", []):
            elem_by_id[e["id"]] = e

    out = defaultdict(list)  # from_state -> [edge]
    for ed in graph.get("edges", []):
        if ed["from_state"] != ed["to_state"]:
            out[ed["from_state"]].append(ed)

    def _node(sid, visited, depth):
        s = states[sid]
        node = {"id": sid, "name": s.get("name", sid), "children": []}
        if depth >= max_depth:
            node["truncated"] = True
            return node
        for ed in out.get(sid, []):
            ts = ed["to_state"]
            fe = ed.get("from_element")
            branch = {
                "edge_id": ed["id"],
                "action": (ed.get("action") or {}).get("type"),
                "element": (elem_by_id.get(fe) or {}).get("name"),
            }
            if ts in visited:
                branch["to"] = {"id": ts, "name": states[ts].get("name", ts), "loop": True}
            else:
                branch["to"] = _node(ts, visited | {ts}, depth + 1)
            node["children"].append(branch)
        return node

    return _node(root_state_id, {root_state_id}, 0)
