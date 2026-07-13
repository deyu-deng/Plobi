没问题，交给我。既然 Claude 的额度用尽，我将接力完成这个核心的 UI 规范文档。

根据你提供的 NotebookLM 截图和全面对标的诉求，我为你重新编写了 `UI_DESIGN_SPEC.md`。这份文档彻底剥离了原先的“橙棕米白”色调，全面转向 **Google Material 3 (M3)** 的设计语言：纯净的淡蓝灰背景、圆润的卡片、悬浮式药丸输入框，以及右侧 Studio 面板中标志性的“莫兰迪色系”网格卡片。

你可以直接复制以下内容并在你的项目中覆盖原文件。

---

```markdown
# UI_DESIGN_SPEC.md — Plobi Vaelis 2.0 视觉设计规范 (NotebookLM 风格版)

> **用途**：本文件是项目 UI 的唯一视觉标准。所有前端开发、组件封装及 CSS 编写必须严格遵循本文档。
>
> **风格基准**：Google NotebookLM / Material Design 3。
> **核心特征**：浅灰蓝底色、大圆角纯白面板、悬浮式药丸组件、莫兰迪彩色网格卡片、极度扁平化（仅悬浮层使用柔和阴影）。

---

## 一、设计哲学与基本原则

**核心原则**：模块化、纯净感、任务聚焦。

- **去边框化**：摒弃生硬的线条边框，利用背景色块的差异（如浅灰蓝底色 vs 纯白面板）来划分空间。
- **大圆角 (Pill & Large Radius)**：卡片和面板使用 `16px` 到 `24px` 的圆角，输入框和按钮大量使用 `999px`（药丸形）圆角。
- **彩色网格 (Studio Grid)**：右侧的 Sub-Agent 阵列是视觉中心，每个 Agent 使用低饱和度的莫兰迪色（淡紫、淡蓝、淡粉）作为卡片背景，以便快速区分能力域。
- **悬浮感**：聊天输入框不固定在底部边缘，而是悬浮在聊天流上方的居中位置。

---

## 二、色彩系统（CSS 变量定义）

```css
/* src/styles/globals.css — 完整 CSS 变量定义 */

:root {
  /* ── 结构背景层级 ── */
  --bg-app:          #F0F4F9;   /* 整个应用最底层的底色（典型 Google 浅灰蓝） */
  --bg-panel:        #FFFFFF;   /* 左、中、右三大面板的背景色（纯白） */
  --bg-hover:        #F5F6F8;   /* 列表项悬浮色 */
  --bg-active:       #E8DEF8;   /* 选中状态（淡紫色） */
  
  /* ── 组件与输入区域 ── */
  --bg-input:        #FFFFFF;   /* 悬浮输入框背景 */
  --bg-user-bubble:  #F0F4F9;   /* 用户消息气泡背景（与应用底色同色，融入感强） */

  /* ── 文字层级 ── */
  --text-primary:    #1F1F1F;   /* 纯正的正文黑 */
  --text-secondary:  #444746;   /* 次要文本/辅助说明 */
  --text-muted:      #747775;   /* 弱化文本/时间/来源统计 */

  /* ── 边框与阴影 ── */
  --border-light:    #E3E3E3;   /* 面板和输入框的细边框 */
  --shadow-float:    0 4px 12px rgba(0, 0, 0, 0.08), 0 1px 4px rgba(0, 0, 0, 0.04);

  /* ── Sub-Agent 彩色卡片系统 (Studio Colors) ── */
  /* 用于右侧 Agent Grid 阵列的背景色 */
  --agent-purple:    #E8DEF8;
  --agent-blue:      #D3E3FD;
  --agent-green:     #C4EED0;
  --agent-yellow:    #FFF0C2;
  --agent-pink:      #F2B8B5;
  --agent-grey:      #E0E2E8;

  /* ── 尺寸与圆角令牌 ── */
  --radius-sm:       8px;       /* 小图标/提示框 */
  --radius-md:       16px;      /* 面板内的小卡片 */
  --radius-lg:       24px;      /* 左中右三大主面板的圆角 */
  --radius-pill:     999px;     /* 输入框、主要按钮 */
  
  --panel-gap:       16px;      /* 面板之间的间距 */
  --nav-width:       320px;     /* 左侧 Sources 面板默认宽度 */
  --studio-width:    360px;     /* 右侧 Studio 面板默认宽度 */
}

[data-theme="dark"] {
  --bg-app:          #131314;
  --bg-panel:        #1E1F22;
  --bg-hover:        #282A2D;
  --bg-active:       #4A4458;
  --bg-input:        #1E1F22;
  --bg-user-bubble:  #282A2D;

  --text-primary:    #E3E3E3;
  --text-secondary:  #C4C7C5;
  --text-muted:      #8E918F;

  --border-light:    #444746;
  --shadow-float:    0 4px 12px rgba(0, 0, 0, 0.4);

  /* 暗黑模式下的 Agent 卡片需降低明度 */
  --agent-purple:    #4A4458;
  --agent-blue:      #394457;
  --agent-green:     #354A3B;
  --agent-yellow:    #4C4639;
  --agent-pink:      #4E3737;
  --agent-grey:      #3C3D3F;
}

```

---

## 三、全局布局规范 (Layout Structure)

完全摒弃边缘贴合的侧边栏。采用 **“深色背景托起纯白面板”** 的卡片式空间布局。

```css
.app-container {
  background-color: var(--bg-app);
  height: 100vh;
  padding: 16px;
  display: flex;
  gap: var(--panel-gap);
  box-sizing: border-box;
}

/* 左、中、右三大面板通用样式 */
.main-panel {
  background: var(--bg-panel);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  /* 不用全阴影，用极淡的边框勾勒边缘 */
  border: 1px solid var(--border-light); 
}

```

---

## 四、左侧面板：资源与任务 (Sources & Kanban)

**视觉特征**：顶部提供明显的大号“Add sources”按钮，下方列表带多选 Checkbox。

```css
/* 顶部添加按钮区域 */
.sources-header {
  padding: 16px;
  border-bottom: 1px solid var(--border-light);
}

.add-source-btn {
  width: 100%;
  height: 40px;
  border-radius: var(--radius-pill);
  border: 1px dashed var(--text-muted);
  background: transparent;
  color: var(--text-primary);
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: background 0.2s;
}
.add-source-btn:hover { background: var(--bg-hover); }

/* 资源列表项 (包含 Checkbox) */
.source-item {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  gap: 12px;
  cursor: pointer;
  transition: background 0.2s;
}
.source-item:hover { background: var(--bg-hover); }

/* 复选框样式覆盖为 Material 风格圆角方形 */
.source-item input[type="checkbox"] {
  width: 18px;
  height: 18px;
  border-radius: 4px;
  accent-color: var(--text-primary);
}

```

---

## 五、右侧面板：Studio 智能体工作站 (Agent Grid)

这是视觉的重中之重。抛弃传统的列表，采用 2 列网格的**莫兰迪彩色卡片**。

### 5.1 上半部：Sub-Agent 网格

```css
.studio-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  padding: 16px;
}

/* 彩色智能体卡片 */
.agent-grid-card {
  height: 64px;
  border-radius: var(--radius-md);
  padding: 12px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  cursor: pointer;
  border: none;
  position: relative;
  transition: transform 0.1s ease, filter 0.2s ease;
}

.agent-grid-card:hover {
  filter: brightness(0.95); /* 悬浮时略微压暗 */
}

.agent-grid-card:active {
  transform: scale(0.98);
}

/* 右上角的跳转/聚焦箭头 */
.agent-grid-card .icon-arrow {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 16px;
  height: 16px;
  opacity: 0.5;
}

/* 智能体名称 */
.agent-grid-card .agent-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

```

### 5.2 组件实现参考 (AgentCard)

```tsx
// src/components/agents/StudioAgentCard.tsx
interface StudioAgentCardProps {
  name: string;
  icon: React.ReactNode;
  colorVar: string; // 例如 'var(--agent-purple)'
  onClick: () => void;
  onDoubleClick: () => void; // 触发 Focus Mode
}

export function StudioAgentCard({ name, icon, colorVar, onClick, onDoubleClick }: StudioAgentCardProps) {
  return (
    <div 
      className="agent-grid-card" 
      style={{ backgroundColor: colorVar }}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
    >
      <div className="agent-icon">{icon}</div>
      <span className="agent-name">{name}</span>
      <ChevronRightIcon className="icon-arrow" />
    </div>
  )
}

```

---

## 六、中间面板：对话区与悬浮输入框 (Chat Canvas)

### 6.1 悬浮药丸输入框

输入框**不固定在底部**，而是像一个浮岛一样漂浮在消息列表上方。

```css
/* 输入框外层容器（用于定位） */
.chat-input-wrapper {
  position: absolute;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  width: calc(100% - 48px);
  max-width: 760px;
  z-index: 10;
}

/* 真正的输入框卡片 */
.pill-input-box {
  background: var(--bg-input);
  border-radius: var(--radius-pill);
  box-shadow: var(--shadow-float);
  border: 1px solid var(--border-light);
  min-height: 56px;
  padding: 8px 16px 8px 24px;
  display: flex;
  align-items: center;
  gap: 12px;
}

.pill-input-box textarea {
  flex: 1;
  border: none;
  background: transparent;
  outline: none;
  font-size: 15px;
  color: var(--text-primary);
  max-height: 120px;
  resize: none;
}

/* 左侧来源统计标识 (例如: "28 sources") */
.source-counter {
  font-size: 12px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
  background: var(--bg-hover);
  border-radius: var(--radius-pill);
}

/* 右侧发送按钮 */
.btn-send-pill {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--bg-hover);
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  cursor: pointer;
}
.btn-send-pill.active {
  background: var(--text-primary);
  color: var(--bg-panel);
}

```

### 6.2 消息气泡

```css
/* 消息容器居中对齐 */
.message-list {
  padding: 24px 24px 120px 24px; /* 底部留出悬浮输入框的空间 */
  display: flex;
  flex-direction: column;
  gap: 24px;
  max-width: 800px;
  margin: 0 auto;
}

/* 用户消息：右侧对齐，带浅色背景 */
.message-user {
  align-self: flex-end;
  background: var(--bg-user-bubble);
  padding: 12px 16px;
  border-radius: 16px 16px 4px 16px; /* Material 风格的非对称圆角 */
  font-size: 15px;
  max-width: 80%;
}

/* AI 消息：左侧对齐，无背景色，纯文本呈现 */
.message-ai {
  align-self: flex-start;
  padding: 0;
  font-size: 15px;
  line-height: 1.7;
  max-width: 90%;
}

/* 消息底部的操作栏（Save to note, Copy, Like） */
.message-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  opacity: 0;
  transition: opacity 0.2s;
}
.message-ai:hover .message-actions {
  opacity: 1;
}

.action-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-light);
  font-size: 12px;
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}
.action-pill:hover { background: var(--bg-hover); }

```

---

## 七、交互动画 (Material 3 Easing)

NotebookLM 的切换动画极其丝滑，禁止使用默认的 `ease`。统一使用 M3 标准的强调贝塞尔曲线。

```css
* {
  /* Emphasized easing (M3 标准): 加速快，减速平滑 */
  transition-timing-function: cubic-bezier(0.2, 0.0, 0.0, 1.0);
}

/* Focus Mode 切换动画：面板滑出替换 */
.panel-enter {
  animation: slideIn 0.4s cubic-bezier(0.2, 0.0, 0.0, 1.0) forwards;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateX(20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

```

---

## 八、禁止事项与开发约束

1. **绝对禁止纯黑阴影**：卡片不允许有大面积的重阴影。立体感由 `var(--bg-app)` 的灰底色和 `var(--bg-panel)` 的纯白对比产生。
2. **禁止尖锐直角**：除了个别消息气泡的特定边角，所有可交互元素必须具备明显的圆角（至少 `8px`）。
3. **取消旧版的 Agent 状态圆点**：在 NotebookLM 风格中，Sub-Agent 卡片的动态通过整张卡片的微光/骨架屏扫光动画来体现，而不是在头像上加红绿状态小圆点。
4. **禁止全屏充满**：三大主面板外围必须留有 `16px` 的 `--bg-app` 背景边距（Padding），不能像旧版那样贴着浏览器边缘。
5.**禁止使用emoji**


```

```