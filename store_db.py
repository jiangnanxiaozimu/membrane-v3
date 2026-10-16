"""
store_db.py — 膜数据团 SQLite 封装（阶段 1 后端核心）

职责：数据团（apps/pages/elements/workflows）+ 租户（tenants）的存储与查询。
所有查询统一带租户过滤：`tenant_id IS NULL`（官方共享）OR `tenant_id = 当前用户`。

设计要点：
- 单文件、无 UI、无第三方依赖（仅 sqlite3），可独立 import / 单测。
- JSON 字段（manifest/skeleton/fingerprint/features/graph）统一 dict 进、dict 出，
  内部自动 json.dumps / loads，调用方不用碰序列化。
- elements 表：结构化字段（elem_type/name/x/y/w/h）供 SQL 粗筛，
  features_json 存完整原始元素 dict（含 cx/cy 等），匹配时还原，match_ratio 逻辑不动。
- tenant_id 为 NULL 的 UNIQUE 在 SQLite 里不生效（NULL != NULL），
  所以 upsert 一律「先查后写」，绕开这个坑。

数据库路径：环境变量 MEMBRANE_DB 可覆盖，默认本目录 membrane.db（服务器部署时指向服务器路径）。
"""
import json
import os
import sqlite3
import threading
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "membrane.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id          TEXT PRIMARY KEY,
    name        TEXT,
    token       TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS apps (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT,
    name          TEXT,
    manifest_json TEXT,
    FOREIGN KEY (tenant_id) REFERENCES tenants(id)
);

CREATE TABLE IF NOT EXISTS pages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id           TEXT NOT NULL,
    tenant_id        TEXT,
    url_key          TEXT,
    skeleton_json    TEXT,
    fingerprint_json TEXT,
    UNIQUE (app_id, tenant_id, url_key)
);

CREATE TABLE IF NOT EXISTS elements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id       INTEGER NOT NULL,
    elem_type     TEXT,
    name          TEXT,
    x REAL, y REAL, w REAL, h REAL,
    features_json TEXT,
    FOREIGN KEY (page_id) REFERENCES pages(id)
);
CREATE INDEX IF NOT EXISTS idx_elements_page ON elements(page_id);
CREATE INDEX IF NOT EXISTS idx_elements_type_name ON elements(page_id, elem_type, name);

CREATE TABLE IF NOT EXISTS workflows (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT,
    name       TEXT,
    graph_json TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False) if obj is not None else None


def _loads(s):
    return json.loads(s) if s else None


def _tenant_where(tenant_id):
    """租户过滤：官方共享(NULL) + 本人私有。tenant_id=None 表示未登录，只看官方。"""
    if tenant_id is None:
        return "tenant_id IS NULL", ()
    return "(tenant_id IS NULL OR tenant_id = ?)", (tenant_id,)


class StoreDB:
    """膜数据团 SQLite 存储。线程安全（单连接 + 锁），Flask 多线程可直接用。"""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else Path(
            os.environ.get("MEMBRANE_DB", DEFAULT_DB))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # check_same_thread=False：连接跨线程复用，靠 _lock 保证串行
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        with self._lock:
            self.conn.close()

    # ============================================================
    # tenants
    # ============================================================
    def upsert_tenant(self, tenant_id, name=None, token=None):
        """建/更新租户。token 用于鉴权。"""
        with self._lock:
            self.conn.execute(
                "INSERT INTO tenants (id, name, token) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, token=excluded.token",
                (tenant_id, name, token))
            self.conn.commit()

    def get_tenant_by_token(self, token):
        """token → 租户，鉴权用。找不到返回 None。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM tenants WHERE token = ?", (token,)).fetchone()
            return dict(row) if row else None

    def list_tenants(self):
        with self._lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM tenants").fetchall()]

    # ============================================================
    # apps
    # ============================================================
    def upsert_app(self, app_id, name=None, manifest=None, tenant_id=None):
        with self._lock:
            self.conn.execute(
                "INSERT INTO apps (id, tenant_id, name, manifest_json) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET tenant_id=excluded.tenant_id, "
                "name=excluded.name, manifest_json=excluded.manifest_json",
                (app_id, tenant_id, name, _dumps(manifest)))
            self.conn.commit()

    def get_app(self, app_id, tenant_id=None):
        with self._lock:
            where, params = _tenant_where(tenant_id)
            row = self.conn.execute(
                f"SELECT * FROM apps WHERE id = ? AND {where}",
                (app_id, *params)).fetchone()
            return self._row_app(row) if row else None

    def list_apps(self, tenant_id=None):
        with self._lock:
            where, params = _tenant_where(tenant_id)
            rows = self.conn.execute(
                f"SELECT * FROM apps WHERE {where} ORDER BY id", params).fetchall()
            return [self._row_app(r) for r in rows]

    @staticmethod
    def _row_app(row):
        d = dict(row)
        d["manifest"] = _loads(d.pop("manifest_json"))
        return d

    # ============================================================
    # pages
    # ============================================================
    def upsert_page(self, app_id, url_key, skeleton=None, fingerprint=None, tenant_id=None):
        """先查后写（绕开 NULL tenant_id 的 UNIQUE 失效）。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT id FROM pages WHERE app_id=? AND url_key=? AND tenant_id IS ?",
                (app_id, url_key, tenant_id)).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE pages SET skeleton_json=?, fingerprint_json=? WHERE id=?",
                    (_dumps(skeleton), _dumps(fingerprint), row["id"]))
                pid = row["id"]
            else:
                cur = self.conn.execute(
                    "INSERT INTO pages (app_id, tenant_id, url_key, skeleton_json, fingerprint_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (app_id, tenant_id, url_key, _dumps(skeleton), _dumps(fingerprint)))
                pid = cur.lastrowid
            self.conn.commit()
            return pid

    def get_page_by_url_key(self, app_id, url_key, tenant_id=None):
        with self._lock:
            where, params = _tenant_where(tenant_id)
            row = self.conn.execute(
                f"SELECT * FROM pages WHERE app_id=? AND url_key=? AND {where}",
                (app_id, url_key, *params)).fetchone()
            return self._row_page(row) if row else None

    def get_page(self, page_id):
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
            return self._row_page(row) if row else None

    def list_pages(self, tenant_id=None, app_id=None):
        with self._lock:
            where, params = _tenant_where(tenant_id)
            sql = f"SELECT * FROM pages WHERE {where}"
            if app_id:
                sql += " AND app_id=?"
                params = (*params, app_id)
            sql += " ORDER BY app_id, url_key"
            rows = self.conn.execute(sql, params).fetchall()
            return [self._row_page(r) for r in rows]

    @staticmethod
    def _row_page(row):
        d = dict(row)
        d["skeleton"] = _loads(d.pop("skeleton_json"))
        d["fingerprint"] = _loads(d.pop("fingerprint_json"))
        return d

    # ============================================================
    # elements
    # ============================================================
    def replace_elements(self, page_id, elems):
        """整页替换元素（迁移 / 重新扫描用）。elems 是原始元素 dict 列表。"""
        with self._lock:
            self.conn.execute("DELETE FROM elements WHERE page_id=?", (page_id,))
            for el in elems:
                self.conn.execute(
                    "INSERT INTO elements (page_id, elem_type, name, x, y, w, h, features_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (page_id, el.get("type"), (el.get("name") or "").strip(),
                     el.get("x"), el.get("y"), el.get("w"), el.get("h"), _dumps(el)))
            self.conn.commit()

    def upsert_element(self, page_id, el):
        """存单个元素（el 为原始 dict，含 type/name/x/y/w/h 等）。"""
        with self._lock:
            self.conn.execute(
                "INSERT INTO elements (page_id, elem_type, name, x, y, w, h, features_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (page_id, el.get("type"), (el.get("name") or "").strip(),
                 el.get("x"), el.get("y"), el.get("w"), el.get("h"), _dumps(el)))
            self.conn.commit()

    def list_elements(self, page_id):
        """取某页全部元素（还原为原始元素 dict）。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM elements WHERE page_id=? ORDER BY id", (page_id,)).fetchall()
            return [self._row_element(r) for r in rows]

    def list_elements_by_url_key(self, app_id, url_key, tenant_id=None):
        """按 url_key 取元素（供 actor_library.load_from_sqlite 还原 actor）。"""
        page = self.get_page_by_url_key(app_id, url_key, tenant_id)
        if not page:
            return None
        return self.list_elements(page["id"])

    @staticmethod
    def _row_element(row):
        d = dict(row)
        feat = _loads(d.pop("features_json"))
        # 还原原始元素 dict；若 features_json 缺失，用结构化字段兜底
        if feat:
            return feat
        return {"type": d["elem_type"], "name": d["name"],
                "x": d["x"], "y": d["y"], "w": d["w"], "h": d["h"]}

    # ============================================================
    # workflows
    # ============================================================
    def upsert_workflow(self, wf_id, name=None, graph=None, tenant_id=None):
        with self._lock:
            self.conn.execute(
                "INSERT INTO workflows (id, tenant_id, name, graph_json, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET tenant_id=excluded.tenant_id, "
                "name=excluded.name, graph_json=excluded.graph_json, updated_at=datetime('now')",
                (wf_id, tenant_id, name, _dumps(graph)))
            self.conn.commit()

    def get_workflow(self, wf_id, tenant_id=None):
        with self._lock:
            where, params = _tenant_where(tenant_id)
            row = self.conn.execute(
                f"SELECT * FROM workflows WHERE id=? AND {where}",
                (wf_id, *params)).fetchone()
            return self._row_workflow(row) if row else None

    def list_workflows(self, tenant_id=None):
        with self._lock:
            where, params = _tenant_where(tenant_id)
            rows = self.conn.execute(
                f"SELECT * FROM workflows WHERE {where} ORDER BY updated_at DESC",
                params).fetchall()
            return [self._row_workflow(r) for r in rows]

    @staticmethod
    def _row_workflow(row):
        d = dict(row)
        d["graph"] = _loads(d.pop("graph_json"))
        return d


if __name__ == "__main__":
    # 冒烟：建库 + 走一遍基本 CRUD
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "smoke.db"
    db = StoreDB(tmp)
    db.upsert_tenant("user_new", "新电脑", token="tok_new")
    db.upsert_tenant("user_old", "旧电脑", token="tok_old")

    # 官方共享 app（tenant_id=None）
    db.upsert_app("qianniu", "千牛", {"version": "9.0"})
    # 私有 app（user_new）
    db.upsert_app("my_private", "私有应用", {"v": 1}, tenant_id="user_new")

    pid = db.upsert_page("qianniu", "home", {"layout": "nav"}, {"fp": "nav_item"})
    db.replace_elements(pid, [
        {"type": "button", "name": "待发货", "x": 100, "y": 120, "w": 80, "h": 30,
         "cx": 140, "cy": 135},
    ])

    print("user_new 能看到:", [a["id"] for a in db.list_apps("user_new")])
    print("user_old 能看到:", [a["id"] for a in db.list_apps("user_old")])
    print("未登录能看到:", [a["id"] for a in db.list_apps(None)])
    print("按 token 查租户:", db.get_tenant_by_token("tok_new")["id"])
    print("元素还原:", db.list_elements_by_url_key("qianniu", "home"))
    db.close()
    print("[store_db] smoke test PASSED")
