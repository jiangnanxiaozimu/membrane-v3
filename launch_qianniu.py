"""
launch_qianniu.py — 一次性启动：虚拟屏(副屏) + Edge(千牛 Web) + 设置扫描边界，并保持运行。

用法:
  python launch_qianniu.py           # 启动后保持运行（虚拟屏心跳线程保活）
  python launch_qianniu.py --status  # 只报告当前显示器 + 扫描边界，不建屏

状态落盘到 launch_state.json，供后续（扫描/跑工作流）读取虚拟屏坐标。
"""
import sys, os, time, json, ctypes, subprocess
from ctypes import wintypes
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
QIANNIU_URL = "https://myseller.taobao.com/home.htm/QnworkbenchHome/"
PROFILE = r"D:\AI\membrane\_edge_cdp_profile"
CDP_PORT = 9222
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_state.json")

user32 = ctypes.windll.user32
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def enum_displays():
    monitors = []
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)
    def _cb(h, dc, r, p):
        rect = r.contents
        monitors.append({'left': rect.left, 'top': rect.top,
                         'width': rect.right - rect.left, 'height': rect.bottom - rect.top})
        return True
    ctypes.windll.user32.EnumDisplayMonitors(None, None, CB(_cb), 0)
    monitors.sort(key=lambda m: m['left'])
    return monitors


def snapshot_windows():
    known = set()
    @_WNDENUMPROC
    def _cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            known.add(hwnd)
        return True
    user32.EnumWindows(_cb, 0)
    return known


def move_window(hwnd, left, top, w, h):
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.15)
    user32.SetWindowPos(hwnd, 0, left, top, w, h, 0x0004)  # SWP_NOZORDER
    return hwnd


def catch_and_move(known, left, top, w, h, timeout=12.0):
    moved = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        @_WNDENUMPROC
        def _cb(hwnd, lparam):
            if (user32.IsWindowVisible(hwnd) and hwnd not in known and hwnd not in moved):
                move_window(hwnd, left, top, w, h)
                moved.append(hwnd)
            return True
        user32.EnumWindows(_cb, 0)
        time.sleep(0.5)
    return moved


def create_virtual_display():
    import membrane_virtual_display as mvd
    vd = mvd.get_display_manager()
    vd.start()
    idx = vd.add()
    virt = None
    for _ in range(60):
        time.sleep(0.5)
        for d in enum_displays():
            if not (d['left'] == 0 and d['top'] == 0):  # 非主屏
                virt = d
                break
        if virt:
            break
    if virt is None:
        try:
            vd.remove(idx)
        except Exception:
            pass
        raise RuntimeError("虚拟屏创建超时（30s 后显示器数量未增加）")
    virt['vdd_index'] = idx
    return vd, virt


def main():
    if '--status' in sys.argv:
        print(json.dumps(enum_displays(), ensure_ascii=False))
        return

    vd, virt = create_virtual_display()
    print(f"[ok] 虚拟屏: left={virt['left']} top={virt['top']} {virt['width']}x{virt['height']}", flush=True)

    import membrane_vision as mv
    mv.set_display_bounds(virt['left'], virt['top'], virt['width'], virt['height'])

    known = snapshot_windows()
    args = [
        EDGE,
        f'--window-position={virt["left"] + 8},{virt["top"] + 8}',
        f'--window-size={virt["width"] - 16},{virt["height"] - 16}',
        f'--remote-debugging-port={CDP_PORT}',
        '--remote-allow-origins=*',
        f'--user-data-dir={PROFILE}',
        '--new-window',
        QIANNIU_URL,
    ]
    proc = subprocess.Popen(args)
    print(f"[ok] Edge 已启动 PID={proc.pid}", flush=True)
    moved = catch_and_move(known, virt['left'], virt['top'], virt['width'], virt['height'])
    print(f"[ok] 搬了 {len(moved)} 个窗口到虚拟屏", flush=True)

    state = {
        'left': virt['left'], 'top': virt['top'],
        'width': virt['width'], 'height': virt['height'],
        'edge_pid': proc.pid, 'cdp_port': CDP_PORT,
        'qianniu_url': QIANNIU_URL, 'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"[ok] 状态已写 {STATE_FILE}", flush=True)

    print("[ok] 保持运行中（心跳保活虚拟屏）…", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        vd.stop()
        print("[done] 虚拟屏已清理", flush=True)


if __name__ == "__main__":
    main()
