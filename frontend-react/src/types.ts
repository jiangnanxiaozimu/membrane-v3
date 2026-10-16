// 后端 /api/v2/* 返回类型（与 server/api_server.py + store_db.py 对齐）

export interface GraphState {
  id: string
  name: string
  layer?: number
  elements?: unknown[]
  url_pattern?: string
}

export interface GraphEdge {
  id: string
  from_state: string
  from_element?: string
  action?: { type?: string }
  to_state: string
}

export interface Graph {
  version?: number
  app_id?: string
  description?: string
  states: GraphState[]
  edges: GraphEdge[]
}

export interface WorkflowMeta {
  id: string
  name: string
  tenant_id?: string | null
  updated_at?: string
  graph?: Graph
}

export interface BackendStatus {
  ok: boolean
  lines: string[]
}

export interface HealthStatus {
  status: string
  tenant: string | null
  db: string
  stats: { apps: number; pages: number; workflows: number }
}

export interface LogEntry {
  step: number
  type: string
  edge?: string
  state?: string
  to_state?: string
  action?: string
  element?: string
  intent?: string
  candidate?: { cx?: number; cy?: number; name?: string } | null
  decision?: { reason?: string; confidence?: number; approved?: boolean } | null
  expected?: string
  got?: string
  here?: string
}

export interface RunResult {
  wf_id: string
  name: string
  dry_run: boolean
  bounds?: { left: number; top: number; width: number; height: number } | null
  log: LogEntry[]
}

export interface ScanElement {
  name: string
  type: string
  x: number
  y: number
  w: number
  h: number
  cx: number
  cy: number
}

export interface ScanFrame {
  image_b64: string
  width: number
  height: number
  count: number
  elements: ScanElement[]
}

// ── 数据团（membrane.db）浏览 ──────────────────────────────────────
export interface DataApp {
  id: string
  name?: string
  tenant_id?: string | null
}

export interface DataPage {
  id: number
  app_id: string
  url_key?: string | null
  tenant_id?: string | null
}

export interface DataElement {
  type?: string
  name?: string
  x?: number
  y?: number
  w?: number
  h?: number
  [k: string]: unknown
}

// ── 导航拓扑（/api/v2/navmap） ──────────────────────────────
export interface NavState {
  id: string
  name: string
  x: number
  y: number
  z: number
  layer: number
}

export interface NavElement {
  id: string
  name?: string
  type?: string
  x: number
  y: number
  z: number
  state_id?: string
  layer?: number
  has_out?: boolean
}

export interface NavEdge {
  id: string
  action?: string
  from_element?: string
  from_state: string
  to_state: string
}

export interface NavData {
  root: string
  plane_width?: number
  plane_height?: number
  states: NavState[]
  elements: NavElement[]
  edges: NavEdge[]
}

export interface NavMapResult {
  app: string
  stats: { states: number; main: number; sub: number; elements: number; edges: number }
  data: NavData
}

// ── 渲染后端状态（/api/v2/render/status） ──────────────────
export interface RenderStatus {
  ok: boolean
  url: string
  system?: Record<string, unknown>
  error?: string
}
