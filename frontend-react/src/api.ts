import type {
  BackendStatus,
  DataApp,
  DataElement,
  DataPage,
  Graph,
  HealthStatus,
  NavMapResult,
  RenderStatus,
  RunResult,
  ScanFrame,
  WorkflowMeta,
} from './types'

// 后端固定端口 8931（server/api_server.py 默认，可被 UI_API_PORT 覆盖后需改这里）。
// vite dev 与 pywebview 生产都走绝对地址，后端已加 CORS 头。
export const API_BASE = 'http://127.0.0.1:8931'

async function req<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  let r: Response
  try {
    r = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (e) {
    throw new Error(`后端连不上（${API_BASE}）：请先启动 python server/api_server.py --port 8931`)
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const j = await r.json()
      detail = j.detail || j.error || detail
    } catch {
      /* 非 JSON 错误体，保留 HTTP 状态 */
    }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

export const api = {
  backendStatus: () => req<BackendStatus>('GET', '/api/v2/backend/status'),
  listWorkflows: () => req<{ count: number; workflows: WorkflowMeta[] }>('GET', '/api/v2/workflows'),
  getWorkflow: (id: string) => req<{ workflow: WorkflowMeta }>('GET', `/api/v2/workflows/${id}`),
  runWorkflow: (id: string, execute: boolean) =>
    req<RunResult>('POST', `/api/v2/workflows/${id}/run`, { execute }),
  scanFrame: () => req<ScanFrame>('GET', '/api/v2/scan/frame'),
  // 数据团（membrane.db）
  listApps: () => req<{ tenant: string; count: number; apps: DataApp[] }>('GET', '/api/v2/apps'),
  listPages: (appId?: string) =>
    req<{ tenant: string; count: number; pages: DataPage[] }>(
      'GET',
      `/api/v2/pages${appId ? `?app_id=${encodeURIComponent(appId)}` : ''}`,
    ),
  listElements: (pageId: number) =>
    req<{ tenant: string; count: number; elements: DataElement[] }>(
      'GET',
      `/api/v2/elements?page_id=${pageId}`,
    ),
  // 导航拓扑 + 渲染后端状态
  navmap: () => req<NavMapResult>('GET', '/api/v2/navmap'),
  renderStatus: () => req<RenderStatus>('GET', '/api/v2/render/status'),
  health: () => req<HealthStatus>('GET', '/api/v2/health'),
}
