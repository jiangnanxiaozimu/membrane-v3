import { useApp } from '../store'

function lineColor(line: string): string {
  if (line.includes('[✓]')) return 'var(--green)'
  if (line.includes('[✗]')) return 'var(--red)'
  if (line.includes('[△]')) return 'var(--yellow)'
  return 'var(--text-dim)'
}

export default function StatusBar() {
  const { backend, backendError, refreshBackend, selectedName, activeScreen } = useApp()

  return (
    <div className="status-bar">
      {backend ? (
        <div className="status-lines">
          {backend.lines.map((l, i) => (
            <span key={i} style={{ color: lineColor(l) }}>
              {l.replace(/^\[[✓✗△]\]\s*/, '')}
            </span>
          ))}
        </div>
      ) : backendError ? (
        <span className="status-error">{backendError}</span>
      ) : (
        <span className="status-dim">检查后端中…</span>
      )}
      <span className={`status-dot ${backend ? (backend.ok ? 'ok' : 'warn') : 'err'}`} />
      <span className="status-dim">副屏 {activeScreen + 1}</span>
      {selectedName && <span className="status-dim">工作流：{selectedName}</span>}
      <button className="status-refresh" onClick={refreshBackend} title="刷新后端状态">
        ↻
      </button>
    </div>
  )
}
