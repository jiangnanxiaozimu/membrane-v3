import { useEffect, useState } from 'react'
import { API_BASE, api } from '../api'
import { useApp } from '../store'
import type { ModuleId } from '../store'
import type { DataApp, DataElement, DataPage, HealthStatus, LogEntry } from '../types'

interface Props {
  active: ModuleId
  onClose: () => void
}

const MODULE_TITLES: Record<ModuleId, string> = {
  screens: '屏幕',
  workflows: '工作流',
  datacorpus: '数据团',
  execute: '执行',
  nav: '导航',
  settings: '设置',
}

const FAIL_TYPES = ['not_found', 'rejected', 'migration_failed']
const OK_TYPES = ['executed', 'already_there']

function fmtDecision(log: LogEntry): string {
  const d = log.decision
  if (!d) return ''
  const pos = log.candidate?.cx != null ? `(${log.candidate.cx},${log.candidate.cy})` : ''
  const conf = d.confidence != null ? ` · ${(d.confidence * 100).toFixed(0)}%` : ''
  return `${d.reason ?? ''}${pos ? ' @' + pos : ''}${conf}`
}

// ── 副屏墙（PPT 式缩略图，点击切换主窗口画面） ─────────────────────────
function ScreensView() {
  const { activeScreen, setActiveScreen } = useApp()
  const [thumb, setThumb] = useState<string | null>(null)

  useEffect(() => {
    let stop = false
    const load = async () => {
      try {
        const f = await api.scanFrame()
        if (!stop) setThumb(f.image_b64)
      } catch {
        /* 副屏未激活等，忽略 */
      }
    }
    load()
    const t = setInterval(load, 5000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [])

  return (
    <div className="sidebar-view">
      <div className="sidebar-section-title">屏幕墙 · 点击切换主画面</div>
      <div className="screen-wall">
        <button className={`screen-thumb ${activeScreen === 0 ? 'active' : ''}`} onClick={() => setActiveScreen(0)}>
          <div className="screen-thumb-preview">
            {thumb ? (
              <img src={`data:image/png;base64,${thumb}`} className="screen-thumb-img" alt="副屏 1" />
            ) : (
              <span className="screen-thumb-off">加载中…</span>
            )}
          </div>
          <div className="screen-thumb-label">
            副屏 1 <span className="dot on" />
          </div>
        </button>
        <button className={`screen-thumb ${activeScreen === 1 ? 'active' : ''}`} onClick={() => setActiveScreen(1)}>
          <div className="screen-thumb-preview">
            <span className="screen-thumb-off">未开启</span>
          </div>
          <div className="screen-thumb-label">
            副屏 2 <span className="dot off" />
          </div>
        </button>
        <button className={`screen-thumb ${activeScreen === 2 ? 'active' : ''}`} onClick={() => setActiveScreen(2)}>
          <div className="screen-thumb-preview">
            <span className="screen-thumb-off">未开启</span>
          </div>
          <div className="screen-thumb-label">
            副屏 3 <span className="dot off" />
          </div>
        </button>
      </div>
    </div>
  )
}

// ── 工作流列表 ─────────────────────────────────────────────────────────
function WorkflowsView() {
  const { workflows, selectedId, selectWorkflow } = useApp()
  return (
    <div className="sidebar-view">
      <div className="sidebar-section-title">工作流文件</div>
      {workflows.length === 0 ? (
        <div className="sidebar-empty">工作流库为空</div>
      ) : (
        workflows.map((w) => {
          const steps = w.graph?.edges?.length ?? 0
          const active = w.id === selectedId
          return (
            <button key={w.id} className={`sidebar-row ${active ? 'active' : ''}`} onClick={() => selectWorkflow(w.id)}>
              <div className="sidebar-row-title">{w.name}</div>
              <div className="sidebar-row-sub">{w.id} · {steps} 步</div>
            </button>
          )
        })
      )}
    </div>
  )
}

// ── 数据团（应用 → 页面 → 元素 三级浏览） ────────────────────────────
function DatacorpusView() {
  const [apps, setApps] = useState<DataApp[]>([])
  const [pages, setPages] = useState<DataPage[]>([])
  const [elements, setElements] = useState<DataElement[]>([])
  const [elemTotal, setElemTotal] = useState(0)
  const [appId, setAppId] = useState<string | null>(null)
  const [pageId, setPageId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadApps = async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.listApps()
      setApps(r.apps)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const openApp = async (id: string) => {
    setAppId(id)
    setPageId(null)
    setElements([])
    setLoading(true)
    setError(null)
    try {
      const r = await api.listPages(id)
      setPages(r.pages)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const openPage = async (id: number) => {
    setPageId(id)
    setLoading(true)
    setError(null)
    try {
      const r = await api.listElements(id)
      setElements(r.elements.slice(0, 100))
      setElemTotal(r.count)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  const back = () => {
    if (pageId != null) {
      setPageId(null)
      setElements([])
    } else if (appId != null) {
      setAppId(null)
      setPages([])
    }
  }

  useEffect(() => {
    loadApps()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const crumb = appId ? `应用 ${appId}${pageId != null ? ` → 页面 #${pageId}` : ''}` : '全部应用'

  return (
    <div className="sidebar-view sidebar-view-fill">
      <div className="sidebar-section-title">数据团 · 元素库</div>
      <div className="dc-crumbs">
        {(appId != null || pageId != null) && (
          <button className="dc-back" onClick={back} title="返回上一层">
            ←
          </button>
        )}
        <span className="dc-crumb-text">{crumb}</span>
      </div>
      {loading && <div className="sidebar-empty">加载中…</div>}
      {error && <div className="dc-error">{error}</div>}
      {!loading && !error && (
        <div className="dc-list">
          {appId == null &&
            apps.map((a) => (
              <button key={a.id} className="sidebar-row" onClick={() => openApp(a.id)}>
                <div className="sidebar-row-title">{a.name || a.id}</div>
                <div className="sidebar-row-sub">{a.id}</div>
              </button>
            ))}
          {appId != null && pageId == null && pages.length === 0 && (
            <div className="sidebar-empty">该应用下没有页面记录</div>
          )}
          {appId != null &&
            pageId == null &&
            pages.map((p) => (
              <button key={p.id} className="sidebar-row" onClick={() => openPage(p.id)}>
                <div className="sidebar-row-title">{p.url_key || `页面 #${p.id}`}</div>
                <div className="sidebar-row-sub">页面 id {p.id}</div>
              </button>
            ))}
          {pageId != null && (
            <>
              <div className="dc-hint">共 {elemTotal} 个元素，显示前 100</div>
              {elements.map((el, i) => (
                <div key={i} className="dc-elem" title={String(el.type ?? '')}>
                  <span className="dc-elem-type">{el.type ?? '?'}</span>
                  <span className="dc-elem-name">{el.name || '(未命名)'}</span>
                  <span className="dc-elem-pos">
                    ({el.x ?? 0},{el.y ?? 0})
                  </span>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── 执行（干跑/真点 + 日志） ───────────────────────────────────────────
function ExecuteView() {
  const { selectedName, logs, runMeta, running, selectedId, runWorkflow } = useApp()
  const nFail = logs.filter((l) => FAIL_TYPES.includes(l.type)).length
  const nOk = logs.filter((l) => OK_TYPES.includes(l.type)).length
  return (
    <div className="sidebar-view sidebar-view-fill">
      <div className="sidebar-section-title">执行 · {selectedName || '未选择工作流'}</div>
      <div className="exec-actions">
        <button className="btn btn-primary" disabled={!selectedId || running} onClick={() => runWorkflow(false)}>
          干跑
        </button>
        <button className="btn btn-danger" disabled={!selectedId || running} onClick={() => runWorkflow(true)}>
          真点
        </button>
      </div>
      {runMeta && (
        <div className="exec-meta" style={{ color: runMeta.dry_run ? 'var(--yellow)' : 'var(--red)' }}>
          {runMeta.name} · {runMeta.dry_run ? '干跑（不动鼠标）' : '真执行！'}
        </div>
      )}
      {running && <div className="exec-running">执行中…（真点请盯着屏幕）</div>}
      <div className="exec-logs">
        {logs.length === 0 && <div className="sidebar-empty">点「干跑」查看执行日志</div>}
        {logs.map((l, i) => (
          <div key={i} className={`log-${l.type}`}>
            <span className="log-step">#{l.step}</span> [{l.type}]
            {l.intent ? ` ${l.intent}` : l.element ? ` ${l.element}` : ''}
            {l.got ? ` → ${l.got}` : ''}
            <span className="log-decision"> {fmtDecision(l)}</span>
          </div>
        ))}
      </div>
      {logs.length > 0 && (
        <div className="exec-summary">
          成功 <span className="ok">{nOk}</span> / 失败 <span className="fail">{nFail}</span> / 共 {logs.length} 条
        </div>
      )}
    </div>
  )
}

// ── 导航 ───────────────────────────────────────────────────────────────
function NavView() {
  return (
    <div className="sidebar-view">
      <div className="sidebar-section-title">导航</div>
      <div className="sidebar-empty">
        全站导航拓扑（3D 导航图）规划中，尚未完工。占位：接入后展示「根 → 主页 → 子菜单」拓扑。
      </div>
    </div>
  )
}

// ── 设置（系统信息 + 后端组件状态） ─────────────────────────────────────
function SettingsView() {
  const { backend, backendError } = useApp()
  const [health, setHealth] = useState<HealthStatus | null>(null)

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null))
  }, [])

  return (
    <div className="sidebar-view">
      <div className="sidebar-section-title">系统信息</div>
      <div className="settings-block">
        <div className="settings-row">
          <span className="settings-key">后端</span>
          <span className="settings-val">{API_BASE}</span>
        </div>
        {health && (
          <>
            <div className="settings-row">
              <span className="settings-key">数据团库</span>
              <span className="settings-val settings-mono">{health.db}</span>
            </div>
            <div className="settings-row">
              <span className="settings-key">统计</span>
              <span className="settings-val">
                {health.stats.apps} 应用 · {health.stats.pages} 页 · {health.stats.workflows} 工作流
              </span>
            </div>
          </>
        )}
      </div>
      <div className="sidebar-section-title">后端组件</div>
      {backendError ? (
        <div className="dc-error">{backendError}</div>
      ) : backend ? (
        <div className="settings-lines">
          {backend.lines.map((l, i) => (
            <div
              key={i}
              className={`settings-line ${l.includes('[✓]') ? 'ok' : l.includes('[✗]') ? 'fail' : ''}`}
            >
              {l}
            </div>
          ))}
        </div>
      ) : (
        <div className="sidebar-empty">检查中…</div>
      )}
    </div>
  )
}

export default function SideBar({ active, onClose }: Props) {
  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <span>{MODULE_TITLES[active]}</span>
        <button className="sidebar-close" title="折叠侧边栏" onClick={onClose}>
          ×
        </button>
      </div>
      {active === 'screens' && <ScreensView />}
      {active === 'workflows' && <WorkflowsView />}
      {active === 'datacorpus' && <DatacorpusView />}
      {active === 'execute' && <ExecuteView />}
      {active === 'nav' && <NavView />}
      {active === 'settings' && <SettingsView />}
    </div>
  )
}
