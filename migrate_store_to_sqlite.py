"""
migrate_store_to_sqlite.py — 一次性迁移脚本（阶段 1）

把现有 JSON 数据灌进 membrane.db（SQLite）：
  store/index.json + store/apps/qianniu/{skeleton,fingerprints,pages/*}  → apps / pages / elements
  actors/*.json                                                          → apps / pages / elements

关键决策（对应 REPLAN_STAGE1.md）：
1. 所有官方数据 tenant_id = NULL（人人可读）。两个测试用户 user_new/user_old 只建号、先不写私有数据。
2. 两套元素格式统一成「演员格式」：DOM 扫描 {tag/cls/id/role/text/x/y/w/h} 归一化为
   {type=tag, name=text, x/y/w/h, cx/cy 补齐}，原始字段全保留在元素 dict 里（features_json 存全量）。
   actors 的 {type/name/cx/cy} 已是统一格式，原样保留。
   → 这样 actor_library.match_ratio()（用 type/name/cx/cy）未来直接可用。
3. 千牛页面 url_key 用 nav_id（"1.1"~"1.13"）/ parent_nav_id_sub_index（"1.3_0"），
   因为千牛是 SPA，URL 前缀相同，url_key() 会撞车。
4. store/index.json 引用的 manifest.json 缺失 → 用 index 信息 + skeleton + fingerprints 重建 manifest，
   标记 _manifest_missing=True，不 crash。

幂等：可重复跑（upsert 覆盖、replace_elements 整页替换），旧 JSON 文件不动。
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store_db import StoreDB

BASE = Path(__file__).resolve().parent
STORE = BASE / "store"
ACTORS = BASE / "actors"
QN_PAGES = STORE / "apps" / "qianniu" / "pages"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_element(el):
    """DOM 扫描元素 → 演员统一格式；已是演员格式（有 type）则原样返回。"""
    if "type" in el:
        return el  # actors 格式，原样
    x, y, w, h = el.get("x", 0), el.get("y", 0), el.get("w", 0), el.get("h", 0)
    norm = {
        "type": el.get("tag", ""),
        "name": (el.get("text") or "").strip(),
        "x": x, "y": y, "w": w, "h": h,
        "cx": x + w / 2.0, "cy": y + h / 2.0,
    }
    norm.update(el)  # 原始 tag/cls/id/role/text/x/y/w/h 全保留
    return norm


def migrate():
    db = StoreDB()  # 默认 D:\AI\membrane\membrane.db
    stats = {"apps": 0, "pages": 0, "elements": 0,
             "raw_pages": 0, "sub_pages": 0, "home_pages": 0, "actor_pages": 0}

    # ---- 1. tenants：两个测试用户 ----
    tokens = {}
    for tid, name in (("user_new", "新电脑"), ("user_old", "旧电脑")):
        tok = uuid.uuid4().hex
        tokens[tid] = tok
        db.upsert_tenant(tid, name, tok)

    # ---- 2. apps ----
    # 2a. qianniu（官方共享）—— manifest.json 缺失，用 index + skeleton + fingerprints 重建
    index = load_json(STORE / "index.json")
    qn = index["apps"]["qianniu"]
    manifest = dict(qn)
    manifest["skeleton"] = load_json(STORE / "apps" / "qianniu" / "skeleton.json")
    manifest["fingerprints"] = load_json(STORE / "apps" / "qianniu" / "fingerprints.json")
    manifest["_manifest_missing"] = True
    db.upsert_app("qianniu", qn.get("name", "千牛卖家工作台"), manifest)
    stats["apps"] += 1

    # 2b. actors（官方共享）—— 每个 actor 一个 app
    for af in sorted(ACTORS.glob("*.json")):
        actor = load_json(af)
        url_key = actor.get("url_key") or af.stem
        # 施工图例子用 'deepseek_web'；通用起见 app_id = url_key（域名+首段）
        app_id = url_key
        db.upsert_app(app_id, url_key, {
            "url": actor.get("url", ""),
            "viewport": actor.get("viewport", {}),
            "source": "actors/" + af.name,
        })
        stats["apps"] += 1

    # ---- 3. qianniu raw 页（13 个一级导航） ----
    for rf in sorted(QN_PAGES.glob("page_1_*_raw.json")):
        d = load_json(rf)
        url_key = d.get("nav_id") or rf.stem  # "1.1"~"1.13"
        skeleton = {
            "nav_id": d.get("nav_id"), "nav_text": d.get("nav_text"),
            "page_title": d.get("page_title"), "page_url": d.get("page_url"),
            "element_count": d.get("element_count"),
        }
        pid = db.upsert_page("qianniu", url_key, skeleton=skeleton, fingerprint=None)
        elems = [normalize_element(e) for e in d.get("elements", [])]
        db.replace_elements(pid, elems)
        stats["raw_pages"] += 1
        stats["elements"] += len(elems)

    # ---- 4. qianniu sub 页（二级菜单） ----
    for sf in sorted(QN_PAGES.glob("page_1_*_sub_*.json")):
        d = load_json(sf)
        url_key = f"{d.get('parent_nav_id', 'x')}_{d.get('sub_index', 0)}"  # "1.3_0"
        skeleton = {
            "parent_nav_id": d.get("parent_nav_id"),
            "parent_nav_text": d.get("parent_nav_text"),
            "sub_index": d.get("sub_index"), "sub_text": d.get("sub_text"),
            "page_title": d.get("page_title"), "page_url": d.get("page_url"),
            "element_count": d.get("element_count"),
        }
        pid = db.upsert_page("qianniu", url_key, skeleton=skeleton, fingerprint=None)
        elems = [normalize_element(e) for e in d.get("elements", [])]
        db.replace_elements(pid, elems)
        stats["sub_pages"] += 1
        stats["elements"] += len(elems)

    # ---- 5. qianniu home.json（页面布局描述，无元素坐标） ----
    home = load_json(QN_PAGES / "home.json")
    pid = db.upsert_page(
        "qianniu", "home",
        skeleton=home.get("layout"),
        fingerprint={"fingerprints_used": home.get("fingerprints_used"),
                     "actions": home.get("actions")})
    stats["home_pages"] += 1

    # ---- 6. actors 的 pages + elements ----
    for af in sorted(ACTORS.glob("*.json")):
        actor = load_json(af)
        url_key = actor.get("url_key") or af.stem
        pid = db.upsert_page(
            url_key, url_key,
            skeleton={"url": actor.get("url"), "viewport": actor.get("viewport")},
            fingerprint={"created_at": actor.get("created_at"),
                         "updated_at": actor.get("updated_at"),
                         "scan_count": actor.get("scan_count")})
        elems = [normalize_element(e) for e in actor.get("elements", [])]
        db.replace_elements(pid, elems)
        stats["actor_pages"] += 1
        stats["elements"] += len(elems)

    stats["pages"] = (stats["raw_pages"] + stats["sub_pages"]
                      + stats["home_pages"] + stats["actor_pages"])

    # ---- 汇总 ----
    print("=" * 60)
    print("[migrate] DONE")
    print(f"  tenants: {len(db.list_tenants())}")
    print(f"  apps:    {stats['apps']}")
    print(f"  pages:   {stats['pages']} "
          f"(raw={stats['raw_pages']}, sub={stats['sub_pages']}, "
          f"home={stats['home_pages']}, actor={stats['actor_pages']})")
    print(f"  elements:{stats['elements']}")
    print("  tokens:")
    for tid, tok in tokens.items():
        print(f"    {tid} = {tok}")
    print("=" * 60)
    db.close()


if __name__ == "__main__":
    migrate()
