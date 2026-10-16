#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
operator_verify.py — 膜式操控员校验器 v2（回到膜初衷）

膜 = 外骨骼：看 → 判断 → 点，三步闭环。
  - 看    membrane_vision.detect_ui_elements()  视觉扫描当前屏幕（含坐标）
  - 判断  qwen3:14b 拿「结构化元素(类型+文字+坐标)」判断导航/广告/业务，决定下一步
  - 点    exec.executor.click 真实鼠标（SetCursorPos + mouse_event），鼠标真的会动

v2 与 v1(CDP 命令版) 的区别（用户指出的违背初衷之处）：
  1. 导航/点击全部用真实鼠标，不再 Input.dispatchMouseEvent / JS .click()
  2. 广告由 qwen 判断（拿坐标看位置），真实点关闭，不再用 class 规则后端删库
  3. 扫描用视觉(membrane_vision)，不再 CDP 深扫 DOM

分工（用户定）：
  - qwen3:14b = 执行主体：判断广告/业务/漏爬，指挥真实鼠标点导航、关广告、补漏，
    不会的 ask_human 求助用户。
  - 小龙虾(Claude) = 教练：造工具 + 写循环 + 写 prompt + 护栏，不实际点击。

用法：
  python operator_verify.py --limit 3     # 只校验前 3 页（调试）
  python operator_verify.py --page 1.4    # 只校验指定页
  python operator_verify.py --list        # 只看进度
  python operator_verify.py --classify    # 只扫描分类当前页（调试广告判断）

核心结论（实测）：
  1. qwen14b 判断广告靠「位置结构」不靠「文字语义」——给坐标后分类全对；
     之前纯文本名单会判反（把「购买/确认」当业务、把「场景推荐」当营销）。
  2. 左侧 x≈21 竖排 = 一级导航；x≈128 = 二级子菜单；右侧 x>500 成组的
     「购买/安装使用/去使用/确认」= 应用市场广告卡片。
  3. 广告分两种：可关闭的弹窗/横幅(有×/关闭钮→真实点关闭)；应用市场卡片
     (购买/安装使用等，是应用商店内容关不掉→标记排除，不当作业务功能)。
  4. 视觉只能看到当前视口，DB 是全量累积，所以校验「只补不漏」。
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from store_db import StoreDB                              # noqa: E402
from membrane_operator import _extract_json, DEFAULT_MODEL, DeepSeekClient  # noqa: E402
from membrane_vision import detect_ui_elements            # noqa: E402
from exec.executor import click, hover, wait, press_key   # noqa: E402

APP_SLUG = "qianniu"
STATE_FILE = BASE / "verify_state.json"
REPORT_FILE = BASE / "verify_report.md"
MEMORY_FILE = BASE / "verify_memory.md"

# 一级导航（url_key "1.X" → NAV_ITEMS[X-1]），与 crawl_qianniu_lv 一致
NAV_ITEMS = ["首页", "常用", "交易", "商品", "营销", "推广", "客服",
             "店铺", "私域", "财务", "金融", "数据", "服务"]
SYCM_KEY = "1.12"   # 数据 = 生意参谋，点导航开新 tab，跳过

MAX_ROUNDS = 3      # 每页最多 look→判断 轮数（关广告循环）
MAX_QUESTIONS = 20  # 最多攒多少求助问题


# ============================================================
# 看（视觉扫描）
# ============================================================
def look_web():
    """看：视觉扫描当前屏幕，返回网页元素清单（简化成 dict 列表）。

    返回 (elems, err)。elems 每项含 idx/type/name/x/y/cx/cy，cx/cy 是中心点，
    直接喂给 exec.executor.click。只保留有文字的网页元素(depth=99)。
    """
    try:
        r = detect_ui_elements()
    except Exception as e:
        return [], f"扫描异常: {str(e)[:100]}"
    if "error" in r:
        return [], r["error"]

    elems = []
    for e in r["elements"]:
        if e.get("depth", 0) != 99:          # 只取网页元素，忽略 Edge 窗口 chrome
            continue
        name = (e.get("name") or "").strip()
        if not name:
            continue
        x1, y1 = int(e.get("x1", 0)), int(e.get("y1", 0))
        x2, y2 = int(e.get("x2", 0)), int(e.get("y2", 0))
        elems.append({
            "idx": len(elems),
            "type": e.get("type", ""),
            "name": name,
            "x": x1, "y": y1,
            "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
            "href": e.get("href", ""),
        })
    return elems, None


def look_popups():
    """看：扫描浏览器原生弹窗（UIA 层，depth<99，内容区 y>90）。

    千牛/Edge 会弹原生对话框（通知权限「不显示/推迟」、提示框「确认/知道了」等），
    这些不在网页 DOM 里，look_web 扫不到，但会挡住真实鼠标点击，必须单独扫。
    返回 (popups, err)。popups 每项含 type/name/x/y/cx/cy。
    """
    try:
        r = detect_ui_elements()
    except Exception as e:
        return [], f"扫描异常: {str(e)[:100]}"
    if "error" in r:
        return [], r["error"]

    popups = []
    for e in r["elements"]:
        if e.get("depth", 0) >= 99:          # 排除网页元素
            continue
        name = (e.get("name") or "").strip()
        if not name:                          # 无名字的控件（如空白容器）跳过
            continue
        x1, y1 = int(e.get("x1", 0)), int(e.get("y1", 0))
        x2, y2 = int(e.get("x2", 0)), int(e.get("y2", 0))
        if y1 <= 90:                          # 排除顶部工具栏/书签栏
            continue
        popups.append({
            "type": e.get("type", ""),
            "name": name,
            "x": x1, "y": y1,
            "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
        })
    return popups, None


# 原生弹窗关闭按钮的优先级：永久关闭 > 通用关闭 > 确认类 > 推迟
POPUP_CLOSE_PRIORITY = ["关闭", "不显示", "知道了", "稍后再说", "确定", "确认", "推迟"]


def close_popup(popups, exclude=None):
    """从弹窗按钮里挑一个真实点掉，返回 (按钮名, 描述)；没匹配到返回 (None, None)。"""
    exclude = exclude or set()
    for name in POPUP_CLOSE_PRIORITY:
        for p in popups:
            if (p["name"] == name or name in p["name"]) and name not in exclude:
                click(p["cx"], p["cy"])
                return name, f"点「{p['name']}」@({p['cx']},{p['cy']})"
    return None, None


def clear_popups(url_key, max_tries=3):
    """清掉挡路的原生弹窗（真实点关闭），返回清理了几个。点不掉的记录并放弃，不死循环。"""
    n = 0
    tried = set()
    for _ in range(max_tries):
        popups, err = look_popups()
        if err or not popups:
            break
        name, desc = close_popup(popups, exclude=tried)
        if not desc:
            break
        tried.add(name)
        do_report(f"{url_key} 真实鼠标尝试关闭原生弹窗：{desc}")
        wait(2.0)
        # 确认是否真关掉
        popups2, _ = look_popups()
        if not popups2:
            do_report(f"{url_key} 原生弹窗已关闭")
            n += 1
        else:
            do_report(f"{url_key} 原生弹窗点不掉（{desc} 无效），跳过")
    return n


def clear_dom_modal():
    """ESC 关掉网页 DOM 弹窗（Ant Design Modal 等全屏遮罩）。

    这类弹窗的 backdrop 是透明无文字的全屏层，look_web（只保留有文字元素）扫不到它，
    但它会挡住真实鼠标点击，导致点导航不生效。Ant Design Modal 默认支持 ESC 关闭。
    真实键盘 ESC，符合膜「真实操作」原则。
    """
    press_key("esc")
    wait(1.2)


def _cdp_eval(expr, timeout=10):
    """CDP Runtime.evaluate 在千牛主 tab 上执行 JS，返回 value。失败返回 None。

    只用于排障（读状态/移除卡死的引导遮罩），不做业务点击——业务操作走真实鼠标。
    """
    import websocket
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
            tabs = json.loads(r.read().decode("utf-8"))
        ws_url = None
        for t in tabs:
            if t.get("type") == "page" and "myseller" in (t.get("url") or ""):
                ws_url = t.get("webSocketDebuggerUrl")
        if not ws_url:
            return None
        ws = websocket.create_connection(ws_url, timeout=timeout)
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "returnByValue": True}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    return msg.get("result", {}).get("result", {}).get("value")
        finally:
            ws.close()
    except Exception:
        return None


def clear_joyride():
    """移除 React Joyride 新手引导遮罩（全屏半透明 z=100，挡住所有真实鼠标点击）。

    点「配置与装修」等页会触发千牛新手引导，引导气泡(tooltip)有时卡住不渲染，
    只剩全屏 overlay + spotlight 残留，ESC 和点遮罩都关不掉（实测），只能 CDP 移除。
    这是排障（把页面恢复到可操作状态），不是业务操作，不影响任何业务数据。
    """
    removed = _cdp_eval(
        "(function(){var n=0;"
        "document.querySelectorAll('.react-joyride__overlay,.react-joyride__spotlight,.react-joyride__tooltip')"
        ".forEach(function(e){e.remove();n++;});return n;})()"
    )
    if removed:
        do_report(f"清除新手引导遮罩(react-joyride) {removed} 个元素")
        wait(0.5)
    return removed


def _fmt_elems(elems, limit=80):
    lines = []
    for e in elems[:limit]:
        lines.append(f"[{e['idx']}] {e['type']} '{e['name']}' @({e['x']},{e['y']})")
    return "\n".join(lines)


# ============================================================
# 点（真实鼠标）
# ============================================================
def click_elem(e):
    """真实鼠标点一个元素的中心。"""
    click(e["cx"], e["cy"])
    return e


def find_by_name(elems, name):
    """按文字找元素（导航用），优先精确匹配。"""
    for e in elems:
        if (e["name"] or "").strip() == name:
            return e
    for e in elems:
        if name in (e["name"] or ""):
            return e
    return None


# ============================================================
# 判断（qwen 分类）
# ============================================================
CLASSIFY_SYSTEM = """你是千牛商家工作台的「膜」操控员，用真实鼠标操作千牛。
我给你当前屏幕扫到的网页元素清单，每行：编号|类型|文字|坐标(x,y)。

千牛布局规律（很重要，靠坐标判断，不是靠文字猜）：
- 左侧竖排一级导航，x 很小(约21)：首页/常用/交易/商品/营销/推广/客服/店铺/私域/财务/金融/数据/服务
- 点一级导航后，x≈128 处会展开二级子菜单
- 右侧内容区 x>500 成组出现「购买/安装使用/去使用/确认」按钮 = 应用市场广告卡片

把每个编号归类：
- nav：左侧一级导航（x很小，点它切换页面）
- submenu：二级子菜单（x≈128）
- ad_popup：可关闭的广告弹窗/遮罩——必须「挡住页面、要点×或「关闭/知道了/稍后再说」才能继续」。顶部横幅链接、营销入口（点了跳转而非关闭）→ 归 ad_card 或 other，不归 ad_popup
- ad_card：应用市场广告卡片（购买/安装使用/去使用/确认等按钮，是应用商店内容，关不掉）
- business：业务功能入口（菜单/按钮/表单，膜要操作的真实功能）
- other：其他（搜索框、链接、提示文字等）

只输出 JSON，字段固定：
{"nav":[...], "submenu":[...], "ad_popup":[...], "ad_card":[...],
 "business":[...], "other":[...], "close_idx": 关闭按钮的编号或 -1, "comment":"一句话"}

close_idx：如果 ad_popup 里有可关闭的弹窗/横幅，填它的关闭按钮(×或「关闭/知道了」)的编号；
如果清单里看不到关闭按钮，填 -1（我会再想别的办法定位）。没有可关闭弹窗时也填 -1。
"""


def qwen_classify(client, elems):
    """让 qwen 分类当前元素。返回 dict(分类结果) 或 None(失败)。"""
    feed = _fmt_elems(elems)
    try:
        resp = client.chat(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": CLASSIFY_SYSTEM},
                {"role": "user", "content": feed},
            ],
            format="json",
            options={"temperature": 0.0},
        )
    except Exception as e:
        return {"error": f"qwen 调用失败: {str(e)[:100]}"}
    d = _extract_json(resp["message"]["content"])
    for k in ("nav", "submenu", "ad_popup", "ad_card", "business", "other"):
        d.setdefault(k, [])
    d.setdefault("close_idx", -1)
    d.setdefault("comment", "")
    return d


def qwen_is_nav_submenu(client, nav_name, subs):
    """用 qwen 判断当前子菜单列表是否属于 nav_name 导航的子菜单。

    千牛子页 URL 模块段分散（营销子页可能是 starb/price-refund-manage/tmc-next），
    URL 不可靠，但子菜单文字是稳定的——靠子菜单内容判断「当前在哪个导航」。
    """
    if not subs:
        return False
    names = "、".join(subs[:20])
    try:
        resp = client.chat(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content":
                 "你是千牛商家工作台的导航判断助手。千牛左侧有一级导航："
                 "首页/常用/交易/商品/营销/推广/客服/店铺/私域/财务/金融/数据/服务。"
                 "点一级导航后 x≈128 处展开该导航的二级子菜单。"
                 "我给你一个一级导航名和一组子菜单文字，判断这组子菜单是否属于该一级导航。"
                 "只输出 JSON：{\"yes\": true 或 false, \"comment\":\"一句话\"}"},
                {"role": "user", "content": f"一级导航：{nav_name}\n子菜单：{names}"},
            ],
            format="json",
            options={"temperature": 0.0},
        )
        d = _extract_json(resp["message"]["content"])
        return bool(d.get("yes"))
    except Exception:
        return False


# 店铺/私域的子菜单静态特征词（qwen 不知道千牛模块结构，会误判「退货宝/内容中心」不属于店铺）。
# 这些词是千牛各导航的独有子菜单项，其他导航不会出现，用它做确定性匹配替代 qwen 语义猜测。
NAV_SUBMENU_MARKS = {
    "店铺": ["店铺信息", "店铺体检", "店铺工具", "子账号管理", "店铺直播", "层级与权益"],
    "私域": ["人群运营", "会员运营", "用户触达", "粉丝运营"],
}


def _is_nav_submenu(client, nav_name, subs):
    """判断 subs 是否属于 nav_name 导航的子菜单。

    店铺/私域用静态特征词匹配（qwen 会误判）；其他导航用 qwen 语义判断。
    """
    marks = NAV_SUBMENU_MARKS.get(nav_name)
    if marks:
        hit = sum(1 for m in marks if any(m in s for s in subs))
        return hit >= 2
    return qwen_is_nav_submenu(client, nav_name, subs)


# ============================================================
# 导航（膜式：真实鼠标点导航词）
# ============================================================
# 点导航切换成功后记住该导航的 URL 主路径，用于判断「是否已在该导航」。
# 持久化到文件（跨进程/跨页可用），否则冷启动时无法识别「已经在目标导航」。
NAV_URL_FILE = BASE / "verify_nav_urls.json"


def _load_nav_urls():
    try:
        return json.loads(NAV_URL_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_nav_urls(d):
    try:
        NAV_URL_FILE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


NAV_URL_MARK = _load_nav_urls()

# 千牛各导航 URL 的静态关键词（冷启动兜底，判断「是否已在该导航」）。
# 其余导航靠第一次切换成功后动态记住并持久化。
NAV_URL_KEYWORDS = {
    "商品": "SellManage",
    "营销": "starb",
    "店铺": "voc",
    "私域": "private-domain-workbench",
}


# 这些导航的 URL 模块段与其他导航共享（首页/常用都 QnworkbenchHome），
# URL 无法区分「是否已在」，必须强制点导航，改用子菜单变化来验证。
# 注：店铺(voc)/私域(private-domain-workbench) 的 URL 段其实各自独立、可区分，不在共享段。
SHARED_SEG_NAVS = {"首页", "常用"}


def _nav_matches(nav_name, url):
    """判断 url 是否已经是 nav_name 导航的页面（动态映射优先，静态关键词兜底）。

    共享段的导航返回 False（URL 不可靠，交给子菜单验证）。
    """
    if not url:
        return False
    if nav_name in SHARED_SEG_NAVS:
        return False
    mark = NAV_URL_MARK.get(nav_name)
    if mark:
        return _url_path(url) == mark
    kw = NAV_URL_KEYWORDS.get(nav_name)
    if kw:
        return kw in url
    return False


def _submenu_names(elems):
    """提取当前二级子菜单的名字集合（x 40~300 的 a 元素，去掉一级导航名）。"""
    return {e["name"] for e in elems
            if 40 <= e["x"] <= 300 and e["type"] == "a" and e["name"] not in NAV_ITEMS}


def _content_signature(elems):
    """主内容区（x>300）元素名签名，用于判断点子菜单后页面是否真的切换了。

    千牛子页 URL 模块段分散不可靠，但子页主内容区的文字集合是稳定的——
    点了子菜单切到新页，主内容区文字必然变；没变 = 点击没生效（被遮罩挡住等）。
    """
    return sorted({e["name"] for e in elems if e["x"] > 300})


def _content_switched(before, after):
    """主内容区是否真的切换了（URL 不变时兜底）。排除轮播/通知栏 1~2 词的小变化。"""
    changed = len(set(after) - set(before)) + len(set(before) - set(after))
    return changed >= 3 and (not before or changed >= 0.3 * len(before))


def _url_path(url):
    """提取 URL 的导航模块段（home.htm/ 后第一段），用于判断导航切换。

    千牛是 SPA，同一导航下的所有子页面共享同一模块段：
    营销首页 mkt-tools、营销活动报名 tmc-next/sale 都属 starb 模块 → 段=starb；
    商品所有子页段=SellManage。比完整路径更能区分「是否已在某导航」。
    """
    try:
        path = urlparse(url).path
        if "home.htm/" in path:
            return path.split("home.htm/")[1].split("/")[0]
        return path
    except Exception:
        return url or ""


def _same_page(url_a, url_b):
    """两个 URL 是否指向同一页（比较 path，忽略 query/hash）。

    用于点子菜单前判断「是否已经在该子页」——千牛子菜单 <a> 的 href 就是目标子页地址，
    当前 URL 已等于它时，再点子菜单也不会切换（已在目标页），应直接算成功。
    """
    if not url_a or not url_b:
        return False
    try:
        return urlparse(url_a).path == urlparse(url_b).path
    except Exception:
        return False


def get_qianniu_url():
    """读当前千牛 tab 的 URL（CDP 只读，不改页面）。失败返回 None。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        for t in data:
            if t.get("type") == "page" and "myseller" in (t.get("url") or ""):
                return t["url"]
    except Exception:
        pass
    return None


# ============================================================
# 自愈：多余 tab 检测 + 安全关闭 + 回主页面
# ============================================================
def get_all_tabs():
    """读 CDP 所有 page tab，返回 [{id,title,url}]。失败返回 []。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [{"id": t.get("id"), "title": t.get("title", ""), "url": t.get("url", "")}
                for t in data if t.get("type") == "page"]
    except Exception:
        return []


def _is_main_tab(tab):
    """千牛主工作台：myseller 的 home.htm 主页面（排除 item.upload 商品发布等子页）。"""
    u = tab.get("url", "")
    return "myseller.taobao.com" in u and "home.htm" in u and "item.upload" not in u


def _tab_kind(tab):
    """判断一个多余 tab 是干嘛的（用于日志和关闭决策）。"""
    u = tab.get("url", "")
    t = tab.get("title", "")
    if "item.upload" in u:
        return f"商品发布页「{t}」"
    if any(k in u for k in ("sycm", "shusheng", "shengchanmou", "data.taobao")):
        return f"生意参谋「{t}」"
    if "alimama.com" in u:
        return f"阿里妈妈投放页「{t}」"
    if "taobao.com" in u or "tmall.com" in u:
        return f"其他淘宝页「{t}」"
    return None      # 非淘宝/阿里站点（用户自己的网页），不碰


def activate_tab(tab_id):
    """切到指定 tab（CDP activate）。"""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:9222/json/activate/{tab_id}", timeout=5)
    except Exception:
        pass


def close_extra_tabs():
    """自愈：扫描所有 tab，关掉多余淘宝页（商品发布/生意参谋等），回到主千牛 tab。

    只关淘宝/天猫相关的多余 tab，绝不碰用户自己开的其他网站。返回 (关了几个, 说明)。
    """
    tabs = get_all_tabs()
    main_tabs = [t for t in tabs if _is_main_tab(t)]
    closed = []
    for t in tabs:
        if _is_main_tab(t):
            continue
        kind = _tab_kind(t)
        if kind is None:                 # 非淘宝站点的 tab，不碰
            continue
        try:
            urllib.request.urlopen(f"http://127.0.0.1:9222/json/close/{t['id']}", timeout=5)
            closed.append(kind)
        except Exception:
            pass
    if main_tabs:
        activate_tab(main_tabs[0]["id"])
    return len(closed), closed


def goto_page(client, url_key):
    """膜式导航：真实鼠标点一级导航词 → 等加载 → 验证切换 → 点子菜单。返回 (ok, err)。

    关键修正（此前页面卡在「商品」导航的根因）：点导航前先清原生弹窗，点完验证真的切过去了。
    验证用 URL 模块段 + 子菜单内容(qwen) 双重兜底——千牛子页 URL 模块段分散
    （营销子页可能是 starb/price-refund-manage/tmc-next），URL 不可靠，子菜单文字才可靠。
    """
    parts = url_key.split("_")
    nav_idx = int(parts[0].split(".")[1]) - 1
    nav_name = NAV_ITEMS[nav_idx]
    sub_idx = int(parts[1]) if len(parts) > 1 else None

    if nav_idx == int(SYCM_KEY.split(".")[1]) - 1:
        return False, "数据(生意参谋)开新 tab，跳过"

    # 点导航前先清掉可能挡导航的原生弹窗 + 网页 DOM Modal + 新手引导遮罩
    clear_popups(url_key)
    clear_dom_modal()
    clear_joyride()

    elems, err = look_web()
    if err:
        return False, err
    nav = find_by_name(elems, nav_name)
    if not nav:
        return False, f"没扫到一级导航「{nav_name}」，当前可见导航：{[e['name'] for e in elems if e['x'] < 40]}"

    # 判断当前是否已在目标导航：URL 模块段优先，不可靠时用 qwen 看子菜单内容
    cur_subs = _submenu_names(elems)
    in_target = _nav_matches(nav_name, get_qianniu_url())
    if not in_target and cur_subs and sub_idx is not None:
        in_target = _is_nav_submenu(client, nav_name, sorted(cur_subs))

    if not in_target:
        before_subs = cur_subs
        for attempt in range(3):
            before = get_qianniu_url()
            click_elem(nav)
            wait(4.0)
            elems, err = look_web()
            if err:
                return False, err
            after = get_qianniu_url()
            after_subs = _submenu_names(elems)
            url_changed = bool(after and after != before)
            subs_changed = bool(after_subs != before_subs)
            # 成功：URL 变了（跨导航）或 子菜单变了（共享段导航 如首页/常用、店铺/私域）
            if url_changed or subs_changed:
                if url_changed:
                    NAV_URL_MARK[nav_name] = _url_path(after)   # 记住新路径，供同导航续跑判断
                    _save_nav_urls(NAV_URL_MARK)
                break
            # URL/子菜单都没变：可能已在目标导航(URL 判断不了)，用 qwen 看子菜单归属
            if after_subs and _is_nav_submenu(client, nav_name, sorted(after_subs)):
                break
            # 被弹窗/引导遮罩挡住没点中 → 清原生弹窗 + ESC 关网页 Modal + 清引导遮罩，重试
            clear_popups(url_key)
            clear_dom_modal()
            clear_joyride()
            nav = find_by_name(elems, nav_name)
            if not nav:
                return False, f"重试时没扫到一级导航「{nav_name}」"
        else:
            return False, f"点「{nav_name}」导航 3 次 URL/子菜单均未变化，疑似被弹窗挡住，跳过"

    if sub_idx is None:
        return True, None

    # 点二级子菜单：再扫，取 x 在导航列右侧(40~300)、且不是一级导航的元素，按 y 排序
    elems, err = look_web()
    if err:
        return False, err
    subs = [e for e in elems if 40 <= e["x"] <= 300 and e["type"] == "a"]
    subs.sort(key=lambda e: e["y"])
    # 去掉跟一级导航同名的（有些导航项会重复出现在二级）
    subs = [e for e in subs if e["name"] not in NAV_ITEMS]
    if not subs:
        return False, "没扫到二级子菜单"
    if sub_idx >= len(subs):
        return False, f"二级子菜单只有 {len(subs)} 项，想点第 {sub_idx} 项"

    # 已经在目标子页（当前 URL == 该子菜单项 href）→ 再点也不会切换，直接算成功
    if _same_page(get_qianniu_url(), subs[sub_idx].get("href", "")):
        return True, None

    # 点子菜单后必须验证真的切过去了。此前只点不验证（return True 了事），
    # 被新手引导遮罩挡住时页面没切，verify_one_page 就扫错页面补漏错误内容。
    # 判据：URL 变化（千牛子页 URL 唯一）为主，主内容区大变化兜底。
    before_url = get_qianniu_url()
    before_sig = _content_signature(elems)
    for attempt in range(3):
        clear_joyride()      # 点子菜单前清掉新手引导遮罩，确保点得中
        click_elem(subs[sub_idx])
        wait(5.0)
        elems2, err = look_web()
        if err:
            return False, err
        after_url = get_qianniu_url()
        after_sig = _content_signature(elems2)
        if (after_url and after_url != before_url) or _content_switched(before_sig, after_sig):
            return True, None
        # 没切换：清遮罩重扫子菜单（遮罩清除后布局可能变），重试
        do_report(f"{url_key} 点子菜单第 {sub_idx} 项未切换，重试 {attempt+1}")
        clear_dom_modal()
        elems, err = look_web()
        if err:
            return False, err
        subs = [e for e in elems if 40 <= e["x"] <= 300 and e["type"] == "a"]
        subs.sort(key=lambda e: e["y"])
        subs = [e for e in subs if e["name"] not in NAV_ITEMS]
        if not subs or sub_idx >= len(subs):
            return False, "重试时二级子菜单变了/消失"
    return False, f"点二级子菜单第 {sub_idx} 项 3 次页面均未切换，跳过"


# ============================================================
# 数据团读写
# ============================================================
def read_db(url_key):
    db = StoreDB()
    elems = db.list_elements_by_url_key(APP_SLUG, url_key)
    db.close()
    return elems or []


def _db_names(db_elems):
    return {str((e.get("name") or "").strip()).lower() for e in db_elems
            if (e.get("name") or "").strip()}


# ============================================================
# 反馈 + 记忆
# ============================================================
def do_report(text):
    with open(REPORT_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.now().strftime('%H:%M:%S')} {text}\n")
    return "已记入报告"


def do_remember(text):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
    return "已记入记忆"


# ============================================================
# 状态
# ============================================================
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"status": {}, "questions": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def list_pending(state):
    db = StoreDB()
    pages = db.list_pages(app_id=APP_SLUG)
    db.close()
    status = state.get("status", {})
    pending, done_count, total = [], 0, 0
    for p in pages:
        uk = p.get("url_key")
        if uk is None or not re.fullmatch(r"1\.\d+(_\d+)?", uk):
            continue
        if uk == SYCM_KEY:
            continue
        total += 1
        if status.get(uk, {}).get("st") in ("fixed", "ok"):
            done_count += 1
        else:
            pending.append(uk)
    return pending, total, done_count


# ============================================================
# 单页校验（膜式闭环）
# ============================================================
# 最近补漏的页面 URL 主路径（_url_path），供 main 检测「连续多页没切换」异常。
# 用 URL 而非补漏词判断——推广子页业务词天然相似（都是万相台「立即下单/添加商品」），
# 补漏词相似是正常现象；真正的「页面没切换」= URL 主路径没变。
RECENT_URLS = []


def _detect_repeat():
    """连续 3 页补漏落在同一个 URL 主路径 = 页面没切换（跳错页），返回 True。"""
    if len(RECENT_URLS) < 3:
        return False
    a, b, c = RECENT_URLS[-3:]
    return bool(a) and a == b == c


def verify_one_page(client, url_key, state):
    """膜式校验一页：导航→看→判断广告(真实点关闭)→看→对比补漏。

    返回 (状态, 说明)。状态：fixed/ok/ask/skipped。
    """
    # 自愈：导航前先清掉多余 tab（商品发布/生意参谋等意外开的），回到主千牛页
    n_closed, closed = close_extra_tabs()
    if n_closed:
        do_report(f"{url_key} 自愈：关掉多余 tab {n_closed} 个（{closed}），回到主页面")

    ok, err = goto_page(client, url_key)
    if err:
        return "skipped", err

    db_elems = read_db(url_key)
    db_names = _db_names(db_elems)
    clear_popups(url_key)        # 导航后可能弹原生弹窗，先清

    # ---- 闭环：看 → 判断广告 → 真实点关闭 → 再看，最多 MAX_ROUNDS 轮 ----
    final_cls = None
    close_attempted = 0
    popup_unclosable = 0
    for rnd in range(MAX_ROUNDS):
        clear_popups(url_key)    # 每轮先清原生弹窗，避免挡点击
        elems, err = look_web()
        if err:
            return "skipped", f"扫描失败: {err}"
        cls = qwen_classify(client, elems)
        if "error" in cls:
            return "skipped", cls["error"]
        final_cls = cls

        # 有可关闭的广告弹窗 → 真实鼠标点关闭按钮；找不到关闭按钮就停下，绝不瞎点
        if cls.get("ad_popup"):
            close_idx = cls.get("close_idx", -1)
            if 0 <= close_idx < len(elems):
                target = elems[close_idx]
                click_elem(target)
                close_attempted += 1
                do_report(f"{url_key} 真实鼠标关闭广告弹窗：点「{target['name']}」@({target['cx']},{target['cy']})")
                wait(2.0)
                continue      # 关完再看，确认弹窗没了
            popup_unclosable = len(cls.get("ad_popup", []))
        break                 # 没有可关的弹窗了（或关不掉），进入校验

    if final_cls is None:
        return "skipped", "未完成分类"

    # ---- 校验：只补不漏。business 是页面结构，实时有、DB 无的 = 漏爬候选 ----
    # 子菜单(submenu)是全局左侧导航，每个子页都一样；只在导航首页(无 _ 后缀)补一次，
    # 子页重复补会污染数据团，并导致「连续多页补漏内容高度重复」的误报暂停。
    struct_names = set()
    idx_by_name = {}
    struct_idxs = list(final_cls.get("business", []))
    if "_" not in url_key:
        struct_idxs += list(final_cls.get("submenu", []))
    for i in struct_idxs:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(elems):
            nm = elems[i]["name"].strip().lower()
            struct_names.add(nm)
            idx_by_name.setdefault(nm, i)

    missing = sorted(nm for nm in struct_names if nm and nm not in db_names)
    ad_parts = [f"应用市场卡片 {len(final_cls.get('ad_card', []))} 个(已排除)"]
    if close_attempted:
        ad_parts.append(f"真实关闭广告弹窗 {close_attempted} 个")
    if popup_unclosable:
        ad_parts.append(f"有 {popup_unclosable} 个弹窗但没找到关闭按钮(未点)")
    elif final_cls.get("ad_popup"):
        ad_parts.append(f"广告弹窗 {len(final_cls.get('ad_popup', []))} 个(无关闭钮)")
    ad_note = "、".join(ad_parts)

    if not missing:
        do_report(f"{url_key} 校验通过：{ad_note}，无漏爬结构元素")
        return "ok", f"正常（{ad_note}）"

    # 有漏爬候选，让 qwen 决定补哪些（排除它刚判为广告的，二次确认）
    keep_names = ask_keep(client, url_key, missing, final_cls)
    if keep_names is None:
        q = (f"页面 {url_key} 有 {len(missing)} 个实时有、数据库无的结构元素，"
             f"操控员拿不准要不要补：{missing[:8]}")
        state["questions"].append({"url_key": url_key, "question": q,
                                   "ts": datetime.now().strftime("%H:%M:%S")})
        return "ask", q

    added = 0
    if keep_names:
        db = StoreDB()
        page = db.get_page_by_url_key(APP_SLUG, url_key)
        if page:
            pid = page["id"]
            for nm in keep_names:
                i = idx_by_name.get(nm.lower())
                if i is None:
                    continue
                e = elems[i]
                db.upsert_element(pid, {
                    "elem_type": e["type"], "name": e["name"],
                    "x": e["x"], "y": e["y"], "w": 0, "h": 0,
                })
                added += 1
        db.close()

    if added:
        RECENT_URLS.append(_url_path(get_qianniu_url()))
        if len(RECENT_URLS) > 8:
            RECENT_URLS.pop(0)
        do_report(f"{url_key} 补漏 {added} 个结构元素：{keep_names}（{ad_note}）")
        return "fixed", f"补漏 {added} 个（{ad_note}）"
    do_report(f"{url_key} 校验通过（候选 {len(missing)} 个判定为动态/无漏爬）：{ad_note}")
    return "ok", f"正常（{ad_note}，候选 {len(missing)} 个不补）"


KEEP_SYSTEM = """你是千牛商家工作台的膜操控员，负责判断「实时扫描有、但数据库没有」的结构元素
是否真漏爬了、要不要补进数据库。
给你一份候选名单（每行一个名字）。这些名字已经排除了广告(应用市场卡片)。
判断标准：
- 真业务功能（菜单/按钮/表单/功能入口，如 待发货/包裹中心/批量修改/导出 等）→ 补
- 拿不准的、或像动态内容（价格/日期/用户名/营销文案）→ 不补
只输出 JSON：{"keep":["要补的名字"], "comment":"一句话"}，都不补则 keep 为空数组 []。
"""


def ask_keep(client, url_key, missing, cls):
    """让 qwen 从 missing 名单里挑出真结构（二次确认，防误补广告）。返回 keep 列表或 None(失败)。"""
    feed = "\n".join(f"- {nm}" for nm in missing)
    try:
        resp = client.chat(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": KEEP_SYSTEM},
                {"role": "user", "content": f"页面 {url_key}\n\n候选名单：\n{feed}"},
            ],
            format="json",
            options={"temperature": 0.0},
        )
    except Exception:
        return None
    d = _extract_json(resp["message"]["content"])
    keep = d.get("keep", []) or []
    return [k for k in keep if k in missing]   # 只保留合法名字


# ============================================================
# 调试：只扫描分类当前页（不导航不点击）
# ============================================================
def cmd_classify():
    client = DeepSeekClient()
    elems, err = look_web()
    if err:
        print("扫描失败:", err)
        return
    print(f"扫描到 {len(elems)} 个网页元素，让 qwen 分类 ...\n", flush=True)
    cls = qwen_classify(client, elems)
    for k in ("nav", "submenu", "ad_popup", "ad_card", "business", "other"):
        print(f"{k:10}: {cls.get(k)}")
    print("close_idx:", cls.get("close_idx"), "| comment:", cls.get("comment"))
    print("\n--- 元素清单 ---")
    print(_fmt_elems(elems))


# ============================================================
# 主流程
# ============================================================
def main():
    import argparse
    ap = argparse.ArgumentParser(description="膜式操控员校验器 v2")
    ap.add_argument("--limit", type=int, help="只校验前 N 个待校验页")
    ap.add_argument("--page", help="只校验指定 url_key（如 1.4 或 1.4_3）")
    ap.add_argument("--list", action="store_true", help="只看进度")
    ap.add_argument("--classify", action="store_true", help="只扫描分类当前页（调试）")
    args = ap.parse_args()

    if args.classify:
        cmd_classify()
        return

    state = load_state()

    if args.list:
        cnt = {}
        for uk, v in state.get("status", {}).items():
            st = v.get("st", "skipped")
            cnt[st] = cnt.get(st, 0) + 1
        pending, total, done = list_pending(state)
        print(f"共 {total} 页，已校验 {done}，待校验 {len(pending)}")
        print(f"状态分布: {cnt}")
        if state.get("questions"):
            print(f"\n待求助问题 {len(state['questions'])} 个：")
            for q in state["questions"]:
                print(f"  - [{q['url_key']}] {q['question']}")
        return

    pending, total, done = list_pending(state)
    if args.page:
        pending = [args.page]
    if args.limit:
        pending = pending[:args.limit]

    if not pending:
        print("✅ 没有待校验的页面，全部完成")
        return

    client = DeepSeekClient()

    print(f"== 膜式操控员校验：待校验 {len(pending)} 页（共 {total} 页，已 {done}） ==", flush=True)
    print("（真实鼠标操作，注意看光标会动）\n", flush=True)

    n_fixed = n_ok = n_skip = n_ask = 0
    for i, url_key in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] 校验 {url_key} ...", flush=True)
        try:
            st, note = verify_one_page(client, url_key, state)
        except Exception as e:
            st, note = "skipped", f"异常: {str(e)[:120]}"
        state["status"][url_key] = {"st": st, "note": note,
                                    "ts": datetime.now().strftime("%H:%M:%S")}
        save_state(state)
        print(f"  → {st}: {note}", flush=True)
        if st == "fixed":
            n_fixed += 1
        elif st == "ok":
            n_ok += 1
        elif st == "ask":
            n_ask += 1
        else:
            n_skip += 1

        # 自愈检测：连续 3 页补漏内容高度重复 = 疑似页面没切换（跳错页），暂停求助
        if _detect_repeat():
            msg = ("⚠️ 连续 3 页补漏内容高度重复，疑似页面未切换/跳错页，"
                   "已暂停，需人工确认")
            do_report(msg)
            state["questions"].append({"url_key": url_key, "question": msg,
                                       "ts": datetime.now().strftime("%H:%M:%S")})
            save_state(state)
            print(f"  {msg}", flush=True)
            break

    print(f"\n===== 校验汇总 =====\n待校验 {len(pending)} | 补漏 {n_fixed} | 正常 {n_ok} | 跳过 {n_skip} | 求助 {n_ask}", flush=True)
    if state.get("questions"):
        print(f"\n⚠️ 有 {len(state['questions'])} 个问题要问用户：", flush=True)
        for q in state["questions"][-n_ask:]:
            print(f"  - [{q['url_key']}] {q['question']}", flush=True)


if __name__ == "__main__":
    main()
