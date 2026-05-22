# AI Web Tester 技术决策与优化路线图

**作者**：Manus AI → 补充调研 by Cascade  
**日期**：2026-03-19（调研更新）

基于 `architecture-and-blockers.md`、`bad-cases.md`（19 份报告分析）、`core-flow.md`、实际代码审计以及 **Radix Vue 源码深度阅读**，对原路线图进行修正和补充。

---

## 0. 调研核心发现（纠正原路线图的关键错误）

### 🔴 发现一：Radix Vue Popover 的根因判断有误

**原路线图的说法**："Radix Vue 监听 `pointerdown` 事件而非 `click`"  
**实际情况（源码验证）**：

阅读 Radix Vue 仓库 `PopoverTrigger.vue` 源码，发现：

```vue
<!-- radix-vue/src/Popover/PopoverTrigger.vue -->
<Primitive
  @click="rootContext.onOpenToggle"   <!-- ✅ 用的是 @click，不是 @pointerdown -->
>
```

`onOpenToggle` 定义在 `PopoverRoot.vue` 中，就是简单的 `open.value = !open.value`。

**所以 PopoverTrigger 本身监听的就是标准 `click` 事件**，Playwright `locator.click()` 理论上完全能触发。

**真正的问题在于 `DismissableLayer`**：当 Popover 打开后，`DismissableLayer` 会在 `document` 上通过 `setTimeout(0)` 延迟注册一个 `pointerdown` 监听器来检测"外部点击"。核心机制如下：

```typescript
// radix-vue/src/DismissableLayer/utils.ts
// 1. PopoverContent 渲染时，DismissableLayer 挂载
// 2. DismissableLayer 在 document 上注册 pointerdown 监听
// 3. 如果 pointerdown 的 target 不在 DismissableLayer 内 → 触发 dismiss
// 4. 但有一个关键保护：onPointerDownCapture 会设置 isPointerInsideDOMTree = true
```

**这意味着 Playwright click 的问题可能是**：
1. **Popover 确实打开了，但随即被关闭** — `locator.click()` 触发 `click` → Popover 打开 → 但 Playwright 的 `pointerdown` 事件冒泡到 `document` → DismissableLayer 检测到"外部点击"（因为 `PopoverContent` 是 Portal 到 `body` 末尾，trigger 的 `pointerdown` 被判定为 outside）→ Popover 立即关闭
2. **这正好解释了 GitHub Issue #2288** 的描述："the element appears then disappears right away"

### 🔴 发现二：当前 JS 降级方案只在 `locator.click()` 抛异常时才触发

```python
# playwright_bridge.py 第614-638行 现有逻辑
def click(self, ref):
    locator = self._get_locator(ref)
    try:
        locator.click(timeout=5000)    # ← 这一步不会抛异常
    except Exception as e:
        # ← 只有抛异常才走降级链
        # 但 Popover "打开又关闭" 不会抛异常！
```

**问题**：`locator.click()` 技术上是成功的（元素被点击了），所以不会进入 `except` 分支。但 Popover 在 click 触发后瞬间被 DismissableLayer 的 `pointerdown` outside 检测关闭，最终效果是"没打开"。

### 🔴 发现三：原路线图的代码方案无法解决问题

原路线图建议：
```python
el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
```

这个方案有两个问题：
1. 它依赖 `selector` 参数，但当前架构用的是 `ref` → `locator`，不走 CSS selector
2. 即使事件触发成功打开了 Popover，`pointerdown` 冒泡到 `document` 后仍然会触发 DismissableLayer 的 outside 检测（因为 trigger 不在 PopoverContent 的 DOM 子树内）

### 🟢 实际验证结果（2026-03-19 运行 verify_popover.py）

**结论：DismissableLayer 假设被推翻，真正的根因是遮罩层。**

| 验证项 | 结果 | 关键发现 |
|--------|------|----------|
| 1. `aria-haspopup` | ✅ | 登录后 9 个元素：1 个 `menu`（语言切换）+ 8 个 `dialog`（项目 ··· 按钮） |
| 2. `el.click()` | ✅ | **纯 JS click 能打开 Popover 且保持 open**（`data-state: closed→open`） |
| 3. `locator.click()` | ✅ | **登录后 locator.click() 也能正常打开 Popover 且保持 open！** |
| 4. DismissableLayer | ✅ | 通过 Teleport 渲染到 `[data-radix-popper-content-wrapper]` 内，`isPointerInsideDOMTree` 保护机制正常 |
| 5. `stopPropagation` | ✅ | 方案可行但不需要（因为 locator.click 本身就能工作） |
| 6. `el.click()` 降级 | ✅ | 可作为遮罩层场景的可靠降级方案 |

**真正的根因**：
- 未登录时，页面有 `div.fixed.inset-0.z-9999` 登录弹窗遮罩层，拦截了所有 pointer events
- `locator.click()` 因遮罩层超时报错："element intercepts pointer events"
- 之前的测试报告中，Agent cookies 失效后遇到遮罩层，误判为"Popover 点击失效"

**已实施的修复方案**：
1. `playwright_bridge.py` click 方法：`locator.click()` → 捕获异常 → 降级 `el.click()`（绕过 hit-testing）
2. 对 `aria-haspopup` 元素：点击后验证 `data-state`，如果未变为 `open` 则用 `el.click()` 重试
3. `llm_engine.py` system prompt：新增硬性规则"遮罩层/弹窗优先处理"

---

## 1. 核心技术决策

**结论不变**：继续在 Python + Playwright + LLM 架构上深耕，不切换工具。

**补充理由**：
- Maestro Web 模式 Beta，无 JS 注入能力，无法解决 Radix Vue 问题
- Midscene.js 定位为"AI 辅助选择器"，不具备完整的 Agent 流程控制能力
- 当前架构的"证据采集"（Console/Network 监听）是独特优势

---

## 2. 架构演进方案：双引擎驱动

> 原路线图方案合理，保留。

1. **探索引擎**：LLM Agent + Playwright Bridge → 首次测试/UI 大改/探索未知路径
2. **回归引擎**：探索引擎生成 Playwright 脚本 → CI/CD 高频回归，零 Token 消耗

**补充建议**：回归引擎的优先级应降低（Phase 4），当前应全力解决误报率（69%）问题。只有当探索引擎能稳定跑通 core-flow.md 的全部流程后，才具备"固化"的条件。

---

## 3. 卡点解决路线图（基于调研修正）

### Phase 1：解决致命交互问题（P0）

#### 1.1 彻底解决 Radix Vue Popover/DropdownMenu 点击问题

**根因**（调研修正）：不是 `pointerdown` vs `click` 的问题，而是 **DismissableLayer 的 outside 检测机制**在 Playwright 自动化场景下将 trigger 的 `pointerdown` 误判为"外部点击"，导致 Popover "打开又立即关闭"。

**解决方案（按推荐度排序）**：

**方案 A（推荐）：直接操作 Vue 响应式状态，绕过 DOM 事件**
```javascript
// 在 playwright_bridge.py 的 click 方法中，检测目标是否为 Popover trigger
// 如果是，直接通过 JS 切换 Radix Vue 的 open 状态
el => {
    // 查找最近的 PopoverRoot 的 Vue 组件实例
    // Radix Vue 的 PopoverTrigger 渲染的元素带有 data-state="open/closed"
    // 和 aria-haspopup="dialog"
    if (el.getAttribute('aria-haspopup') === 'dialog') {
        // 方式1：直接模拟 click 但阻止 pointerdown 冒泡到 document
        const originalAddEventListener = document.addEventListener;
        const blocked = [];
        document.addEventListener = function(type, fn, opts) {
            if (type === 'pointerdown') {
                blocked.push({type, fn, opts});
                return;
            }
            return originalAddEventListener.call(this, type, fn, opts);
        };
        el.click();
        document.addEventListener = originalAddEventListener;
        // 恢复被阻止的监听器
        blocked.forEach(b => originalAddEventListener.call(document, b.type, b.fn, b.opts));
    }
}
```

**方案 B（更简洁）：在 click 前临时禁用 DismissableLayer 的 pointerdown 监听**
```javascript
el => {
    // 临时移除 document 上所有 pointerdown 监听器
    // 通过 getEventListeners (CDP) 获取并暂存
    el.click();
    // click 之后等待 Vue nextTick，让 PopoverContent 渲染
    // PopoverContent 渲染后 DismissableLayer 会重新注册监听器
}
```

**方案 C（最可靠但有侵入性）：直接通过 Vue 实例操作 open 状态**
```javascript
el => {
    // Vue 3 的组件实例挂在 el.__vueParentComponent 或 el._vnode
    // 找到 PopoverRoot 的 open ref 并切换
    const vnode = el.__vueParentComponent;
    // 沿组件树向上查找，找到 provides 中包含 PopoverRootContext 的组件
    // 直接调用 onOpenToggle()
}
```

**方案 D（最实用的工程方案）：click 后检测 + 重试机制**
```python
def click(self, ref):
    info = self._ref_map[ref]
    locator = self._get_locator(ref)
    
    # 检测是否为 Popover/Dropdown trigger
    is_popover_trigger = locator.evaluate(
        "el => el.getAttribute('aria-haspopup') === 'dialog' || el.getAttribute('aria-haspopup') === 'menu'"
    )
    
    if is_popover_trigger:
        # 对 Popover trigger 使用特殊点击策略
        locator.evaluate("""el => {
            // 在 trigger 的 pointerdown capture 阶段阻止事件传播到 document
            const handler = (e) => {
                e.stopPropagation();
                el.removeEventListener('pointerdown', handler, true);
            };
            el.addEventListener('pointerdown', handler, true);
        }""")
        locator.click(timeout=5000)
        
        # 等待 Popover 内容渲染
        import time
        time.sleep(0.3)
        
        # 验证是否打开：检查 data-state 是否变为 "open"
        state = locator.evaluate("el => el.getAttribute('data-state')")
        if state != 'open':
            # 降级：直接操作 Vue 状态
            locator.evaluate("""el => {
                el.click();  // 纯 JS click，不产生 pointer 事件
            }""")
    else:
        # 非 Popover 的常规点击
        try:
            locator.click(timeout=5000)
        except Exception as e:
            # 现有的降级逻辑...
```

> ⚠️ **方案 D 是我最推荐的**，因为：
> 1. 不侵入被测应用代码
> 2. 有明确的检测条件（`aria-haspopup`）
> 3. 有验证机制（检查 `data-state`）
> 4. 兼容现有架构（基于 ref → locator）

**需要验证的假设**：
- 确认被测应用的 Popover trigger 确实带有 `aria-haspopup="dialog"` 属性（Radix Vue 默认添加）
- 确认 `data-state` 属性在 trigger 元素上正确切换
- 确认 `el.click()` (JS 层面) 不会产生 `pointerdown` 事件（标准行为：`el.click()` 只触发 `click` 事件，不触发 `pointerdown`/`pointerup`）

**验证命令**（可在测试环境执行）：
```python
# 在 playwright_bridge.py 中添加临时调试代码
page.evaluate("""() => {
    document.addEventListener('pointerdown', e => console.log('DOC pointerdown', e.target.tagName, e.target.textContent?.slice(0,20)), true);
    document.addEventListener('click', e => console.log('DOC click', e.target.tagName, e.target.textContent?.slice(0,20)), true);
}""")
```

---

#### 1.2 修复 fill 操作对 Vue v-model 的兼容性问题

**根因**：Playwright `fill()` 内部通过 `element.value = text` 设值后触发 `input` + `change` 事件。Vue 3 的 `v-model` 在 `<input>` 上监听 `input` 事件来更新响应式状态。**理论上 `fill()` 应该能正确触发 Vue v-model**。

**但实际可能失败的情况**：
1. **组件库封装**：Radix Vue / shadcn-vue 的 Input 组件可能在原生 `<input>` 外包了一层，事件被组件内部拦截
2. **IME 输入法**：某些中文输入场景下，`compositionstart`/`compositionend` 事件缺失导致 v-model 不更新
3. **debounce/throttle**：搜索框可能有防抖逻辑，fill 后立即检查结果会看到旧数据

**解决方案**：
```python
def fill(self, ref, text):
    locator = self._get_locator(ref)
    locator.fill(text, timeout=5000)
    
    # 补充：手动触发 Vue 能识别的事件序列
    locator.evaluate("""(el, text) => {
        // 确保 Vue v-model 正确更新
        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        nativeInputValueSetter.call(el, text);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    }""", text)
    
    # 等待 debounce（搜索框通常有 300ms 防抖）
    import time
    time.sleep(0.5)
    return {"success": True}
```

**更好的替代方案**：对搜索场景，改用 `type_text()` 逐字输入（当前已实现 `press_sequentially`），更接近真实用户行为且能正确触发所有事件。可在 `core-flow.md` 中指导 Agent 对搜索框使用 `type` 而非 `fill`。

---

### Phase 2：解决 Agent 行为问题（P1/P2）

#### 2.1 阻止 Agent 进入禁止区域

> 原路线图方案正确，补充完善。

```python
# action_executor.py - execute() 方法中
BLOCKED_PATTERNS = [
    "projectId=25",
    # 未来可扩展
]

def execute(self, action):
    # 在所有可能导致导航的 action 前检查
    if action_type == "navigate":
        url = params.get("url", "")
        for pattern in BLOCKED_PATTERNS:
            if pattern in url:
                return {"success": False, "message": f"⛔ Guardrail: 禁止访问包含 '{pattern}' 的 URL"}
    
    if action_type == "click":
        # click 后检查 URL 是否进入禁区
        result = self._do_click(ref)
        current_url = self.bridge._page.url
        for pattern in BLOCKED_PATTERNS:
            if pattern in current_url:
                self.bridge._page.go_back()
                return {"success": False, "message": f"⛔ Guardrail: 点击后进入禁区，已自动返回"}
```

**同时**在 `llm_engine.py` 的 system prompt 顶部（永不裁剪区域）加入：
```
【硬性规则 - 永远不要违反】
- 禁止访问 projectId=25 的项目
```

#### 2.2 解决重复执行 / 循环检测

> 原路线图的 State Hash 方案可行，补充实现细节。

```python
# test_runner.py 中
import hashlib

class LoopDetector:
    def __init__(self, threshold=3):
        self.threshold = threshold
        self.recent_states = []  # [(url, refs_hash, action_type)]
    
    def check(self, url, refs, action_type):
        # 对 refs 的 role+name 列表做 hash（忽略 backendDOMNodeId 等变化部分）
        refs_sig = hashlib.md5(
            json.dumps([(r['role'], r['name']) for r in refs], sort_keys=True).encode()
        ).hexdigest()[:8]
        
        state = (url, refs_sig, action_type)
        self.recent_states.append(state)
        
        # 保留最近 N 条
        if len(self.recent_states) > 10:
            self.recent_states = self.recent_states[-10:]
        
        # 检测：最近 threshold 次的 (url, refs_hash) 完全相同
        if len(self.recent_states) >= self.threshold:
            recent = self.recent_states[-self.threshold:]
            if len(set((s[0], s[1]) for s in recent)) == 1:
                return True  # 卡死
        return False
```

**将阈值从 6 步降到 3 步**，同时在检测到卡死时：
1. 先尝试不同策略（如 hover → click、Tab → Enter）
2. 如果 2 次策略切换后仍卡死，强制跳过当前子步骤

---

### Phase 3：上下文管理优化（P2）

#### 3.1 滚动摘要（Rolling Summarization）

> 原路线图方案方向正确，补充关键细节。

**触发时机**：不是 80% token 时才触发（太晚了），而是**每完成一个流程**就做一次摘要。

**摘要结构**（固定格式，注入 system prompt）：
```
【测试进度摘要 - 自动生成，勿忽略】
- 已完成流程：流程一(✅)、流程二(✅ 除2.2跳过)
- 当前流程：流程三 步骤3.2
- 已完成子步骤：3.1查看任务列表(✅)
- 已知问题：Popover菜单无法弹出(已跳过相关子步骤)
- 关键约束：禁止访问 projectId=25
```

#### 3.2 持久化关键约束

将以下信息从 `core-flow.md`（可能被裁剪）提升到 system prompt（永不裁剪）：
- 禁止访问的资源列表
- 关键操作的正确方式（如"搜索用 type 不用 fill"）
- 每个流程的前置/后置条件

---

### Phase 4：iframe 内容感知（P2）

**当前问题**：3D 查看器（点云/3DGS/Mesh/CAD）通过 iframe 加载，AX Tree 无法穿透。

**解决方案**：

```python
# playwright_bridge.py - snapshot 方法增加 iframe 检测
def snapshot(self, filter_interactive=True):
    # ... 现有逻辑 ...
    
    # 检测 iframe 并获取基本信息
    iframes = self._page.evaluate("""() => {
        const frames = document.querySelectorAll('iframe');
        return Array.from(frames).map(f => ({
            src: f.src,
            width: f.offsetWidth,
            height: f.offsetHeight,
            loaded: f.contentDocument !== null,  // 同源可访问
            visible: f.offsetWidth > 0 && f.offsetHeight > 0
        }));
    }""")
    
    if iframes:
        # 将 iframe 信息追加到 refs，让 Agent 知道页面有 iframe
        for i, frame_info in enumerate(iframes):
            nodes.append({
                "ref": f"iframe_{i}",
                "role": "iframe",
                "name": f"[iframe src={frame_info['src'][:80]}] loaded={frame_info['loaded']} size={frame_info['width']}x{frame_info['height']}",
            })
        
        # 对同源 iframe，尝试获取其内部 AX Tree
        for frame in self._page.frames:
            if frame != self._page.main_frame and frame.url:
                try:
                    inner_snap = frame.accessibility.snapshot()
                    # 合并到主 refs 列表
                except:
                    pass
```

**对于跨域 iframe**（如第三方 3D 查看器）：
- 只能验证 iframe src 是否正确加载（HTTP 状态码）
- 通过 `page.frame()` 切换到 iframe context 获取其 AX Tree
- 在 `core-flow.md` 中标注：对 iframe 页面使用 `execute_js` 检查 iframe DOM 而非依赖 AX Tree

---

### Phase 5：探索→固化（远期）

> 原路线图方案保留，优先级降低。在 Phase 1-3 完成、误报率降至 20% 以下后再启动。

---

## 4. 实施优先级与预期收益

| 优先级 | 任务 | 工作量 | 预期收益 |
|--------|------|--------|----------|
| **P0** | 1.1 Popover 点击修复（方案 D） | 2-3 天 | 消除 **60% 误报**，解锁流程三全部测试 |
| **P0** | 1.2 fill 兼容性修复 | 0.5 天 | 消除搜索/过滤/排序误报 |
| **P1** | 2.1 Guardrail 禁区拦截 | 0.5 天 | 消除 projectId=25 违规 |
| **P1** | 2.2 循环检测优化（3步阈值） | 1 天 | 节省 20% 步数，提升覆盖率 |
| **P2** | 3.1 滚动摘要 | 1-2 天 | 长测试稳定性提升 |
| **P2** | 3.2 持久化约束 | 0.5 天 | 减少规则遗忘 |
| **P2** | 4 iframe 感知 | 1-2 天 | 消除 3D 查看器误报 |
| **P3** | 5 脚本固化 | 3-5 天 | 零 Token 回归测试 |

**预计完成 P0+P1（约 4-5 天）后**：
- 误报率从 69% 降至 ~15%
- 流程三覆盖率从 ~30% 提升至 ~80%
- 步数浪费从 ~20% 降至 ~5%

---

## 5. 需立即验证的假设

在实施前，建议先在测试环境手动验证以下假设：

1. **Popover trigger 的 `aria-haspopup` 属性**：
   ```javascript
   // 在浏览器 DevTools 中执行
   document.querySelectorAll('[aria-haspopup]')
   ```

2. **`el.click()` 是否能打开 Popover 且不被关闭**：
   ```javascript
   // 找到一个 ··· 按钮，在 DevTools 中执行
   const btn = document.querySelector('button[aria-haspopup="dialog"]');
   btn.click();  // 观察 Popover 是否打开且保持
   ```

3. **Playwright `locator.click()` 触发的事件序列**：
   ```javascript
   // 在按钮上监听所有事件
   const btn = document.querySelector('button[aria-haspopup="dialog"]');
   ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(evt => {
       btn.addEventListener(evt, e => console.log(evt, 'on button'), true);
   });
   document.addEventListener('pointerdown', e => console.log('pointerdown on document, target:', e.target.tagName), true);
   ```
   然后通过 Playwright `locator.click()` 触发，观察事件顺序和 document 上的 pointerdown 是否被触发。

4. **DismissableLayer 的 outside 检测范围**：
   ```javascript
   // 检查 PopoverContent 渲染位置
   document.querySelectorAll('[data-dismissable-layer]')
   // 确认其是否通过 Teleport 渲染到 body 末尾（与 trigger 不在同一 DOM 子树）
   ```

这些验证结果将决定方案 A/B/C/D 中哪个最适合。
