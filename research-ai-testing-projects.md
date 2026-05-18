# AI Web 测试项目调研报告

> 调研时间：2026-05-18
> 目的：对标业界主流 AI Web 测试/浏览器自动化项目，找出我们 ai-web-tester 可参考优化的方向

---

## 1. 调研项目概览

| 项目 | Stars | 定位 | 核心技术 |
|------|-------|------|---------|
| **[browser-use](https://github.com/browser-use/browser-use)** | 70k+ | 通用 AI 浏览器 Agent | Python + Playwright + CDP + DOM Tree + 可选 Vision |
| **[Stagehand](https://github.com/browserbase/stagehand)** | 14k+ | 浏览器自动化 SDK（代码+NL 混合） | TS/Python + Playwright + act/observe/extract 三原语 |
| **[Playwright MCP](https://github.com/microsoft/playwright-mcp)** | 19k+ | MCP Server，LLM 通过 MCP 协议操控浏览器 | Node.js + Playwright AX Tree + MCP 协议 |
| **[Playwright CLI](https://github.com/microsoft/playwright-cli)** | - | CLI 工具，coding agent 直接调命令 | CLI 命令（比 MCP 更省 token） |

---

## 2. 关键架构差异对比

### 2.1 页面理解方式

| 维度 | 我们 (ai-web-tester) | browser-use | Stagehand | Playwright MCP |
|------|---------------------|-------------|-----------|----------------|
| **DOM 提取** | Playwright AX Tree + CDP | 自建 `buildDomTree.js` 注入脚本，提取完整 DOM 树，给每个交互元素分配 index | Playwright AX Tree + 自有 DOM 处理 | Playwright AX Tree |
| **Vision (截图)** | ❌ 不支持 | ✅ 支持 `use_vision="auto"` 按需截图 | ✅ 支持 | ❌ 纯文本 |
| **非标准元素** | 自写 JS 检测 `cursor:pointer` / Vue `_vei` / React props | `buildDomTree.js` 中检测 `getEventListeners`（CDP API）+ cursor:pointer | 依赖 AX Tree + LLM 推理 | 纯 AX Tree |
| **空名按钮** | 自写位序推断 `[按钮N/M @ 卡片名]` | 给每个元素分配数字 index，LLM 通过 index 引用 | LLM 自然语言描述 + observe 匹配 | AX Tree ref |

**📌 可参考优化**：
1. **browser-use 的 `buildDomTree.js` 方案**：它不依赖 AX Tree，而是直接遍历 DOM，给每个可交互元素注入 `browser-user-highlight-id` 属性并分配数字 index。这比 AX Tree 更可靠地捕获所有可交互元素（包括 Shadow DOM、自定义组件等）。**但代价是脚本较重（Amazon 首页需 5-6 秒）**。
2. **Vision 能力（截图 + 视觉模型）**：当 AX Tree 无法准确描述 UI（如图标按钮、Canvas 图表）时，截图可作为补充。browser-use 的 `use_vision="auto"` 模式只在需要时发送截图，平衡了 token 成本和准确性。**对我们的 Popover 按钮问题，截图可以帮助 Agent 确认菜单是否打开**。

---

### 2.2 LLM 交互模式

| 维度 | 我们 | browser-use | Stagehand |
|------|------|-------------|-----------|
| **每步动作数** | 1 个 action/step | **最多 N 个 action/step**（`max_actions_per_step=4`） | 1 个原语/step |
| **动作格式** | 自定义 JSON `{type, params}` | Function Calling / Tool Use（原生 LLM 工具调用） | `act("click the button")` 自然语言 |
| **LLM 调用协议** | 纯文本 prompt + JSON 解析 | **原生 Function Calling**（OpenAI/Anthropic/Google） | 原生 Function Calling |
| **思考过程** | `thinking` 字段在 JSON 中 | 专门的 `use_thinking` 模式 + `flash_mode`（跳过思考） | 内部推理 |
| **上下文管理** | 手动裁剪旧消息 | `max_history_items` + **compaction（压缩旧步骤）** | 内部管理 |

**📌 可参考优化**：
1. **多动作批量执行（multi-action per step）**：browser-use 允许 LLM 一次返回多个动作（如填写表单时一次输出 3-4 个 fill），顺序执行直到页面变化。**对我们的表单填写场景（登录、创建任务、重命名）可大幅减少 LLM 调用次数**。
2. **原生 Function Calling 替代 JSON 解析**：我们目前用纯文本 prompt 让 LLM 输出 JSON，然后手动解析。如果改用 OpenAI/Anthropic 的原生 function calling，可以：
   - 消除 JSON 解析失败问题
   - LLM 输出更结构化
   - 减少 prompt 中描述 JSON 格式的 token 开销
3. **历史压缩（compaction）**：browser-use 对旧步骤做摘要压缩而非直接删除。我们目前是裁剪旧消息，导致 Agent "忘记"之前做过的事。**可以实现类似机制：每 N 步对已完成流程的历史做 LLM 摘要**。
4. **flash_mode（快速模式）**：browser-use 的 flash mode 跳过评估/思考，只保留记忆和动作。对于简单确定性步骤（如登录），可以节省大量 token。

---

### 2.3 元素点击与交互

| 维度 | 我们 | browser-use | Playwright MCP |
|------|------|-------------|----------------|
| **点击方式** | `locator.click()` → 降级 JS 事件序列 | **CDP `Input.dispatchMouseEvent`**（原生输入事件） | Playwright `locator.click()` |
| **键盘导航** | 不支持 | ✅ `send_keys("Tab Tab Enter")` | ✅ `browser_press_key` |
| **表单填写** | `fill()` 或 `type_text()` | `input_text(index, text)` | `browser_fill` |
| **下拉选择** | `select()` | `select_dropdown(index, value)` + `dropdown_options(index)` | `browser_select_option` |
| **拖拽** | ❌ | ❌ | ✅ `browser_drag` |

**📌 可参考优化**：
1. **CDP `Input.dispatchMouseEvent` 替代 Playwright click**：browser-use 通过 CDP 直接注入鼠标事件，这比 Playwright 的 `locator.click()` 更底层。**这可能是解决我们 Radix Vue Popover 点击失效的关键**——CDP 原生输入事件更接近真实用户操作，能正确触发 pointerdown/pointerup 事件链。
2. **键盘导航作为 fallback**：browser-use 的官方文档明确建议"如果按钮无法点击，用 Tab + Enter 键盘导航"。**我们可以在 click 失败后自动尝试键盘导航（focus → Enter）**。
3. **`dropdown_options` 先查再选**：browser-use 分两步处理下拉：先获取选项列表，再选择。这比我们的 `select(ref, value)` 更健壮。

---

### 2.4 Agent 生命周期管理

| 维度 | 我们 | browser-use |
|------|------|-------------|
| **卡死检测** | 自写：连续失败 + 导航循环 | `max_failures=3` 自动重试 + 结构化错误恢复 |
| **任务完成** | Agent 输出 `finish` action | Agent 调用 `done` tool |
| **初始化动作** | 无 | ✅ `initial_actions`（预定义的无 LLM 步骤） |
| **结构化输出** | 自定义 JSON | ✅ Pydantic `output_model_schema` |
| **GIF/视频** | ✅ 视频录制 | ✅ `generate_gif` |
| **成本追踪** | ❌ | ✅ `calculate_cost=True` |

**📌 可参考优化**：
1. **`initial_actions`（预定义初始动作）**：对于确定性步骤（打开 URL、登录），可以跳过 LLM 直接执行。**节省前 5-10 步的 LLM 调用成本**。
2. **成本追踪**：记录每次测试的 API token 用量和费用，便于优化。
3. **结构化输出验证**：用 Pydantic schema 约束 LLM 输出格式，比正则解析更可靠。

---

### 2.5 Stagehand 的独特设计

Stagehand 的思路和我们不同——它不是纯 Agent，而是 **"代码 + AI 混合"**：

```python
# Stagehand 方式：用自然语言描述意图，框架负责定位和执行
client.sessions.act(session_id, input="Click the comments link for the top post")
client.sessions.extract(session_id, instruction="extract the top comment text")
client.sessions.observe(session_id, instruction="find the login button")
```

三个核心原语：
- **`act`**：执行一个自然语言描述的操作
- **`observe`**：观察页面，返回匹配的元素列表
- **`extract`**：从页面提取结构化数据

**关键特性：自动缓存 + 自愈**
- 第一次 `act("click login")` 时 LLM 定位元素，后续自动缓存，不再调 LLM
- 当页面 DOM 变化导致缓存失效时，自动重新调 LLM（self-healing）

**📌 对我们的启发**：
- 我们可以对高频确定性操作（如"点击导航栏的项目链接"）做 **action 缓存**：第一次用 LLM 定位，后续直接用缓存的 selector
- `observe` 原语的思路值得借鉴：先让 LLM 观察页面找到目标元素，再执行操作，而非一步到位

---

### 2.6 Playwright MCP 的设计哲学

微软的建议很明确：

> **CLI 适合 coding agent**（更省 token，避免加载大型工具 schema 和冗长的 AX Tree）
> **MCP 适合自治 Agent**（持久状态、丰富内省、迭代推理）

我们的场景属于后者（自治测试 Agent），MCP 模式更合适。但 CLI 的 token 效率思路值得参考：
- 不必每次都传完整的 tool schema
- AX Tree 可以按需裁剪（只传可视区域的元素）

---

## 3. 针对我们项目卡点的优化建议

### 卡点 1：Radix Vue Popover 点击失效

| 方案 | 来源 | 可行性 | 推荐度 |
|------|------|--------|--------|
| **CDP `Input.dispatchMouseEvent`** | browser-use | ⭐⭐⭐⭐ 我们已有 CDP 连接 | **★★★★★** |
| 键盘导航 fallback（Tab + Enter） | browser-use 文档 | ⭐⭐⭐ 简单实现 | ★★★★ |
| 截图确认 + 重试 | browser-use vision | ⭐⭐ 需要视觉模型 | ★★ |

**具体方案**：修改 `playwright_bridge.py` 的 `click()` 方法，在 `locator.click()` 失败后，不是降级为 JS `dispatchEvent`，而是降级为 **CDP `Input.dispatchMouseEvent`**：

```python
# 通过 CDP 发送原生鼠标事件（比 JS dispatchEvent 更底层）
def _cdp_click(self, x, y):
    cdp = self._cdp  # 已有的 CDP session
    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
```

### 卡点 2：Agent 重复执行 / 上下文遗忘

| 方案 | 来源 | 可行性 | 推荐度 |
|------|------|--------|--------|
| **历史压缩（compaction）** | browser-use | ⭐⭐⭐⭐ | **★★★★★** |
| `initial_actions` 跳过确定性步骤 | browser-use | ⭐⭐⭐⭐ | ★★★★ |
| `max_history_items` 精细控制 | browser-use | ⭐⭐⭐ | ★★★ |
| action 缓存（重复操作免 LLM） | Stagehand | ⭐⭐ 实现复杂 | ★★ |

**具体方案**：
1. 每 15-20 步对历史做一次 LLM 摘要（"你已完成：登录、项目列表验证…正在执行：训练任务重命名"）
2. 登录流程改为 `initial_actions`（直接执行 navigate + fill + click，不经过 LLM）
3. 保留最近 10 步完整记录 + 之前的摘要

### 卡点 3：空名按钮识别

| 方案 | 来源 | 可行性 | 推荐度 |
|------|------|--------|--------|
| **自建 DOM 遍历脚本（类 buildDomTree.js）** | browser-use | ⭐⭐⭐ 重构量大 | ★★★ |
| 当前方案（AX Tree + 位序推断） | 我们自己 | ⭐⭐⭐⭐ 已实现 | ★★★★ |
| Vision 辅助（截图让 LLM 看） | browser-use | ⭐⭐ 成本高 | ★★ |

当前方案已经比较好了，短期不需要大改。**长期可考虑 buildDomTree.js 方案，但需要控制性能**。

### 新增优化方向

| 优化 | 来源 | 效果 | 优先级 |
|------|------|------|--------|
| **多动作批量执行** | browser-use `max_actions_per_step` | 表单场景 LLM 调用减少 50-70% | P1 |
| **原生 Function Calling** | browser-use | 消除 JSON 解析问题，token 更少 | P2 |
| **成本追踪** | browser-use | 可量化优化效果 | P3 |
| **`go_back` action** | browser-use / Playwright MCP | Agent 可明确后退而非 navigate 回去 | P3 |
| **`send_keys` action** | browser-use | 键盘导航，解决部分点击失效问题 | P2 |

---

## 4. 总结

### 我们做得好的地方
- **非标准可点击元素检测**（Vue `_vei` / React props 检测）—— 比 browser-use 的方案更精准
- **空名按钮位序推断**（`[按钮N/M @ 卡片名]`）—— 独创方案，AX Tree 场景下很实用
- **卡死检测**（操作模式 + 导航循环）—— 比 browser-use 的简单 `max_failures` 更智能
- **测试流程文档注入**—— 让 Agent 按照明确的测试规范执行，而非纯开放式探索

### 我们需要改进的地方（按优先级）
1. **P0：CDP 原生点击替代 JS dispatchEvent** — 解决 Popover 等组件库的点击问题
2. **P1：多动作批量执行** — 减少 LLM 调用次数，加速表单填写场景
3. **P1：历史压缩** — 解决长测试流程中的上下文遗忘问题
4. **P2：键盘导航 fallback（send_keys）** — 点击失效时的备选方案
5. **P2：initial_actions（预定义初始步骤）** — 跳过登录等确定性步骤
6. **P2：原生 Function Calling** — 提高输出可靠性，减少 token
7. **P3：Vision 模式（可选截图）** — 补充 AX Tree 无法描述的 UI 场景
8. **P3：成本追踪** — 量化每次测试的 API 费用
