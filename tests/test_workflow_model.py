"""
tests/test_workflow_model.py — 体检：workflow/model 校验

跑法：python tests/test_workflow_model.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from workflow.model import validate_graph


def _graph(**overrides):
    g = {
        "version": 1,
        "states": [
            {"id": "s_home", "name": "首页", "elements": [
                {"id": "el_ship", "type": "button", "name": "待发货", "cx": 140, "cy": 115},
            ]},
            {"id": "s_pending", "name": "待发货列表", "elements": []},
        ],
        "edges": [
            {"id": "e1", "from_state": "s_home", "from_element": "el_ship",
             "action": {"type": "click"}, "to_state": "s_pending"},
        ],
    }
    g.update(overrides)
    return g


def test_valid():
    assert validate_graph(_graph()) == []


def test_bad_from_state():
    g = _graph()
    g["edges"][0]["from_state"] = "s_ghost"
    assert validate_graph(g), "不存在的 from_state 应报错"


def test_bad_element_owner():
    g = _graph()
    g["edges"][0]["from_element"] = "el_nope"
    assert validate_graph(g), "不存在的 from_element 应报错"


def test_bad_action_type():
    g = _graph()
    g["edges"][0]["action"] = {"type": "fly"}
    assert validate_graph(g), "非法 action.type 应报错"


def test_dup_element_id():
    g = _graph()
    g["states"][1]["elements"] = [{"id": "el_ship", "type": "button", "name": "x"}]
    assert validate_graph(g), "元素 id 全局重复应报错"


if __name__ == "__main__":
    test_valid()
    test_bad_from_state()
    test_bad_element_owner()
    test_bad_action_type()
    test_dup_element_id()
    print("✅ 体检通过：workflow/model 校验全绿")
