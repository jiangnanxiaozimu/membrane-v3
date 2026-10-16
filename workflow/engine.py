"""
workflow/engine.py — 工作流执行引擎（MVP 灵魂）

职责：跑流程。从根状态出发，一步一步：
  看（scan）→ 认（match 找候选）→ 判断（operator 校验）→ 点（execute）→ 迁移

纯编排，不 import 具体实现：scan / match / execute 由调用方注入（真实现来自 scan/exec 抽屉），
operator 默认注入 membrane_operator.Operator。这样引擎可独立单测（喂假服务）。

依赖注入是刻意的——执行引擎不该内部 import 扫描器/鼠标，否则又变成 membrane_app.pyw
那种「改一处崩一片」的耦合体。所有 IO 走门口（函数参数），不走窗户（内部 import）。
"""
from datetime import datetime


ACTION_TYPES = {"click", "input", "scroll", "hover", "wait"}
VERB = {"click": "点击", "scroll": "滚动", "hover": "悬停", "wait": "等待", "input": "输入"}


def _migrates(edge):
    """这条边是否迁移状态。对齐 WORKFLOW_3D_DESIGN.md §1：
    click 迁移；input/scroll/hover/wait 一般不迁移（当前状态里的中间操作）。"""
    atype = (edge.get("action") or {}).get("type")
    return atype == "click" or edge.get("from_state") != edge.get("to_state")


def _default_match(intent_element, live_elements):
    """默认匹配：在 live 扫描结果里找 type+name 相同的元素（= actor_library 的元素指纹键）。"""
    if not intent_element or not live_elements:
        return None
    t = intent_element.get("type", "")
    n = (intent_element.get("name", "") or "").strip()
    for el in live_elements:
        if el.get("type") == t and (el.get("name", "") or "").strip() == n:
            return el
    return None


class Engine:
    """状态机执行引擎。一次 run() = 跑一条路径。"""

    def __init__(self, graph, operator=None, scan=None, match=None, execute=None,
                 verify_migration=None, match_state=None):
        self.graph = graph
        self.states = {s["id"]: s for s in graph.get("states", [])}
        self.operator = operator          # 需有 decide(intent, candidate, context) -> dict
        self.scan = scan or (lambda state: [])        # -> live 元素列表
        self.match = match or _default_match
        self.execute = execute or (lambda action, target, edge: None)
        # 迁移验证：点完一条会迁移的边后，确认页面真的跳到了 to_state。
        # verify_migration(edge) -> (ok: bool, got: 实际识别到的状态标识)。
        # None = 不验证（保持旧行为，纯逻辑测试用）。
        self.verify_migration = verify_migration
        # 状态自检：match_state() -> 当前 state_id 或 None（URL path 匹配）。
        # 两个用途：①点边前判断「已在目标页」则跳过点击；②迁移失败时判定「现在实际在哪」。
        self.match_state = match_state
        self.log = []

    # ---- 图内部查询 ----
    def _outgoing(self, state_id):
        return [e for e in self.graph.get("edges", []) if e["from_state"] == state_id]

    def _element(self, state_id, element_id):
        if not element_id:
            return None
        state = self.states.get(state_id, {})
        for el in state.get("elements", []):
            if el.get("id") == element_id:
                return el
        return None

    def _choose_edge(self, edges, state_id):
        """v1：线性，取第一条出边。分支规划（operator 挑路 / 目标驱动）留到 v2。"""
        return edges[0] if edges else None

    def _intent(self, edge, element):
        atype = (edge.get("action") or {}).get("type") or "?"
        name = (element or {}).get("name") or "?"
        if atype == "input":
            return f"在「{name}」输入「{edge.get('input')}」"
        verb = VERB.get(atype, atype)
        return f"{verb}「{name}」"

    # ---- 主循环 ----
    def run(self, root_state_id=None, max_steps=100):
        """跑流程，返回执行日志列表（每步一个 dict，含 decision）。"""
        self.log = []
        if root_state_id is None:
            # 默认根：layer=0 的状态，否则第一个状态
            root_state_id = next(
                (s["id"] for s in self.graph.get("states", []) if s.get("layer") == 0),
                (self.graph.get("states") or [{}])[0].get("id"),
            )
        if root_state_id not in self.states:
            raise ValueError(f"根状态不存在: {root_state_id}")

        current = root_state_id
        visited = [current]
        steps = 0

        while steps < max_steps:
            edges = self._outgoing(current)
            if not edges:
                self.log.append({"step": steps, "type": "terminal", "state": current})
                break
            edge = self._choose_edge(edges, current)

            # 已在目标页则跳过点击（点一级导航 no-op 的边界修复）。
            # 例：路径第一步点「财务」，但当前已经在财务导航下，点一级导航不会跳转 → 直接算迁移成功。
            if (self.match_state is not None and _migrates(edge)
                    and self.match_state() == edge.get("to_state")):
                self.log.append({
                    "step": steps, "type": "already_there", "edge": edge["id"],
                    "state": current, "to_state": edge.get("to_state"),
                    "decision": {"approved": True, "confidence": 1.0,
                                 "reason": "已在目标页，跳过点击", "correction": None},
                })
                current = edge["to_state"]
                visited.append(current)
                steps += 1
                continue

            element = self._element(current, edge.get("from_element"))
            action = edge.get("action") or {}
            intent = self._intent(edge, element)

            # 看：扫描当前状态
            live = self.scan(self.states[current]) or []
            # 认：找候选（type+name 匹配）
            candidate = self.match(element, live) if element else None

            # 边要求操作某个元素，但屏幕上没扫到 → 硬失败停步。
            # 不拿语义锚（图里的 element）当候选喂裁判——语义锚没有坐标，不是真元素。
            if element is not None and candidate is None:
                self.log.append({
                    "step": steps, "type": "not_found", "edge": edge["id"], "state": current,
                    "action": action.get("type"), "element": element.get("name"),
                    "intent": intent, "candidate": None,
                    "decision": {"approved": False, "confidence": 0.0,
                                 "reason": "屏幕上未找到目标元素", "correction": None},
                })
                break

            # 判断：operator 校验（无 operator 则放行，方便纯逻辑测试）
            decision = {"approved": True, "confidence": 1.0,
                        "reason": "无操控员，放行", "correction": None}
            if self.operator is not None:
                decision = self.operator.decide(
                    intent, candidate or {},
                    {"state": current, "path": list(visited)},
                )

            record = {
                "step": steps, "type": None, "edge": edge["id"], "state": current,
                "action": action.get("type"), "element": (element or {}).get("name"),
                "intent": intent, "candidate": candidate, "decision": decision,
            }

            if not decision.get("approved"):
                record["type"] = "rejected"
                self.log.append(record)
                break

            # 点：执行
            self.execute(action, candidate, edge)
            record["type"] = "executed"
            self.log.append(record)

            # 迁移（点完不盲走，先验证页面真的跳到了 to_state —— 纠错闭环）
            if _migrates(edge):
                if self.verify_migration is not None:
                    ok, got = self.verify_migration(edge)
                    if not ok:
                        # 判定「现在实际在哪」，供纠错（回原页 / 报告卡点）。
                        here = self.match_state() if self.match_state is not None else None
                        self.log.append({
                            "step": steps, "type": "migration_failed", "edge": edge["id"],
                            "state": current, "expected": edge.get("to_state"),
                            "got": got, "here": here,
                            "decision": {"approved": False, "confidence": 0.0,
                                         "reason": f"迁移验证失败：页面未跳到 {edge.get('to_state')}（实际 path {got}，判定在 {here}）",
                                         "correction": None},
                        })
                        break
                current = edge["to_state"]
                visited.append(current)
            steps += 1
        else:
            self.log.append({"step": steps, "type": "max_steps", "state": current})

        return self.log
