import { useEffect, useState } from 'react'
import type { IDockviewPanelProps } from 'dockview'
import { API_BASE, api } from '../../api'
import { useApp } from '../../store'
import type { ScanFrame } from '../../types'

const TYPE_COLORS: Record<string, string> = {
  button: '#4ec9b0',
  a: '#c050c0',
  input: '#c8c840',
  text: '#5050c8',
  checkbox: '#c8c840',
  radio: '#8cc840',
  select: '#c86400',
  tab: '#c86400',
  menu: '#6440c8',
  tree: '#40c864',
  slider: '#c87800',
}

const colorFor = (t: string) => TYPE_COLORS[t.toLowerCase()] ?? '#4ec9b0'

// 副屏画面：实时流（ffmpeg gdigrab → MJPEG）+ 元素标注叠加。
// 画面走 /api/v2/scan/stream 实时动；标注框每 2s 轮询 scan/frame 拿坐标，用百分比对齐（副屏 1920x1080）。
export default function ScreenPanel(_props: IDockviewPanelProps) {
  const { activeScreen } = useApp()
  const [frame, setFrame] = useState<ScanFrame | null>(null)
  const [showBoxes, setShowBoxes] = useState(true)
  const [streamFailed, setStreamFailed] = useState(false)

  useEffect(() => {
    if (activeScreen !== 0) return
    let stop = false
    const poll = async () => {
      try {
        const f = await api.scanFrame()
        if (!stop) setFrame(f)
      } catch {
        /* 轮询失败忽略：实时流可能仍正常 */
      }
    }
    poll()
    const t = setInterval(poll, 2000)
    return () => {
      stop = true
      clearInterval(t)
    }
  }, [activeScreen])

  // 副屏 2/3 尚未接入真实源
  if (activeScreen !== 0) {
    return (
      <div className="panel-fill panel-placeholder">
        <div className="ph-icon">▣</div>
        <div className="ph-title">副屏 {activeScreen + 1}</div>
        <div className="ph-sub">此副屏尚未开启。当前仅副屏 1 接入真实源。</div>
      </div>
    )
  }

  return (
    <div className="panel-fill screen-panel">
      <div className="panel-toolbar">
        <span className="panel-toolbar-title">副屏 1 · 实时画面</span>
        <label className="panel-toolbar-item">
          <input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} /> 标注框
        </label>
        {frame && <span className="panel-toolbar-item">{frame.count} 元素</span>}
      </div>
      <div className="screen-canvas">
        {streamFailed && (
          <div className="screen-msg error">实时流连接失败 — 检查副屏是否激活（python launch_qianniu.py）</div>
        )}
        <div className="screen-wrap">
          <img
            src={`${API_BASE}/api/v2/scan/stream`}
            className="screen-stream"
            alt="副屏实时画面"
            onError={() => setStreamFailed(true)}
            onLoad={() => setStreamFailed(false)}
          />
          {showBoxes && frame && frame.elements.length > 0 && (
            <div className="screen-boxes">
              {frame.elements.map((el, i) => (
                <div
                  key={i}
                  className="screen-box"
                  title={`${el.name} (${el.type})`}
                  style={{
                    left: `${(el.x / frame.width) * 100}%`,
                    top: `${(el.y / frame.height) * 100}%`,
                    width: `${Math.max(el.w / frame.width, 0.002) * 100}%`,
                    height: `${Math.max(el.h / frame.height, 0.002) * 100}%`,
                    borderColor: colorFor(el.type),
                  }}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
