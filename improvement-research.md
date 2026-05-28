# AI Web Tester 改进方向深度研究

> 研究时间：2026-05-25
> 基于：项目全部源码分析 + 业界 2025-2026 最新 Agent 技术调研

---

## 一、现状总结

### 当前架构定位

本项目是一个**单 Agent + ReAct 循环**架构的 Web 自动化测试工具：

```
单一 LLM Agent（感知 + 规划 + 执行 + 判断 全部耦合在一个 decide() 调用中）
     │
     ├── 感知：AX Tree snapshot → refs 列表
     ├── 规划：thinking 字段 + next_plan（隐式，单步规划）
     ├── 执行：20+ action types 通过 ActionExecutor
     ├── 判断：found_issues + flow_status（自评，无独立审核）
     └── 记忆：action_history + flow_progress（短期，随上下文裁剪丢失）
```

### 已有的工程亮点

| 能力 | 实现方式 | 业界对标 |
|------|---------|---------|
| 非标准元素检测 | Vue `_vei` / React `__reactProps$` + cursor:pointer | 优于 browser-use 的纯 DOM 遍历 |
| 空名按钮位序推断 | `[按钮N/M @ 卡片名]` | 独创方案 |
| 多级卡死检测 | 操作模式 + 导航循环 + State Hash | 优于 browser-use 的 `max_failures` |
| 双模式测试 | 探索式 + 脚本化（YAML） | 业界少见的混合模式 |
| 证据采集 | Console + Network + API 拦截 | 测试领域独特优势 |
| 断点续测 | 快照（LLM 对话 + 浏览器状态 + 流程进度） | 工程成熟度高 |

### 核心痛点（从 bad-cases 和 architecture-and-blockers 提炼）

1. **69% 误报率**：主要来自 Popover 点击失效（已修复）和 AX Tree 盲区
2. **上下文遗忘**：100+ 步后裁剪丢信息，Agent 重复操作
3. **单 Agent 过载**：一个 LLM 同时承担规划、执行、判断，职责混乱
4. **无自我审视**：Agent 自己报 Bug、自己判 pass，没有独立的 Reviewer
5. **Token 效率低**：全文注入 spec + 完整 AX Tree，大量无关信息

---

## 二、业界 2025-2026 六大 Agent 设计模式

基于 SitePoint 2026 Agentic Design Patterns、Anthropic Harness Design、Mem0 State of Memory 2026 等研究整理：

### 模式 1：Reflection（自我反思循环）

**核心思想**：生成 → 评估 → 修正，Agent 成为自己的 Reviewer。

**业界实践**：
- 通常 2-3 轮迭代即可产出合格结果
- 适用于代码生成、长文写作、结构化数据提取

**对本项目的适用性**：⭐⭐⭐⭐⭐
- Bug 报告质量可通过 Reflection 提升（当前 69% 误报率）
- 流程完成度判断可引入自评循环
- **最小实现**：每个流程结束时，让 LLM 回顾该流程的操作历史，自评"是否充分测试了所有子节"

### 模式 2：Plan-and-Execute（规划与执行分离）

**核心思想**：先生成完整计划，再逐步执行，失败时触发重新规划。

**业界实践**：
- **Plan-and-Execute** 适合定义明确的多步任务（如本项目的测试流程）
- **ReAct** 适合探索性任务（如自由探索未知网站）
- 两者可混合：Plan 框架内嵌 ReAct 执行

**对本项目的适用性**：⭐⭐⭐⭐⭐
- `core-flow.md` 本身就是"计划"，但当前是全文丢给 Agent 让它自己理解
- **改进方向**：测试开始前，用 Planner Agent 将 spec 拆解为结构化任务 DAG，Executor 按 DAG 逐节点执行
- 脚本化模式（`script_runner.py`）本质上就是 Plan-and-Execute 的手动版

### 模式 3：Multi-Agent Collaboration（多 Agent 协作）

**三种拓扑**：
1. **Peer-to-Peer**：Agent 共享状态、协作产出（适合协同写作）
2. **Hierarchical**：Manager 分配任务给 Worker（适合任务分解）
3. **Adversarial Debate**：Agent 互相对抗、压力测试推理（适合发现盲点）

**Anthropic 的三 Agent 架构**（Harness Design for Long-Running Apps）：
- **Planner**：将简短描述展开为完整 spec
- **Generator**：按 sprint 逐个实现，自评后交给 QA
- **Evaluator**：用 Playwright MCP 实际点击测试，对每个 sprint 打分

**对本项目的适用性**：⭐⭐⭐⭐⭐
- Anthropic 的三 Agent 架构几乎完美对标本项目的需求
- 详见下文"改进方案一"

### 模式 4：Orchestrator-Worker（动态任务分解）

**核心思想**：与静态 Plan 不同，Orchestrator 根据实时情况动态派发子任务。

**对本项目的适用性**：⭐⭐⭐
- 当某个流程失败时，Orchestrator 可以动态决定"跳过"还是"换策略重试"
- 当前的卡死检测 + 跳流程逻辑就是手写的简化版 Orchestrator

### 模式 5：Evaluator-Optimizer（测试驱动的 Agent 开发）

**核心思想**：将"做事"和"评判"分离，Evaluator 用 rubric 打分，Optimizer 根据反馈调整策略。

**对本项目的适用性**：⭐⭐⭐⭐
- 当前 `eval_engine.py` 已有轻量评估（84/100 on TodoMVC），但只在测试结束后评估
- **改进方向**：将评估嵌入循环中——每完成一个流程就评估一次，分数低则 Optimizer 调整后续策略

### 模式 6：Memory Systems（记忆系统）

**Mem0 2026 的三层记忆模型**：
1. **Episodic Memory**（情节记忆）：发生了什么 → 操作历史
2. **Semantic Memory**（语义记忆）：已知什么 → 流程状态、发现的 Bug
3. **Procedural Memory**（过程记忆）：应该怎么做 → 测试策略、元素定位模式

**Multi-Scope Memory**（四级作用域）：
- `user_id`：跨会话持久（如"该网站的 Popover 需要 el.click() 降级"）
- `agent_id`：单个 Agent 实例
- `session_id`：单次测试会话
- `app_id`：组织级共享

**Graph Memory**（实体关联记忆）：
- 不只是向量相似度检索，而是实体+关系感知
- 例如：记住"项目卡片的更多按钮" → "Popover trigger" → "需要 el.click()" 这条关系链

**对本项目的适用性**：⭐⭐⭐⭐⭐
- 当前最大痛点之一就是上下文遗忘
- 详见下文"改进方案二"

---

## 三、具体改进方案

### 方案一：三 Agent 架构重构（ROI 最高）

参考 Anthropic 的 Planner-Generator-Evaluator 模式，重构为：

```
                    ┌─────────────────────────┐
                    │    Planner Agent         │
                    │ （测试规划师）              │
                    │                         │
                    │ 输入：core-flow.md       │
                    │ 输出：结构化测试计划       │
                    │   {flows: [{             │
                    │     name, preconditions, │
                    │     steps: [{action,     │
                    │       verify, priority}] │
                    │   }]}                   │
                    └────────┬────────────────┘
                             │ 测试计划 (JSON)
                             ▼
                    ┌─────────────────────────┐
                    │    Executor Agent        │
                    │ （测试执行者）              │
                    │                         │
                    │ 输入：当前步骤 + AX Tree  │
                    │ 输出：action (click/fill) │
                    │                         │
                    │ 职责：只关注"怎么做"       │
                    │ 不判断 Bug，不决定流程     │
                    └────────┬────────────────┘
                             │ 执行结果 + 截图
                             ▼
                    ┌─────────────────────────┐
                    │    Reviewer Agent        │
                    │ （测试审查员）              │
                    │                         │
                    │ 输入：预期结果 + 实际状态  │
                    │ 输出：pass/fail + Bug     │
                    │                         │
                    │ 职责：只关注"对不对"       │
                    │ 独立判断，消除自评偏差     │
                    └─────────────────────────┘
```

**核心收益**：

| 维度 | 当前（单 Agent） | 改进后（三 Agent） |
|------|-----------------|-------------------|
| 上下文窗口 | 1 个 Agent 承载全部信息（spec + AX Tree + 历史 + 证据） | 每个 Agent 只看自己需要的信息 |
| 误报率 | 69%（自己执行自己判断） | 预计 <30%（独立 Reviewer，类似代码 CR） |
| 规划质量 | 隐式（thinking 字段） | 显式结构化计划，可检查 |
| Token 效率 | 每步都传完整 spec + 完整上下文 | Executor 只看当前步骤，Reviewer 只看预期 vs 实际 |

**实现路径**：

```python
# planner_agent.py（新文件）
class PlannerAgent:
    """将 core-flow.md 解析为结构化测试计划"""

    def plan(self, spec_content: str) -> dict:
        """
        返回结构化计划：
        {
            "flows": [
                {
                    "name": "流程一：用户登录",
                    "preconditions": ["未登录状态"],
                    "steps": [
                        {
                            "id": "1.1",
                            "description": "导航到登录页",
                            "action_hint": "navigate",
                            "verify": "URL 包含 /login",
                            "priority": "required"
                        },
                        ...
                    ]
                }
            ]
        }
        """
        # 用 LLM 将自然语言 spec 转为结构化 JSON
        # 这只需调用一次 LLM，后续 Executor 和 Reviewer 不再需要读原始 spec
        pass


# reviewer_agent.py（新文件）
class ReviewerAgent:
    """独立于 Executor 的测试审查员"""

    def review_step(self, expected: str, actual_state: dict,
                    evidence: dict, screenshot_b64: str = None) -> dict:
        """
        输入：预期结果 + 实际页面状态 + 证据
        输出：{"passed": bool, "is_bug": bool, "severity": "P0/P1/P2",
               "reason": "...", "confidence": "high/medium/low"}
        """
        # 独立 LLM 调用，短上下文，高准确率
        # 可选：传入截图让多模态模型辅助判断
        pass

    def review_flow(self, flow_plan: dict, step_results: list) -> dict:
        """流程级审查：对照计划，判断整个流程是否通过"""
        pass
```

**关键设计决策**：

1. **Agent 间通信用文件/结构化数据**，不共享 LLM 对话历史（参考 Anthropic 的做法："Communication was handled via files"）
2. **Planner 只运行一次**（测试开始前），不参与执行循环
3. **Reviewer 每步运行**（轻量调用，200 token 级别），类似 `script_runner.py` 的 `AIVerifier`
4. **Executor 的 prompt 大幅精简**：不需要描述 Bug 判断规则、finish 条件，只关注"如何操作页面"

**渐进式迁移路径（不需要一次性重写）**：

- **Phase 1**：先独立出 ReviewerAgent（最小改动，最大收益）
  - 在 `_test_loop` 每步执行后，调用 ReviewerAgent 独立判断
  - 对比 Executor 自报的 `found_issues` 和 Reviewer 的判断，取交集
  - 预计减少 50%+ 误报

- **Phase 2**：独立出 PlannerAgent
  - 测试前将 spec 转为结构化计划
  - `_test_loop` 按计划逐步执行，不再依赖 Agent 自己判断"下一步做什么流程"
  - Executor 的 system prompt 从 126 行精简到 ~40 行

- **Phase 3**：完全分离 Executor
  - Executor 只输出 action，不输出 flow_status / found_issues / should_continue
  - 这些全部由 Reviewer + Orchestrator 判断

---

### 方案二：结构化记忆系统

参考 Mem0 2026 的三层记忆模型，为项目设计轻量记忆层：

```python
# memory.py（新文件）
class TestMemory:
    """测试过程中的结构化记忆，不随 LLM 上下文裁剪而丢失"""

    def __init__(self):
        # Episodic Memory（情节记忆）— 发生了什么
        self.flow_summaries = {}  # {flow_name: "流程摘要"}
        self.key_events = []     # 关键事件时间线

        # Semantic Memory（语义记忆）— 已知什么
        self.confirmed_bugs = []   # 已确认的 Bug
        self.page_patterns = {}    # {url_pattern: "页面特征描述"}
        self.element_cache = {}    # {描述: ref 定位策略}

        # Procedural Memory（过程记忆）— 应该怎么做
        self.learned_strategies = []  # 学到的操作策略
        # 例如："该网站的 Popover 需要 el.click() 而非 locator.click()"
        # 例如："搜索框有 300ms 防抖，fill 后需要 wait 500ms"

    def on_flow_complete(self, flow_name: str, steps: list, result: str):
        """流程完成时，LLM 生成摘要存入情节记忆"""
        summary = self._summarize_flow(flow_name, steps, result)
        self.flow_summaries[flow_name] = summary

    def on_action_failed(self, action: dict, error: str, recovery: str):
        """操作失败并恢复时，记录过程知识"""
        self.learned_strategies.append({
            "trigger": f"{action['type']} 失败: {error}",
            "strategy": recovery,
            "confidence": "high"
        })

    def get_context_for_step(self, current_flow: str, max_tokens: int = 500) -> str:
        """生成当前步骤需要的精简记忆上下文"""
        parts = []

        # 已完成流程的摘要（不是完整历史）
        if self.flow_summaries:
            parts.append("## 已完成流程摘要")
            for name, summary in self.flow_summaries.items():
                parts.append(f"- {name}: {summary}")

        # 相关的过程知识
        if self.learned_strategies:
            parts.append("## 已知策略（避免重复踩坑）")
            for s in self.learned_strategies[-5:]:
                parts.append(f"- {s['trigger']} → {s['strategy']}")

        # 已确认的 Bug（避免重复报告）
        if self.confirmed_bugs:
            parts.append("## 已确认 Bug（不要重复报告）")
            for bug in self.confirmed_bugs:
                parts.append(f"- [{bug['severity']}] {bug['title']}")

        return "\n".join(parts)
```

**与当前实现的对比**：

| 维度 | 当前 | 改进后 |
|------|------|--------|
| 流程历史 | `flow_progress` 每步重建（只有名称列表） | 每个流程有 LLM 摘要，保留关键发现 |
| 操作策略 | 无记忆，每次从零推理 | 失败恢复后学到的策略持久化 |
| Bug 记录 | `all_issues` 列表（可能被裁剪上下文丢失） | 独立存储，永不丢失 |
| 上下文注入 | 每步注入完整 flow_progress（固定格式） | 按需检索相关记忆，控制 token |

**跨会话持久化**（远期）：

```python
# 测试结束后，将记忆序列化到 JSON
memory.save("memory_cache/{target_domain}.json")

# 下次测试同一网站时，加载历史记忆
memory.load("memory_cache/{target_domain}.json")
# Agent 直接知道："上次测试发现这个网站的 Popover 需要 el.click() 降级"
```

---

### 方案三：Reflection 自评循环

在不改变整体架构的前提下，最小化引入 Reflection 模式：

**3.1 流程级 Reflection**

每完成一个流程，触发一次自评：

```python
# 在 _test_loop 的流程切换检测处添加
if flow != current_flow_name:  # 流程切换
    # 触发 Reflection：回顾刚完成的流程
    reflection = llm.reflect_on_flow(
        flow_name=current_flow_name,
        flow_plan=plan.get_flow(current_flow_name),  # 结构化计划
        action_history=[a for a in action_history if a.get("flow") == current_flow_name],
        all_issues=[i for i in all_issues if i.get("flow") == current_flow_name],
    )
    # reflection = {
    #   "coverage": "8/10 子节已测试",
    #   "missed_items": ["3.7 下载验证未执行"],
    #   "confidence": "medium",
    #   "recommendation": "建议补测 3.7",
    #   "false_positive_suspects": ["3.2 报告的'页面空白'可能是 Canvas 页面"]
    # }
    if reflection["missed_items"]:
        extra_context += f"自评发现遗漏：{reflection['missed_items']}，请补测"
    if reflection["false_positive_suspects"]:
        # 从 all_issues 中移除疑似误报
        for suspect in reflection["false_positive_suspects"]:
            all_issues = [i for i in all_issues if suspect not in i.get("title", "")]
```

**3.2 Bug 报告 Reflection**

每次 Agent 报告 Bug 前，先让独立的 Reviewer（或同一 LLM 的不同 prompt）审查：

```python
# 在 issues 处理逻辑中添加
for issue in issues:
    # 先审查再采纳
    review = reviewer.verify_bug(
        issue=issue,
        page_state=page_state,
        evidence=new_evidence,
        known_false_positives=[  # 已知的误报模式
            "Canvas/WebGL 页面 AX Tree 稀疏不是 Bug",
            "手机号框显示 +86 是正常的",
            "新标签页下载不等于无响应",
        ]
    )
    if review["is_valid_bug"]:
        all_issues.append(issue)
    else:
        emit("log", {"message": f"🔍 Reviewer 否决了疑似误报: {issue['title']} | 原因: {review['reason']}"})
```

---

### 方案四：Vision 多模态增强

当前使用 `qwen-vl-max`（多模态模型）但只用了文本能力。截图已有但未喂 LLM。

**4.1 按需 Vision（参考 browser-use 的 `use_vision="auto"`）**

不是每步都传截图（太贵），而是在特定条件下触发：

```python
# 触发 Vision 的条件：
should_use_vision = (
    # 条件 1：AX Tree 元素过少（可能是 Canvas/WebGL 页面）
    len(page_state["refs"]) < 5
    # 条件 2：Agent 连续 2 步报告"页面空白"或"元素不存在"
    or _recent_complaints(action_history, "空白|不存在|找不到", last_n=2)
    # 条件 3：Popover 点击后需要验证是否打开（AX Tree 可能有延迟）
    or (last_action_type == "click" and is_popover_trigger)
    # 条件 4：Reviewer 对某个判断 confidence=low，需要视觉辅助
    or (review_result and review_result["confidence"] == "low")
)

if should_use_vision:
    screenshot_b64 = executor.take_screenshot(step, "vision")["base64"]
    # 将截图作为 vision input 追加到 LLM 调用
    messages.append({
        "role": "user",
        "content": [
            {"type": "text", "text": obs},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64}"}}
        ]
    })
```

**4.2 Vision 的具体价值场景**

| 场景 | AX Tree 能力 | Vision 补充 |
|------|-------------|------------|
| WebGL/Canvas 3D 页面 | 几乎为空 | 能看到 3D 渲染结果 |
| Popover 是否真的打开 | 需要等 DOM 更新 | 直接看到菜单是否可见 |
| 页面布局/样式问题 | 完全无法感知 | 能发现 CSS 错位、颜色异常 |
| 图标按钮的含义 | 需要推断 | 直接"看"图标 |
| Toast/Snackbar 提示 | 可能消失太快 | 截图能捕获 |

---

### 方案五：Plan-and-Execute 混合模式

将当前的纯 ReAct 循环升级为 Plan → Execute → Replan 模式：

```
┌──────────────────────────────────────────────────────┐
│                  Orchestrator                         │
│                                                      │
│  1. Planner 将 spec → 结构化计划                      │
│  2. 按计划逐流程执行                                   │
│  3. 每个流程内用 ReAct 循环（Executor + Reviewer）      │
│  4. 流程失败 → 触发 Replanner 调整后续计划              │
│  5. 所有流程完成 → Aggregator 汇总报告                  │
│                                                      │
│  ┌──────┐   ┌──────────┐   ┌──────────┐             │
│  │Planner│──►│ Executor │──►│ Reviewer │             │
│  └──────┘   │ (ReAct)  │   └────┬─────┘             │
│      ▲      └──────────┘        │                    │
│      │                          │ fail               │
│      │      ┌──────────┐        │                    │
│      └──────│Replanner │◄───────┘                    │
│             └──────────┘                             │
└──────────────────────────────────────────────────────┘
```

**与当前架构的关键区别**：

1. **当前**：Agent 自己决定"下一步测哪个流程" → 经常跳跃、遗漏
2. **改进后**：Orchestrator 按计划分配流程，Agent 只关注当前流程内的操作

**Replanner 触发条件**：
- 某个流程失败（如前置条件不满足）
- 发现计划中的假设不成立（如某个页面结构与 spec 描述不符）
- 已消耗 70% 步数但覆盖率不足 50%

---

### 方案六：成本控制与可观测性

**6.1 Token Budget 管理**

```python
# config.py 新增
TOKEN_BUDGET = 500_000  # 单次测试 token 预算
TOKEN_WARNING_THRESHOLD = 0.8  # 80% 时警告

# _test_loop 中检查
total_tokens = llm.total_input_tokens + llm.total_output_tokens
if total_tokens > TOKEN_BUDGET * TOKEN_WARNING_THRESHOLD:
    extra_context += "⚠️ Token 预算即将耗尽，请加速完成剩余流程"
if total_tokens > TOKEN_BUDGET:
    emit("log", {"message": f"Token 预算耗尽 ({total_tokens:,} / {TOKEN_BUDGET:,})，强制终止"})
    break
```

**6.2 LLM 调用追踪（Observability）**

```python
# 每次 LLM 调用记录详细 trace
trace = {
    "step": step,
    "agent": "executor",  # executor / reviewer / planner
    "input_tokens": response.usage.prompt_tokens,
    "output_tokens": response.usage.completion_tokens,
    "latency_ms": (time.time() - start) * 1000,
    "model": self.model,
    "cache_hit": False,  # 未来支持语义缓存
}
```

**6.3 语义缓存（Semantic Caching）**

对重复的 LLM 调用做缓存，减少 API 费用：

- 相同的 AX Tree + 相同的步骤描述 → 直接返回缓存结果
- 特别适用于 Reviewer 的 verify 调用（相同页面状态 + 相同验证问题 → 结果不变）

---

## 四、实施优先级与路线图

### 按 ROI 排序

| 优先级 | 方案 | 工作量 | 预期收益 | 依赖 |
|--------|------|--------|---------|------|
| **P0** | **方案三 3.2：Bug 报告 Reflection** | 1-2 天 | 误报率 69% → ~35% | 无 |
| **P0** | **方案一 Phase 1：独立 ReviewerAgent** | 2-3 天 | 误报率 ~35% → ~15% | 无 |
| **P1** | **方案二：结构化记忆（Session 级）** | 2-3 天 | 消除上下文遗忘，减少重复操作 | 无 |
| **P1** | **方案三 3.1：流程级 Reflection** | 1-2 天 | 提升流程覆盖完整性 | 无 |
| **P2** | **方案四：按需 Vision** | 2-3 天 | 消除 Canvas/WebGL 误报 | qwen-vl-max 已支持 |
| **P2** | **方案一 Phase 2：PlannerAgent** | 3-5 天 | 结构化测试计划，减少 50% Token | 方案一 P1 |
| **P2** | **方案六：成本控制** | 1 天 | Token 预算管理 + 可观测性 | 无 |
| **P3** | **方案五：Plan-and-Execute** | 5-7 天 | 完整的 Orchestrator 架构 | 方案一+二 |
| **P3** | **方案二：跨会话记忆持久化** | 2-3 天 | 同网站重测时复用策略 | 方案二 Session 级 |

### 建议的实施顺序

```
Week 1: Bug Reflection + ReviewerAgent（解决 69% 误报这个最大痛点）
Week 2: 结构化记忆 + 流程 Reflection（解决遗忘和重复操作）
Week 3: 按需 Vision + Token 管理（增强感知 + 控制成本）
Week 4+: PlannerAgent + Plan-and-Execute（架构升级）
```

---

## 五、与业界项目的差异化定位

本项目在"AI 驱动的 Web 测试"这个细分赛道上，与通用 browser agent（browser-use / Stagehand）的核心差异：

| 维度 | browser-use | Stagehand | 本项目 |
|------|------------|-----------|--------|
| **定位** | 通用浏览器 Agent SDK | 浏览器自动化 SDK | **专注 Web 测试验收** |
| **输入** | 自然语言任务描述 | 代码 + NL 混合 | **功能预期文档 (spec)** |
| **输出** | 任务完成结果 | 提取的数据/操作结果 | **测试报告 + Bug 列表 + 证据** |
| **独特价值** | - | 缓存 + 自愈 | **证据链（Console+Network+截图+视频）** |
| **评估** | 无内置 | 无内置 | **eval_engine 多维评分** |
| **质量保证** | 用户自行判断 | 用户自行判断 | **独立 Reviewer（待实现）** |

**差异化方向建议**：

1. **"AI QA Engineer"** 而非 "Browser Agent" — 强调测试专业性（spec 驱动 + 证据链 + 独立审查）
2. **"测试即文档"** — spec → 结构化计划 → 测试报告 形成闭环
3. **"可信的测试结果"** — 通过 Reviewer + Reflection 将误报率降到业界最低

---

## 六、总结

当前项目已经建立了扎实的工程基础（ReAct 循环 + 20+ action types + 证据采集 + 双模式测试），但在 **Agent 智能层面** 还处于"单一 Agent 包揽一切"的阶段。

业界 2025-2026 的核心趋势是：

1. **职责分离**（Planner / Executor / Reviewer 不同角色）
2. **结构化记忆**（不依赖 LLM 上下文窗口）
3. **自我反思**（生成 → 评估 → 修正循环）
4. **多模态融合**（文本 + 视觉按需使用）

这四个方向恰好对应项目的四大痛点：误报率高、上下文遗忘、无独立审查、AX Tree 盲区。

**最小可行改进**：只需引入 ReviewerAgent（~200 行代码）+ Bug Reflection（~50 行代码），预计就能将误报率从 69% 降到 20% 以下，这是 ROI 最高的第一步。
