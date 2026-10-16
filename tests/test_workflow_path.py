"""
tests/test_workflow_path.py — 体检：workflow/path_tree 路径树

跑法：python tests/test_workflow_path.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from workflow.path_tree import build_path_tree

GRAPH = {
    "version": 1,
    "viewport": {"width": 1920, "height": 1080},
    "states": [
        {"id": "s_home", "name": "首页", "elements": [
            {"id": "el_ship", "type": "button", "name": "待发货", "x": 100, "y": 100, "w": 80, "h": 30, "cx": 140, "cy": 115},
            {"id": "el_goods", "type": "button", "name": "商品管理", "x": 100, "y": 200, "w": 80, "h": 30, "cx": 140, "cy": 215},
        ]},
        {"id": "s_pending", "name": "待发货列表", "elements": [
            {"id": "el_order", "type": "a", "name": "第一个订单", "x": 300, "y": 100, "w": 120, "h": 30, "cx": 360, "cy": 115},
        ]},
        {"id": "s_order", "name": "订单详情", "elements": [
            {"id": "el_confirm", "type": "button", "name": "确认发货", "x": 500, "y": 500, "w": 100, "h": 40, "cx": 550, "cy": 520},
        ]},
    ],
    "edges": [
        {"id": "e1", "from_state": "s_home", "from_element": "el_ship", "action": {"type": "click"}, "to_state": "s_pending"},
        {"id": "e2", "from_state": "s_home", "from_element": "el_goods", "action": {"type": "click"}, "to_state": "s_order"},
        {"id": "e3", "from_state": "s_pending", "from_element": "el_order", "action": {"type": "click"}, "to_state": "s_order"},
        {"id": "e4", "from_state": "s_order", "from_element": "el_confirm", "action": {"type": "click"}, "to_state": "s_home"},
    ],
}


def test_root():
    tree = build_path_tree(GRAPH, "s_home")
    assert tree["id"] == "s_home"
    assert len(tree["children"]) == 2, "home 应有 2 条迁移出边"


def test_loop_detected():
    tree = build_path_tree(GRAPH, "s_home")

    def find(sid):
        out = []
        def walk(n):
            if n["id"] == sid:
                out.append(n)
            for c in n.get("children", []):
                walk(c["to"])
        walk(tree)
        return out

    orders = find("s_order")
    assert orders, "应能到达 order 节点"
    found_loop = any(
        c["to"].get("id") == "s_home" and c["to"].get("loop")
        for o in orders for c in o.get("children", [])
    )
    assert found_loop, "s_order → s_home 应被标为回路"


def test_branch_label():
    tree = build_path_tree(GRAPH, "s_home")
    names = {c["element"] for c in tree["children"]}
    assert "待发货" in names and "商品管理" in names


if __name__ == "__main__":
    test_root()
    test_loop_detected()
    test_branch_label()
    print("✅ 体检通过：workflow/path_tree 路径树全绿")
