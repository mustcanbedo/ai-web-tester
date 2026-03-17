# AI Web Tester - Playwright + LLM 自动化测试系统

基于 Playwright 和大型语言模型（LLM）的自主探索式 Web 自动化测试工具。根据自然语言编写的"功能预期文档"，像人类测试工程师一样自主探索 Web 应用，发现功能缺陷并生成测试报告。

## 核心特性

- **预期驱动**：测试输入是自然语言的功能预期文档（`specs/core-flow.md`），描述系统应如何工作，无需编写脚本。
- **自主探索**：Agent 根据预期文档和当前页面的 Accessibility Tree 状态，自主决策下一步操作。
- **智能判断**：实时监听 Console 错误和 Network 异常，结合页面状态判断是否为 Bug。
- **视频录制**：Playwright 原生录制完整测试过程，事后可回看。
- **实时截图**：每步操作自动截图并推送到前端 LIVE 面板，实时观察 AI 正在做什么。
- **断点续测**：每步自动保存快照（含 LLM 对话历史、浏览器状态），异常中断后可恢复继续。
- **登录态复用**：自动保存/恢复 session cookies，跨测试会话保持登录。
- **人工介入**：Agent 可通过 `request_human_input` 请求人工输入（如短信验证码），前端弹窗交互。
- **自动报告**：测试结束后自动生成 Markdown 格式测试报告。
- **SSRF 防护**：`call_api` 操作限制仅允许访问目标网站同域的 API。
- **智能终止**：检测连续相同操作/连续失败/连续 wait，自动终止卡死的测试。

## 架构

```
┌──────────────┐     SSE (带 event ID)     ┌───────────────────┐   OpenAI API   ┌──────┐
│  index.html  │ ◄───────────────────────► │   app.py (FastAPI) │ ◄────────────► │  LLM │
│  (前端 SPA)  │        REST API           │   路由 + 任务管理  │               └──────┘
└──────────────┘                           └────────┬──────────┘
                                                    │
                                    ┌───────────────┼───────────────┐
                                    ▼               ▼               ▼
                              test_runner.py   llm_engine.py   action_executor.py
                              (测试循环)       (LLM 引擎)      (操作执行)
                                    │
                                    ▼
                             playwright_bridge.py ──► Playwright (Chromium)
                             (浏览器桥接层)              │
                                                        ▼
                                                   目标 Web 应用
```

## 目录结构

```
/ai-web-tester
├── app.py                # FastAPI 路由 + 任务管理（精简版，~360行）
├── config.py             # 全局配置（常量、目录、阈值）
├── llm_engine.py         # LLM 引擎 + System Prompt
├── action_executor.py    # 浏览器操作执行器（含 SSRF 防护）
├── test_runner.py        # 测试循环（run / resume 共用）
├── evidence.py           # 证据采集器（Console/Network 错误）
├── playwright_bridge.py  # Playwright 浏览器桥接层（CDP + Accessibility Tree）
├── index.html            # Web 面板前端（单页应用）
├── prompt-generate-docs.md # 提示词模板：用 AI 生成测试文档
├── specs/
│   └── core-flow.md      # 功能预期文档（测试输入）
├── reports/              # Markdown 测试报告
├── screenshots/          # 每步截图 + 错误快照
├── videos/               # Playwright 视频录制
├── logs/                 # JSON 执行日志
├── snapshots/            # 断点续测快照
├── requirements.txt      # Python 依赖
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt

# 安装 Playwright 浏览器驱动（仅首次需要）
playwright install chromium
```

### 2. 启动服务

```bash
python3 app.py
```

打开浏览器访问 **http://localhost:8080**，在面板中：

1. 配置 LLM（API Key、Base URL、模型名称），点击"测试连接"验证
2. 输入目标网站 URL
3. 上传或编辑功能预期文档
4. 配置登录信息（如需要，支持账号密码/手机验证码两种方式）
5. 点击"开始测试"

所有配置通过 UI 完成，无需设置环境变量。

### 3. 查看结果

- **右侧 LIVE 面板** — 实时查看 AI 当前看到的页面
- **执行日志** — 每步操作的详细记录
- **reports/** — Markdown 测试报告
- **screenshots/** — 每步截图和错误快照
- **videos/** — 完整测试过程视频录制
- **logs/** — JSON 格式完整执行日志

### 4. 生成测试文档

使用 `prompt-generate-docs.md` 中的提示词，在 AI IDE（Cursor/Windsurf）中打开你的前端项目，让 AI 自动生成 `core-flow.md` 和 `api-docs.md` 两份测试文档。

## 如何定制

- **测试你自己的网站**：参考 `prompt-generate-docs.md`，用 AI 自动生成测试文档，或手动编写 `specs/core-flow.md`。
- **修改 LLM 提示**：编辑 `llm_engine.py` 中的 `SYSTEM_PROMPT` 变量。
- **调整 LLM 模型**：Web 面板中直接配置，或修改 `config.py` 中的 `LLM_MODEL` 默认值。
- **增加浏览器操作**：在 `action_executor.py` 的 `ActionExecutor` 类中添加新操作，并在 `llm_engine.py` 的 `SYSTEM_PROMPT` 中告知 Agent。
- **调整阈值**：`config.py` 中可配置最大步数（`MAX_STEPS`）、最小步数（`MIN_STEPS_BEFORE_FINISH`）、卡死检测阈值（`STUCK_THRESHOLD`）等。

## 模块说明

| 模块 | 职责 |
|------|------|
| `app.py` | FastAPI 路由、SSE 事件流（带 event ID 断连重连）、任务生命周期管理、过期任务自动清理 |
| `config.py` | 常量配置（目录路径、阈值、默认模型等） |
| `llm_engine.py` | LLM 对话管理、System Prompt、健壮 JSON 解析（5 层修复）、消息历史裁剪、流程切换上下文重置 |
| `action_executor.py` | 浏览器操作执行（16 种操作类型）、SSRF 防护、截图、数据验证记录 |
| `test_runner.py` | 公共测试循环（run/resume 共用）、证据采集、智能终止检测、快照保存、视频保存、报告生成 |
| `evidence.py` | JS 注入采集 Console 错误和 Network 异常 |
| `playwright_bridge.py` | Playwright 封装：Accessibility Tree 快照、CDP 精确定位、click fallback 链（normal→force→JS）、视频录制 |

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/test/start` | 启动新测试 |
| POST | `/api/test/{id}/pause` | 暂停测试 |
| POST | `/api/test/{id}/resume` | 恢复暂停的测试 |
| POST | `/api/test/{id}/cancel` | 终止测试 |
| POST | `/api/test/{id}/human-input` | 提交人工输入（如验证码） |
| POST | `/api/test/{id}/resume-snapshot` | 从快照断点续测 |
| GET | `/api/test/{id}/stream` | SSE 事件流（支持 Last-Event-ID 重连） |
| GET | `/api/test/{id}/status` | 查询任务状态 |
| GET | `/api/report/{filename}` | 获取测试报告 |
| GET | `/api/screenshot/{filename}` | 获取截图 |
| GET | `/api/video/{filename}` | 获取录制视频 |
| GET | `/api/snapshots` | 列出可用快照 |
| GET | `/api/specs/default` | 获取默认功能预期文档 |
| POST | `/api/llm/test` | 测试 LLM API 连通性 |
