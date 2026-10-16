# ③ 手接口 — 物理鼠标

**文件**：`exec/executor.py`

**职责**：真实物理鼠标操作（Windows `SetCursorPos` + `mouse_event`）。**不是 JS 模拟点击** —— 点完页面真的跳，测试出来的才是真效果。

## 接口

```python
from exec.executor import click, hover, type_text, press_key, hotkey, scroll, wait

click(x, y)               # 左键点击屏幕坐标 (x, y)
hover(x, y)               # 移动鼠标到 (x, y)，不点击
type_text(x, y, text)     # 点击 (x, y) 后输入文本
press_key(key)            # 按单个键，如 'enter' / 'tab' / 'esc'
hotkey(keys)              # 组合键，如 'ctrl+c'
scroll(x, y, amount=-120) # 滚轮（负数向下）
wait(seconds)             # 睡眠
```

## 输入 / 输出

- **输入**：屏幕绝对坐标（与眼睛 `detect_ui_elements` 返回的 `cx/cy` 同坐标系）。
- **输出**：无返回值；副作用是鼠标真的移动/点击。

## 对接注意

- 真实鼠标会抢占用户光标 —— 真点执行时请盯着屏幕（干跑 `dry_run=True` 不动鼠标）。
- 坐标需先经 `run_workflow.apply_display_bounds()` 或眼睛的 `_to_local` 换算到副屏局部坐标，再喂进来。
