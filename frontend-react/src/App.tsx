import { useCallback, useEffect, useRef, useState } from 'react'
import { DockviewReact } from 'dockview-react'
import { themeDark, type DockviewApi, type DockviewReadyEvent } from 'dockview'
import { AppProvider, useApp } from './store'
import type { ModuleId } from './store'
import MenuBar from './components/MenuBar'
import ActivityBar from './components/ActivityBar'
import SideBar from './components/SideBar'
import StatusBar from './components/StatusBar'
import ScreenPanel from './components/panels/ScreenPanel'

// 主工作区 dockview 组件注册表。
// 当前只挂「副屏画面」这一个完工面板；工作流编辑 / 导航图 / 渲染图均为未完工占位（见 interfaces/）。
const components = {
  screen: ScreenPanel,
}

// 默认布局：副屏画面占满主工作区。
function buildDefaultLayout(api: DockviewApi) {
  api.addPanel({ id: 'screen', component: 'screen', title: '副屏画面' })
}

function Layout() {
  const [activeModule, setActiveModule] = useState<ModuleId>('screens')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const apiRef = useRef<DockviewApi | null>(null)
  const { setDockviewApi } = useApp()

  const onReady = useCallback(
    (event: DockviewReadyEvent) => {
      apiRef.current = event.api
      setDockviewApi(event.api)
      buildDefaultLayout(event.api)
    },
    [setDockviewApi],
  )

  const onSelectModule = useCallback(
    (m: ModuleId) => {
      if (m === activeModule) {
        // 点击当前模块 → 折叠/展开侧边栏
        setSidebarOpen((v) => !v)
      } else {
        setActiveModule(m)
        setSidebarOpen(true)
      }
    },
    [activeModule],
  )

  // 菜单栏「查看 → 切换侧边栏」通过自定义事件触发（MenuBar 拿不到 Layout 的 sidebar 状态）
  useEffect(() => {
    const toggle = () => setSidebarOpen((v) => !v)
    window.addEventListener('membrane:toggle-sidebar', toggle)
    return () => window.removeEventListener('membrane:toggle-sidebar', toggle)
  }, [])

  return (
    <div className="app-root">
      <MenuBar />
      <div className="app-body">
        <ActivityBar active={activeModule} onSelect={onSelectModule} />
        {sidebarOpen && <SideBar active={activeModule} onClose={() => setSidebarOpen(false)} />}
        <div className="app-workspace">
          <DockviewReact
            components={components}
            theme={themeDark}
            onReady={onReady}
            className="dockview-instance"
          />
        </div>
      </div>
      <StatusBar />
    </div>
  )
}

export default function App() {
  return (
    <AppProvider>
      <Layout />
    </AppProvider>
  )
}
