"""
workflow/layout.py — 3D 分层布局算法

职责：纯计算，无 IO、无副作用。给定已校验的图 + 根状态，算出：
  - 每个 state 的层号（BFS 最短距离）
  - 每个 state 的 3D 坐标（z = 层号 × 层间距，同层左右错开）
  - 每个元素的 3D 坐标（元素按真实页面坐标 (cx,cy) 归一化铺在平面内）
  - 每条边的两端 3D 坐标（供前端画贝塞尔线）

算法对齐 WORKFLOW_3D_DESIGN.md §2。
"""
from collections import defaultdict, deque


def compute_layout(graph, root_state_id, layer_gap=400, plane_width=800):
    """计算 3D 布局，返回 dict：layers / state_pos / element_pos / edge_pos。"""
    states = {s["id"]: s for s in graph["states"]}
    if root_state_id not in states:
        raise ValueError(f"根状态不存在: {root_state_id}")

    vp = graph.get("viewport") or {"width": 1920, "height": 1080}
    vw = vp.get("width") or 1920
    vh = vp.get("height") or 1080
    plane_height = plane_width * (vh / vw) if vw else plane_width

    # ---- 1. BFS 层号（只算迁移边 from_state != to_state）----
    adj = defaultdict(list)
    for ed in graph.get("edges", []):
        if ed["from_state"] != ed["to_state"]:
            adj[ed["from_state"]].append(ed["to_state"])

    layers = {root_state_id: 0}
    q = deque([(root_state_id, 0)])
    while q:
        sid, lvl = q.popleft()
        for nxt in adj.get(sid, []):
            if nxt not in layers:
                layers[nxt] = lvl + 1
                q.append((nxt, lvl + 1))
    for sid in states:
        layers.setdefault(sid, 0)  # 不可达状态 → 层 0

    # ---- 2. 同层左右排开（按 id 排序，居中）----
    by_layer = defaultdict(list)
    for sid, lvl in layers.items():
        by_layer[lvl].append(sid)
    state_gap = plane_width * 1.5
    state_pos = {}
    for lvl in sorted(by_layer):
        ids = sorted(by_layer[lvl])
        n = len(ids)
        for i, sid in enumerate(ids):
            x = (i - (n - 1) / 2) * state_gap
            state_pos[sid] = {"x": x, "y": 0.0, "z": lvl * layer_gap, "layer": lvl}

    # ---- 3. 元素 3D 坐标（页面坐标归一化 → 平面内偏移）----
    element_pos = {}
    for sid, s in states.items():
        sc = state_pos[sid]
        for e in s.get("elements", []):
            nx = (e.get("cx", e.get("x", 0)) or 0) / vw
            ny = (e.get("cy", e.get("y", 0)) or 0) / vh
            nx = max(0.0, min(1.0, nx))
            ny = max(0.0, min(1.0, ny))
            element_pos[e["id"]] = {
                "x": sc["x"] + (nx - 0.5) * plane_width,
                "y": sc["y"] + (0.5 - ny) * plane_height,  # 屏幕 Y 向下 → 3D Y 向上
                "z": sc["z"],
                "state_id": sid,
                "layer": sc["layer"],
            }

    # ---- 4. 边两端 3D 坐标 ----
    edge_pos = []
    for ed in graph.get("edges", []):
        fe = ed.get("from_element")
        frm = element_pos.get(fe) if fe else None
        if frm is None:
            frm = state_pos[ed["from_state"]]
        edge_pos.append({
            "id": ed["id"],
            "action": (ed.get("action") or {}).get("type"),
            "from": frm,
            "to": state_pos[ed["to_state"]],
        })

    return {
        "layer_gap": layer_gap,
        "plane_width": plane_width,
        "plane_height": plane_height,
        "layers": layers,
        "state_pos": state_pos,
        "element_pos": element_pos,
        "edge_pos": edge_pos,
    }
