import { useState } from 'react'
import { useApp } from '../store'

// 菜单栏：查看/运行接真实动作（聚焦面板 / 干跑真点），其余保留占位。
interface MenuItem {
  label: string
  hint?: string
  action?: () => void
  disabled?: boolean
}
interface MenuDef {
  label: string
  items: MenuItem[]
}

const placeholders = (items: string[]): MenuItem[] => items.map((label) => ({ label }))

export default function MenuBar() {
  const { focusPanel, runWorkflow, selectedId, running, refreshBackend } = useApp()
  const [open, setOpen] = useState<string | null>(null)

  const MENUS: MenuDef[] = [
    {
      label: '文件',
      items: [
        { label: '刷新后端状态', action: () => refreshBackend() },
        ...placeholders(['新建工作流', '打开…', '保存', '另存为…', '退出']),
      ],
    },
    { label: '编辑', items: placeholders(['撤销', '重做', '剪切', '复制', '粘贴']) },
    { label: '选择', items: placeholders(['全选', '取消选择', '查找元素…']) },
    {
      label: '查看',
      items: [
        { label: '副屏画面', action: () => focusPanel('screen') },
        { label: '工作流编辑（未完工）', disabled: true },
        { label: '导航图（未完工）', disabled: true },
        { label: '渲染图（未完工）', disabled: true },
        {
          label: '切换侧边栏',
          action: () => window.dispatchEvent(new CustomEvent('membrane:toggle-sidebar')),
        },
      ],
    },
    {
      label: '运行',
      items: [
        {
          label: '干跑（不动鼠标）',
          action: () => runWorkflow(false),
          disabled: !selectedId || running,
        },
        {
          label: '真点执行',
          action: () => runWorkflow(true),
          disabled: !selectedId || running,
          hint: '真点会真实移动鼠标点击，请盯着副屏',
        },
        { label: '停止', hint: '真点执行中不支持中途停止（后续迭代）' },
      ],
    },
    { label: '帮助', items: [{ label: '关于膜 3.0' }] },
  ]

  return (
    <div className="menu-bar">
      <div className="menu-bar-brand">膜</div>
      {MENUS.map((m) => (
        <div
          key={m.label}
          className={`menu-item ${open === m.label ? 'open' : ''}`}
          onMouseEnter={() => open !== null && setOpen(m.label)}
          onClick={() => setOpen(open === m.label ? null : m.label)}
        >
          {m.label}
          {open === m.label && (
            <div className="menu-dropdown">
              {m.items.map((it) => (
                <div
                  key={it.label}
                  className={`menu-dropdown-item${it.disabled ? ' disabled' : ''}`}
                  title={it.hint}
                  onClick={(e) => {
                    e.stopPropagation()
                    setOpen(null)
                    if (it.action && !it.disabled) it.action()
                    else if (!it.action) console.log(`[占位] ${m.label} → ${it.label}`)
                  }}
                >
                  {it.label}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
      <div className="flex-1" />
      <button className="menu-quick" title="撤销（占位）" onClick={() => console.log('[撤销] 占位')}>
        ↶
      </button>
      <button className="menu-quick" title="重做（占位）" onClick={() => console.log('[重做] 占位')}>
        ↷
      </button>
    </div>
  )
}
