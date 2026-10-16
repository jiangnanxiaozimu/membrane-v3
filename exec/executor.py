"""
exec/executor.py — 执行抽屉（「手」）

膜的手：把语义动作翻译成真实的鼠标 / 键盘事件。

坐标约定：所有 x/y 都是「虚拟屏本地坐标」（跟 membrane_vision 扫描结果一致），
内部通过 membrane_vision.get_display_bounds() 加回显示器偏移得到绝对屏幕坐标。

从 membrane_app.pyw 的 MembraneBridge 里剥离出来的纯底层原语，去掉显示器管理 /
VDD / pywebview 的耦合，让执行引擎（workflow/engine.py）能直接注入，
不用拉起整个膜前端。
"""
import ctypes
import time
import subprocess

user32 = ctypes.windll.user32

_MODIFIER_VK = {'ctrl': 0x11, 'shift': 0x10, 'alt': 0x12, 'win': 0x5B}
_SPECIAL_VK = {
    'enter': 0x0D, 'return': 0x0D, 'tab': 0x09, 'esc': 0x1B,
    'space': 0x20, 'backspace': 0x08, 'delete': 0x2E,
    'home': 0x24, 'end': 0x23, 'up': 0x26, 'down': 0x28,
    'left': 0x25, 'right': 0x27, 'pageup': 0x21, 'pagedown': 0x22,
}

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800


def _vk_code(key):
    k = str(key).lower()
    if k in _MODIFIER_VK:
        return _MODIFIER_VK[k]
    if k in _SPECIAL_VK:
        return _SPECIAL_VK[k]
    if len(k) == 1 and 'a' <= k <= 'z':
        return ord(k.upper())
    if len(k) == 1 and '0' <= k <= '9':
        return ord(k)
    return 0


def _abs(x, y):
    """本地坐标 → 绝对屏幕坐标（加回虚拟屏偏移）。"""
    from membrane_vision import get_display_bounds
    b = get_display_bounds()
    return int(b['left'] + x), int(b['top'] + y)


def _move(x, y):
    abs_x, abs_y = _abs(x, y)
    user32.SetCursorPos(abs_x, abs_y)
    time.sleep(0.02)
    return abs_x, abs_y


def _key_event(vk, down):
    flag = 0 if down else 0x0002  # KEYEVENTF_KEYUP
    user32.keybd_event(vk, 0, flag, 0)


def click(x, y):
    """左键单击 (x, y)（本地坐标）。"""
    abs_x, abs_y = _move(x, y)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    return {"ok": True, "x": abs_x, "y": abs_y}


def hover(x, y):
    """鼠标移到 (x, y)，不点击。"""
    abs_x, abs_y = _move(x, y)
    return {"ok": True, "x": abs_x, "y": abs_y}


def type_text(x, y, text):
    """点击定位输入框后输入文字。中文走剪贴板粘贴（全选+粘贴，避搜狗吞键）。"""
    click(x, y)
    time.sleep(0.3)
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception:
        p = subprocess.Popen(
            ['powershell', '-NoProfile', '-Command', '$input | Set-Clipboard'],
            stdin=subprocess.PIPE)
        p.communicate(input=str(text).encode('utf-16-le'))
    time.sleep(0.15)
    hotkey('ctrl+a')
    hotkey('ctrl+v')
    return {"ok": True, "text": text}


def press_key(key):
    """按一个键（enter / tab / esc 等）。"""
    vk = _vk_code(key)
    if not vk:
        return {"ok": False, "error": f"未知按键: {key}"}
    _key_event(vk, True)
    _key_event(vk, False)
    return {"ok": True, "key": key}


def hotkey(keys):
    """组合键，如 'ctrl+v' / 'ctrl+a' / 'ctrl+l'（空格或 + 分隔）。"""
    parts = [p for p in str(keys).strip().replace('+', ' ').split() if p]
    if not parts:
        return {"ok": False, "error": "组合键为空"}
    vks = [_vk_code(p) for p in parts]
    for vk in vks:
        if vk:
            _key_event(vk, True)
    time.sleep(0.03)
    for vk in reversed(vks):
        if vk:
            _key_event(vk, False)
    time.sleep(0.05)
    return {"ok": True, "keys": parts}


def scroll(x, y, amount=-120):
    """在 (x, y) 滚轮。amount 正=向上、负=向下（每 120 = 一格）。"""
    _move(x, y)
    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(amount), 0)
    return {"ok": True, "amount": amount}


def wait(seconds):
    """等待 seconds 秒。"""
    time.sleep(seconds)
    return {"ok": True, "waited": seconds}
