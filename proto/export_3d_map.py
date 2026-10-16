"""
proto/export_3d_map.py — 千牛全站导航地图 3D 数据生成器

从 membrane.db 读取真实爬取数据（pages + elements），生成 3D 分层导航地图：
  - 根「千牛工作台」→ 13 个业务模块主页（交易/商品/营销/…）→ 88 个子菜单
  - 树形布局：子菜单围绕各自主页横向展开（不是全局横排，避免 88 个挤成一条线）
  - 元素精简：每页取前 K 个有名字的真实元素（22238 全渲染会卡）
  - 导航关系用「导航元素」承载：根里有 13 个模块入口，主页里有子菜单入口，
    路径树因此能显示「交易 → 已卖出宝贝」这种有意义的分支

跑法：python proto/export_3d_map.py
输出：proto/3d_data.js（window.DATA，覆盖旧的发货流程演示数据）
"""
import json
import math
import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from workflow.path_tree import build_path_tree

DB = BASE / "membrane.db"
OUT = BASE / "proto" / "3d_data.js"

VIEWPORT = {"width": 1920, "height": 1080}
PLANE_WIDTH = 600
LAYER_GAP = 1100
K_ELEMS = 10                 # 每页取前 K 个有名字的真实元素
MAIN_GAP = PLANE_WIDTH * 1.5   # 主页之间最小间距
SUB_GAP = PLANE_WIDTH * 0.42   # 子菜单之间间距

URL_KEY_RE = re.compile(r"^1\.\d+(_\d+)?$")


def nav_key(nav_id):
    """'1.10' -> (1, 10)，数字排序（字符串排序会把 1.10 排到 1.2 前面）。"""
    return tuple(int(x) for x in nav_id.split("."))


def sid(url_key):
    return "s_" + url_key.replace(".", "_")


def eid(prefix, url_key):
    return prefix + "_" + url_key.replace(".", "_")


def load_db():
    """读 pages + elements，返回 (mains, subs, elems_by_page)。"""
    db = sqlite3.connect(DB)
    c = db.cursor()

    pages = c.execute(
        "SELECT url_key, skeleton_json FROM pages WHERE app_id='qianniu'"
    ).fetchall()

    mains = {}   # nav_id -> {name, url_key}
    subs = {}    # parent_nav_id -> [(url_key, name, sub_index)]
    for url_key, skj in pages:
        if not URL_KEY_RE.match(url_key):
            continue  # 跳过旧浅爬残留（如 home 页）
        sk = json.loads(skj)
        if "_" in url_key:
            nav = url_key.split("_")[0]
            sub_index = int(url_key.split("_")[1])
            name = sk.get("sub_text") or sk.get("page_title") or url_key
            subs.setdefault(nav, []).append((url_key, name, sub_index))
        else:
            name = sk.get("nav_text") or sk.get("page_title") or url_key
            mains[url_key] = name

    elems_by_page = {}  # url_key -> [ {type,name,cx,cy} ]
    rows = c.execute(
        "SELECT p.url_key, e.elem_type, e.name, e.features_json "
        "FROM elements e JOIN pages p ON e.page_id=p.id WHERE p.app_id='qianniu'"
    ).fetchall()
    for url_key, et, name, fj in rows:
        if not URL_KEY_RE.match(url_key):
            continue
        f = json.loads(fj) if fj else {}
        cx = f.get("cx", f.get("x", 0))
        cy = f.get("cy", f.get("y", 0))
        if name:  # 只留有名元素
            elems_by_page.setdefault(url_key, []).append(
                {"type": et, "name": name, "cx": cx, "cy": cy})

    db.close()
    return mains, subs, elems_by_page


def pick_real(url_key, elems_by_page, k=K_ELEMS):
    """每页取前 k 个真实元素（按爬取 id 顺序，已是 features_json 里的顺序）。"""
    return elems_by_page.get(url_key, [])[:k]


def build_graph(mains, subs, elems_by_page):
    """组装 GRAPH：states（含导航元素 + 精简真实元素）+ edges。"""
    states = []
    edges = []

    # 根状态：13 个模块入口
    root_elems = []
    for i, (nav, name) in enumerate(sorted(mains.items(), key=lambda kv: nav_key(kv[0]))):
        root_elems.append({
            "id": eid("el_nav", nav), "type": "button", "name": name,
            "cx": 60, "cy": 120 + i * 42,
        })
    states.append({"id": "s_root", "name": "千牛工作台", "elements": root_elems})

    # 主页 + 子菜单状态
    for nav, name in sorted(mains.items(), key=lambda kv: nav_key(kv[0])):
        # 主页状态：子菜单入口（左侧导航模拟）+ 精简真实元素
        main_elems = []
        sub_list = sorted(subs.get(nav, []), key=lambda t: t[2])
        for j, (surl, sname, _si) in enumerate(sub_list):
            main_elems.append({
                "id": eid("el_sub", surl), "type": "button", "name": sname,
                "cx": 50, "cy": 130 + j * 34,
            })
        for e in pick_real(nav, elems_by_page):
            main_elems.append({"id": eid("el_", nav) + "_" + str(len(main_elems)),
                               "type": e["type"], "name": e["name"],
                               "cx": e["cx"], "cy": e["cy"]})
        states.append({"id": sid(nav), "name": name, "elements": main_elems})

        # 子菜单状态：精简真实元素
        for surl, sname, _si in sub_list:
            sub_elems = [{"id": eid("el_", surl) + "_" + str(k),
                          "type": e["type"], "name": e["name"],
                          "cx": e["cx"], "cy": e["cy"]}
                         for k, e in enumerate(pick_real(surl, elems_by_page))]
            states.append({"id": sid(surl), "name": sname, "elements": sub_elems})

    # 边：根 → 主页；主页 → 子菜单
    for nav in mains:
        edges.append({"id": "e_" + nav.replace(".", "_"), "from_state": "s_root",
                      "from_element": eid("el_nav", nav),
                      "action": {"type": "click"}, "to_state": sid(nav)})
        for surl, _sname, _si in sorted(subs.get(nav, []), key=lambda t: t[2]):
            edges.append({"id": "e_" + surl.replace(".", "_"),
                          "from_state": sid(nav),
                          "from_element": eid("el_sub", surl),
                          "action": {"type": "click"}, "to_state": sid(surl)})

    return {
        "version": 1, "app_id": "store:qianniu", "viewport": VIEWPORT,
        "states": states, "edges": edges,
    }


def prism_layout(graph):
    """四棱柱/散弹枪布局：根在起点，主页 4×4 方形截面，子菜单全局大方阵（散弹发散）。
    面板互不重叠：主页间距 1200、子菜单间距 700×450（> 面板 600×337.5）。"""
    L1 = 2400.0      # 主页层 z
    L2 = 4800.0      # 子菜单层 z（主页前方）
    G1 = 1200.0      # 主页方形网格间距
    G2X = 700.0      # 子菜单水平间距
    G2Y = 450.0      # 子菜单垂直间距
    COLS = 4         # 主页 4×4 网格
    SCOLS = 10       # 子菜单方阵列数

    main_ids = [ed["to_state"] for ed in graph["edges"] if ed["from_state"] == "s_root"]
    main_ids = sorted(main_ids, key=lambda s: nav_key(s.replace("s_", "").replace("_", ".")))
    subs_of = {m: [] for m in main_ids}
    for ed in graph["edges"]:
        if ed["from_state"] in subs_of:
            subs_of[ed["from_state"]].append(ed["to_state"])

    state_pos = {"s_root": {"x": 0.0, "y": 0.0, "z": 0.0, "layer": 0}}

    for i, m in enumerate(main_ids):
        row = i // COLS
        col = i % COLS
        x = (col - (COLS - 1) / 2) * G1
        y = ((COLS - 1) / 2 - row) * G1
        state_pos[m] = {"x": x, "y": y, "z": L1, "layer": 1}

    # 子菜单：全局大方阵（按主页顺序依次排入），间距足够大互不重叠
    all_subs = [s for m in main_ids for s in subs_of[m]]
    for j, s in enumerate(all_subs):
        row = j // SCOLS
        col = j % SCOLS
        x = (col - (SCOLS - 1) / 2) * G2X
        y = ((SCOLS - 1) / 2 - row) * G2Y
        state_pos[s] = {"x": x, "y": y, "z": L2, "layer": 2}

    # 元素：贴在各自面板平面上（cx/cy 归一化到 PW×PH 面板，z 与状态面板一致，
    # 这样橙色元素点不会飘到蓝色面板外面）
    vw = VIEWPORT["width"]
    vh = VIEWPORT["height"]
    ph = PLANE_WIDTH * (vh / vw)
    element_pos = {}
    for st in graph["states"]:
        sid_ = st["id"]
        sc = state_pos[sid_]
        for e in st.get("elements", []):
            nx = max(0.0, min(1.0, (e.get("cx", 0) or 0) / vw))
            ny = max(0.0, min(1.0, (e.get("cy", 0) or 0) / vh))
            element_pos[e["id"]] = {
                "x": sc["x"] + (0.5 - nx) * PLANE_WIDTH,
                "y": sc["y"] + (0.5 - ny) * ph,
                "z": sc["z"], "state_id": sid_, "layer": sc["layer"],
            }

    # 绕 y 轴旋转 180 度（x、z 取反）：纠正摄像机视角左右反了。
    # 配合上方元素镜像，最终导航（页面左，cx 小）落在面板左边。
    for p in state_pos.values():
        p["x"] = -p["x"]
        p["z"] = -p["z"]
    for p in element_pos.values():
        p["x"] = -p["x"]
        p["z"] = -p["z"]

    return state_pos, element_pos, ph


def build_render_data(mains, subs, elems_by_page):
    """组装前端渲染所需的 render_data（states/elements/edges/path_tree）。

    独立成函数，供 proto/export_3d_map.py 的 main() 与 server/api_server.py 的
    /api/v2/navmap 端点复用，避免两处重复组装逻辑。
    """
    graph = build_graph(mains, subs, elems_by_page)
    state_pos, element_pos, plane_height = prism_layout(graph)

    state_name = {s["id"]: s["name"] for s in graph["states"]}
    elem_info = {}
    for s in graph["states"]:
        for e in s.get("elements", []):
            elem_info[e["id"]] = {"name": e.get("name"), "type": e.get("type")}

    out_count = {}
    for ed in graph["edges"]:
        fe = ed.get("from_element")
        if fe:
            out_count[fe] = out_count.get(fe, 0) + 1

    render_edges = []
    for ed in graph["edges"]:
        fe = ed.get("from_element")
        frm = element_pos.get(fe) if fe else state_pos[ed["from_state"]]
        render_edges.append({
            "id": ed["id"], "action": (ed.get("action") or {}).get("type"),
            "from_element": fe, "from_state": ed["from_state"], "to_state": ed["to_state"],
            "from": frm, "to": state_pos[ed["to_state"]],
        })

    tree = build_path_tree(graph, "s_root")

    return {
        "root": "s_root",
        "plane_width": PLANE_WIDTH,
        "plane_height": plane_height,
        "layer_gap": LAYER_GAP,
        "states": [{"id": sid_, "name": state_name[sid_], **state_pos[sid_]}
                   for sid_ in sorted(state_pos)],
        "elements": [{"id": eid_, **elem_info.get(eid_, {}), **element_pos[eid_],
                      "has_out": out_count.get(eid_, 0) > 0}
                     for eid_ in sorted(element_pos)],
        "edges": render_edges,
        "path_tree": tree,
    }


def main():
    mains, subs, elems_by_page = load_db()
    render_data = build_render_data(mains, subs, elems_by_page)

    OUT.write_text("window.DATA = " + json.dumps(render_data, ensure_ascii=False) + ";\n",
                   encoding="utf-8")
    n_main = sum(1 for s in render_data["states"] if s["layer"] == 1)
    n_sub = sum(1 for s in render_data["states"] if s["layer"] == 2)
    print(f"✅ 生成 {OUT.name}")
    print(f"   states={len(render_data['states'])}（根1 + 主页{n_main} + 子菜单{n_sub}）")
    print(f"   elements={len(render_data['elements'])}  edges={len(render_data['edges'])}")
    xs = [s["x"] for s in render_data["states"]]
    print(f"   横向范围 x=[{min(xs):.0f}, {max(xs):.0f}]（跨度 {max(xs)-min(xs):.0f}）")


if __name__ == "__main__":
    main()
