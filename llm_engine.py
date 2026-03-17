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
  "found_issues": [{"severity": "P0/P1/P2", "title": "问题标题", "description": "问题描述", "evidence": "证据说明"}],
  "current_flow": "当前正在测试的流程名称",
  "flow_status": "testing/passed/failed",
  "checklist_item": "本步骤对应的检查项描述（简短，如：填写用户名 standard_user）",
  "should_continue": true,
  "next_plan": "下一步打算做什么"
}
```

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
15. **finish** - params: {"summary": "测试总结"}
16. **request_human_input** - params: {"title": "标题", "description": "说明", "placeholder": "占位符"}

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
6. **只有当功能预期文档中的所有功能流程都已测试完毕后**，才能使用 finish 操作结束。完成一个流程后，必须继续测试下一个流程，不要提前结束
7. **下拉菜单操作**：当需要从下拉菜单选择选项时，必须分两步：先 click 下拉触发元素展开列表，等下一步 snapshot 后再 click 目标选项的 ref。**不要在输入框中手动输入下拉菜单应选择的值**
8. **fill 只填纯值**：例如手机号只填数字（如 15912345678），不要加国家区号前缀
9. 如果 refs 列表中没有你需要的元素，可能需要先 scroll 或 click 展开隐藏区域，然后等待下一步 snapshot 刷新
10. **表单提交前检查**：在点击提交/登录/确认类按钮前，主动检查当前 refs 中所有 checkbox 元素的状态。如果有未勾选的必选 checkbox（如同意条款、用户协议等），先勾选再提交。对于非必选的 checkbox（如营销授权、信息分享等），保持默认状态不勾选
11. **从失败中恢复**：如果操作失败或页面显示错误提示（红色文字、toast 警告等），不要重复相同操作。应该：分析错误提示内容→检查是否有遗漏的必填项或未勾选的 checkbox→修正后重试
12. **每步只能执行一个操作**：action 字段必须是一个对象（不能是数组）。如果你想连续执行多个操作（如先填写再勾选再点击），必须每步只返回一个操作，等系统执行完后在下一步返回下一个操作
13. **跳过标记**：功能预期文档中标有 `[SKIP]` 的章节表示该功能暂不可用，必须完全跳过，不要测试该节的任何步骤
14. **不要轻易放弃**：如果导航或操作失败，尝试其他方法（如点击页面上的链接/按钮、滚动页面寻找入口、返回上一页重试等）。只有在尝试了 3 种以上不同方法后仍然失败，才能记录问题并跳过该功能继续测试下一个流程。绝不要因为单个功能失败就 finish 整个测试
15. **搜索/筛选后必须恢复**：测试搜索或筛选功能后，必须清空搜索框（用 fill 填入空字符串 ""）并恢复筛选条件为默认值，确保列表显示完整数据后再继续后续流程。不要在搜索/筛选状态下判断列表是否为空
16. **严格按文档顺序执行**：必须严格按照功能预期文档中的章节顺序（流程一→流程二→流程三…）依次测试。每个流程内部也必须按照子节顺序执行（2.1→2.2→2.3…）。完成当前流程的所有子节后，才能进入下一个流程。不要跳跃、穿插或提前测试后面的流程
17. **ref 会随页面变化重新分配**：当页面 DOM 发生变化时（弹出对话框/模态框、关闭弹窗、Tab 切换、导航跳转、列表刷新等），所有 ref ID 会被重新分配。**绝对不要**记住或复用上一步的 ref ID，必须根据当前步骤给你的最新 refs 列表来确定操作目标。例如：上一步点击按钮 ref=e4 打开了对话框，对话框内的输入框在新的 refs 中可能是 e0 或 e1，不再是 e4。每一步都要重新阅读 refs 列表！
18. **不要重复报告相同问题**：如果你在之前的步骤中已经报告过某个 issue（相同 title），不要再次将它添加到 found_issues 中

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

    def decide(self, page_state, evidence, step, extra_context="", recent_api_responses=None):
        refs = page_state.get("refs", [])
        truncated_refs, refs_hint = self._smart_truncate_refs(refs)
        refs_str = json.dumps(truncated_refs, ensure_ascii=False) if truncated_refs else "[]"
        if refs_hint:
            refs_str += f"\n{refs_hint}"
        page_text = page_state.get("text", "")[:1500]
        obs = f"## 第 {step} 步观察\n\n**页面URL**: {page_state.get('url','')}\n**页面标题**: {page_state.get('title','')}\n\n**可交互元素 (Accessibility Tree refs)**:\n{refs_str}\n\n**页面文本**:\n{page_text}\n\n"
        if evidence.get("new_console_errors"):
            obs += f"**新 Console 错误**:\n{json.dumps(evidence['new_console_errors'], ensure_ascii=False)}\n\n"
        if evidence.get("new_network_errors"):
            obs += f"**新 Network 错误 (4xx/5xx)**:\n{json.dumps(evidence['new_network_errors'], ensure_ascii=False)}\n\n"
        if recent_api_responses:
            obs += f"**最近拦截到的 API 响应**:\n{json.dumps(recent_api_responses[:5], ensure_ascii=False)}\n\n"
        if extra_context:
            obs += f"**额外上下文**:\n{extra_context}\n\n"
        obs += "请决定下一步操作。只返回 JSON。"
        self.messages.append({"role": "user", "content": obs})

        # DEBUG: LLM 输入
        print(f"\n{'='*60}")
        print(f"[DEBUG][Step {step}] LLM INPUT (obs 前500字):")
        print(obs[:500])
        if extra_context:
            print(f"[DEBUG][Step {step}] extra_context: {extra_context}")
        print(f"[DEBUG] messages 数量: {len(self.messages)}, 总字符: {sum(len(m.get('content','')) for m in self.messages)}")

        # 消息历史过长时裁剪
        total_chars = sum(len(m.get('content', '')) for m in self.messages)
        if total_chars > 80000 and len(self.messages) > 10:
            preserved = self.messages[:3]
            recent = self.messages[-16:]
            trimmed_count = len(self.messages) - 3 - 16
            summary_msg = {"role": "user", "content": f"[系统提示：为节省 token，已省略中间 {trimmed_count} 条对话记录。请基于最近的上下文继续测试。]"}
            self.messages = preserved + [summary_msg] + recent
            print(f"[DEBUG][Step {step}] 消息历史裁剪: 删除 {trimmed_count} 条，剩余 {len(self.messages)} 条")

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
