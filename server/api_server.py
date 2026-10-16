"""
膜 UI 图谱 API 服务器

部署在 owlgugugu 服务器上，暴露 Obsidian UI 图谱的坐标数据。
任何客户端（膜、手机、其他 Agent）都可以调这个 API 获取 UI 坐标。

启动：
  python api_server.py                    # 默认 :8930
  python api_server.py --port 8931        # 自定义端口
  python api_server.py --readonly         # 只读模式（禁止修改坐标）

API 端点：
  GET  /api/v1/health                     → 服务状态
  GET  /api/v1/elements?software=千牛     → 所有控件列表
  GET  /api/v1/element/<path>             → 单个控件详情
  GET  /api/v1/pages?software=千牛        → 页面列表
  GET  /api/v1/page/<path>                → 页面详情+所有子控件
  GET  /api/v1/navigate?from=<a>&to=<b>   → 最短路径规划
  POST /api/v1/element/<path>/verify      → 手动触发验证
  GET  /api/v1/stats                      → 图谱统计
"""

import os
import sys
import json
import time
import logging
import argparse
import subprocess
from pathlib import Path
from datetime import datetime
from functools import lru_cache
from typing import Optional

from flask import Flask, jsonify, request, abort, Response

# ============================================================
# 配置
# ============================================================

UI_MAPS_ROOT = Path(os.environ.get("UI_MAPS_ROOT", Path(__file__).parent.parent.parent / "brain" / "ui_maps"))
PORT = int(os.environ.get("UI_API_PORT", 8930))
READONLY = os.environ.get("UI_API_READONLY", "0") == "1"

app = Flask(__name__)
log = logging.getLogger("ui_api")

# ---- SQLite 数据团（阶段 1）----
# store_db.py 在项目根 D:\AI\membrane，api_server.py 在 server/ 子目录
sys.path.insert(0, str(Path(__file__).parent.parent))
from store_db import StoreDB

DB_PATH = Path(os.environ.get("MEMBRANE_DB", Path(__file__).parent.parent / "membrane.db"))
db = StoreDB(DB_PATH)  # 线程安全（内部锁），Flask 多线程直接用


# ---- CORS：开发期 vite dev 跨域；生产 pywebview 加载时同源，加头无害 ----
@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp


@app.route("/<path:_>", methods=["OPTIONS"])
@app.route("/", methods=["OPTIONS"])
def _cors_preflight(_=None):
    return "", 204


# ============================================================
# 图谱读取（从 Obsidian Markdown 文件）
# ============================================================

@lru_cache(maxsize=256)
def _read_frontmatter(filepath: Path) -> dict:
    """读取 Markdown 文件的 YAML frontmatter（缓存）"""
    if not filepath.exists():
        return {}
    content = filepath.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}

    fm = {}
    in_fm = False
    for line in content.split("\n"):
        if line.strip() == "---":
            if not in_fm:
                in_fm = True
                continue
            else:
                break
        if in_fm and ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val.startswith("[") and val.endswith("]"):
                try:
                    val = json.loads(val)
                except json.JSONDecodeError:
                    pass
            try:
                val = int(val)
            except (ValueError, TypeError):
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
            fm[key] = val
    return fm


def _scan_elements(software: Optional[str] = None) -> list[dict]:
    """扫描图谱中所有控件节点"""
    elements = []
    search_root = UI_MAPS_ROOT / software if software else UI_MAPS_ROOT

    for md_file in search_root.rglob("*.md"):
        if md_file.name.startswith("_"):
            continue
        fm = _read_frontmatter(md_file)
        if fm.get("type") in ("button", "input", "link", "dropdown", "checkbox", "text", "image", "container"):
            elements.append({
                "path": fm.get("path", ""),
                "name": fm.get("name", md_file.stem),
                "type": fm["type"],
                "fingerprint": fm.get("fingerprint", ""),
                "bbox_abs": fm.get("bbox_abs", []),
                "state": fm.get("state", "draft"),
                "confidence": fm.get("confidence", 0.0),
                "last_verified": fm.get("last_verified", ""),
                "parent_page": fm.get("parent_page", ""),
            })

    return elements


def _scan_pages(software: Optional[str] = None) -> list[dict]:
    """扫描图谱中所有页面节点"""
    pages = []
    search_root = UI_MAPS_ROOT / software if software else UI_MAPS_ROOT

    for md_file in search_root.rglob("*.md"):
        if md_file.name.startswith("_"):
            continue
        fm = _read_frontmatter(md_file)
        if fm.get("type") == "page":
            # 收集该页面的子控件
            page_name = fm.get("name", md_file.stem)
            children = [e for e in _scan_elements(software) if page_name in e.get("parent_page", "")]
            pages.append({
                "name": page_name,
                "path": fm.get("path", ""),
                "fingerprint": fm.get("fingerprint", {}),
                "state": fm.get("state", "draft"),
                "last_verified": fm.get("last_verified", ""),
                "reachable_via": fm.get("reachable_via", []),
                "outgoing_links": fm.get("outgoing_links", []),
                "children_count": len(children),
                "children": children,
            })

    return pages


def _get_file_by_path(path_id: str) -> Optional[Path]:
    """按 tree path（如 '1.2.1'）查找文件"""
    for md_file in UI_MAPS_ROOT.rglob("*.md"):
        fm = _read_frontmatter(md_file)
        if fm.get("path") == path_id:
            return md_file
    return None


def _find_shortest_path(from_path: str, to_path: str) -> Optional[dict]:
    """
    BFS 最短路径规划

    在图谱中找从 from_path 到 to_path 的最短路径。
    考虑：父子关系（树）、跨链接（outgoing_links）、根节点回退
    """
    # 构建邻接表
    graph = {}
    all_pages = _scan_pages()

    for page in all_pages:
        p = page["path"]
        if p not in graph:
            graph[p] = []

        # 父子关系
        parts = p.split(".")
        if len(parts) > 1:
            parent = ".".join(parts[:-1])
            graph[p].append({"target": parent, "cost": 1, "type": "parent"})
            if parent not in graph:
                graph[parent] = []
            graph[parent].append({"target": p, "cost": 1, "type": "child"})

        # 根节点回退（任何节点→根=1）
        graph[p].append({"target": "1", "cost": 2, "type": "hub_return"})

        # 跨链接
        for link in page.get("outgoing_links", []):
            target = link.get("target", "").replace("[[", "").replace("]]", "")
            target_page = next((pg for pg in all_pages if pg["name"] == target), None)
            if target_page:
                graph[p].append({
                    "target": target_page["path"],
                    "cost": link.get("cost", 3),
                    "type": "cross_link",
                    "trigger": link.get("trigger", ""),
                })

    # BFS with costs (Dijkstra)
    if from_path not in graph or to_path not in graph:
        return None

    import heapq
    dist = {from_path: 0}
    prev = {from_path: None}
    path_type = {from_path: "start"}
    pq = [(0, from_path)]

    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        if u == to_path:
            break
        if u not in graph:
            continue
        for edge in graph[u]:
            v = edge["target"]
            nd = d + edge.get("cost", 1)
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = u
                path_type[v] = edge.get("type", "unknown")

    if to_path not in prev:
        return None

    # 重建路径
    path = []
    cur = to_path
    while cur is not None:
        path.append({"node": cur, "type": path_type.get(cur, "unknown")})
        cur = prev[cur]
    path.reverse()

    return {
        "path": path,
        "total_cost": dist[to_path],
        "steps": len(path) - 1,
    }


# ============================================================
# API 端点
# ============================================================

@app.route("/api/v1/health")
def health():
    """服务状态"""
    elements = _scan_elements()
    verified = [e for e in elements if e["state"] == "verified"]
    drifted = [e for e in elements if e["state"] == "drifted"]

    return jsonify({
        "status": "ok",
        "readonly": READONLY,
        "timestamp": datetime.now().isoformat(),
        "stats": {
            "total_elements": len(elements),
            "verified": len(verified),
            "drifted": len(drifted),
        },
    })


@app.route("/api/v1/elements")
def list_elements():
    """控件列表"""
    software = request.args.get("software")
    state = request.args.get("state")
    elements = _scan_elements(software)
    if state:
        elements = [e for e in elements if e["state"] == state]
    return jsonify({"count": len(elements), "elements": elements})


@app.route("/api/v1/element/<path:path_id>")
def get_element(path_id):
    """单个控件详情"""
    filepath = _get_file_by_path(path_id)
    if not filepath:
        abort(404, f"找不到节点: {path_id}")
    fm = _read_frontmatter(filepath)
    return jsonify(fm)


@app.route("/api/v1/pages")
def list_pages():
    """页面列表"""
    software = request.args.get("software")
    pages = _scan_pages(software)
    # 不返回 children 详情（太大），只返回摘要
    summary = [{k: v for k, v in p.items() if k != "children"} for p in pages]
    return jsonify({"count": len(pages), "pages": summary})


@app.route("/api/v1/page/<path:path_id>")
def get_page(path_id):
    """页面详情 + 所有子控件"""
    filepath = _get_file_by_path(path_id)
    if not filepath:
        abort(404, f"找不到页面: {path_id}")
    fm = _read_frontmatter(filepath)
    software = path_id.split("::")[0] if "::" in path_id else None

    # 找到该页面下所有控件
    page_name = fm.get("name", "")
    all_elements = _scan_elements(software)
    children = [e for e in all_elements if page_name in e.get("parent_page", "")]

    return jsonify({**fm, "children": children, "children_count": len(children)})


@app.route("/api/v1/navigate")
def navigate():
    """最短路径规划"""
    from_path = request.args.get("from")
    to_path = request.args.get("to")
    if not from_path or not to_path:
        abort(400, "需要 from 和 to 参数")

    _read_frontmatter.cache_clear()
    result = _find_shortest_path(from_path, to_path)
    if not result:
        abort(404, f"找不到路径: {from_path} → {to_path}")

    return jsonify({"from": from_path, "to": to_path, **result})


@app.route("/api/v1/stats")
def stats():
    """图谱统计"""
    elements = _scan_elements()
    pages = _scan_pages()

    by_state = {}
    for e in elements:
        by_state[e["state"]] = by_state.get(e["state"], 0) + 1

    by_type = {}
    for e in elements:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    by_software = {}
    for e in elements:
        sw = e.get("software", "未知")
        by_software[sw] = by_software.get(sw, 0) + 1

    return jsonify({
        "elements_total": len(elements),
        "pages_total": len(pages),
        "by_state": by_state,
        "by_type": by_type,
        "by_software": by_software,
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/v1/element/<path:path_id>/verify", methods=["POST"])
def trigger_verify(path_id):
    """手动触发单个控件验证（返回验证任务ID）"""
    if READONLY:
        abort(403, "只读模式")
    # TODO: 对接刷石机，触发即时验证
    return jsonify({
        "status": "queued",
        "path": path_id,
        "message": "验证任务已排入队列（TODO: 对接刷石机）",
    })


# ============================================================
# 数据团 API v2（SQLite + token 鉴权 + 租户过滤）
#   旧 /api/v1/* 是 brain/ui_maps markdown 图谱，保留不动；
#   新 /api/v2/* 读 membrane.db 数据团，施工图 §4 的 5 个端点。
# ============================================================

def _current_tenant():
    """从 Authorization: Bearer <token> 解析当前租户。
    有效 token → tenant_id；无/无效 → None（未登录，只看官方共享）。
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):].strip()
        tenant = db.get_tenant_by_token(token)
        return tenant["id"] if tenant else None
    return None


def _require_tenant():
    """写操作要求有效 token（否则不知道写谁名下），无 token → 401。"""
    tid = _current_tenant()
    if not tid:
        abort(401, "需要有效的 Authorization: Bearer <token>")
    return tid


@app.route("/api/v2/health")
def health_v2():
    tid = _current_tenant()
    return jsonify({
        "status": "ok",
        "tenant": tid,
        "db": str(DB_PATH),
        "stats": {
            "apps": len(db.list_apps(tid)),
            "pages": len(db.list_pages(tid)),
            "workflows": len(db.list_workflows(tid)),
        },
    })


@app.route("/api/v2/apps")
def list_apps_v2():
    """应用索引（官方 + 当前用户私有）"""
    tid = _current_tenant()
    apps = db.list_apps(tid)
    return jsonify({"tenant": tid, "count": len(apps), "apps": apps})


@app.route("/api/v2/pages")
def list_pages_v2():
    """列页面（自动过滤租户）；?app_id= 可筛选应用"""
    tid = _current_tenant()
    app_id = request.args.get("app_id")
    pages = db.list_pages(tid, app_id=app_id)
    # 只返回元数据摘要，不返回 skeleton/fingerprint（太大）
    summary = [{k: v for k, v in p.items() if k not in ("skeleton", "fingerprint")}
               for p in pages]
    return jsonify({"tenant": tid, "count": len(pages), "pages": summary})


@app.route("/api/v2/elements")
def list_elements_v2():
    """取元素：?page_id=<id> 或 ?app_id=&url_key= """
    tid = _current_tenant()
    page_id = request.args.get("page_id", type=int)
    app_id = request.args.get("app_id")
    url_key = request.args.get("url_key")
    if page_id:
        elems = db.list_elements(page_id)
    elif app_id and url_key:
        elems = db.list_elements_by_url_key(app_id, url_key, tid) or []
    else:
        abort(400, "需要 page_id 或 (app_id + url_key) 参数")
    return jsonify({"tenant": tid, "count": len(elems), "elements": elems})


@app.route("/api/v2/elements", methods=["POST"])
def upsert_element_v2():
    """存新元素（强制写当前用户 tenant_id）。body: {page_id, element}"""
    tid = _require_tenant()
    data = request.get_json(silent=True) or {}
    page_id = data.get("page_id")
    el = data.get("element")
    if not page_id or not isinstance(el, dict):
        abort(400, "需要 page_id + element 对象")
    db.upsert_element(page_id, el)
    return jsonify({"status": "ok", "tenant": tid, "page_id": page_id}), 201


@app.route("/api/v2/workflows")
def list_workflows_v2():
    """列工作流（自动过滤租户）"""
    tid = _current_tenant()
    wfs = db.list_workflows(tid)
    return jsonify({"tenant": tid, "count": len(wfs), "workflows": wfs})


@app.route("/api/v2/workflows", methods=["POST"])
def upsert_workflow_v2():
    """存工作流（强制写当前用户 tenant_id）。body: {id, name?, graph?}"""
    tid = _require_tenant()
    data = request.get_json(silent=True) or {}
    wf_id = data.get("id")
    if not wf_id:
        abort(400, "需要 id 字段")
    db.upsert_workflow(wf_id, data.get("name"), data.get("graph"), tid)
    return jsonify({"status": "ok", "tenant": tid, "id": wf_id}), 201


# ============================================================
# 膜总控台专用端点（复用 membrane.py / run_workflow.py 纯函数，零重写）
# ============================================================

@app.route("/api/v2/backend/status")
def backend_status_v2():
    """膜后端 4 组件状态（虚拟屏 / Edge CDP / 数据团 / 操控员），全部只读探测。"""
    import membrane
    lines, ok_all = membrane.check_backend()
    return jsonify({"ok": ok_all, "lines": lines})


@app.route("/api/v2/workflows/<wf_id>")
def get_workflow_v2(wf_id):
    """单条工作流（含 graph_json 解析后的 graph）。"""
    tid = _current_tenant()
    wf = db.get_workflow(wf_id, tid)
    if not wf:
        abort(404, f"工作流不存在: {wf_id}")
    return jsonify({"tenant": tid, "workflow": wf})


@app.route("/api/v2/workflows/<wf_id>/run", methods=["POST"])
def run_workflow_v2(wf_id):
    """执行工作流。body: {execute: bool}。干跑默认；真点需虚拟屏已激活 + 人盯场。

    复用 run_workflow.apply_display_bounds + build_engine + engine.run。
    真点是长任务（扫描 + 操控员校验 + 点击），同步阻塞，前端需 spinner。
    """
    tid = _current_tenant()
    wf = db.get_workflow(wf_id, tid)
    if not wf:
        abort(404, f"工作流不存在: {wf_id}")
    graph = wf.get("graph")
    if not graph:
        abort(400, "工作流没有 graph 数据")

    data = request.get_json(silent=True) or {}
    execute = bool(data.get("execute"))

    import run_workflow
    bounds = run_workflow.apply_display_bounds()
    engine = run_workflow.build_engine(graph, dry_run=not execute)
    log = engine.run()
    return jsonify({
        "wf_id": wf_id,
        "name": wf.get("name"),
        "dry_run": not execute,
        "bounds": bounds,
        "log": log,
    })


@app.route("/api/v2/scan/frame")
def scan_frame_v2():
    """副屏截图 + 元素标注（转成相对副屏的局部像素坐标，供前端叠加框）。

    截图读 launch_state.json 副屏 rect；元素复用 membrane_vision.detect_ui_elements。
    """
    import base64
    import io
    import run_workflow
    import membrane_vision as mv
    from PIL import ImageGrab

    b = run_workflow.apply_display_bounds()
    if not b:
        abort(400, "launch_state.json 不存在，副屏未激活（先 python launch_qianniu.py）")
    left, top, w, h = b["left"], b["top"], b["width"], b["height"]

    try:
        img = ImageGrab.grab(bbox=(left, top, left + w, top + h), all_screens=True)
    except Exception as e:
        abort(500, f"副屏截图失败: {e}")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode()

    result = mv.detect_ui_elements()
    elements = result.get("elements", []) if isinstance(result, dict) else []

    norm = []
    for el in elements:
        if "x1" in el:
            x, y = el["x1"], el["y1"]
            ww = el.get("x2", x) - x
            hh = el.get("y2", y) - y
        else:
            x, y = el.get("x", 0), el.get("y", 0)
            ww = el.get("w", 0)
            hh = el.get("h", 0)
        # detect_ui_elements 返回的元素坐标已是副屏局部坐标（UIA 的 _to_local、
        # CDP 的 scan_web_elements 都已减 VIRTUAL_SCREEN_LEFT），无需再减 left/top。
        # 只保留落在副屏内的元素（UIA 可能扫到主屏窗口）。
        if not (0 <= x < w and 0 <= y < h):
            continue
        cx = el.get("cx") or (x + ww // 2)
        cy = el.get("cy") or (y + hh // 2)
        norm.append({
            "name": el.get("name", ""),
            "type": str(el.get("type", "")),
            "x": x, "y": y, "w": ww, "h": hh,
            "cx": cx, "cy": cy,
        })

    return jsonify({
        "image_b64": image_b64,
        "width": w,
        "height": h,
        "count": len(norm),
        "elements": norm,
    })


@app.route("/api/v2/scan/stream")
def scan_stream_v2():
    """副屏实时流（MJPEG）：ffmpeg gdigrab 抓副屏 → multipart/x-mixed-replace。

    每个连接一个 ffmpeg 子进程（5fps），客户端断开时自动终止。前端 <img> 直接显示。
    """
    import run_workflow
    b = run_workflow.apply_display_bounds()
    if not b:
        abort(400, "launch_state.json 不存在，副屏未激活（先 python launch_qianniu.py）")
    left, top, w, h = b["left"], b["top"], b["width"], b["height"]

    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-f", "gdigrab", "-framerate", "5",
        "-offset_x", str(left), "-offset_y", str(top),
        "-video_size", f"{w}x{h}", "-i", "desktop",
        "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "12",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def gen():
        buf = b""
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    soi = buf.find(b"\xff\xd8")
                    if soi < 0:
                        break
                    eoi = buf.find(b"\xff\xd9", soi)
                    if eoi < 0:
                        break
                    frame = buf[soi : eoi + 2]
                    buf = buf[eoi + 2 :]
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                        + frame + b"\r\n"
                    )
        finally:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/v2/navmap")
def navmap_v2():
    """千牛全站导航拓扑（根→13 主页→88 子菜单，3D 布局坐标 + 路径树）。

    复用 proto/export_3d_map.py 的 load_db/build_render_data 纯函数（零重写），
    数据从 membrane.db 实时生成，不落 3d_data.js 文件。
    """
    import sys as _sys
    proto_dir = Path(__file__).parent.parent / "proto"
    if str(proto_dir) not in _sys.path:
        _sys.path.insert(0, str(proto_dir))
    import export_3d_map as m3

    # 保证读同一个库（默认都是 membrane.db；被 MEMBRANE_DB 覆盖时保持一致）
    m3.DB = DB_PATH
    try:
        mains, subs, elems_by_page = m3.load_db()
        data = m3.build_render_data(mains, subs, elems_by_page)
    except Exception as e:
        abort(500, f"导航数据生成失败: {e}")

    n_main = sum(1 for s in data["states"] if s["layer"] == 1)
    n_sub = sum(1 for s in data["states"] if s["layer"] == 2)
    return jsonify({
        "app": "qianniu",
        "stats": {
            "states": len(data["states"]),
            "main": n_main,
            "sub": n_sub,
            "elements": len(data["elements"]),
            "edges": len(data["edges"]),
        },
        "data": data,
    })


@app.route("/api/v2/render/status")
def render_status_v2():
    """ComfyUI 渲染后端状态检测（:8188），只读探测，不触发渲染。"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=2) as r:
            j = json.loads(r.read().decode())
        return jsonify({"ok": True, "url": "http://127.0.0.1:8188", "system": j})
    except Exception as e:
        return jsonify({"ok": False, "url": "http://127.0.0.1:8188",
                        "error": f"ComfyUI 未运行: {e}"})


# ============================================================
# 错误处理
# ============================================================

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "not_found", "detail": str(e)}), 404


@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "bad_request", "detail": str(e)}), 400


@app.errorhandler(403)
def forbidden(e):
    return jsonify({"error": "forbidden", "detail": str(e)}), 403


@app.errorhandler(401)
def unauthorized(e):
    return jsonify({"error": "unauthorized", "detail": str(e)}), 401


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="膜 UI 图谱 API 服务器")
    parser.add_argument("--port", type=int, default=PORT, help=f"端口（默认 {PORT}）")
    parser.add_argument("--readonly", action="store_true", help="只读模式")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    args = parser.parse_args()

    global READONLY
    READONLY = args.readonly or READONLY

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    print(f"""
╔══════════════════════════════════════════════╗
║      膜 API 服务器（图谱 v1 + 数据团 v2）      ║
╠══════════════════════════════════════════════╣
║  数据团库: {DB_PATH}
║  监听地址: http://{args.host}:{args.port}
║  模式:     {'只读' if READONLY else '读写'}
║  ── 数据团 v2（SQLite + token 鉴权）─────────
║   GET  /api/v2/health / apps / pages
║   GET  /api/v2/elements?page_id=
║   POST /api/v2/elements / workflows
║   GET  /api/v2/workflows
║  ── 图谱 v1（markdown，无鉴权）───────────────
║   GET  /api/v1/health / elements / pages
║         /api/v1/navigate / stats
╚══════════════════════════════════════════════╝
""")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
