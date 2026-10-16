"""
gen_workflow_from_db.py — 从数据团（membrane.db）生成真实工作流图（graph_json）

解决断点：执行引擎（run_workflow.py）跑的是手填的 SAMPLE_GRAPH 假数据，
数据团里 102 页真实元素（已补漏校验）没被消费。本脚本把数据团真实元素抽成
graph_json（语义锚 = type+name），交给执行引擎跑。

支持「导航 + 子菜单」混合多步路径：点全局导航 → 点子菜单 → 再点子菜单 …，
到达一个需要多步点击才能进去的深层页面。

用法：
  python gen_workflow_from_db.py                # 生成 3 条抽样测试路径
  python gen_workflow_from_db.py --out x.json --steps 交易:发货   # 自定义

step 语法（--steps 用分号分隔，每步用「种类:名字[:所属导航]」）：
  nav:财务                点全局导航「财务」
  sub:账户明细:1.10       点财务(1.10)下的子菜单「账户明细」
"""
import argparse
import json

from store_db import StoreDB

DOMAIN = "https://myseller.taobao.com"

# 左侧全局导航：name -> 数据团导航首页 url_key
NAV_TO_URLKEY = {
    "首页": "1.1",
    "交易": "1.3",
    "商品": "1.4",
    "营销": "1.5",
    "客服": "1.7",
    "店铺": "1.8",
    "私域": "1.9",
    "财务": "1.10",
    "服务": "1.13",
}


def _get_page_url(db, url_key):
    """读某页面的真实 page_url（skeleton 里）。"""
    p = db.get_page_by_url_key("qianniu", url_key)
    if p and isinstance(p.get("skeleton"), dict):
        return p["skeleton"].get("page_url")
    return None


def _get_submenu(db, nav_url_key, sub_name):
    """读某导航首页里的子菜单 <a> 元素，返回 (语义锚 dict, 目标绝对 URL)。"""
    p = db.get_page_by_url_key("qianniu", nav_url_key)
    if not p:
        return None
    for e in db.list_elements(p["id"]):
        if e.get("type") == "a" and (e.get("name") or "").strip() == sub_name and e.get("href"):
            href = e["href"]
            if href.startswith("/"):
                href = DOMAIN + href
            return {"type": "a", "name": sub_name}, href
    return None


def _resolve_step(db, step):
    """把一步描述解析成 (元素语义锚 dict, 目标 page_url, 显示名)。

    step 为 ("nav", name) 或 ("sub", name, nav_url_key)。
    """
    kind = step[0]
    if kind == "nav":
        name = step[1]
        url_key = NAV_TO_URLKEY.get(name)
        if not url_key:
            raise ValueError(f"未知导航名: {name}")
        return {"type": "a", "name": name}, _get_page_url(db, url_key), name
    if kind == "sub":
        name, nav_key = step[1], step[2]
        r = _get_submenu(db, nav_key, name)
        if not r:
            raise ValueError(f"导航 {nav_key} 下找不到子菜单「{name}」")
        elem, to_url = r
        return elem, to_url, name
    raise ValueError(f"未知 step 种类: {kind}")


def build_multistep(db, steps):
    """steps = [("nav", name) | ("sub", name, nav_key), ...] → 链式 graph_json。

    每个 state 只放「从它出发要点的那一个元素」（语义锚），
    url_pattern 存该 state 的真实 page_url，供执行引擎迁移验证。
    """
    resolved = [_resolve_step(db, s) for s in steps]
    n = len(resolved)

    states = []
    for i in range(n + 1):
        if i == 0:
            # 起点 = 当前任意千牛页，不锚定具体页
            elem = resolved[0][0]
            states.append({
                "id": "s_0", "name": "起点（任意千牛页）", "url_pattern": None, "layer": 0,
                "elements": [{"id": "s_0_el", "type": elem["type"], "name": elem["name"]}],
            })
        else:
            prev_elem, prev_url, prev_name = resolved[i - 1]
            elems = []
            if i < n:
                next_elem = resolved[i][0]
                elems = [{"id": f"s_{i}_el", "type": next_elem["type"], "name": next_elem["name"]}]
            states.append({
                "id": f"s_{i}", "name": prev_name, "url_pattern": prev_url, "layer": i,
                "elements": elems,
            })

    edges = []
    for i in range(n):
        edges.append({
            "id": f"e_{i}", "from_state": f"s_{i}", "from_element": f"s_{i}_el",
            "action": {"type": "click"}, "input": None, "to_state": f"s_{i+1}",
        })

    return {
        "version": 1,
        "app_id": "qianniu",
        "viewport": {"width": 1920, "height": 1080},
        "states": states,
        "edges": edges,
        "layout": {"layer_gap": 400, "states_pos": {}},
    }


# 3 条抽样测试路径：跨财务/商品/交易，每条 3 步（点导航→点子菜单→再点子菜单）
SAMPLE_PATHS = {
    "finance": [("nav", "财务"), ("sub", "账户明细", "1.10"), ("sub", "收支账单", "1.10")],
    "goods":   [("nav", "商品"), ("sub", "仓储库存", "1.4"), ("sub", "SKU管理", "1.4")],
    "trade":   [("nav", "交易"), ("sub", "退款管理", "1.3"), ("sub", "发货", "1.3")],
}


def main():
    ap = argparse.ArgumentParser(description="从数据团生成真实工作流图（支持导航+子菜单多步）")
    ap.add_argument("--out", default=None, help="输出 JSON 路径（缺省按路径名输出）")
    ap.add_argument("--name", default=None, help="抽样路径名（finance/goods/trade）")
    ap.add_argument("--steps", default=None, help="自定义 step：nav:财务;sub:账户明细:1.10")
    args = ap.parse_args()

    db = StoreDB()
    try:
        if args.steps:
            # 解析自定义 step
            steps = []
            for tok in args.steps.split(";"):
                parts = tok.split(":")
                if parts[0] == "nav":
                    steps.append(("nav", parts[1]))
                elif parts[0] == "sub":
                    steps.append(("sub", parts[1], parts[2]))
                else:
                    raise ValueError(f"非法 step: {tok}")
            graph = build_multistep(db, steps)
            out = args.out or "wf_custom.json"
            _emit(graph, out)
            return

        # 默认：生成 3 条抽样路径
        names = [args.name] if args.name else list(SAMPLE_PATHS.keys())
        for nm in names:
            graph = build_multistep(db, SAMPLE_PATHS[nm])
            out = args.out or f"wf_test_{nm}.json"
            _emit(graph, out)
    finally:
        db.close()


def _emit(graph, out):
    with open(out, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)
    print(f"== 生成 {out} ==")
    for e in graph["edges"]:
        src = graph["states"][int(e["from_state"].split("_")[1])]
        dst = graph["states"][int(e["to_state"].split("_")[1])]
        print(f"  {src['name']} --点[{e['from_element']}]--> {dst['name']}")


if __name__ == "__main__":
    main()
