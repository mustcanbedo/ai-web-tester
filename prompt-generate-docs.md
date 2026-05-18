# AI Web Tester — 测试文档生成提示词

> **用途**：将下面的提示词复制到任意 AI IDE（Cursor / Windsurf / GitHub Copilot Chat 等），在你的**前端项目**中执行，AI 会自动分析代码并生成两份文档供 [AI Web Tester](https://github.com/xxx/ai-web-tester) 使用。
>
> **原理**：AI Web Tester 的 Agent 通过 Playwright Accessibility Tree（可交互元素的 role / name / checked 等属性列表）+ 页面文本来观测页面状态，由 LLM 决策下一步操作。Agent **看不到截图**，只能靠元素文本/角色定位操作。因此文档中每一步描述都必须精确到可仅凭文本定位。

---

## 快速开始

1. 在 AI IDE 中打开你的**前端项目**
2. 复制 [提示词正文](#提示词正文) 发给 AI
3. AI 自动分析代码，生成 `core-flow.md` + `api-docs.md` + YAML 测试脚本
4. 将 `core-flow.md` 和 `api-docs.md` 放到 AI Web Tester 的 `specs/` 目录，YAML 脚本放到 `test_scripts/` 目录

> **网页版 AI**（ChatGPT / Claude）：把提示词 + 关键源码文件一起粘贴，见 [附录：网页版用法](#附录网页版用法)。

---

## 提示词正文

> 以下内容从 ` ``` ` 开始到 ` ``` ` 结束，整段复制即可。

````
你是一个专业的 QA 测试架构师。请你通读我当前项目的源代码，自动生成两份 Markdown 文档，用于 AI 自动化测试系统（AI Web Tester）。

## 背景

这两份文档将被一个 AI 测试 Agent 消费：
- Agent 通过 Playwright 操作浏览器
- Agent 能看到页面的 **Accessibility Tree**（可交互元素列表，包含 role、name、checked 等属性）和**页面文本**
- Agent **看不到截图**
- 因此文档中的操作描述必须精确到 Agent 能仅凭元素文本/角色/placeholder 定位并操作

**关键限制（必须理解）**：
- **纯图标按钮问题**：如果一个 `<Button>` 内部只有图标（如 `<Icon icon="mdi:dots-horizontal" />`）没有文字，它在 Accessibility Tree 中的 name 会是空字符串。Agent 无法通过文本定位这种按钮。文档中必须通过位置/顺序描述（如“操作区第 4 个按钮”“最右侧的 button”）。
- **非标准可点击元素**：`<div @click>`、`<h3 @click>` 等非原生交互元素不在标准 AX Tree 中。AI Web Tester 会将它们检测为 role=`clickable`。文档中对这类元素应用 `role=clickable` + name 描述。
- **多按钮操作区**：卡片组件底部常有多个按钮（下载、查看、详情、更多等），必须明确按钮数量和从左到右的顺序，告诉 Agent 每个按钮的功能。

---

## 第一步：分析代码

请先通读项目代码，重点关注：

### 1. 路由与页面
- 路由配置（router/index.ts、App.tsx 路由定义、pages/ 目录等）
- 每个路由对应的页面组件及功能
- 路由守卫（未登录跳转、权限拦截、重定向规则）
- 路由 meta（layout、requiresAuth 等）

### 2. API 调用
- API 封装文件（api/、services/、request.ts 等）
- 每个接口：请求方式（GET/POST）、路径、参数、返回结构
- 请求拦截器：认证方式（Token Header 名称、存储位置）
- 响应拦截器：错误处理（如 status 999 自动登出）
- 后端服务 baseURL 配置

### 3. 页面交互
- 表单：输入字段、校验规则（必填/格式/长度）、校验时机、错误提示文案
- 列表：搜索（防抖时间）、筛选、排序、分页（每页条数）、轮询刷新
- 弹窗/对话框：触发条件、标题、确认/取消按钮文本
- 下拉选择器：选项来源、默认值
- Switch/Checkbox：默认状态、关联校验
- 特殊交互：文件上传、Tab 切换、拖拽、倒计时、iframe 嵌入
- **❗ 窗口打开方式**：区分 `router.push`（当前窗口导航）和 `window.open`（新窗口/标签页）。`window.open` 打开新标签页后系统会自动切换过去，Agent 可用 `close_tab` / `switch_tab` 返回原页面。文档中必须明确标注每个导航操作使用哪种方式。**特别注意**：如果同一个按钮（如"创建训练"）在不同条件下触发不同行为（如 CAD 类型不弹对话框而是 `router.push` 跳转），必须在文档中明确说明"这是当前窗口导航，不要使用 close_tab"
- **❗ 跳转类子节的验证边界**：当某个子节触发页面跳转时，必须明确写清楚：①验证什么（如"确认 URL 变为 xxx"）、②不验证什么（如"不需要等待目标页面加载完成，那属于其他流程"）、③验证后做什么（如"立即返回原页面，继续下一个子节"）。避免 Agent 在目标页面上做不属于当前子节的额外验证
- **❗ iframe 子应用操作**：如果某个流程的关键步骤需要在 iframe 内的子应用中操作（如填写表单、点击按钮），而测试框架无法切换到 iframe 上下文，则该子节应标记为 `[SKIP]`。文档中仍需写出完整的人工验证步骤供参考，但要在标题后加 `[SKIP]` 标记，并在开头用 `> ⚠️` 说明跳过原因
- **❗ 按钮数量差异**：同一组件在不同条件下（**任务类型 + 任务状态**，如 `v-if="hasViewResult && !isMeshTask"`）可能显示不同数量的按钮。文档必须**按条件分别列出所有可能的按钮组合**（例如："成功的点云任务 4 个按钮，非成功任务 2 个按钮"）。**不要用固定位置定位按钮**（如"第 4 个按钮"），应使用"最后一个 button"或"最右侧的 button"等相对描述
- **❗ 纯图标按钮**：记录所有只有图标没有文字的按钮（如 `<Button><Icon icon="mdi:download" /></Button>`）。**如果按钮有 `aria-label`（如 `aria-label="下载"`），AX Tree 中会显示该标签文本**；如果没有 `aria-label`，则显示为 `[按钮N/M @ 卡片名]` 的位序格式或 `[更多操作]`（当有 `aria-haspopup` 时）。文档中应提供**三级定位策略**：①优先用位序格式中"编号最大=最后一个"来定位；②用"最后一个 button"等相对位置描述；③提供**试探法兜底**——描述每个按钮点击后的预期反应（如"弹出菜单"vs"卡片翻转"），让 Agent 通过观察结果确认是否点对。**避免使用绝对位序**（如"第 N 个按钮"），因为 `v-if` 条件渲染会导致按钮数量变化
- **❗ 非标准可点击元素**：记录 `<div @click>` / `<h3 @click>` 等非原生交互元素，它们在 AX Tree 中作为 role=`clickable` 出现
- **❗ 单按钮卡片**：如果一个卡片组件只有一个按钮（如项目卡片只有 `···` 菜单按钮），AX Tree 中该按钮会显示为 `[唯一按钮 @ 卡片名]`。文档中必须明确写出"该卡片只有一个按钮"，避免 Agent 误以为需要在多个按钮中选择而反复尝试。同时注意区分按钮点击和卡片区域点击（如点图片/标题进入详情 vs 点按钮弹出菜单）
- **❗ 跨子节引用必须写明具体操作**：当一个子节需要执行另一个子节中已描述的操作时（如"5.3 删除后数据一致性"需要执行"2.4 删除项目"），不能只写"删除一个项目"，必须写明"按 2.4 的步骤执行删除：找到菜单按钮 → 点击 → 弹出菜单 → 点删除 → 确认"。Agent 无法自动关联不同子节的操作细节
- **❗ 遮罩层/弹窗阻断**：如果应用存在全屏遮罩层（如登录弹窗 `z-index: 9999`、loading overlay），**必须在对应流程的前置条件中写明**："如果页面出现遮罩层/弹窗，必须先处理（关闭/完成登录）再操作底层元素"。遮罩层会拦截所有 pointer events，导致 Agent 误判为"按钮无响应"或"组件渲染失败"。常见场景：① 未登录时的登录弹窗 ② 页面加载中的 loading 蒙版 ③ 确认对话框的背景遮罩
- **❗ 搜索/筛选框使用 type 而非 fill**：如果搜索框有防抖逻辑（`debounce`），文档中应注明"搜索框需用 `type`（逐字输入）操作，不要用 `fill`"，因为 `fill` 是一次性设置 value，可能不触发组件的 `@input` 监听和防抖定时器
- **❗ WebGL/Canvas 3D 可视化页面**：3DGS 编辑器、点云查看器、Mesh 编辑器、CAD 查看器等页面使用 WebGL/Canvas 渲染 3D 内容，Accessibility Tree **天然只有根元素和少量 UI 控件**，这是正常的。文档中必须在对应流程的验证标准里写明："该页面使用 WebGL 渲染，AX Tree 元素极少是正常的。验证标准：① URL 正确（含 taskId 参数）② 无 console 错误 ③ 页面有基础 UI 控件（如工具栏按钮）。不要因为 AX Tree 简单就报'页面空白'"
- **❗ 下载按钮也是 Popover trigger**：如果下载功能是通过卡片操作区的独立按钮（带 `aria-label="下载"`）点击后弹出格式选择 Popover（如 las/ply/splat 等），文档中必须明确写出：①下载按钮在卡片操作区（与详情、更多按钮并列），不在"更多"Popover 菜单里；②点击下载按钮会弹出格式选择菜单；③下载按钮仅在成功任务上显示（`v-if="hasViewResult"`）。避免 Agent 在"更多"Popover 里找下载选项而误报
- **❗ 下载通过 `window.open` 触发**：如果下载功能是通过 `window.open(url, '_blank')` 在新标签页打开文件 URL 实现的，文档中必须写明："下载通过新标签页打开文件 URL 触发，在自动化测试环境中当前页面不会有明显变化（无 URL 跳转、无 DOM 变更），操作结果中出现'触发了新标签页打开'即为下载成功"
- **❗ 输入框区号前缀**：如果手机号输入框内嵌了不可编辑的区号前缀（如 `+86`），文档中必须在异常场景说明："手机号输入框的 `+86` 是固有区号前缀，不属于用户输入内容，`fill("")` 后 value 仍显示 `+86` 是正常行为，不应作为'无法清空'的异常"
- **❗ 浏览器存储操作必须给出完整 JS 代码**：当流程需要操作 `localStorage`/`sessionStorage`/`cookies` 时（如清除 token、设置无效 token），文档中必须给出完整的 `execute_js` action JSON 示例（含 `expression` 和 `description`），不能只写"清除 localStorage 中的 token"——Agent 不知道该用什么 action type 执行，会导致跳过或误报

### 4. 认证与权限
- 登录方式（账号密码 / 手机验证码 / SSO 等）
- 登录表单完整字段
- Token 存储位置和字段名
- 路由守卫行为（弹登录弹窗 vs 重定向）

### 5. 数据模型
- TypeScript 类型定义
- 状态枚举及 UI 文案映射（如 status=4 → "成功"）
- 实体层级关系（如项目 → 任务）

### 6. UI 文案
- i18n 国际化文件（中文翻译）
- 硬编码文本（按钮文字、placeholder、toast、错误信息）
- 页面标题（document.title）

---

## 第二步：生成文档

### 文档一：core-flow.md（功能预期文档）

描述 AI Agent 需要执行的完整测试流程。

**格式规则**：
1. 顶部引用块说明用途 + 登录凭据占位符
2. 按业务流程分章节（`## 流程一：xxx`）
3. 每个流程开头写 **前置条件**
4. 用编号列表描述操作步骤，每步包含 **具体动作** + **预期结果**
5. 复杂交互拆分子步骤（先 click 展开 → 等待出现 → 再 click 选项）
6. 每个流程末尾列 **异常场景**

**深度要求**：

A. **精确 UI 文案** — 所有按钮/placeholder/toast/错误提示从代码提取，**粗体**标注
B. **状态变化** — disabled/loading/倒计时等状态描述
C. **存储层验证** — localStorage/sessionStorage/cookie 变化
D. **URL 变化** — 每次导航后的预期 URL（含 query 参数）
E. **数据一致性**（独立流程）— 列表→详情一致、CRUD 后数据刷新、数量统计校验
F. **认证守卫**（独立流程）— 未登录访问受保护页的行为
G. **异常穷举** — 每个字段的校验规则 + 错误文案
H. **图标按钮定位** — 对于纯图标按钮（AX Tree 中 name 为空），必须用位置描述：“操作区从左到右第 N 个按钮”或“最右侧的 button”。明确操作区按钮总数和每个按钮的功能
I. **非标准可点击元素** — `<div @click>`、`<h3 @click>` 等在 AX Tree 中作为 role=`clickable` 出现，用 `role=clickable` + name 描述定位方式

**流程顺序**：
1. 登录流程（第一个，包含完整表单交互）。**注意**：测试系统可能会自动恢复上次的登录态（cookies），因此登录流程必须包含"已登录检测"逻辑——如果检测到已登录（导航栏显示用户信息而非登录按钮），应跳过登录流程直接进入后续流程
2. 核心 CRUD 流程（按业务主线）
3. 数据一致性验证
4. 认证守卫验证

**❗ 子流程粒度要求**（防止 Agent 浅尝辄止）：
- 每个流程必须拆分为**明确编号的子节**（如 3.1、3.2、3.3…），每个子节描述一个独立的可验证操作
- 每个子节必须包含**具体的操作步骤**和**预期结果**，Agent 必须逐一执行才能标记流程为通过
- **不要合并子流程**：例如"创建、重命名、删除"应分别作为 3.2、3.3、3.4 三个子节，不能合并为一个子节
- 每个子节至少包含 2-3 个操作步骤（如 click→验证→再 click），避免 Agent 只看一眼就跳过
- **如果一个流程有 N 个子节，Agent 至少需要 N×2 步操作才算充分测试**

### 文档二：api-docs.md（接口文档）

描述后端 API 接口，Agent 用它做三层数据验证（前端展示 vs API 返回 vs 数据逻辑）。

**格式规则**：
1. 按功能模块分章节
2. 每个接口包含：请求方式+路径、Content-Type、请求参数表格、返回 JSON 示例
3. 说明认证机制（Token 传递方式）

**必须包含最后一节：数据逻辑关联**：
- 层级关联（如 project.id ↔ task.project_id）
- 数量校验（如 project.task_count = 该项目下 task 列表 total）
- 名称一致性（如 task.project_name = project.project_name）
- 状态与数据关联（如 status=成功时 output_result 不应为空）
- CRUD 幂等性（修改后重查应为新值，删除后不应出现）

### 文档三：YAML 测试脚本（脚本化测试用）

将 `core-flow.md` 中的每个流程转化为可执行的 YAML 脚本，供 AI Web Tester 的脚本化测试模式使用。

**每个流程一个文件**，文件名格式 `flow_N_描述.yaml`（如 `flow_1_login.yaml`、`flow_2_dataset_crud.yaml`）。

**YAML 格式规范**：
```yaml
flow: "流程名称"
precondition: "前置条件描述（如：已登录）"
steps:
  - id: "1.1"
    name: "步骤描述"
    action: find_and_click | find_and_fill | navigate | press | scroll | execute_js | wait | wait_for | snapshot
    params:
      # find_and_click / find_and_fill 参数：
      strategy: by_text | by_role | by_name | by_placeholder
      value: "定位值（按钮文字/role名/name属性/placeholder）"
      text: "填写内容（仅 find_and_fill）"
      # navigate 参数：
      url: "目标URL"
      # press 参数：
      key: "Enter | Escape | Tab 等"
      # execute_js 参数：
      expression: "JavaScript 表达式"
      description: "执行描述"
      # wait 参数：
      duration: 2000  # 毫秒
      # wait_for 参数：
      text: "等待出现的文本"
      timeout: 5000
      # snapshot 参数：（无额外参数）
    verify:
      - type: text_contains | text_not_contains | url_contains | url_equals | element_exists | element_not_exists | ai_judge
        value: "验证值"
        # ai_judge 特有参数：
        prompt: "判断条件描述"
```

**strategy 选择指南**：
- `by_text`：按钮/链接有明确可见文字时使用（如 `"登录/注册"`、`"确认"`）
- `by_role`：按 AX Tree role 定位（如 `role=button`），适合无文字但有 aria-label 的元素
- `by_name`：按元素 name 属性定位（如 input name）
- `by_placeholder`：按 placeholder 文本定位输入框（如 `"请输入邮箱地址"`）

**verify 规则说明**：
- `text_contains` / `text_not_contains`：页面文本包含/不包含指定内容
- `url_contains` / `url_equals`：当前 URL 包含/等于指定值
- `element_exists` / `element_not_exists`：AX Tree 中存在/不存在匹配元素
- `ai_judge`：LLM 判断（用于复杂验证，如 "列表中应该出现刚创建的项目"），prompt 需写清判断标准

**注意事项**：
- 验证码、人工交互步骤用 `execute_js` + `request_human_input` 或在 precondition 中说明
- 涉及 WebGL/Canvas/iframe 的步骤，verify 只检查 URL 和 console 错误，不检查 AX Tree
- 下载操作（`window.open`）的 verify 用 `ai_judge` + prompt "操作结果中出现新标签页打开即为成功"
- 每个 step 的 id 与 `core-flow.md` 中的子节编号对应
- `find_and_fill` 会先清空输入框再填入，如果需要追加输入请用 `press` 逐字输入
- 搜索框有防抖时，在 `find_and_fill` 后加 `wait` 步骤等待防抖完成

---

## 第三步：输出

1. 直接生成 `core-flow.md`、`api-docs.md` 和若干 `flow_N_xxx.yaml` 文件，不要输出额外解释
2. 所有 UI 文案**必须**从代码/i18n 提取真实值，用粗体标注，**不要编造**
3. 登录凭据用占位符（如手机号 `13800138000`、密码 `TestPass123`）
4. 无法从代码确认的细节用 `<!-- TODO: 请确认 -->` 标注
5. 每个异常场景写出精确的错误提示文案

## 补充信息（可选，按需填写后发送）

- 项目线上地址：___
- 测试账号凭据：___
- 重点测试模块：___
- 已知 bug 区域：___
- Swagger/OpenAPI 文档地址：___
- 只生成文档不生成 YAML：___（填 `true` 则跳过 YAML 脚本生成）
````

---

## 附录

### 附录：网页版用法

在 ChatGPT / Claude 等网页版中使用时，把提示词和关键源码一起粘贴：

```
【粘贴上面的提示词】

以下是我的项目关键文件：

--- router/index.ts ---
【粘贴路由配置】

--- api/index.ts ---
【粘贴 API 封装】

--- i18n/zh-CN.json ---
【粘贴中文国际化文件】

--- pages/Login.vue ---
【粘贴登录页组件】

...其他关键页面组件...
```

### 附录：有 Swagger/OpenAPI 文档时

```
【粘贴上面的提示词】

补充：以下是项目的 Swagger JSON，请优先基于此生成 api-docs.md：
【粘贴 swagger.json 内容】
```

### 附录：生成后使用

1. 将 `core-flow.md` 和 `api-docs.md` 放到 AI Web Tester 项目的 `specs/` 目录下
2. 将 `flow_N_xxx.yaml` 文件放到 `test_scripts/` 目录下
3. 通过 Web UI 发起测试：
   - **探索式测试**：选择 `core-flow.md` 作为功能预期文档，AI Agent 自主探索
   - **脚本化测试**：选择模式为"脚本化测试"，勾选要执行的 YAML 脚本，精确回归验证
