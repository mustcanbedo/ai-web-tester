# AI Web Tester 架构与卡点分析

> 更新时间：2026-04-02

---

## 1. 系统架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    前端 UI (index.html)                   │
│              单文件 SPA，SSE 实时日志流                     │
└─────────────────────┬───────────────────────────────────┘
                      │ HTTP API (FastAPI)
┌─────────────────────▼───────────────────────────────────┐
│                  app.py (FastAPI)                         │
│        任务管理 (创建/暂停/取消/恢复)                       │
│        SSE 日志推送，报告/视频/截图下载                      │
└──────┬──────────────────────────────────────────────────┘
       │ 启动测试线程
┌──────▼──────────────────────────────────────────────────┐
│              test_runner.py (主循环)                      │
│                                                          │
│  while step < MAX_STEPS:                                 │
│    1. evidence.collect()          # 采集控制台/网络错误    │
│    2. bridge.snapshot()           # 获取 AX Tree          │
│    3. llm.decide(page_state)      # LLM 决策下一步        │
│    4. executor.execute(action)    # 执行浏览器操作         │
│    5. 卡死检测 / 流程切换 / 上下文管理                      │
└──────┬──────────┬───────────────┬───────────────────────┘
       │          │               │
┌──────▼──┐ ┌────▼─────┐ ┌──────▼────────────────────────┐
│ LLM     │ │ Action   │ │ Playwright Bridge              │
│ Engine  │ │ Executor │ │ (playwright_bridge.py)          │
│         │ │          │ │                                │
│ OpenAI  │ │ 20 种    │ │ - Chromium CDP 控制             │
│ 兼容API │ │ action   │ │ - AX Tree snapshot             │
│         │ │ types    │ │   + 空名按钮位序推断            │
│ 系统    │ │          │ │   + 非标准可点击元素检测         │
│ prompt  │ │ navigate │ │ - click/fill/hover/scroll...   │
│ +       │ │ click    │ │ - 多标签页管理                  │
│ 流程    │ │ fill     │ │ - 视频录制                      │
│ 文档    │ │ execute_ │ │                                │
│ 注入    │ │ js (新)  │ │ 元素定位链路:                    │
│         │ │ hover    │ │   ref(e5)                      │
│ 上下文  │ │ ...      │ │   → backendDOMNodeId           │
│ 裁剪    │ │          │ │   → CDP.DOM.resolveNode        │
│         │ │          │ │   → Playwright locator         │
└─────────┘ └──────────┘ └────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────┐
│              evidence.py (证据采集)                       │
│        Console 错误 / Network 错误 / API 响应拦截         │
└─────────────────────────────────────────────────────────┘
```

### 文档层

| 文件 | 作用 |
|------|------|
| `core-flow.md` | 测试流程文档（7 个流程），Agent 按此执行测试 |
| `api-docs.md` | 被测应用的 API 接口文档 |
| `prompt-generate-docs.md` | 文档生成提示词（经验规则库，每次踩坑后反向更新） |

---

## 2. 核心模块职责

### 2.1 `playwright_bridge.py` — 浏览器控制层

- **启动浏览器**：Chromium，支持 headless/headed，自动恢复 cookies
- **AX Tree 快照**：调用 Playwright Accessibility snapshot，生成 `refs` 列表（`[{ref, role, name}]`）
  - **空名按钮推断**：对 `name` 为空的 button/link/tab，通过 CDP 注入 JS 推断名称：
    1. `aria-label` / `title`
    2. Iconify `data-icon` 属性
    3. 子元素 `icon` 属性
    4. SVG title / img alt
    5. **位序+上下文**：找最近的卡片级容器（`rounded+shadow` / 有 heading 的语义结构），返回 `[按钮2/2 @ test-3dgs-001]` 或 `[唯一按钮 @ 项目名]`
  - **非标准可点击元素检测**：扫描 `cursor:pointer` / Vue `_vei` / React `__reactProps$` 的 div/span 等，追加到 refs
- **元素定位**：`ref → backendDOMNodeId → CDP.DOM.resolveNode → Playwright locator`
- **点击**：先 `locator.click()`，被拦截时降级为 JS 完整事件序列（pointerdown→mousedown→pointerup→mouseup→click）

### 2.2 `llm_engine.py` — LLM 决策层

- **系统 prompt**：定义 20 种 action type + 规则
- **输入构造**：AX Tree refs + 页面文本 + 流程进度 + 证据 + 上下文
- **上下文裁剪**：消息历史超限时裁剪旧消息，保留系统 prompt 和最近上下文
- **输出解析**：JSON `{thinking, action, found_issues, current_flow, flow_status, checklist_item, ...}`
- **报告生成**：测试结束后 LLM 生成结构化测试报告

### 2.3 `action_executor.py` — 动作执行层

20 种 action type：
`navigate` `click` `fill` `type` `press` `hover` `select` `wait` `screenshot` `scroll` `assert_text` `verify_data` `call_api` `compare_api_vs_page` `extract_data` `execute_js`(新) `switch_tab` `close_tab` `get_tabs` `finish` `request_human_input`

### 2.4 `test_runner.py` — 测试主循环

- **流程管理**：跟踪 `current_flow` / `completed_flows` / `failed_flows`
- **子步骤去重**：`done_items_in_current_flow` 注入 LLM 上下文，避免重复执行
- **卡死检测**：
  - 模式 1：同一流程内连续 6 步高比例 click + 多次失败 → 强制跳子节/流程
  - 模式 2（新）：导航循环（2 个 URL 间来回跳，即使全部成功）→ 也判定卡死
- **finish 拒绝**：流程子节 ≥5 且 failed 时才建议跳流程，防止过早结束
- **cookies 持久化**：测试结束保存，下次恢复登录态

### 2.5 `evidence.py` — 证据采集层

- 监听 `console.error` 事件
- 监听 network response 失败（status ≥ 400）
- 拦截 API 响应摘要

### 2.6 脚本化测试模块（新增 v7）

> **设计理念**：操作路径确定（脚本定义），元素定位确定（策略函数），只有结果验证调用 LLM（YES/NO 判定）。

| 文件 | 作用 |
|------|------|
| `script_runner.py` | 脚本执行器：解析 YAML → 按步执行 → AI 验证结果 |
| `element_resolver.py` | 智能元素定位：按策略（by_text/by_role/card_menu_button）在 AX Tree refs 中精确定位 |
| `test_scripts/*.yaml` | 测试脚本：每个 YAML 文件对应一个测试流程 |

**架构对比**：

```
探索式（旧）：LLM 决定操作 → LLM 选元素 → LLM 判结果  （三层不确定性）
脚本化（新）：脚本定义操作 → 策略函数选元素 → LLM 判结果（一层不确定性）
```

**YAML 脚本格式**：
```yaml
flow: "流程名"
precondition: "user_logged_in"
steps:
  - id: "2.1.1"
    name: "导航到项目列表页"
    action: navigate          # 确定性操作
    params:
      url: "{base_url}/datasets/list"
    verify:                    # AI 验证（可选）
      - type: url_contains     # 确定性验证（不需要 LLM）
        value: "/datasets"
      - type: ai_judge         # AI 验证（需要 LLM）
        question: "页面是否显示了项目列表？"
```

**元素定位策略**：
- `by_text`：按文本内容匹配（如"重命名"）
- `by_role`：按角色匹配（如 searchbox），支持 fallback
- `by_name`：按 name 精确/模糊匹配
- `card_menu_button`：卡片菜单按钮定位（识别 `[更多操作]` / `[按钮N/M @]` 模式）

**API 入口**：
- `GET /api/scripts` — 列出可用脚本
- `POST /api/test/script/start` — 启动脚本化测试
- SSE 日志流复用现有 `/api/test/{task_id}/stream`

---

## 3. LLM 数据流

```
                    ┌─────────────────────┐
                    │     LLM 输入         │
                    ├─────────────────────┤
                    │ 系统 prompt          │
                    │ + core-flow.md 注入  │
                    │ + AX Tree refs      │
                    │ + 页面文本(截断)      │
                    │ + 流程进度            │
                    │   - completed_flows  │
                    │   - done_items       │
                    │   - failed_flows     │
                    │ + 证据(console/net)  │
                    │ + 上一步执行结果      │
                    │ + 额外上下文(卡死等)  │
                    └─────────┬───────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │      LLM 输出       │
                    ├─────────────────────┤
                    │ thinking            │
                    │ action {type,params} │
                    │ found_issues []     │
                    │ current_flow        │
                    │ flow_status         │
                    │ checklist_item      │
                    │ should_continue     │
                    │ next_plan           │
                    └─────────────────────┘
```

---

## 4. 当前卡点（按严重程度排序）

### 卡点 1 ⭐ Radix Vue Popover 点击失效

**严重程度**：P0 — 导致流程三（训练任务管理）几乎全部失败

**现象**：
- 点击任务卡片的"更多"按钮（Popover trigger），Popover 菜单不弹出
- 点击"下载"按钮（也是 Popover trigger），下载菜单不弹出
- 流程三所有依赖 Popover 的操作（重命名、删除、下载、查看结果）全部报 P1 Bug

**根因分析**：
- Playwright 的 `locator.click()` 是原生鼠标事件，对标准 HTML 按钮有效
- Radix Vue 的 `PopoverTrigger` 内部监听的是 `pointerdown` 事件（不是 `click`）
- 当 `locator.click()` 正常执行（没有被拦截）时，它**确实**会触发 pointerdown，理论上应该能工作
- **但实际不工作**，可能原因：
  1. Radix Vue PopoverTrigger 的 `asChild` 模式下，事件绑定在 Slot 的子元素上而非 button 本身
  2. Playwright 的 AX Tree ref 指向的 DOM 节点和实际绑定事件的 DOM 节点不是同一个
  3. Radix Vue 的 `@pointerdown` 可能通过 Vue 事件系统绑定（`_vei`），而非标准 `addEventListener`，Playwright 的原生鼠标事件可能触发了标准事件但没触发 Vue 事件
  4. 时序问题：Playwright click 太快，Popover 内部状态还没初始化

**待验证方向**：
- 用 `execute_js` 在实际页面上调试：`document.querySelector('button').dispatchEvent(new PointerEvent('pointerdown', {bubbles:true}))` 看 Popover 是否打开
- 检查 Radix Vue 源码中 PopoverTrigger 的事件绑定方式
- 对比 Playwright `locator.click()` vs `locator.dispatchEvent('click')` 的行为差异
- 考虑是否需要先 hover 再 click（模拟真实用户行为）

**影响范围**：整个流程三（约 30% 的测试覆盖率）

---

### 卡点 2 ⭐ Agent 重复执行已完成的子步骤

**严重程度**：P1 — 浪费步数，导致后续流程覆盖不足

**现象**：
- Agent 在 5.3（删除项目）时反复点击卡片→进入详情→返回→再点击，循环 8+ 次
- Agent 在流程三中反复尝试同一个操作

**已实施的修复**（效果待验证）：
- ✅ `done_items_in_current_flow` 注入 LLM 上下文
- ✅ 导航循环卡死检测（2 URL 来回跳也判卡死）
- ✅ `core-flow.md` 补充明确操作步骤（5.3 引用 2.4）

**剩余风险**：
- 卡死检测需要累积 6 步才触发，前 5 步仍是浪费
- Agent 的 thinking 每次描述不同（虽然做同样的事），导致文本去重失败
- LLM 上下文裁剪可能丢失了关键的"已做过"信息

---

### 卡点 3 ⭐ 空名按钮识别的容器边界问题

**严重程度**：P1 — 已修复，但新逻辑未经实测验证

**现象**：
- 项目列表页：每个项目卡片只有 1 个按钮（菜单 `···`），旧逻辑向上找"包含 ≥2 按钮的容器"，一直找到整个列表（15 个按钮），输出 `[按钮3/15 @ 项目]`
- Agent 看到 15 个按钮完全无法定位

**已实施的修复**：
- ✅ 改为"卡片级容器识别"：通过 `rounded+shadow` class 特征 或 有 heading + ≤8 按钮的语义结构
- ✅ 单按钮卡片返回 `[唯一按钮 @ 卡片名]`

**剩余风险**：
- 正则 `/\brounded.*shadow/` 对不同 CSS 框架的 class 命名是否通用
- 如果卡片嵌套层级变化（如加了外层 wrapper），可能需要调整向上查找的层数

---

### 卡点 4 Agent 进入禁止的 projectId=25

**严重程度**：P2 — 导致测试数据被污染

**现象**：Agent 两次进入了文档明确禁止的 `projectId=25` 项目

**根因**：
- `core-flow.md` 用 `> ⚠️ 不要进入 projectId=25 的项目` 标注，但 Agent 可能在裁剪上下文后丢失了这个约束
- 或者 Agent 按某种逻辑（如"选择第一个项目"）自动选中了它

**可能的解决方向**：
- 在系统 prompt 顶部（不会被裁剪的位置）硬编码 `projectId=25` 的禁令
- 在 `action_executor.py` 的 `navigate` 中加前置检查，URL 含 `projectId=25` 时拒绝执行

---

### 卡点 5 LLM 上下文窗口管理

**严重程度**：P2 — 间接导致重复执行和规则遗忘

**现象**：
- 测试到 100+ 步后，消息历史被大幅裁剪
- 早期的流程进度、禁止规则可能被丢弃
- Agent "忘记"自己之前做过什么

**当前策略**：
- `flow_progress` 每步重新构建并注入（不依赖历史消息）
- 系统 prompt 始终保留

**可优化方向**：
- 增加"持久记忆"机制：关键决策/发现写入独立的 memory 结构，不随消息裁剪
- 对已完成流程的历史做摘要压缩而非直接删除

---

## 5. 技术栈

| 组件 | 技术 |
|------|------|
| 后端框架 | Python + FastAPI |
| 浏览器自动化 | Playwright (Chromium) + CDP |
| LLM | OpenAI 兼容 API（可切换模型） |
| 前端 UI | 单文件 HTML（无构建工具） |
| 被测应用 | Vue 3 + Radix Vue + Iconify v5 + TailwindCSS |

## 6. 文件清单

| 文件 | 大小 | 职责 |
|------|------|------|
| `app.py` | 14KB | FastAPI 路由 + 任务管理 |
| `test_runner.py` | 51KB | 测试主循环 + 卡死检测 + 流程管理 |
| `playwright_bridge.py` | 34KB | 浏览器控制 + AX Tree + 按钮推断 |
| `llm_engine.py` | 26KB | LLM 调用 + prompt + 报告生成 |
| `action_executor.py` | 13KB | 20 种 action 执行 |
| `evidence.py` | 4KB | 证据采集 |
| `config.py` | 1KB | 配置常量 |
| `core-flow.md` | 27KB | 测试流程文档（7 个流程） |
| `api-docs.md` | 23KB | API 接口文档 |
| `prompt-generate-docs.md` | 13KB | 文档生成提示词（经验规则库） |
| `index.html` | 74KB | 前端 UI |
