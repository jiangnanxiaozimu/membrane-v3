"""
膜 — 虚拟屏驱动模块 v1.0
owllxc 自动化进程管理

直接调用 Parsec VDD 驱动的 IOCTL 接口创建/移除虚拟显示器。
不依赖 ParsecVDisplay.exe — 驱动已安装，我们只需要 Python 说话。

基于 nomi-san/parsec-vdd (BSD-2) 的 C API 移植到 ctypes。
"""

import ctypes
from ctypes import wintypes, c_void_p, c_char_p, c_ulong, c_int, c_ubyte
from ctypes import Structure, POINTER, byref, sizeof, cast, create_string_buffer
import threading
import time
import winreg


# ============================================================
# Win32 API 声明
# ============================================================
setupapi = ctypes.windll.setupapi
kernel32 = ctypes.windll.kernel32

# GUID 结构
class GUID(Structure):
    _fields_ = [
        ("Data1", c_ulong),
        ("Data2", wintypes.USHORT),
        ("Data3", wintypes.USHORT),
        ("Data4", c_ubyte * 8),
    ]

# SP_DEVICE_INTERFACE_DATA
class SP_DEVICE_INTERFACE_DATA(Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", c_void_p),
    ]

# SP_DEVICE_INTERFACE_DETAIL_DATA_A
# 注意: DevicePath 是内联 TCHAR[] 数组（ANYSIZE_ARRAY），不是指针
# 在 ctypes 中不能直接建模，通过手动字节偏移读取
class SP_DEVICE_INTERFACE_DETAIL_DATA_A(Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        # DevicePath 从 offset 4 开始，手动读取
    ]

# OVERLAPPED
class OVERLAPPED(Structure):
    _fields_ = [
        ("Internal", c_void_p),
        ("InternalHigh", c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]

# 常量
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010
SPDRP_HARDWAREID = 0x00000001
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_NO_BUFFERING = 0x20000000
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_WRITE_THROUGH = 0x80000000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

# Setup API 函数签名
setupapi.SetupDiGetClassDevsA.argtypes = [POINTER(GUID), c_char_p, wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiGetClassDevsA.restype = wintypes.HANDLE

setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, c_void_p, POINTER(GUID), wintypes.DWORD, POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

setupapi.SetupDiGetDeviceInterfaceDetailA.argtypes = [wintypes.HANDLE, POINTER(SP_DEVICE_INTERFACE_DATA), c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD), c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailA.restype = wintypes.BOOL

setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

# Kernel32 函数签名
kernel32.CreateFileA.argtypes = [c_char_p, wintypes.DWORD, wintypes.DWORD, c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateFileA.restype = wintypes.HANDLE

kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, c_void_p, wintypes.DWORD, c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD), POINTER(OVERLAPPED)]
kernel32.DeviceIoControl.restype = wintypes.BOOL

kernel32.CreateEventA.argtypes = [c_void_p, wintypes.BOOL, wintypes.BOOL, c_char_p]
kernel32.CreateEventA.restype = wintypes.HANDLE

kernel32.GetOverlappedResultEx.argtypes = [wintypes.HANDLE, POINTER(OVERLAPPED), POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.BOOL]
kernel32.GetOverlappedResultEx.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


# ============================================================
# Parsec VDD 核心常量
# ============================================================
# 驱动识别
VDD_HARDWARE_ID = b"Root\\Parsec\\VDA"

# 适配器 GUID  {00b41627-04c4-429e-a26e-0265cf50c8fa}
VDD_ADAPTER_GUID = GUID(
    0x00b41627, 0x04c4, 0x429e,
    (c_ubyte * 8)(0xa2, 0x6e, 0x02, 0x65, 0xcf, 0x50, 0xc8, 0xfa),
)

# 显示类 GUID  {4d36e968-e325-11ce-bfc1-08002be10318}
VDD_CLASS_GUID = GUID(
    0x4d36e968, 0xe325, 0x11ce,
    (c_ubyte * 8)(0xbf, 0xc1, 0x08, 0x00, 0x2b, 0xe1, 0x03, 0x18),
)

# IOCTL 控制码
VDD_IOCTL_ADD     = 0x0022e004  # CTL_CODE(UNKNOWN, 0x801, BUFFERED, READ|WRITE)
VDD_IOCTL_REMOVE  = 0x0022a008  # CTL_CODE(UNKNOWN, 0x802, BUFFERED, WRITE)
VDD_IOCTL_UPDATE  = 0x0022a00c  # CTL_CODE(UNKNOWN, 0x803, BUFFERED, WRITE)
VDD_IOCTL_VERSION = 0x0022e010  # CTL_CODE(UNKNOWN, 0x804, BUFFERED, READ|WRITE)

# 设备状态枚举
DEVICE_OK = 0
DEVICE_INACCESSIBLE = 1
DEVICE_UNKNOWN = 2
DEVICE_UNKNOWN_PROBLEM = 3
DEVICE_DISABLED = 4
DEVICE_DRIVER_ERROR = 5
DEVICE_RESTART_REQUIRED = 6
DEVICE_DISABLED_SERVICE = 7
DEVICE_NOT_INSTALLED = 8

STATUS_NAMES = {
    0: "OK",
    1: "不可访问",
    2: "未知状态",
    3: "未知问题",
    4: "已禁用",
    5: "驱动错误",
    6: "需要重启",
    7: "服务已禁用",
    8: "驱动未安装",
}

MAX_DISPLAYS = 8     # v0.45 允许每个适配器 16 个，保守用 8
HEARTBEAT_MS = 100   # 心跳间隔，超 100ms 虚拟屏会消失


# ============================================================
# 驱动操作函数
# ============================================================
def _ioctl(vdd_handle, code, data=None):
    """向驱动发送 IOCTL 命令。"""
    if vdd_handle is None or vdd_handle == INVALID_HANDLE_VALUE:
        raise RuntimeError("驱动句柄无效")

    in_buf = (c_ubyte * 32)()
    if data and len(data) <= 32:
        ctypes.memmove(in_buf, bytes(data), len(data))

    out_buf = wintypes.DWORD(0)
    bytes_returned = wintypes.DWORD(0)

    overlapped = OVERLAPPED()
    overlapped.hEvent = kernel32.CreateEventA(None, True, False, None)

    if not overlapped.hEvent:
        raise OSError("CreateEvent 失败")

    try:
        ok = kernel32.DeviceIoControl(
            wintypes.HANDLE(vdd_handle),
            wintypes.DWORD(code),
            in_buf, 32,
            ctypes.byref(out_buf), ctypes.sizeof(out_buf),
            None,
            ctypes.byref(overlapped),
        )
        if not ok:
            # 异步 I/O，等待完成
            ok = kernel32.GetOverlappedResultEx(
                wintypes.HANDLE(vdd_handle),
                ctypes.byref(overlapped),
                ctypes.byref(bytes_returned),
                5000,  # 5 秒超时
                False,
            )
        if not ok:
            raise OSError(f"DeviceIoControl 失败, code=0x{code:08X}, err={ctypes.get_last_error()}")
        return out_buf.value
    finally:
        if overlapped.hEvent:
            kernel32.CloseHandle(overlapped.hEvent)


def _open_device_handle():
    """打开驱动的设备句柄 — 从注册表枚举设备接口，绕过 SetupDi 在 ctypes 下的兼容问题。"""
    guid_str = "{00b41627-04c4-429e-a26e-0265cf50c8fa}"
    reg_path = f"SYSTEM\\CurrentControlSet\\Control\\DeviceClasses\\{guid_str}"

    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
    except FileNotFoundError:
        raise OSError(f"注册表中找不到设备接口: {reg_path} — 驱动可能未安装")

    handle = None
    try:
        for i in range(32):
            try:
                sub_name = winreg.EnumKey(key, i)
            except OSError:
                break

            # 注册表中的键名用 # 替换 \：
            #   例如: ##?#ROOT#DISPLAY#0002#{guid}
            #   真实路径: \\?\ROOT#DISPLAY#0002#{guid}
            #   前缀 "##?#" = "\\?\", 其余 # 是路径分隔符
            if sub_name.startswith("##?#"):
                device_path = "\\\\?\\" + sub_name[4:]
            elif sub_name.startswith("##"):
                device_path = "\\\\" + sub_name[2:]
            else:
                device_path = sub_name  # 未知格式，原样尝试
            handle = kernel32.CreateFileA(
                device_path.encode('ascii'),
                GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL | FILE_FLAG_NO_BUFFERING | FILE_FLAG_OVERLAPPED | FILE_FLAG_WRITE_THROUGH,
                None,
            )

            if handle is not None and handle != INVALID_HANDLE_VALUE:
                break
            handle = None

    finally:
        winreg.CloseKey(key)

    if handle is None:
        raise OSError("找不到可用的 Parsec VDD 设备接口 — CreateFile 全部失败")

    return handle


def _close_device_handle(handle):
    """释放设备句柄。"""
    if handle is not None and handle != INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(wintypes.HANDLE(handle))


# ============================================================
# VirtualDisplay 类 — 膜的外部接口
# ============================================================
class VirtualDisplay:
    """
    虚拟显示器管理器。

    用法:
        vd = VirtualDisplay()
        vd.start()              # 打开驱动 + 启动心跳线程
        idx = vd.add()          # 创建虚拟屏，返回索引
        vd.remove(idx)          # 移除指定虚拟屏
        vd.count                # 当前虚拟屏数量
        vd.stop()               # 移除所有 + 停止心跳 + 释放句柄

    with 语句支持:
        with VirtualDisplay() as vd:
            idx = vd.add()
            # ... 用虚拟屏做事 ...
        # 自动清理
    """

    def __init__(self):
        self._handle = None
        self._displays = []     # 已添加的显示索引
        self._heartbeat_thread = None
        self._running = False

    # ---------- 属性 ----------

    @property
    def count(self):
        return len(self._displays)

    @property
    def is_running(self):
        return self._running

    # ---------- 生命周期 ----------

    def start(self):
        """打开驱动连接，启动心跳。"""
        if self._running:
            return

        try:
            self._handle = _open_device_handle()
        except OSError as e:
            raise RuntimeError(f"无法连接 Parsec VDD 驱动: {e}") from e

        # 检查驱动版本
        minor = _ioctl(self._handle, VDD_IOCTL_VERSION)
        if minor < 0:
            raise RuntimeError("驱动版本查询失败")

        self._running = True
        self._heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._heartbeat_thread.start()

    def stop(self):
        """移除所有虚拟屏，停止心跳，释放句柄。"""
        self._running = False

        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=1.0)

        # 删除所有虚拟屏
        for idx in list(self._displays):
            try:
                self._remove(idx)
            except Exception:
                pass
        self._displays.clear()

        _close_device_handle(self._handle)
        self._handle = None

    # ---------- 虚拟屏操作 ----------

    def add(self):
        """创建一块虚拟屏幕，返回索引。"""
        if not self._running:
            self.start()

        if len(self._displays) >= MAX_DISPLAYS:
            raise RuntimeError(f"虚拟屏数量已达上限 ({MAX_DISPLAYS})")

        idx = _ioctl(self._handle, VDD_IOCTL_ADD)
        if idx < 0:
            raise RuntimeError("虚拟屏创建失败 — 驱动返回错误")

        _ioctl(self._handle, VDD_IOCTL_UPDATE)  # 立即心跳刷新
        self._displays.append(idx)
        return idx

    def remove(self, index=None):
        """移除一块虚拟屏幕。不传 index 则移除最后添加的。"""
        if not self._displays:
            return

        if index is None:
            index = self._displays[-1]

        if index not in self._displays:
            raise ValueError(f"索引 {index} 不存在于已创建的虚拟屏列表中")

        self._remove(index)
        self._displays.remove(index)

    def _remove(self, index):
        """内部移除 — 不修改 self._displays。"""
        # IOCTL_REMOVE 需要 16-bit big-endian 索引
        index_be = bytes([(index >> 8) & 0xFF, index & 0xFF])
        _ioctl(self._handle, VDD_IOCTL_REMOVE, index_be)
        _ioctl(self._handle, VDD_IOCTL_UPDATE)

    # ---------- 心跳 ----------

    def _heartbeat(self):
        """后台线程 — 每 100ms 发一次心跳，保持虚拟屏存活。"""
        while self._running:
            try:
                if self._handle:
                    _ioctl(self._handle, VDD_IOCTL_UPDATE)
            except Exception:
                pass  # 心跳失败不崩溃，最多虚拟屏消失
            time.sleep(HEARTBEAT_MS / 1000.0)

    # ---------- with 支持 ----------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
        return False


# ============================================================
# 单例 — 膜全局用
# ============================================================
_display_manager = None


def get_display_manager() -> VirtualDisplay:
    """获取全局 VirtualDisplay 实例（懒加载）。"""
    global _display_manager
    if _display_manager is None:
        _display_manager = VirtualDisplay()
    return _display_manager


# ============================================================
# CLI 测试入口
# ============================================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("=== 膜 VirtualDisplay 驱动测试 ===\n")

        try:
            vd = VirtualDisplay()
            vd.start()
            print(f"✓ 驱动连接成功\n")

            idx = vd.add()
            print(f"✓ 虚拟屏已创建, index={idx}")
            print(f"  当前虚拟屏数: {vd.count}")

            input("\n按 Enter 移除虚拟屏并退出...")

            vd.stop()
            print("✓ 虚拟屏已移除, 驱动已释放")

        except Exception as e:
            print(f"✗ 失败: {e}")
            sys.exit(1)
