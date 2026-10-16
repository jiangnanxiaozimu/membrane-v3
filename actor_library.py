"""
膜 2.2 · 演员库 + 匹配引擎（「数据团」）

核心思想：每个网址 / 界面一个「演员」——记录该界面的元素（类型 + 文本 + 坐标）。
扫描当前 UI 后提取特征指纹，去库里匹配：
  - 命中（误差容忍内）→ 直接用库里的演员数据，不用重新标注
  - 未命中           → 用本次扫描新建一个演员入库

库慢慢完善后，扫到见过的界面就直接复用，越用越省事。

纯逻辑模块，无 UI，可独立 import / 单测。
"""
import json
import re
from datetime import datetime
from pathlib import Path

# ---- 可调参数 ----
DEFAULT_TOL_PX = 4          # 坐标容差（像素）：用户实测每次有 3~4px 偏移
DEFAULT_MATCH_RATIO = 0.7   # 命中阈值：≥70% 元素对上就算「同一个界面」


def url_key(url: str) -> str:
    """URL → 演员库文件名 key（域名 + 首段路径，去非法字符）。

    例如 https://www.deepseek.com/en/ → www.deepseek.com_en
    """
    if not url:
        return "unknown"
    s = re.sub(r'^https?://', '', url).rstrip('/')
    parts = s.split('/')
    base = parts[0] if parts else ""
    sub = parts[1] if len(parts) > 1 and parts[1] else ""
    key = f"{base}_{sub}" if sub else base
    key = re.sub(r'[^A-Za-z0-9._-]', '_', key)
    return key.strip('._') or "unknown"


def _element_key(el: dict):
    """元素指纹键：类型 + 文本（坐标在匹配时单独比，带容差）。"""
    return (el.get('type', ''), (el.get('name', '') or '').strip())


def match_ratio(scan_elements, actor_elements, tol_px=DEFAULT_TOL_PX):
    """计算「扫描元素 → 库演员元素」的匹配率。

    每个扫描元素在库里找一个「类型相同 + 文本相同 + 坐标逐轴差 ≤ tol_px」的元素。
    返回 (ratio, matched, total)。
    """
    if not scan_elements:
        return 0.0, 0, 0

    # 预索引：库元素按 (type, name) 分组，避免 O(n*m) 全量比对
    index = {}
    for ae in actor_elements:
        index.setdefault(_element_key(ae), []).append(ae)

    matched = 0
    for se in scan_elements:
        candidates = index.get(_element_key(se), [])
        scx = se.get('cx', se.get('x', 0))
        scy = se.get('cy', se.get('y', 0))
        best = None
        best_dist = None
        for ae in candidates:
            acx = ae.get('cx', ae.get('x', 0))
            acy = ae.get('cy', ae.get('y', 0))
            # 逐轴容差：元素整体平移，x / y 各自偏移 ≤ tol_px 才算同一个点
            # （不用欧氏距离 —— 3px x + 3px y 偏移时欧氏=√18≈4.24 会误判超容差）
            dx = abs(scx - acx)
            dy = abs(scy - acy)
            if dx <= tol_px and dy <= tol_px:
                d = dx + dy  # 曼哈顿距离，容差内取最近
                if best_dist is None or d < best_dist:
                    best = ae
                    best_dist = d
        if best is not None:
            matched += 1

    total = len(scan_elements)
    return matched / total, matched, total


class ActorLibrary:
    """演员库：加载 / 匹配 / 写入 / 从 HTML 导入。"""

    def __init__(self, actors_dir):
        self.actors_dir = Path(actors_dir)
        self.actors_dir.mkdir(parents=True, exist_ok=True)
        self.actors = {}  # url_key -> actor dict

    # ---- 存取 ----
    def load(self):
        self.actors = {}
        for f in self.actors_dir.glob('*.json'):
            try:
                actor = json.loads(f.read_text(encoding='utf-8'))
                key = actor.get('url_key') or f.stem
                self.actors[key] = actor
            except Exception:
                continue
        return self.actors

    def _save(self, key):
        actor = self.actors[key]
        (self.actors_dir / f"{key}.json").write_text(
            json.dumps(actor, ensure_ascii=False, indent=2), encoding='utf-8')

    def load_from_sqlite(self, tenant_id=None, db_path=None):
        """从 SQLite 数据团（membrane.db）加载演员，替代 load()（阶段 1）。

        key = f"{app_id}:{url_key}"（全局唯一，避免跨 app 撞车）。
        每个 actor dict 含 app_id/url_key/elements；元素已是演员格式（type/name/cx/cy）。
        tenant_id 过滤：官方共享(NULL) + 本人私有；None = 只看官方。
        match_ratio() 逻辑不变，仍消费 elements 里的 type/name/cx/cy。
        """
        from store_db import StoreDB
        db = StoreDB(db_path)
        try:
            self.actors = {}
            for page in db.list_pages(tenant_id):
                app_id = page["app_id"]
                url_key = page["url_key"]
                key = f"{app_id}:{url_key}"
                skeleton = page.get("skeleton") or {}
                actor = {
                    "app_id": app_id,
                    "url_key": url_key,
                    "elements": db.list_elements(page["id"]),
                }
                if isinstance(skeleton, dict):
                    if skeleton.get("url"):
                        actor["url"] = skeleton["url"]
                    if skeleton.get("viewport"):
                        actor["viewport"] = skeleton["viewport"]
                self.actors[key] = actor
            return self.actors
        finally:
            db.close()

    # ---- 匹配 ----
    def match(self, scan_data, tol_px=DEFAULT_TOL_PX, ratio_threshold=DEFAULT_MATCH_RATIO):
        """用扫描数据去库里匹配。

        返回 (actor, ratio, matched, total)：
          - 命中：actor 非 None，ratio ≥ 阈值
          - 未命中：actor 为 None（ratio 为最接近的候选匹配率，供诊断）
        """
        scan_els = scan_data.get('elements', [])
        url = (scan_data.get('meta') or {}).get('url', '')
        key = url_key(url)

        # 先按 url_key 粗筛，命中该 key 的演员优先；无则全库兜底（URL 变了但界面没变）
        if key in self.actors:
            candidates = [self.actors[key]]
        else:
            candidates = list(self.actors.values())

        best_actor = None
        best_ratio = -1.0
        best_info = (0, 0)
        for actor in candidates:
            ratio, matched, total = match_ratio(scan_els, actor.get('elements', []), tol_px)
            if ratio > best_ratio:
                best_ratio = ratio
                best_actor = actor
                best_info = (matched, total)

        if best_actor is not None and best_ratio >= ratio_threshold:
            return best_actor, best_ratio, best_info[0], best_info[1]
        return None, best_ratio, best_info[0], best_info[1]

    # ---- 写入 ----
    def upsert(self, scan_data):
        """未命中时：把本次扫描存成新演员（或更新同 key 演员，计数 +1）。"""
        url = (scan_data.get('meta') or {}).get('url', '')
        key = url_key(url)
        now = datetime.now().isoformat(timespec='seconds')

        actor = {
            "url_key": key,
            "url": url,
            "viewport": (scan_data.get('meta') or {}).get('viewport', {}),
            "elements": scan_data.get('elements', []),
            "created_at": now,
            "updated_at": now,
            "scan_count": 1,
        }
        if key in self.actors:
            old = self.actors[key]
            actor['created_at'] = old.get('created_at', now)
            actor['scan_count'] = old.get('scan_count', 0) + 1

        self.actors[key] = actor
        self._save(key)
        return actor

    # ---- 导入 ----
    def import_from_html(self, html_path):
        """从 wireframe 生成的演员 HTML 里提取 membrane-data JSON，导入库。"""
        html = Path(html_path).read_text(encoding='utf-8')
        m = re.search(
            r'<script type="application/json" id="membrane-data">\s*([\s\S]*?)\s*</script>',
            html,
        )
        if not m:
            raise ValueError(f"未找到 membrane-data 数据块: {html_path}")
        data = json.loads(m.group(1))  # render_html 里的 <\/ 是合法 JSON 转义，直接 loads
        return self.upsert(data)
