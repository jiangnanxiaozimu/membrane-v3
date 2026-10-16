"""
tests/test_workflow_layout.py — 体检：workflow/layout 布局算法

跑法：python tests/test_workflow_layout.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from workflow.layout import compute_layout

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


def test_layers():
    r = compute_layout(GRAPH, "s_home")
    assert r["layers"]["s_home"] == 0
    assert r["layers"]["s_pending"] == 1
    assert r["layers"]["s_order"] == 1  # home→order 直接边，最短距离 1


def test_z_stacking():
    r = compute_layout(GRAPH, "s_home", layer_gap=400)
    assert r["state_pos"]["s_home"]["z"] == 0
    assert r["state_pos"]["s_pending"]["z"] == 400
    assert r["element_pos"]["el_ship"]["z"] == 0, "元素 z 应随所属 state"


def test_edge_endpoints():
    r = compute_layout(GRAPH, "s_home")
    assert len(r["edge_pos"]) == 4
    e1 = next(e for e in r["edge_pos"] if e["id"] == "e1")
    assert e1["from"]["z"] == r["state_pos"]["s_home"]["z"]
    assert e1["to"]["z"] == r["state_pos"]["s_pending"]["z"]


def test_root_missing():
    try:
        compute_layout(GRAPH, "s_ghost")
        assert False, "根状态不存在应报错"
    except ValueError:
        pass


if __name__ == "__main__":
    test_layers()
    test_z_stacking()
    test_edge_endpoints()
    test_root_missing()
    print("✅ 体检通过：workflow/layout 布局全绿")
