import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { DockviewApi } from 'dockview'
import { api } from './api'
import type { BackendStatus, Graph, LogEntry, WorkflowMeta } from './types'

// 活动栏模块（VSCode 式左侧图标列）
export type ModuleId = 'screens' | 'workflows' | 'datacorpus' | 'execute' | 'nav' | 'settings'

// 副屏槽位：主窗口显示哪块副屏。当前仅第 0 屏有真实数据（launch_state.json 一块副屏）。
export type ScreenId = 0 | 1 | 2

interface AppStore {
  // 后端状态
  backend: BackendStatus | null
  backendError: string | null
  refreshBackend: () => Promise<void>
  // 工作流
  workflows: WorkflowMeta[]
  loadWorkflows: () => Promise<void>
  selectedId: string | null
  selectedName: string
  graph: Graph | null
  selectWorkflow: (id: string) => Promise<void>
  // 执行
  logs: LogEntry[]
  runMeta: { name: string; dry_run: boolean } | null
  running: boolean
  runWorkflow: (execute: boolean) => Promise<void>
  // 副屏墙
  activeScreen: ScreenId
  setActiveScreen: (s: ScreenId) => void
  // 面板聚焦（菜单栏「查看」→ 激活对应 dockview 面板）
  dockviewApi: DockviewApi | null
  setDockviewApi: (api: DockviewApi | null) => void
  focusPanel: (id: string) => void
}

const Ctx = createContext<AppStore | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const [backend, setBackend] = useState<BackendStatus | null>(null)
  const [backendError, setBackendError] = useState<string | null>(null)
  const [workflows, setWorkflows] = useState<WorkflowMeta[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedName, setSelectedName] = useState('')
  const [graph, setGraph] = useState<Graph | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [runMeta, setRunMeta] = useState<{ name: string; dry_run: boolean } | null>(null)
  const [running, setRunning] = useState(false)
  const [activeScreen, setActiveScreen] = useState<ScreenId>(0)
  const [dockviewApi, setDockviewApi] = useState<DockviewApi | null>(null)

  const focusPanel = useCallback((id: string) => {
    setDockviewApi((api) => {
      const panel = api?.getPanel(id)
      if (panel) panel.api.setActive()
      return api
    })
  }, [])

  const refreshBackend = useCallback(async () => {
    setBackendError(null)
    try {
      setBackend(await api.backendStatus())
    } catch (e) {
      setBackendError(String(e))
    }
  }, [])

  const loadWorkflows = useCallback(async () => {
    try {
      const r = await api.listWorkflows()
      setWorkflows(r.workflows)
    } catch (e) {
      console.error('listWorkflows 失败', e)
    }
  }, [])

  const selectWorkflow = useCallback(async (id: string) => {
    setSelectedId(id)
    setLogs([])
    setRunMeta(null)
    try {
      const r = await api.getWorkflow(id)
      setGraph(r.workflow.graph ?? null)
      setSelectedName(r.workflow.name ?? id)
    } catch (e) {
      console.error(e)
      setGraph(null)
      setSelectedName(id)
    }
  }, [])

  const runWorkflow = useCallback(
    async (execute: boolean) => {
      if (!selectedId) return
      setRunning(true)
      setLogs([])
      setRunMeta({ name: selectedName, dry_run: !execute })
      try {
        const r = await api.runWorkflow(selectedId, execute)
        setLogs(r.log)
        setRunMeta({ name: r.name, dry_run: r.dry_run })
      } catch (e) {
        setLogs([{ step: 0, type: 'rejected', decision: { reason: String(e), confidence: 0 } }])
      } finally {
        setRunning(false)
      }
    },
    [selectedId, selectedName],
  )

  useEffect(() => {
    refreshBackend()
    loadWorkflows()
  }, [refreshBackend, loadWorkflows])

  const value = useMemo<AppStore>(
    () => ({
      backend,
      backendError,
      refreshBackend,
      workflows,
      loadWorkflows,
      selectedId,
      selectedName,
      graph,
      selectWorkflow,
      logs,
      runMeta,
      running,
      runWorkflow,
      activeScreen,
      setActiveScreen,
      dockviewApi,
      setDockviewApi,
      focusPanel,
    }),
    [
      backend,
      backendError,
      refreshBackend,
      workflows,
      loadWorkflows,
      selectedId,
      selectedName,
      graph,
      selectWorkflow,
      logs,
      runMeta,
      running,
      runWorkflow,
      activeScreen,
      dockviewApi,
      focusPanel,
    ],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useApp(): AppStore {
  const v = useContext(Ctx)
  if (!v) throw new Error('useApp 必须在 AppProvider 内使用')
  return v
}
