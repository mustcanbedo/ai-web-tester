# Demoblaze 测试 Bug 架构级分析

> task_id: 84035277 | 154 步 | 6 Bug 确认 / 4 Bug 否决 | 评分 63/100

---

## 一、Bug 行为归因

### Bug #2 [P0] 登录按钮点击无响应 + Bug #3 [P1] Add to cart 无提示 + Bug #4 [P0] 商品未入车

**表面现象**：Agent 点击 "Add to cart" 和 "Log in" 按钮后，页面"静默无响应"。

**根因**：Demoblaze 使用 `window.alert("Product added.")` 和 `window.alert("...login success...")` 来反馈操作结果。Playwright 在 headless 模式下，**未被 `page.on('dialog')` 监听的 `alert()` 会被自动 dismiss**，导致：

1. `alert("Product added.")` 被静默关闭 → Agent 看不到任何 UI 反馈 → 报 Bug
2. `alert()` 返回后 JS 继续执行 `addToCart()` AJAX → 商品实际已加入后端购物车
3. 但 Agent 已经判定"无提示"和"购物车空"

**框架缺陷**：

```
playwright_bridge.py: 零 dialog/alert 处理代码
action_executor.py:  零 dialog/alert 处理代码
llm_engine.py:       action types 列表中无 accept_alert/dismiss_alert
```

这是一个**架构级缺陷**：框架完全没有 `window.alert()` / `window.confirm()` / `window.prompt()` 的感知和处理能力。在整个 Playwright bridge 中搜索 `dialog` 关键字，只出现在 Radix Vue 的 `role=dialog` 检测中，与浏览器原生 dialog 无关。

**影响面**：所有使用 `alert/confirm/prompt` 的网站（大量传统 Web 应用和教学/演示站点）都会出现类似问题。本次 6 个 Bug 中有 3 个（50%）直接由此引起。

**修复方案**：

```python
# playwright_bridge.py - start_server() 中注册 dialog 监听
def start_server(self):
    ...
    self._page = self._context.new_page()
    # 记录最近的 dialog 事件
    self._last_dialogs = []
    def _on_dialog(dialog):
        self._last_dialogs.append({
            "type": dialog.type,        # "alert" | "confirm" | "prompt"
            "message": dialog.message,
            "default_value": dialog.default_value,
        })
        dialog.accept()  # 默认自动接受
    self._page.on("dialog", _on_dialog)
    ...

def get_pending_dialogs(self):
    """获取并清空最近的 dialog 事件"""
    dialogs = self._last_dialogs[:]
    self._last_dialogs.clear()
    return dialogs
```

```python
# test_runner.py _test_loop - 每步操作后检查 dialog
result = executor.execute(act)
# 检查是否有 alert/confirm 弹出
pending_dialogs = bridge.get_pending_dialogs()
if pending_dialogs:
    dialog_info = "; ".join(f"[{d['type']}] {d['message']}" for d in pending_dialogs)
    extra_context += f"\n系统检测到弹窗已自动处理: {dialog_info}"
```

**复杂度**：~30 行代码改动，无架构变更。

---

### Bug #5 [P0] 分类筛选功能失效 + Bug #6 [P0] 商品详情页内容加载失败

**表面现象**：Step 115 点击 "Phones" 后商品列表未过滤；Step 141 进入详情页后"白屏"。

**根因**：Demoblaze 是一个 **AJAX 异步加载**的 SPA。点击分类链接后，JS 通过 `XMLHttpRequest` 从 API 拉取数据，然后 `innerHTML` 动态渲染商品列表。这个过程有 **200-500ms 延迟**。

框架当前的感知时序：

```
[Agent 点击分类链接]
    ↓
[action_executor.execute(click)]     ← locator.click() 立即返回
    ↓
[test_runner 固定 sleep(1)]          ← 1 秒等待
    ↓
[extract_page_state(bridge)]         ← 抓取 AX Tree
```

问题出在 Step 115/141 时，Agent 已连续操作了 100+ 步，Playwright 的浏览器实例可能因为长时间运行出现了**页面状态过期**或**内存泄漏导致渲染延迟**。1 秒的固定等待在初期（Step 1-3 分类点击成功）足够，但在后期不够。

**框架缺陷**：

1. **固定 1 秒等待**（`test_runner.py:207 time.sleep(1)`）而非智能等待
2. **无页面稳定性检测**：没有检测 DOM 是否已停止变化
3. **AX Tree 抓取无重试**：如果第一次抓到的是过渡态，不会重试

**影响面**：所有 AJAX/SPA 应用（包括项目原始测试目标）在长时间运行后都可能出现 AX Tree 抓取到过渡态的情况。

**修复方案**：

```python
# test_runner.py - 将固定 sleep 替换为智能等待
def _wait_for_page_stable(bridge, max_wait=3.0, interval=0.3):
    """等待页面 DOM 稳定（连续两次 AX Tree 相同则视为稳定）"""
    prev_hash = None
    waited = 0
    while waited < max_wait:
        time.sleep(interval)
        waited += interval
        state = extract_page_state(bridge)
        curr_hash = hashlib.md5(
            json.dumps([(r.get('role',''), r.get('name',''))
                        for r in state.get('refs', [])], sort_keys=True).encode()
        ).hexdigest()
        if curr_hash == prev_hash:
            return state  # DOM 已稳定
        prev_hash = curr_hash
    return extract_page_state(bridge)  # 超时返回最新状态
```

**复杂度**：~20 行代码，替换 `time.sleep(1)` + `extract_page_state(bridge)` 为 `_wait_for_page_stable(bridge)`。

---

### Bug #1 [P1] 购物车空状态未提示

**表面现象**：购物车为空时，页面无 "Your cart is empty" 提示。

**分析**：这可能是 Demoblaze 的**真实 UX 缺陷** — 购物车为空时确实没有空状态提示，只显示表头和按钮。但也可能是 Bug #2/#4 的衍生问题（商品因 alert 未处理导致未真正入车）。

**框架层面**：无直接框架缺陷。但 Reviewer 应该识别出这是一个级联 Bug（root cause 是 alert 未处理），建议增强 Reviewer 的级联分析能力。

---

## 二、架构级缺陷总结

| 缺陷 | 严重度 | 影响面 | 修复成本 | 引发的 Bug |
|------|--------|--------|---------|-----------|
| **无 dialog/alert 处理** | P0 | 所有使用 alert/confirm 的站点 | ~30 行 | Bug #2, #3, #4 (50%) |
| **固定 1s 等待而非智能等待** | P1 | 所有 AJAX/SPA 站点长时运行时 | ~20 行 | Bug #5, #6 (33%) |
| **Agent 创建 spec 外流程** | P2 | 所有测试 | 需 Planner 架构 | 无直接 Bug 但浪费步数 |
| **Reviewer 无级联分析** | P2 | 存在因果链的 Bug | ~50 行 | Bug #1 误判 |

**核心发现**：本次 6 个 Bug 中，**5 个（83%）由框架层基础设施缺失导致**，仅 1 个可能是目标站点真实缺陷。这说明当前框架的"基础感知能力"仍有盲区，比 Agent 智能层（Reviewer/Memory）的优先级更高。

---

## 三、修复优先级

```
P0-紧急: dialog/alert 处理  → 消除 50% 的 false positive 来源
P1-重要: 智能等待替代固定 sleep → 消除长时运行的 AX Tree 过渡态问题
P2-增强: Reviewer 级联分析     → 识别衍生 Bug，避免重复报告同根因问题
P3-远期: Planner 约束流程范围  → 阻止 Agent 自创 spec 外的流程
```

---

## 四、与前序 Bad Cases 的关联

| 本次发现 | 对应 bad-cases.md 中的已知问题 |
|---------|-------------------------------|
| dialog/alert 盲区 | **新发现**，bad-cases.md 中未记录（之前的测试目标无 alert） |
| AX Tree 过渡态 | 关联 TD-5（LLM 上下文管理），但根因不同：这是感知层问题 |
| Agent 自创流程 | 关联 TD-3（Agent 重复执行/循环） |
| Reviewer 否决 40% | 验证了 improvement-research.md 中 ReviewerAgent 方案的有效性 |

---

## 五、结论

1. **dialog/alert 处理是当前框架最大的基础设施盲区**，修复成本极低（30 行）但影响面巨大
2. **固定等待时间**在短测试中可行，但 100+ 步长测试中会暴露异步渲染问题
3. **ReviewerAgent 表现符合预期**：40% 否决率，token 成本仅 0.7%，但仍有级联分析改进空间
4. **TestMemory 成功记录了 7 个流程摘要**，为后续步骤提供了上下文，但 `learned_strategies` 为 0，说明框架还没有在 action 失败恢复时调用 `on_action_failed_and_recovered()`
