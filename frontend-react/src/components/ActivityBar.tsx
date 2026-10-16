import type { ModuleId } from '../store'

// VSCode 式左侧活动栏：纯图标列，hover 显示提示，点击切换侧边栏视图。
interface Props {
  active: ModuleId
  onSelect: (m: ModuleId) => void
}

const ICON_PROPS = {
  width: 22,
  height: 22,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.7,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

function MonitorIcon() {
  return (
    <svg {...ICON_PROPS}>
      <rect x="3" y="4" width="18" height="13" rx="2" />
      <path d="M8 21h8M12 17v4" />
    </svg>
  )
}

function FlowIcon() {
  return (
    <svg {...ICON_PROPS}>
      <circle cx="5" cy="6" r="2.2" />
      <circle cx="19" cy="6" r="2.2" />
      <circle cx="12" cy="18" r="2.2" />
      <path d="M7.2 6h9.6M5 8.2V18M19 8.2v7.6M14.2 18H12M5 18h4.8" />
    </svg>
  )
}

function DatabaseIcon() {
  return (
    <svg {...ICON_PROPS}>
      <ellipse cx="12" cy="5" rx="7" ry="2.6" />
      <path d="M5 5v6c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V5" />
      <path d="M5 11v6c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6v-6" />
    </svg>
  )
}

function PlayIcon() {
  return (
    <svg {...ICON_PROPS}>
      <path d="M7 5l11 7-11 7z" />
    </svg>
  )
}

function CompassIcon() {
  return (
    <svg {...ICON_PROPS}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M15.5 8.5l-2 5-5 2 2-5z" />
    </svg>
  )
}

function GearIcon() {
  return (
    <svg {...ICON_PROPS}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M18.7 5.3l-2.1 2.1M7.4 16.6l-2.1 2.1" />
    </svg>
  )
}

const MODULES: { id: ModuleId; label: string; icon: JSX.Element }[] = [
  { id: 'screens', label: '屏幕', icon: <MonitorIcon /> },
  { id: 'workflows', label: '工作流', icon: <FlowIcon /> },
  { id: 'datacorpus', label: '数据团', icon: <DatabaseIcon /> },
  { id: 'execute', label: '执行', icon: <PlayIcon /> },
  { id: 'nav', label: '导航', icon: <CompassIcon /> },
  { id: 'settings', label: '设置', icon: <GearIcon /> },
]

export default function ActivityBar({ active, onSelect }: Props) {
  return (
    <div className="activity-bar">
      {MODULES.map((m) => (
        <button
          key={m.id}
          className={`activity-item ${active === m.id ? 'active' : ''}`}
          title={m.label}
          onClick={() => onSelect(m.id)}
        >
          {m.icon}
          <span className="activity-tip">{m.label}</span>
        </button>
      ))}
      <div className="flex-1" />
    </div>
  )
}
