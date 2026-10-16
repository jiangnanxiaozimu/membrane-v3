"""
migrate_deep_modal.py — 千牛「深爬(deep) + 弹窗(modal)」数据导入 membrane.db

把第二组记忆团（全站深爬页面主视图 + Phase 2 弹窗/Tab 态）合并写进 SQLite 数据团。

数据文件（store/apps/qianniu/pages/ 下）：
  深爬主页   page_{nav}_deep.json            {nav_id, crawl:"deep", structure, elements}
  深爬子菜单 page_{nav}_sub_{NN}_deep.json    {parent_nav_id, sub_index, crawl:"deep", ...}
  弹窗主页   page_{nav}_modal.json           {parent_nav_id, sub_index:None, crawl:"modal", tabs_clicked, modals_opened, ...}
  弹窗子菜单 page_{nav}_sub_{NN}_modal.json   {parent_nav_id, sub_index, crawl:"modal", ...}

url_key 映射（与 migrate_store_to_sqlite.py 保持一致）：
  deep 主页   nav_id                        → "1.4"
  deep 子菜单 parent_nav_id + "_" + sub_index → "1.4_0"
  modal 主页  sub_index is None              → parent_nav_id → "1.4"（合并进主页，不是独立页）
  modal 子菜单 parent_nav_id + "_" + sub_index → "1.4_0"（合并进子页）

每个 url_key 最终元素 = deep ∪ modal（全量归一化 dict 去重，避免重复）。
复用 migrate_store_to_sqlite.normalize_element() 保证 DOM → actor 转换规则一致。
写库复用 store_db.StoreDB：upsert_page（先查后写）+ replace_elements（先删后插）→ 幂等。

用法：
  python migrate_deep_modal.py --dry-run   只读统计，不写库
  python migrate_deep_modal.py             实际写库
"""
import argparse
import io
import json
import sys
from pathlib import Path

# 中文输出：防止 Windows 控制台 GBK 编码报错
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from store_db import StoreDB                      # noqa: E402
from migrate_store_to_sqlite import normalize_element  # noqa: E402

APP_SLUG = "qianniu"
APP_NAME = "千牛卖家工作台"
QN_PAGES = BASE / "store" / "apps" / "qianniu" / "pages"

DEEP_GLOB = "page_*_deep.json"
MODAL_GLOB = "page_*_modal.json"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def url_key_for(data):
    """从内容推导 url_key，与设计决策一致（deep 用 nav_id；modal 用 parent_nav_id 区分主页/子菜单）。"""
    crawl = data.get("crawl")
    if crawl == "deep":
        nav_id = data.get("nav_id")
        if nav_id is not None and str(nav_id) != "":
            return str(nav_id)                       # 深爬主页 → "1.4"
        return f"{data.get('parent_nav_id')}_{data.get('sub_index')}"  # 深爬子菜单 → "1.4_0"
    # modal：主页 sub_index 是 None，只取 parent_nav_id；子菜单拼 parent_sub
    parent = data.get("parent_nav_id")
    sub = data.get("sub_index")
    if sub is None:
        return str(parent)                            # 弹窗主页 → "1.4"
    return f"{parent}_{sub}"                          # 弹窗子菜单 → "1.4_0"


def _dedup_key(norm):
    """元素去重键：全量归一化 dict 序列化（sort_keys 保证稳定）。"""
    return json.dumps(norm, ensure_ascii=False, sort_keys=True)


def merge_elements(deep_data, modal_data):
    """deep ∪ modal（全量归一化 dict 去重）。返回 (merged, deep_cnt, modal_cnt, dup_cnt)。"""
    deep_elems = deep_data.get("elements", []) if deep_data else []
    modal_elems = modal_data.get("elements", []) if modal_data else []

    seen = set()
    merged = []
    for e in deep_elems:
        norm = normalize_element(e)
        k = _dedup_key(norm)
        if k not in seen:
            seen.add(k)
            merged.append(norm)

    dup = 0
    for e in modal_elems:
        norm = normalize_element(e)
        k = _dedup_key(norm)
        if k in seen:
            dup += 1
            continue
        seen.add(k)
        merged.append(norm)

    return merged, len(deep_elems), len(modal_elems), dup


def build_skeleton(deep_data, modal_data):
    """页面骨架：优先取 deep（主视图），deep 缺失时退回 modal。"""
    src = deep_data or modal_data or {}
    return {
        "nav_id": src.get("nav_id"),
        "nav_text": src.get("nav_text"),
        "parent_nav_id": src.get("parent_nav_id"),
        "parent_nav_text": src.get("parent_nav_text"),
        "sub_index": src.get("sub_index"),
        "sub_text": src.get("sub_text"),
        "page_title": src.get("page_title"),
        "page_url": src.get("page_url"),
        "structure": src.get("structure"),
        "element_count": src.get("element_count"),
    }


def build_fingerprint(deep_data, modal_data, merged_count, dup_count, files):
    """指纹/溯源：记录 deep+modal 各自来源与合并统计，方便日后核对。"""
    fp = {"crawl": "deep+modal", "merged_element_count": merged_count, "deduped": dup_count,
          "files": sorted(files)}
    if deep_data:
        fp["deep"] = {
            "scanned_at": deep_data.get("scanned_at"),
            "element_count": deep_data.get("element_count"),
            "structure": deep_data.get("structure"),
        }
    if modal_data:
        fp["modal"] = {
            "scanned_at": modal_data.get("scanned_at"),
            "element_count": modal_data.get("element_count"),
            "tabs_clicked": modal_data.get("tabs_clicked"),
            "modals_opened": modal_data.get("modals_opened"),
        }
    return fp


def collect_groups():
    """扫描 deep/modal 文件，按 url_key 分组，同时收集异常。

    返回 (groups, anomalies)
      groups    : {url_key: {"deep": data|None, "modal": data|None, "files": [str,...]}}
      anomalies : [(文件名, 描述), ...]
    """
    groups = {}
    anomalies = []

    for pattern in (DEEP_GLOB, MODAL_GLOB):
        for f in sorted(QN_PAGES.glob(pattern)):
            try:
                d = load_json(f)
            except Exception as e:  # noqa: BLE001
                anomalies.append((f.name, f"加载失败: {e}"))
                continue

            crawl = d.get("crawl")
            kind = "deep" if crawl == "deep" else "modal"
            url_key = url_key_for(d)

            g = groups.setdefault(url_key, {"deep": None, "modal": None, "files": []})
            if g[kind] is not None:
                anomalies.append((f.name, f"同一 url_key={url_key} 下重复的 {kind} 文件"))
            g[kind] = d
            g["files"].append(f.name)

            # 元素一致性检查
            els = d.get("elements")
            ec = d.get("element_count")
            if els is None:
                anomalies.append((f.name, "缺 'elements' 字段"))
            elif not isinstance(els, list):
                anomalies.append((f.name, f"'elements' 不是 list（是 {type(els).__name__}）"))
            elif ec is not None and int(ec) != len(els):
                anomalies.append((f.name, f"element_count={ec} != len(elements)={len(els)}"))

    return groups, anomalies


def summarize(groups):
    """对分组做纯内存统计（dry-run 用）。"""
    stats = {
        "url_keys": len(groups),
        "pages_with_deep": 0, "pages_with_modal": 0, "pages_modal_only": 0,
        "deep_elements": 0, "modal_elements": 0,
        "merged_elements": 0, "deduped": 0,
    }
    for url_key, g in groups.items():
        if g["deep"] is not None:
            stats["pages_with_deep"] += 1
        if g["modal"] is not None:
            stats["pages_with_modal"] += 1
        if g["deep"] is None and g["modal"] is not None:
            stats["pages_modal_only"] += 1
        merged, dc, mc, dup = merge_elements(g["deep"], g["modal"])
        stats["deep_elements"] += dc
        stats["modal_elements"] += mc
        stats["merged_elements"] += len(merged)
        stats["deduped"] += dup
    return stats


def print_stats(stats, anomalies):
    print("=" * 64)
    print("[migrate_deep_modal] 统计结果")
    print("=" * 64)
    print(f"  url_key 总数          : {stats['url_keys']}")
    print(f"    含 deep 的页        : {stats['pages_with_deep']}")
    print(f"    含 modal 的页       : {stats['pages_with_modal']}")
    print(f"    modal-only 的页     : {stats['pages_modal_only']}")
    print(f"  deep 元素总数         : {stats['deep_elements']}")
    print(f"  modal 元素总数        : {stats['modal_elements']}")
    print(f"  合并去重后元素总数    : {stats['merged_elements']}")
    print(f"  去重掉的重复元素      : {stats['deduped']}")
    print("=" * 64)

    if anomalies:
        print(f"\n[!] 发现 {len(anomalies)} 处异常：")
        for name, msg in anomalies:
            print(f"  - {name}: {msg}")
    else:
        print("\n[ok] 未发现数据文件异常。")
    print()


def run(dry_run=False):
    groups, anomalies = collect_groups()
    stats = summarize(groups)
    print_stats(stats, anomalies)

    if dry_run:
        print("[dry-run] 未写库（只读文件统计）。")
        return

    db = StoreDB()  # 默认 D:\AI\membrane\membrane.db

    # 确保 app 存在；已存在则不覆盖其 manifest（避免冲掉旧迁移写入的骨架/指纹）
    if db.get_app(APP_SLUG, tenant_id=None) is None:
        db.upsert_app(APP_SLUG, APP_NAME, {"source": "migrate_deep_modal"}, tenant_id=None)

    written_pages = 0
    written_elements = 0
    for url_key in sorted(groups.keys()):
        g = groups[url_key]
        merged, _dc, _mc, dup = merge_elements(g["deep"], g["modal"])
        skeleton = build_skeleton(g["deep"], g["modal"])
        fingerprint = build_fingerprint(g["deep"], g["modal"], len(merged), dup, g["files"])
        pid = db.upsert_page(APP_SLUG, url_key, skeleton=skeleton, fingerprint=fingerprint,
                             tenant_id=None)
        db.replace_elements(pid, merged)
        written_pages += 1
        written_elements += len(merged)

    print(f"[done] 已写入 {written_pages} 个页面、{written_elements} 个元素。")
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="千牛 deep+modal 数据导入 membrane.db")
    parser.add_argument("--dry-run", action="store_true", help="只读统计，不写库")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
