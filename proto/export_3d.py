"""
proto/export_3d.py — 3D 工作流可视化原型的数据生成器

职责：跑 workflow 内核（model 校验 + layout 布局 + path_tree 路径树），
     把结果组装成前端要的 render_data，写到 proto/3d_data.js（window.DATA）。

跑法：python proto/export_3d.py

演示图：千牛发货主流程 + 分支（11 状态 4 层 54 元素 18 边）。
元素坐标模拟千牛真实布局（顶部栏 / 左侧导航 / 内容区）。
真实数据等扫描千牛后，从 elements/pages 表喂进来替换。
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from workflow.model import validate_graph
from workflow.layout import compute_layout
from workflow.path_tree import build_path_tree

GRAPH = {
    "version": 1,
    "app_id": "store:qianniu",
    "viewport": {"width": 1920, "height": 1080},
    "states": [
        {"id": "s_home", "name": "千牛工作台", "url_pattern": "/home",
         "elements": [
             {"id": "el_search", "type": "input", "name": "搜索", "cx": 500, "cy": 30},
             {"id": "el_msg", "type": "button", "name": "消息", "cx": 1800, "cy": 30},
             {"id": "el_nav_ship", "type": "button", "name": "待发货", "cx": 45, "cy": 120},
             {"id": "el_nav_goods", "type": "button", "name": "商品管理", "cx": 45, "cy": 175},
             {"id": "el_nav_logistics", "type": "button", "name": "物流管理", "cx": 45, "cy": 230},
             {"id": "el_nav_marketing", "type": "button", "name": "营销中心", "cx": 45, "cy": 285},
             {"id": "el_nav_customer", "type": "button", "name": "客户管理", "cx": 45, "cy": 340},
             {"id": "el_card_orders", "type": "a", "name": "今日订单", "cx": 400, "cy": 250},
             {"id": "el_card_sales", "type": "a", "name": "今日成交额", "cx": 800, "cy": 250},
             {"id": "el_card_visit", "type": "a", "name": "访客数", "cx": 1200, "cy": 250},
         ]},
        {"id": "s_pending", "name": "待发货列表", "url_pattern": "/pending",
         "elements": [
             {"id": "el_p_filter", "type": "button", "name": "筛选", "cx": 300, "cy": 130},
             {"id": "el_p_order1", "type": "a", "name": "订单#1", "cx": 500, "cy": 250},
             {"id": "el_p_order2", "type": "a", "name": "订单#2", "cx": 500, "cy": 340},
             {"id": "el_p_order3", "type": "a", "name": "订单#3", "cx": 500, "cy": 430},
             {"id": "el_batch_ship", "type": "button", "name": "批量发货", "cx": 1750, "cy": 120},
             {"id": "el_p_refresh", "type": "button", "name": "刷新", "cx": 1700, "cy": 200},
         ]},
        {"id": "s_goods_list", "name": "商品列表", "url_pattern": "/goods",
         "elements": [
             {"id": "el_g_publish", "type": "button", "name": "发布商品", "cx": 1750, "cy": 120},
             {"id": "el_g1", "type": "a", "name": "商品A", "cx": 500, "cy": 300},
             {"id": "el_g2", "type": "a", "name": "商品B", "cx": 500, "cy": 400},
             {"id": "el_g3", "type": "a", "name": "商品C", "cx": 500, "cy": 500},
             {"id": "el_g_search", "type": "input", "name": "搜索商品", "cx": 500, "cy": 120},
         ]},
        {"id": "s_logistics", "name": "物流管理", "url_pattern": "/logistics",
         "elements": [
             {"id": "el_l_track", "type": "button", "name": "轨迹查询", "cx": 1750, "cy": 120},
             {"id": "el_l_search", "type": "input", "name": "输入单号", "cx": 500, "cy": 150},
             {"id": "el_l_result", "type": "a", "name": "轨迹结果", "cx": 500, "cy": 350},
             {"id": "el_l_back", "type": "button", "name": "返回", "cx": 45, "cy": 120},
         ]},
        {"id": "s_marketing", "name": "营销中心", "url_pattern": "/marketing",
         "elements": [
             {"id": "el_m_coupon", "type": "a", "name": "优惠券", "cx": 500, "cy": 300},
             {"id": "el_m_activity", "type": "a", "name": "活动报名", "cx": 800, "cy": 300},
             {"id": "el_m_seed", "type": "a", "name": "直通车", "cx": 1100, "cy": 300},
             {"id": "el_m_back", "type": "button", "name": "返回", "cx": 45, "cy": 120},
         ]},
        {"id": "s_customer", "name": "客户管理", "url_pattern": "/customer",
         "elements": [
             {"id": "el_c_search", "type": "input", "name": "搜索客户", "cx": 500, "cy": 150},
             {"id": "el_c1", "type": "a", "name": "客户A", "cx": 500, "cy": 300},
             {"id": "el_c2", "type": "a", "name": "客户B", "cx": 500, "cy": 390},
             {"id": "el_c_back", "type": "button", "name": "返回", "cx": 45, "cy": 120},
         ]},
        {"id": "s_order_detail", "name": "订单详情", "url_pattern": "/order/detail",
         "elements": [
             {"id": "el_od_close", "type": "button", "name": "关闭", "cx": 1850, "cy": 80},
             {"id": "el_od_buyer", "type": "a", "name": "买家信息", "cx": 500, "cy": 300},
             {"id": "el_od_confirm", "type": "button", "name": "确认发货", "cx": 1750, "cy": 500},
             {"id": "el_od_print", "type": "button", "name": "打印面单", "cx": 1750, "cy": 560},
             {"id": "el_od_note", "type": "a", "name": "订单备注", "cx": 500, "cy": 450},
         ]},
        {"id": "s_ship", "name": "发货操作", "url_pattern": "/ship",
         "elements": [
             {"id": "el_sh_company", "type": "input", "name": "物流公司", "cx": 500, "cy": 300},
             {"id": "el_sh_tracking", "type": "input", "name": "物流单号", "cx": 500, "cy": 380},
             {"id": "el_sh_submit", "type": "button", "name": "提交发货", "cx": 1750, "cy": 500},
             {"id": "el_sh_cancel", "type": "button", "name": "取消", "cx": 1600, "cy": 500},
         ]},
        {"id": "s_goods_detail", "name": "商品详情", "url_pattern": "/goods/detail",
         "elements": [
             {"id": "el_gd_back", "type": "button", "name": "返回", "cx": 45, "cy": 120},
             {"id": "el_gd_edit", "type": "button", "name": "编辑商品", "cx": 1750, "cy": 150},
             {"id": "el_gd_upper", "type": "button", "name": "上架", "cx": 1750, "cy": 210},
             {"id": "el_gd_img", "type": "a", "name": "商品主图", "cx": 500, "cy": 400},
             {"id": "el_gd_price", "type": "a", "name": "价格设置", "cx": 500, "cy": 520},
         ]},
        {"id": "s_goods_edit", "name": "编辑商品", "url_pattern": "/goods/edit",
         "elements": [
             {"id": "el_ge_title", "type": "input", "name": "商品标题", "cx": 500, "cy": 300},
             {"id": "el_ge_price", "type": "input", "name": "商品价格", "cx": 500, "cy": 380},
             {"id": "el_ge_save", "type": "button", "name": "保存", "cx": 1750, "cy": 500},
             {"id": "el_ge_upload", "type": "button", "name": "上传图片", "cx": 500, "cy": 460},
         ]},
        {"id": "s_ship_done", "name": "发货完成", "url_pattern": "/ship/done",
         "elements": [
             {"id": "el_sd_back", "type": "button", "name": "返回首页", "cx": 45, "cy": 120},
             {"id": "el_sd_continue", "type": "button", "name": "继续发货", "cx": 500, "cy": 400},
             {"id": "el_sd_ok", "type": "a", "name": "发货成功", "cx": 500, "cy": 300},
         ]},
    ],
    "edges": [
        {"id": "e1", "from_state": "s_home", "from_element": "el_nav_ship", "action": {"type": "click"}, "to_state": "s_pending"},
        {"id": "e2", "from_state": "s_home", "from_element": "el_nav_goods", "action": {"type": "click"}, "to_state": "s_goods_list"},
        {"id": "e3", "from_state": "s_home", "from_element": "el_nav_logistics", "action": {"type": "click"}, "to_state": "s_logistics"},
        {"id": "e4", "from_state": "s_home", "from_element": "el_nav_marketing", "action": {"type": "click"}, "to_state": "s_marketing"},
        {"id": "e5", "from_state": "s_home", "from_element": "el_nav_customer", "action": {"type": "click"}, "to_state": "s_customer"},
        {"id": "e6", "from_state": "s_pending", "from_element": "el_p_order1", "action": {"type": "click"}, "to_state": "s_order_detail"},
        {"id": "e7", "from_state": "s_pending", "from_element": "el_batch_ship", "action": {"type": "click"}, "to_state": "s_ship"},
        {"id": "e8", "from_state": "s_order_detail", "from_element": "el_od_confirm", "action": {"type": "click"}, "to_state": "s_ship"},
        {"id": "e9", "from_state": "s_order_detail", "from_element": "el_od_print", "action": {"type": "click"}, "to_state": "s_logistics"},
        {"id": "e10", "from_state": "s_ship", "from_element": "el_sh_submit", "action": {"type": "click"}, "to_state": "s_ship_done"},
        {"id": "e11", "from_state": "s_ship_done", "from_element": "el_sd_back", "action": {"type": "click"}, "to_state": "s_home"},
        {"id": "e12", "from_state": "s_goods_list", "from_element": "el_g1", "action": {"type": "click"}, "to_state": "s_goods_detail"},
        {"id": "e13", "from_state": "s_goods_detail", "from_element": "el_gd_edit", "action": {"type": "click"}, "to_state": "s_goods_edit"},
        {"id": "e14", "from_state": "s_goods_edit", "from_element": "el_ge_save", "action": {"type": "click"}, "to_state": "s_goods_list"},
        {"id": "e15", "from_state": "s_goods_detail", "from_element": "el_gd_back", "action": {"type": "click"}, "to_state": "s_goods_list"},
        {"id": "e16", "from_state": "s_logistics", "from_element": "el_l_back", "action": {"type": "click"}, "to_state": "s_home"},
        {"id": "e17", "from_state": "s_marketing", "from_element": "el_m_back", "action": {"type": "click"}, "to_state": "s_home"},
        {"id": "e18", "from_state": "s_customer", "from_element": "el_c_back", "action": {"type": "click"}, "to_state": "s_home"},
    ],
}


def main():
    errors = validate_graph(GRAPH)
    if errors:
        print("❌ 图非法：")
        for e in errors:
            print(f"   - {e}")
        sys.exit(1)

    layout = compute_layout(GRAPH, "s_home", layer_gap=700, plane_width=1100)
    tree = build_path_tree(GRAPH, "s_home")

    state_name = {s["id"]: s["name"] for s in GRAPH["states"]}
    elem_info = {}
    for s in GRAPH["states"]:
        for e in s.get("elements", []):
            elem_info[e["id"]] = {"name": e.get("name"), "type": e.get("type")}

    # 有出边的元素 = 操作点（前端放大 + 常显标签）
    out_count = {}
    for ed in GRAPH["edges"]:
        fe = ed.get("from_element")
        if fe:
            out_count[fe] = out_count.get(fe, 0) + 1

    # 边补语义：前端编辑模式需要「点哪个元素 → 走到哪个状态」
    edge_pos_by_id = {e["id"]: e for e in layout["edge_pos"]}
    render_edges = []
    for ed in GRAPH["edges"]:
        ep = edge_pos_by_id[ed["id"]]
        render_edges.append({
            "id": ed["id"],
            "action": (ed.get("action") or {}).get("type"),
            "from_element": ed.get("from_element"),
            "from_state": ed["from_state"],
            "to_state": ed["to_state"],
            "from": ep["from"],
            "to": ep["to"],
        })

    render_data = {
        "root": "s_home",
        "plane_width": layout["plane_width"],
        "plane_height": layout["plane_height"],
        "layer_gap": layout["layer_gap"],
        "states": [
            {"id": sid, "name": state_name[sid], **layout["state_pos"][sid]}
            for sid in sorted(layout["state_pos"])
        ],
        "elements": [
            {"id": eid, **elem_info.get(eid, {}), **layout["element_pos"][eid],
             "has_out": out_count.get(eid, 0) > 0}
            for eid in sorted(layout["element_pos"])
        ],
        "edges": render_edges,
        "path_tree": tree,
    }

    out = BASE / "proto" / "3d_data.js"
    out.write_text("window.DATA = " + json.dumps(render_data, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"✅ 生成 {out.name}（states={len(render_data['states'])} "
          f"elements={len(render_data['elements'])} edges={len(render_data['edges'])}）")
    print(f"   层分布: " + ", ".join(
        f"层{l}={sum(1 for s in render_data['states'] if s['layer'] == l)}个"
        for l in sorted({s["layer"] for s in render_data["states"]})
    ))


if __name__ == "__main__":
    main()
