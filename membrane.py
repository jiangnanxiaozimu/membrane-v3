"""
membrane.py — 膜统一入口（统筹编排层）

膜的完整生命周期：启动检查 → 工作流库 → 选工作流 → 执行 → 报告

统筹性设计（数据驱动，不硬编码任何一条流程）：
  - 工作流 = 数据（graph_json），存 store_db.workflows 表
    加一条新工作流 = 往库里写一条数据，不改本文件代码
  - 执行 = 通用引擎（run_workflow.build_engine：真眼睛 + 真手 + 真裁判）
  - 生成 = 数据团 → graph_json（gen_workflow_from_db.build_multistep）
  - 本文件只做「编排」，具体能力全在已有零件里，不另起炉灶

用法：
  python membrane.py                  交互式菜单
  python membrane.py status           检查后端状态（虚拟屏 / CDP / 数据团 / 操控员）
  python membrane.py list             列出工作流库
  python membrane.py gen              生成种子工作流（千牛 10 步巡检）写入库
  python membrane.py run <name>       干跑（默认，不动鼠标）
  python membrane.py run <name> --execute    真点（需虚拟屏已激活 + 人盯着）
  python membrane.py add              添加工作流（占位：暂未开发，敬请期待）
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from store_db import StoreDB
import gen_workflow_from_db
import run_workflow
from workflow.model import validate_graph


# ============================================================
# 种子工作流：千牛 10 步巡检
# 全部元素名 / 目标页来自 membrane.db 数据团真实爬取结果（零幻觉）。
# 这是「初始种子数据」，不是代码——真正的扩展入口是 add（暂未开发）。
# ============================================================
SEED_WORKFLOWS = {
    "qianniu_10step": {
        "name": "千牛10步巡检",
        "description": "跨 交易/商品/营销/财务 四导航，10 次真实点击巡检，验证膜连续操作能力",
        "steps": [
            ("nav", "交易"),
            ("sub", "发货", "1.3"),
            ("nav", "商品"),
            ("sub", "我的商品", "1.4"),
            ("sub", "仓储库存", "1.4"),
            ("nav", "营销"),
            ("sub", "营销工具", "1.5"),
            ("nav", "财务"),
            ("sub", "账户明细", "1.10"),
            ("sub", "收支账单", "1.10"),
        ],
    },
}


# ============================================================
# 状态检查（全部只读，零幻觉：以文件 / HTTP / SQLite 实际结果为准）
# ============================================================
def _http_json(url, timeout=3):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def check_backend():
    """检查膜后端 4 组件状态，返回 (行列表, 全部就绪 bool)。"""
    lines = []
    ok_all = True
    root = os.path.dirname(os.path.abspath(__file__))

    # 1. 虚拟屏（真实枚举显示器数量；launch_state.json 只是历史记录，不能证明当前激活）
    import ctypes
    n_monitors = ctypes.windll.user32.GetSystemMetrics(80)  # SM_CMONITORS
    state_file = os.path.join(root, "launch_state.json")
    if n_monitors >= 2:
        lines.append(f"[✓] 虚拟屏  已激活（{n_monitors} 个显示器，含副屏）")
    elif os.path.exists(state_file):
        lines.append(f"[✗] 虚拟屏  未激活（仅 {n_monitors} 个显示器；launch_state.json 是历史记录，"
                     f"`python launch_qianniu.py` 重建）")
        ok_all = False
    else:
        lines.append(f"[✗] 虚拟屏  未创建（{n_monitors} 个显示器）")
        ok_all = False

    # 2. Edge CDP（9222）
    tabs = _http_json("http://127.0.0.1:9222/json")
    if tabs:
        pages = [t for t in tabs if t.get("type") == "page"]
        qn = [t for t in pages if "myseller" in (t.get("url") or "")]
        lines.append(f"[✓] Edge CDP  9222 在线，{len(pages)} 个页面（千牛 {len(qn)} 个）")
    else:
        lines.append("[✗] Edge CDP  9222 未连接")
        ok_all = False

    # 3. 数据团（membrane.db）
    db = StoreDB()
    try:
        pages = len(db.list_pages(app_id="qianniu"))
    finally:
        db.close()
    import sqlite3
    db_path = os.path.join(root, "membrane.db")
    if os.path.exists(db_path):
        with sqlite3.connect(db_path) as c:
            ecount = c.execute("SELECT COUNT(*) FROM elements").fetchone()[0]
        lines.append(f"[✓] 数据团  {pages} 页 / {ecount} 元素")
    else:
        lines.append("[✗] 数据团  membrane.db 不存在")
        ok_all = False

    # 4. 大脑（DeepSeek Flash，云端 API）
    ds_key = os.environ.get("MEMBRANE_DEEPSEEK_KEY", "")
    if ds_key:
        lines.append("[✓] DeepSeek Flash  已配置（MEMBRANE_DEEPSEEK_KEY）")
    else:
        lines.append("[✗] DeepSeek Flash  未配置（设置 MEMBRANE_DEEPSEEK_KEY，engine 会放行）")
        ok_all = False

    # 5. 眼睛（本地 Ollama 11435，qwen3-vl:8b 视觉模型）
    tags = _http_json("http://127.0.0.1:11435/api/tags", timeout=2)
    if tags:
        names = [m.get("name", "") for m in tags.get("models", [])]
        has_vl = any("qwen3-vl" in n or "qwen3:8b" in n for n in names)
        lines.append(f"[{'✓' if has_vl else '△'}] Ollama  11435 在线，模型 {len(names)} 个（视觉 qwen3-vl）")
    else:
        lines.append("[✗] Ollama  11435 未连接（视觉 qwen3-vl 不可用，scan 会降级）")
        ok_all = False

    return lines, ok_all


# ============================================================
# 工作流库（数据驱动：读 store_db.workflows 表）
# ============================================================
def list_workflows():
    db = StoreDB()
    try:
        rows = db.list_workflows()
    finally:
        db.close()
    return rows


def get_workflow(wf_id):
    db = StoreDB()
    try:
        return db.get_workflow(wf_id)
    finally:
        db.close()


def add_workflow():
    """添加工作流 —— 占位。将来接工作流编辑器（LiteGraph/React Flow）或手工 JSON。"""
    return {
        "ok": False,
        "message": "添加工作流：暂未开发，敬请期待。",
        "hint": "当前可先用 `python membrane.py gen` 从数据团生成，"
                "或直接往 workflows 表 upsert 一条 graph_json（见 store_db.upsert_workflow）。",
    }


# ============================================================
# 生成种子工作流：数据团 → graph_json → 入库
# ============================================================
def gen_seed_workflows():
    db = StoreDB()
    results = []
    try:
        for wf_id, meta in SEED_WORKFLOWS.items():
            graph = gen_workflow_from_db.build_multistep(db, meta["steps"])
            graph["description"] = meta["description"]
            errors = validate_graph(graph)
            if errors:
                results.append((wf_id, False, "; ".join(errors)))
                continue
            db.upsert_workflow(wf_id, meta["name"], graph)
            n_edges = len(graph.get("edges", []))
            results.append((wf_id, True, f"{meta['name']}（{n_edges} 步）"))
    finally:
        db.close()
    return results


# ============================================================
# 执行：读库 → 设扫描边界 → 通用引擎 → 跑 → 报告
# ============================================================
def run_one(wf_id, execute=False):
    wf = get_workflow(wf_id)
    if not wf:
        return {"ok": False, "message": f"工作流不存在: {wf_id}（先 `python membrane.py list`）"}
    graph = wf.get("graph")
    if not graph:
        return {"ok": False, "message": f"工作流 {wf_id} 没有 graph 数据"}

    bounds = run_workflow.apply_display_bounds()
    engine = run_workflow.build_engine(graph, dry_run=not execute)
    log = engine.run()
    return {
        "ok": True,
        "wf_id": wf_id,
        "name": wf.get("name"),
        "dry_run": not execute,
        "bounds": bounds,
        "log": log,
    }


def fmt_report(result):
    """把执行结果格式化成给用户看的中文报告。"""
    if not result.get("ok"):
        return f"[错误] {result.get('message')}"

    lines = [
        f"== 工作流「{result['name']}」 {'（干跑，未真点）' if result['dry_run'] else '（真执行！）'} ==",
    ]
    if result.get("bounds"):
        b = result["bounds"]
        lines.append(f"   扫描边界: 副屏 ({b['left']},{b['top']}) {b['width']}x{b['height']}")
    lines.append(run_workflow._fmt_log(result["log"]))

    # 汇总
    log = result["log"]
    n_exec = sum(1 for r in log if r.get("type") in ("executed", "already_there"))
    n_fail = sum(1 for r in log if r.get("type") in ("not_found", "rejected", "migration_failed"))
    lines.append(f"--- 汇总：成功 {n_exec} 步 / 失败 {n_fail} 步 / 共 {len(log)} 条日志 ---")
    return "\n".join(lines)


# ============================================================
# 交互式菜单（无参数启动）
# ============================================================
def interactive():
    while True:
        print()
        print("=" * 46)
        print("  膜 Membrane · 统筹编排层")
        print("=" * 46)
        print("  1. status  检查后端状态")
        print("  2. list    列出工作流库")
        print("  3. gen     生成种子工作流（千牛10步）")
        print("  4. run     执行一条工作流（干跑）")
        print("  5. add     添加工作流")
        print("  0. 退出")
        print("-" * 46)
        choice = input("  选择 [0-5] > ").strip()

        if choice == "0":
            print("再见。")
            break
        elif choice == "1":
            lines, ok = check_backend()
            print("\n".join(lines))
            print("后端就绪" if ok else "后端有组件未就绪（干跑仍可，真点需先补）")
        elif choice == "2":
            rows = list_workflows()
            if not rows:
                print("工作流库为空。先 `gen` 生成种子，或 `add` 添加。")
            for r in rows:
                n = len((r.get("graph") or {}).get("edges", []))
                print(f"  · {r['id']}  {r['name']}（{n} 步）更新于 {r.get('updated_at')}")
        elif choice == "3":
            for wf_id, ok, msg in gen_seed_workflows():
                print(f"  [{'✓' if ok else '✗'}] {wf_id}  {msg}")
        elif choice == "4":
            rows = list_workflows()
            if not rows:
                print("工作流库为空，先 gen。")
                continue
            for i, r in enumerate(rows):
                print(f"  {i}. {r['id']}  {r['name']}")
            try:
                idx = int(input("  执行哪条 [编号] > ").strip())
                wf_id = rows[idx]["id"]
            except (ValueError, IndexError):
                print("  编号无效。")
                continue
            result = run_one(wf_id, execute=False)
            print(fmt_report(result))
        elif choice == "5":
            r = add_workflow()
            print(f"  [暂未开发] {r['message']}")
            print(f"  {r['hint']}")
        else:
            print("  输入无效。")


# ============================================================
# CLI 入口
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="膜统一入口（统筹编排层）")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("status", help="检查后端状态")
    sub.add_parser("list", help="列出工作流库")
    sub.add_parser("gen", help="生成种子工作流写入库")
    sub.add_parser("add", help="添加工作流（占位）")

    p_run = sub.add_parser("run", help="执行工作流")
    p_run.add_argument("name", help="工作流 id")
    p_run.add_argument("--execute", action="store_true", help="真点（默认干跑）")

    args = ap.parse_args()

    if args.cmd == "status":
        lines, ok = check_backend()
        print("\n".join(lines))
    elif args.cmd == "list":
        rows = list_workflows()
        if not rows:
            print("工作流库为空。")
        for r in rows:
            n = len((r.get("graph") or {}).get("edges", []))
            print(f"  {r['id']}  {r['name']}（{n} 步）更新于 {r.get('updated_at')}")
    elif args.cmd == "gen":
        for wf_id, ok, msg in gen_seed_workflows():
            print(f"[{'✓' if ok else '✗'}] {wf_id}  {msg}")
    elif args.cmd == "run":
        print(fmt_report(run_one(args.name, execute=args.execute)))
    elif args.cmd == "add":
        r = add_workflow()
        print(f"[暂未开发] {r['message']}")
        print(r["hint"])
    else:
        interactive()


if __name__ == "__main__":
    main()
