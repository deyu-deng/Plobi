# GUI 自动化适配器：控件抓取步骤（Marvis / Workbuddy）

> 适用：`marvis_gui`（腾讯 Marvis `marvis.qq.com`）、`workbuddy_gui`（Workbuddy 桌面端）。
> 路线定位：**合规的 spawn-CLI 路线覆盖不了的产品**（无 CLI、无 inbound API），
> 用 Windows UI Automation 像真人一样驱动聊天框——GUI 版 spawn-CLI，**不偷 token、不逆向**。

⚠️ 三条硬约束（写进 `aigw/providers/gui.py` 注释）：
1. **FRAGILE** —— 依赖 App 控件布局，版本升级可能要重抓选择器。
2. **仅 Windows + 需活 GUI 会话** —— headless / Linux CI 跑不了，provider 会优雅禁用（0 账号）。
3. **ToS 灰区** —— 自动化提取模型输出可能违反产品条款；仅个人本地研究，别当"破解付费墙"发布。

---

## 第 1 步：装依赖（仅 Windows GUI 机）
```powershell
pip install -r requirements.gui.txt    # 装 uiautomation
```

## 第 2 步：确保目标 App 已登录且窗口可见
启动 Marvis / Workbuddy，登录好，让主窗口停在屏幕上。

## 第 3 步：抓控件树
```powershell
# Marvis
aigw marvis-dump --title "Marvis" --depth 8
# Workbuddy
aigw workbuddy-dump --title "WorkBuddy" --depth 8
```
输出每层的 `Control 类型 / Name / AutomationId / ClassName`。记下：
- **聊天输入框**：通常是 `EditControl` / `DocumentControl`，`Name` 或 `AutomationId` 稳定可定位
- **回复区**：通常是 `TextControl` / `PaneControl`，能读到最新回复文本

## 第 4 步：填 config
把真实选择器填进 `config.yaml` 对应段：

```yaml
marvis_gui:
  window_title: "Marvis"
  send_key: "{Enter}"
  response_timeout: 120
  stable_poll: 1.0
  stable_threshold: 3
  input:
    control: EditControl
    regex_name: "聊天输入.*"        # 用第 3 步看到的真实 Name/AutomationId
  output:
    control: TextControl
    regex_name: ".*回复.*"
  accounts:
    - { id: marvis-gui-default }

workbuddy_gui:        # 或 workbuddy.gui: 段（hybrid 模式）
  window_title: "WorkBuddy"
  input:  { control: EditControl,  regex_name: "..." }
  output: { control: TextControl,  regex_name: "..." }
```

选择器字段全可配（`control` / `name` / `regex_name` / `automation_id` / `class_name` / `depth`），
**代码不写死任何版本**。`regex_name` 支持正则，`name` 为精确匹配。

## 第 5 步：起网关验证
```bash
aigw start
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $AIGW_KEY" -H "Content-Type: application/json" \
  -d '{"model":"marvis_gui/default","messages":[{"role":"user","content":"hi"}]}'
```
正常情况下：网关把 prompt 打进聊天框 → 按 `send_key` 提交 → 轮询回复区直到稳定
（`stable_threshold` 次未变即认为完成）→ 读回最新回复翻成 OpenAI 格式返回。

---

## 工作机制（见 `aigw/providers/gui.py`）

```
_window()      -> 按 window_title 定位主窗口（RegexName 优先，回退 Name）
_control()     -> 按选择器在窗口内定位子控件
_type()        -> SetFocus + SetValue（失败回退 SendKeys）+ 发提交键
_wait_reply()  -> 轮询 output 控件，连续 stable_threshold 次不变即视为稳定
chat_stream()  -> GUI 无法增量流式，读定后一次性发 content chunk + stop chunk
discover_accounts() -> 无 uiautomation / 无窗口时返回 0 账号（优雅禁用，不崩）
```

所有阻塞 UI 操作跑在 `asyncio.to_thread`，不卡网关事件循环。

## 排错
- `0 账号` / 日志 `window not found`：App 没开或 `window_title` 不匹配（用 `-dump` 看真实标题）。
- `chat input control not found`：`input` 选择器不对，重抓。
- 回复为空 / 截断：`output` 选择器没指向最新回复文本，或 `stable_threshold` 太小导致早停。
