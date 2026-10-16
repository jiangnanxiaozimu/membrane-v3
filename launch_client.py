"""
launch_client.py — 膜通用客户端启动器

启动第三方桌面客户端（Electron/CEF 类），绕过它们的「反自动化启动保护」。

为什么需要这个：
  这类客户端（B站、抖音等）检测启动方式——只有用户双击快捷方式（父进程是 explorer）
  才正常启动；直接用 subprocess.Popen / Start-Process 启动会被静默拒绝
  （1 秒退出，退出码 0，连日志都不写）。

解法（2026-10-15 实测验证）：
  1. 动态生成一个临时 .lnk 快捷方式（TargetPath=exe，Arguments=参数，WorkingDirectory=工作目录）
  2. 用 explorer.exe 启动这个 .lnk —— 等价「用户双击」，父进程是 explorer
  3. 等待目标进程出现

用法:
  from launch_client import launch_client
  ok, info = launch_client(r"D:/douyin/douyin.exe", cdp_port=9223)
  # ok=True 表示进程已出现；info 里有 pids / cdp_port 等

注：cdp_port 只是「注入」--remote-debugging-port 参数。能不能真正开 CDP 端口取决于客户端：
  - B站（纯 Electron）：能开，CDP 可读 DOM
  - 抖音（Electron+Lynx+字节反调试）：参数收下但主动禁用，端口不开 → 只能走视觉
"""

import os
import time
import tempfile
import subprocess

try:
    import win32com.client
    _HAS_WIN32COM = True
except ImportError:
    _HAS_WIN32COM = False


def _create_shortcut(lnk_path, exe_path, args, workdir):
    """用 WScript.Shell COM 创建快捷方式（避免 PowerShell 字符串转义坑）。"""
    shell = win32com.client.Dispatch("WScript.Shell")
    lnk = shell.CreateShortcut(lnk_path)
    lnk.TargetPath = exe_path
    lnk.Arguments = " ".join(args)
    lnk.WorkingDirectory = workdir
    lnk.Save()
    return os.path.exists(lnk_path)


def _find_process_pids(exe_name):
    """按进程名（不含 .exe）找 PID 列表。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Name '{exe_name}' -ErrorAction SilentlyContinue).Id -join ','"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        return [int(x) for x in out.split(",") if x.strip().isdigit()]
    except Exception:
        return []


def launch_client(exe_path, cdp_port=None, workdir=None, extra_args=None, wait=8.0, keep_lnk=False):
    """
    启动第三方客户端，绕过反自动化保护。

    返回 (ok, info)：
      ok=True  → info={exe, exe_name, pids, cdp_port, lnk}
      ok=False → info={error}
    """
    if not _HAS_WIN32COM:
        return False, {"error": "缺少 pywin32（pip install pywin32）"}

    exe_path = os.path.abspath(exe_path)
    if not os.path.exists(exe_path):
        return False, {"error": f"exe 不存在: {exe_path}"}
    if workdir is None:
        workdir = os.path.dirname(exe_path)

    args = []
    if cdp_port:
        args.append(f"--remote-debugging-port={cdp_port}")
    if extra_args:
        args.extend(extra_args)

    # 1. 生成临时快捷方式
    exe_name = os.path.splitext(os.path.basename(exe_path))[0]
    lnk_path = os.path.join(
        tempfile.gettempdir(),
        f"_membrane_launch_{exe_name}_{int(time.time() * 1000)}.lnk")
    try:
        if not _create_shortcut(lnk_path, exe_path, args, workdir):
            return False, {"error": "快捷方式创建失败"}
    except Exception as e:
        return False, {"error": f"快捷方式创建异常: {e}"}

    # 2. explorer 启动（等价双击）
    subprocess.run(["explorer.exe", lnk_path])

    # 3. 等待进程出现
    deadline = time.time() + wait
    pids = []
    while time.time() < deadline:
        pids = _find_process_pids(exe_name)
        if pids:
            break
        time.sleep(0.5)

    # 4. 清理临时快捷方式（除非 keep_lnk）
    if not keep_lnk:
        try:
            os.remove(lnk_path)
        except Exception:
            pass

    if not pids:
        return False, {"error": f"进程未出现（{wait}s 内无 {exe_name} 进程）"}

    return True, {
        "exe": exe_path,
        "exe_name": exe_name,
        "pids": pids,
        "cdp_port": cdp_port,
        "lnk": lnk_path if keep_lnk else None,
    }


if __name__ == "__main__":
    import sys
    exe = sys.argv[1] if len(sys.argv) > 1 else r"D:\douyin\douyin.exe"
    ok, info = launch_client(exe, cdp_port=9223)
    print("ok =", ok)
    print("info =", info)
