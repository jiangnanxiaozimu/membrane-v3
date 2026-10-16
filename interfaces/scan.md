# ① 眼睛接口 — 扫描总组件

**文件**：`membrane_vision.py`（约 39KB）

**职责**：扫描当前屏幕，返回可交互元素列表（每个元素含 `type` / `name` / 中心坐标 `cx,cy`）。三路扫描组合：UIA（桌面原生控件）+ CDP（浏览器 DOM）+ 视觉 qwen3-vl（兜底）。

## 核心接口

```python
detect_ui_elements(frame=None) -> list[dict]
# 主入口：合并 UIA + CDP + 视觉三路结果，去重后返回。
# 元素 dict 关键字段：type, name, cx, cy（另有 x/y/w/h 等）

scan_display_elements() -> list[dict]
# UIA 扫描当前前台窗口的桌面控件

scan_web_elements(cdp_ws_url, viewport_screen_x, viewport_screen_y) -> list[dict]
# CDP 扫描浏览器 DOM（Edge 9222），把页面坐标换算到屏幕坐标

set_display_bounds(left, top, width, height)   # 设置副屏扫描边界
get_display_bounds() -> tuple                   # 读取副屏边界

detect_taskbar_buttons(frame=None) -> list[dict]  # 任务栏按钮（可选）
draw_boxes(frame, elements) -> Image              # 在截图上画标注框（调试用）
```

## 输入 / 输出

- **输入**：无（或截图 frame）；依赖运行时的副屏激活（`launch_qianniu.py`）与 Edge CDP 9222。
- **输出**：元素列表，`cx/cy` 是**副屏局部坐标**（可直接喂给 `exec.executor.click`）。

## 对接注意

- 元素坐标统一为副屏局部坐标（`_to_local` 做了换算），手（executor）按同样坐标系点。
- 视觉 qwen3-vl 只做「弹窗/关闭按钮」这类判断，主扫描靠 UIA + CDP（快、准、毫秒级）。
