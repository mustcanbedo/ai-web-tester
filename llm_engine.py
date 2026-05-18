"""
AI Web Tester - LLM 引擎
包含 System Prompt 和 LLM 对话管理
"""

import json
import re
import time
import traceback

from openai import OpenAI
from config import LLM_MODEL


# ============================================================
# System Prompt
# ============================================================

SYSTEM_PROMPT = """你是一个专业的 Web 自动化测试 Agent。你的目标是根据《功能预期文档》验证 Web 系统是否正常工作。

## ⛔ 硬性规则（永远不要违反）
1. **禁止访问 projectId=25 的项目** — 这是生产数据，任何情况下都不能点击、导航或操作该项目
2. **搜索/筛选框使用 type 而非 fill** — 搜索框通常有防抖逻辑，用 type（逐字输入）更可靠；fill 可能不触发组件状态更新
3. **遮罩层/弹窗优先处理** — 如果页面有遮罩层（如登录弹窗），必须先处理或关闭遮罩层，再操作底层元素
4. **Popover/更多按钮定位** — 需要点击"更多"（···）按钮时，**必须选择 name 为 `[更多操作]` 的 button**，这是带 aria-haspopup 属性的真正 Popover trigger。不要点 `[唯一按钮 @ xxx]` 或 `[按钮N/M @ xxx]`，那些是其他功能按钮（如详情、下载）
5. **WebGL/Canvas 页面不报空白** — 3DGS 编辑器、点云查看器、Mesh 编辑器、CAD 查看器等 3D 可视化页面使用 WebGL/Canvas 渲染，Accessibility Tree 只有根元素和少量 UI 控件是**正常的**。判定标准：URL 正确且无 console 错误即为通过，不要报"页面空白"
6. **卡片按钮布局** — 所有成功任务（含 Mesh）卡片操作区有 4 个按钮（下载、查看、详情ⓘ、更多···），其中"下载"和"更多"都是 Popover trigger。点击"下载"弹出格式选择菜单（如 las/ply/glb 等），点击"更多"弹出"重命名"/"删除"菜单。它们在 AX Tree 中都可能显示为 `[更多操作]` 或 `[按钮N/M @ 卡片名]`
7. **下载判定** — 点击下载选项（如 `las(.las)`）后，网站通过 `window.open(url)` 在新标签页打开文件 URL 触发下载。操作结果消息中如果包含"触发了新标签页打开"且 URL 指向文件资源，即视为**下载成功**。不要因为当前页面无变化就报"下载无响应"
8. **手机号区号前缀** — 手机号输入框内嵌 `+86` 区号前缀，这是 UI 组件的固有部分，不是用户输入。`fill("", "")` 后 value 仍显示 `+86` 是正常的，不要报"无法清空"

## 你的工作方式
你在一个循环中工作：每一步你会收到当前页面的 Accessibility Tree（元素列表，每个元素有唯一引用 ID 如 e0, e1, e2...）和页面文本，然后决定下一步操作。

## 页面状态格式
你会收到类似这样的信息：
```
refs: [
  {"id": "e0", "role": "link", "text": "登录"},
  {"id": "e1", "role": "textbox", "label": "手机号"},
  {"id": "e2", "role": "button", "text": "获取验证码"},
  {"id": "e3", "role": "checkbox", "text": "我已同意条款"}
]
text: "页面可读文本内容..."
```

## 可用操作
你必须返回一个 JSON 对象，包含以下字段：

```json
{
  "thinking": "你的思考过程",
  "action": {"type": "操作类型", "params": {}},
  "actions": [{"type": "...", "params": {}}, {"type": "...", "params": {}}],
  "found_issues": [{"severity": "P0/P1/P2", "title": "问题标题", "description": "问题描述", "evidence": "证据说明"}],
  "current_flow": "当前正在测试的流程名称",
  "flow_status": "testing/passed/failed",
  "checklist_item": "本步骤对应的检查项描述（简短，如：填写用户名 standard_user）",
  "should_continue": true,
  "next_plan": "下一步打算做什么"
}
```

> **批量操作**：`action` 和 `actions` 二选一。当你需要连续执行多个不依赖页面变化的操作时（如填写表单的多个字段），可以用 `actions` 数组一次返回最多 4 个操作，系统会按顺序执行直到某个操作失败或页面发生导航。如果只有一个操作，用 `action` 字段即可（`actions` 可省略）。不要同时提供两个字段。
> **不适合批量的操作**：click（可能触发页面跳转/弹窗）、navigate、finish、screenshot 应单独执行，不要放在 actions 数组中与其他操作混合。

## 操作类型 (action.type)
1. **navigate** - params: {"url": "https://..."}
2. **click** - params: {"ref": "e5"}  — 点击元素，使用 refs 中的 id
3. **fill** - params: {"ref": "e1", "text": "要填写的文本"}  — 清空并填写输入框
4. **type** - params: {"ref": "e1", "text": "要输入的文本"}  — 追加输入文本
5. **press** - params: {"key": "Enter/Tab等", "ref": "e1（可选）"}
6. **hover** - params: {"ref": "e3"}
7. **select** - params: {"ref": "e3", "value": "选项值"}  — 选择下拉选项
8. **wait** - params: {"ms": 毫秒数}
9. **screenshot** - params: {"name": "截图名称"}
10. **scroll** - params: {"direction": "down/up", "pixels": 500}
11. **assert_text** - params: {"text": "期望存在的文本"}
12. **verify_data** - params: {"check_type": "sum_check/cross_check/consistency", "description": "验证描述", "data": {}}
13. **call_api** - params: {"method": "GET/POST", "url": "API地址", "headers": {}, "body": {}, "description": "API调用说明"}
14. **compare_api_vs_page** - params: {"description": "对比说明", "api_data": "接口数据", "page_data": "页面数据"}
15. **switch_tab** - params: {"index": 标签页索引，-1表示最新}  — 切换到指定标签页（window.open 打开新标签页后系统会自动切换，通常用于切回原标签页）
16. **close_tab** - params: {"index": -1}  — 关闭指定标签页并切回上一个（-1表示当前标签页）
17. **get_tabs** - params: {}  — 查看所有打开的标签页列表
18. **go_back** - params: {}  — 浏览器后退（等同点击浏览器后退按钮）
19. **send_keys** - params: {"keys": "Tab Tab Enter"}  — 发送键盘按键序列（空格分隔），当按钮无法点击时可用键盘导航，支持 Tab/Enter/Escape/ArrowDown/ArrowUp/Space 等（注意：发送空格键请用 "Space" 而非空格字符）
20. **execute_js** - params: {"expression": "JavaScript代码", "description": "说明"}  — 在页面上下文执行 JS，可用于操作 localStorage/sessionStorage（如清除 token）、读取页面状态等
21. **finish** - params: {"summary": "测试总结"}
22. **request_human_input** - params: {"title": "标题", "description": "说明", "placeholder": "占位符"}

## 重要规则
1. **使用 ref 引用**：所有元素交互（click、fill、type、hover 等）必须通过 ref ID（如 e0、e5、e12）引用元素，不要使用 CSS 选择器或 XPath。
2. 每一步必须提供 **checklist_item** 字段，简要描述本步骤的检查项
3. 仔细观察页面上的数据，特别关注：
   - 表格中的数值是否可以交叉验证
   - 汇总数据是否等于明细数据之和
   - 不同区域展示的相同数据是否一致
4. 发现异常时判断严重等级：
   - P0: 阻断核心流程
   - P1: 功能异常但不阻断
   - P2: 体验问题或非核心异常
5. 每一步操作后系统会自动截图作为证据
6. **finish 条件极其严格**：只有当功能预期文档中的**所有流程的所有子步骤**都已实际执行并验证后，才能使用 finish。仅仅"看到页面"或"进入了某页面"不等于测试通过——你必须按文档中描述的每一步操作（点击、填写、断言预期结果）都实际执行过。如果某个流程有 5 个子节（如 3.1-3.5），你必须逐个执行完所有子节才能标记该流程 passed
7. **下拉菜单操作**：当需要从下拉菜单选择选项时，必须分两步：先 click 下拉触发元素展开列表，等下一步 snapshot 后再 click 目标选项的 ref。**不要在输入框中手动输入下拉菜单应选择的值**
8. **fill 只填纯值**：例如手机号只填数字（如 15912345678），不要加国家区号前缀
9. 如果 refs 列表中没有你需要的元素，可能需要先 scroll 或 click 展开隐藏区域，然后等待下一步 snapshot 刷新
10. **表单提交前检查**：在点击提交/登录/确认类按钮前，主动检查当前 refs 中所有 checkbox 元素的状态。如果有未勾选的必选 checkbox（如同意条款、用户协议等），先勾选再提交。对于非必选的 checkbox（如营销授权、信息分享等），保持默认状态不勾选
11. **从失败中恢复**：如果操作失败或页面显示错误提示（红色文字、toast 警告等），不要重复相同操作。应该：分析错误提示内容→检查是否有遗漏的必填项或未勾选的 checkbox→修正后重试
12. **每步只能执行一个操作**：action 字段必须是一个对象（不能是数组）。如果你想连续执行多个操作（如先填写再勾选再点击），必须每步只返回一个操作，等系统执行完后在下一步返回下一个操作
13. **跳过标记**：功能预期文档中标有 `[SKIP]` 的章节表示该功能暂不可用，必须完全跳过，不要测试该节的任何步骤
14. **不要轻易放弃**：如果导航或操作失败，尝试其他方法（如点击页面上的链接/按钮、滚动页面寻找入口、返回上一页重试等）。只有在尝试了 3 种以上不同方法后仍然失败，才能记录问题并跳过该功能继续测试下一个流程。绝不要因为单个功能失败就 finish 整个测试
15. **搜索/筛选后必须恢复**：测试搜索或筛选功能后，必须清空搜索框（用 fill 填入空字符串 ""）并恢复筛选条件为默认值，确保列表显示完整数据后再继续后续流程。不要在搜索/筛选状态下判断列表是否为空
16. **严格按文档顺序、深入执行每个子流程**：必须严格按照功能预期文档中的章节顺序（流程一→流程二→流程三…）依次测试。**每个流程内部必须按子节顺序逐一执行**（如 3.1→3.2→3.3→…→3.9），每个子节中描述的每一步操作都要实际执行（不能跳过）。只有当前流程的所有子节都执行完毕后，才能进入下一个流程。**严禁跳跃**：不要在测完流程三的 3.2 后就跳去流程五，必须先完成 3.3、3.4…3.9。如果某个子节因为数据条件不满足而无法执行（如没有失败任务无法测重试），记录跳过原因后继续下一个子节，但不要跳到其他流程
17. **ref 会随页面变化重新分配**：当页面 DOM 发生变化时（弹出对话框/模态框、关闭弹窗、Tab 切换、导航跳转、列表刷新等），所有 ref ID 会被重新分配。**绝对不要**记住或复用上一步的 ref ID，必须根据当前步骤给你的最新 refs 列表来确定操作目标。例如：上一步点击按钮 ref=e4 打开了对话框，对话框内的输入框在新的 refs 中可能是 e0 或 e1，不再是 e4。每一步都要重新阅读 refs 列表！
18. **不要重复报告相同问题**：如果你在之前的步骤中已经报告过某个 issue（相同 title），不要再次将它添加到 found_issues 中
19. **clickable 角色**：refs 列表中角色为 `clickable` 的元素是页面上带有 cursor:pointer 样式的可点击区域（如项目卡片预览图、可点击的标题等）。它们不是标准的 button 或 link，但可以正常使用 click 操作。当你需要进入详情页、点击卡片等场景时，优先查找 clickable 角色的元素
20. **子节阻塞时果断跳过**：如果系统提示你在某个子节上卡住了（连续多步失败或无进展），你必须立即：① 在 found_issues 中记录该子节的阻塞原因（如"按钮无响应"、"Popover 未弹出"等）；② 保持 flow_status=testing（单个子节失败不等于整个流程失败）；③ 将 checklist_item 更新为当前流程的下一个子节编号，继续测试。**不要在同一个子节上反复尝试超过 5 步**——把步数留给其他未测试的功能更有价值

只返回 JSON，不要其他内容。"""


# ============================================================
# LLM 引擎
# ============================================================

class LLMEngine:
    def __init__(self, api_key=None, base_url=None, model=None):
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model or LLM_MODEL
        self.messages = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def restore_messages(self, messages, input_tokens=0, output_tokens=0):
        """从快照恢复对话历史"""
        self.messages = messages
        self.total_input_tokens = input_tokens
        self.total_output_tokens = output_tokens

    def reset_for_new_flow(self, spec_content, completed_flows, all_issues, current_url, login_info="", api_doc=""):
        """流程切换时重置上下文，注入摘要"""
        system_msg = SYSTEM_PROMPT
        if api_doc:
            system_msg += f"\n\n## 接口文档\n以下是系统的 API 接口文档，请据此进行三层数据验证：\n\n{api_doc}"

        flows_str = "、".join(completed_flows) if completed_flows else "无"
        issues_summary = ""
        if all_issues:
            issues_summary = "\n已发现的问题：\n" + "\n".join(
                f"- [{i.get('severity', 'P2')}] {i.get('title', '')}: {i.get('description', '')}"
                for i in all_issues[-10:]
            )

        summary = f"""[系统提示：流程切换，上下文已重置]

已完成的测试流程：{flows_str}
{issues_summary}

当前页面URL：{current_url}
{('登录信息：' + login_info) if login_info else ''}

请继续测试功能预期文档中尚未测试的功能流程。不要重复测试已完成的流程。"""

        self.messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"以下是功能预期文档：\n\n{spec_content}\n\n{summary}"},
        ]
        old_chars = sum(len(m.get('content', '')) for m in self.messages)
        print(f"[DEBUG] 流程切换重置上下文: 已完成流程=[{flows_str}], 新消息总字符={old_chars}")

    def init_conversation(self, spec_content, login_info="", api_doc=""):
        system_msg = SYSTEM_PROMPT
        if api_doc:
            system_msg += f"\n\n## 接口文档\n以下是系统的 API 接口文档，请据此进行三层数据验证：\n\n{api_doc}"
        self.messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"以下是功能预期文档：\n\n{spec_content}\n\n{('登录信息：' + login_info) if login_info else '无需登录。'}\n\n请开始测试。第一步应该是导航到目标页面。"},
        ]

    def _robust_json_parse(self, content: str) -> dict:
        """健壮的 JSON 解析：处理 LLM 输出中常见的格式问题"""
        cleaned = content.strip()
        # 1. 去除 Markdown 代码围栏
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[\w]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        # 2. 去除 JS 风格行注释
        cleaned = re.sub(r'(?<=["\d\]\}])\s*//[^\n]*', '', cleaned)
        # 3. 修复 trailing comma（,} 或 ,]）
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)

        # 第一次尝试
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 4. 尝试修复截断的 JSON（补全缺失的括号）
        try:
            fixed = cleaned
            open_braces = fixed.count('{') - fixed.count('}')
            open_brackets = fixed.count('[') - fixed.count(']')
            if open_brackets > 0:
                fixed += ']' * open_brackets
            if open_braces > 0:
                fixed += '}' * open_braces
            fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # 5. 最后手段：用正则提取关键字段
        print(f"[WARN] JSON 解析全部失败，尝试正则提取关键字段")
        action_match = re.search(r'"action"\s*:\s*(\{[^}]*\})', cleaned)
        thinking_match = re.search(r'"thinking"\s*:\s*"([^"]*)"', cleaned)
        result = {
            "thinking": thinking_match.group(1) if thinking_match else "JSON解析失败，已提取部分信息",
            "action": json.loads(action_match.group(1)) if action_match else {"type": "wait", "params": {"ms": 2000}},
            "found_issues": [],
            "current_flow": "",
            "flow_status": "testing",
            "checklist_item": "JSON解析修复",
            "should_continue": True,
        }
        return result

    def _smart_truncate_refs(self, refs, max_total=50):
        """智能截断 refs：当 option 过多时，保留非 option + 相关 option"""
        if len(refs) <= max_total:
            return refs, ""

        non_options = [r for r in refs if r.get("role") != "option"]
        options = [r for r in refs if r.get("role") == "option"]

        if len(options) <= 20:
            return refs[:max_total], f"（共 {len(refs)} 个元素，仅显示前 {max_total} 个）"

        # option 过多（如国家列表），从最近对话中提取关键词过滤
        keywords = set()
        for msg in reversed(self.messages[-5:]):
            content = msg.get("content", "")
            # 提取最近对话中的可能关键词（引号内或特定模式）
            for m in re.findall(r'"([^"]{2,20})"', content):
                keywords.add(m)
        # 兜底关键词
        if not keywords:
            keywords = {"中国", "China", "+86", "86"}

        matched = [o for o in options if any(kw.lower() in o.get("name", "").lower() for kw in keywords)]
        head_options = options[:5]
        filtered_options = head_options + [o for o in matched if o not in head_options]

        result = non_options + filtered_options
        hint = f"（下拉列表共 {len(options)} 个选项，已过滤显示 {len(filtered_options)} 个相关项。如需选择其他选项，请用包含目标文字的关键词描述）"
        return result, hint

    def decide(self, page_state, evidence, step, extra_context="", recent_api_responses=None, flow_progress=None):
        refs = page_state.get("refs", [])
        truncated_refs, refs_hint = self._smart_truncate_refs(refs)
        refs_str = json.dumps(truncated_refs, ensure_ascii=False) if truncated_refs else "[]"
        if refs_hint:
            refs_str += f"\n{refs_hint}"
        page_text = page_state.get("text", "")[:1500]
        obs = f"## 第 {step} 步观察\n\n**页面URL**: {page_state.get('url','')}\n**页面标题**: {page_state.get('title','')}\n"
        # 多标签页信息
        tab_info = page_state.get("tabs")
        if tab_info and len(tab_info) > 1:
            tab_lines = []
            for t in tab_info:
                marker = "→" if t.get("active") else " "
                tab_lines.append(f"  {marker} [{t['index']}] {t['url']}")
            obs += f"\n**标签页**（共 {len(tab_info)} 个，→ 为当前）:\n" + "\n".join(tab_lines) + "\n"
        obs += f"\n**可交互元素 (Accessibility Tree refs)**:\n{refs_str}\n\n**页面文本**:\n{page_text}\n\n"
        if evidence.get("new_console_errors"):
            obs += f"**新 Console 错误**:\n{json.dumps(evidence['new_console_errors'], ensure_ascii=False)}\n\n"
        if evidence.get("new_network_errors"):
            obs += f"**新 Network 错误 (4xx/5xx)**:\n{json.dumps(evidence['new_network_errors'], ensure_ascii=False)}\n\n"
        if recent_api_responses:
            obs += f"**最近拦截到的 API 响应**:\n{json.dumps(recent_api_responses[:5], ensure_ascii=False)}\n\n"
        if extra_context:
            obs += f"**额外上下文**:\n{extra_context}\n\n"
        # 注入流程进度摘要（每步都注入，防止 Agent 重复已完成的操作）
        if flow_progress:
            progress_lines = []
            completed = flow_progress.get("completed_flows", [])
            failed = flow_progress.get("failed_flows", [])
            done_items = flow_progress.get("done_items_in_current_flow", [])
            if completed:
                progress_lines.append(f"✅ 已通过流程：{', '.join(completed)}")
            if failed:
                progress_lines.append(f"❌ 未通过流程：{'; '.join(f.get('flow','') for f in failed)}")
            if done_items:
                progress_lines.append(f"📋 当前流程已完成子节（共{len(done_items)}项，不要重复）：" + "、".join(done_items[-10:]))
            if progress_lines:
                obs += "**测试进度**:\n" + "\n".join(progress_lines) + "\n\n"
        obs += "请决定下一步操作。只返回 JSON。"
        self.messages.append({"role": "user", "content": obs})

        # DEBUG: LLM 输入
        print(f"\n{'='*60}")
        print(f"[DEBUG][Step {step}] LLM INPUT (obs 前500字):")
        print(obs[:500])
        if extra_context:
            print(f"[DEBUG][Step {step}] extra_context: {extra_context}")
        print(f"[DEBUG] messages 数量: {len(self.messages)}, 总字符: {sum(len(m.get('content','')) for m in self.messages)}")

        # 消息历史过长时压缩（提取被删消息中的操作摘要，而非直接丢弃）
        total_chars = sum(len(m.get('content', '')) for m in self.messages)
        if total_chars > 80000 and len(self.messages) > 10:
            preserved = self.messages[:3]
            # 二级压缩：超长时保留更少的 recent
            keep_recent = 12 if total_chars > 120000 else 20
            recent = self.messages[-keep_recent:]
            middle = self.messages[3:-keep_recent]
            trimmed_count = len(middle)

            # 从被删的 assistant 消息中提取操作摘要
            action_summary_lines = []
            for msg in middle:
                if msg.get("role") != "assistant":
                    continue
                try:
                    parsed = json.loads(msg["content"])
                    act = parsed.get("action", {})
                    act_type = act.get("type", "?")
                    flow_name = parsed.get("current_flow", "")
                    ci = parsed.get("checklist_item", "")
                    status = parsed.get("flow_status", "")
                    brief = f"  {act_type}"
                    if ci:
                        brief += f": {ci}"
                    if flow_name:
                        brief += f" [{flow_name}]"
                    if status == "failed":
                        brief += " ❌"
                    action_summary_lines.append(brief)
                except (json.JSONDecodeError, AttributeError):
                    pass

            # 构建压缩摘要
            progress_parts = [f"[系统提示：为节省 token，已压缩中间 {trimmed_count} 条对话记录为摘要。]"]
            if action_summary_lines:
                # 只保留最后 30 条操作摘要
                if len(action_summary_lines) > 30:
                    progress_parts.append(f"（前 {len(action_summary_lines) - 30} 步已省略）")
                    action_summary_lines = action_summary_lines[-30:]
                progress_parts.append("被压缩期间的操作序列：")
                progress_parts.extend(action_summary_lines)

            if flow_progress:
                completed = flow_progress.get("completed_flows", [])
                failed = flow_progress.get("failed_flows", [])
                current = flow_progress.get("current_flow", "")
                if completed:
                    progress_parts.append(f"\n✅ 已通过的流程（不要重复测试）：{', '.join(completed)}")
                if failed:
                    failed_desc = "; ".join([f"{f.get('flow','')}: {f.get('reason','')}" for f in failed])
                    progress_parts.append(f"❌ 未通过的流程：{failed_desc}")
                if current:
                    progress_parts.append(f"📍 当前正在测试：{current}")
                done_items = flow_progress.get("done_items_in_current_flow", [])
                if done_items:
                    progress_parts.append(f"📋 当前流程已完成的子节（不要重复执行）：")
                    for item in done_items[-15:]:
                        progress_parts.append(f"  ✓ {item}")
                progress_parts.append("请继续测试尚未完成的子节和流程，不要重复已完成的操作。")
            summary_msg = {"role": "user", "content": "\n".join(progress_parts)}
            self.messages = preserved + [summary_msg] + recent
            new_chars = sum(len(m.get('content', '')) for m in self.messages)
            print(f"[DEBUG][Step {step}] 历史压缩: {trimmed_count} 条→摘要, {total_chars}→{new_chars} 字符, 保留 {len(self.messages)} 条")

        # 带重试的 LLM 调用
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(model=self.model, messages=self.messages, temperature=0.2, max_tokens=1500, response_format={"type": "json_object"})
                if response.usage:
                    self.total_input_tokens += response.usage.prompt_tokens
                    self.total_output_tokens += response.usage.completion_tokens
                content = response.choices[0].message.content.strip()
                self.messages.append({"role": "assistant", "content": content})

                print(f"[DEBUG][Step {step}] LLM OUTPUT:")
                print(content[:1000])
                print(f"[DEBUG] tokens: in={response.usage.prompt_tokens if response.usage else '?'} out={response.usage.completion_tokens if response.usage else '?'}")
                print(f"{'='*60}\n")

                parsed = self._robust_json_parse(content)
                return parsed
            except Exception as e:
                error_str = str(e)
                is_rate_limit = '429' in error_str or 'rate' in error_str.lower() or 'RPM limit' in error_str
                is_fatal = 'balance' in error_str.lower() or 'insufficient' in error_str.lower() or '30001' in error_str
                if is_fatal:
                    print(f"[FATAL][Step {step}] 账户余额不足或致命错误，立即终止: {e}")
                    traceback.print_exc()
                    return {"thinking": f"LLM API 致命错误: {error_str}", "action": {"type": "fatal_error", "params": {"summary": f"API 错误: {error_str}"}}, "found_issues": [], "current_flow": "", "flow_status": "failed", "checklist_item": "API 致命错误", "should_continue": False}
                if is_rate_limit and attempt < max_retries - 1:
                    wait_sec = (attempt + 1) * 15
                    print(f"[WARN][Step {step}] Rate limit hit, 等待 {wait_sec}s 后重试 ({attempt+1}/{max_retries})")
                    time.sleep(wait_sec)
                    continue
                print(f"[ERROR][Step {step}] LLM 调用异常: {e}")
                traceback.print_exc()
                if is_rate_limit:
                    return {"thinking": f"LLM API 限流，等待后重试", "action": {"type": "wait", "params": {"ms": 5000}}, "found_issues": [], "current_flow": "", "flow_status": "testing", "checklist_item": "API限流等待", "should_continue": True}
                return {"thinking": f"LLM 调用失败: {error_str}", "action": {"type": "fatal_error", "params": {"summary": f"LLM 错误: {error_str}"}}, "found_issues": [], "current_flow": "", "flow_status": "failed", "checklist_item": "LLM 调用失败", "should_continue": False}

    def generate_report(self, action_history, all_issues, evidence_summary, spec_content, login_type="", data_checks=None):
        tested_flows = set()
        for entry in action_history:
            flow = entry.get("flow", "")
            if flow:
                tested_flows.add(flow)
        tested_flows_str = "、".join(tested_flows) if tested_flows else "无"

        prompt = f"""请根据以下测试执行记录生成专业的 Markdown 格式测试报告。

## 功能预期文档
{spec_content}

## 执行历史 ({len(action_history)} 步)
{json.dumps(action_history, ensure_ascii=False)[:6000]}

## 实际测试过的流程
{tested_flows_str}

## 发现的问题 ({len(all_issues)} 个)
{json.dumps(all_issues, ensure_ascii=False) if all_issues else '无'}

## 证据汇总
- Console 错误: {evidence_summary.get('total_console_errors', 0)} 个
- Network 错误: {evidence_summary.get('total_network_errors', 0)} 个
- 拦截 API 响应: {evidence_summary.get('total_api_responses_captured', 0)} 个
- Console 错误详情: {json.dumps(evidence_summary.get('console_errors', [])[:10], ensure_ascii=False)}
- Network 错误详情: {json.dumps(evidence_summary.get('network_errors', [])[:10], ensure_ascii=False)}

## Token 用量
- 输入 tokens: {self.total_input_tokens:,}
- 输出 tokens: {self.total_output_tokens:,}
- 总计 tokens: {self.total_input_tokens + self.total_output_tokens:,}

## 数据验证记录
{json.dumps(data_checks, ensure_ascii=False) if data_checks else '无数据验证'}

## 报告要求（严格遵守）
1. **只报告实际执行过的流程**：只有在"执行历史"中有对应操作步骤的流程才能判定为"通过"或"未通过"
2. **未执行的流程必须标记为"⏭️ 未测试"**：功能预期文档中有但执行历史中没有涉及的流程，一律标记为"未测试"，不要猜测结果
3. **不要编造测试结果**：只基于执行历史中的实际操作和结果来判定

请生成包含以下部分的报告：
1. 测试概要（总共执行了多少步，覆盖了哪些流程，有多少流程未测试）
2. 测试结论（每个流程的状态：✅通过 / ❌未通过 / ⏭️未测试）
3. Bug 列表（含严重等级、描述、证据）
4. 数据一致性验证结果（如有）
5. 异常信息（Console/Network 错误）
6. 执行时间线（仅列出实际执行的步骤）

直接输出 Markdown 内容，不要代码块标记。"""
        try:
            response = self.client.chat.completions.create(model=self.model, messages=[
                {"role": "system", "content": "你是一个专业的测试报告撰写者。请生成清晰、专业的 Markdown 格式测试报告。"},
                {"role": "user", "content": prompt},
            ], temperature=0.3, max_tokens=3000)
            if response.usage:
                self.total_input_tokens += response.usage.prompt_tokens
                self.total_output_tokens += response.usage.completion_tokens
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"# 测试报告生成失败\n\n错误: {str(e)}"
