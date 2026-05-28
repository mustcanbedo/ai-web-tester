"""
AI Web Tester - 独立审查 Agent
职责：独立于 Executor 判断 Bug 是否为真，消除自评偏差。

设计原则：
  - 每次调用短上下文（<2000 token input），快速准确
  - 不共享 Executor 的对话历史，独立视角
  - 内置已知误报模式库，避免重复误判
"""

import json
import logging
import time

from openai import OpenAI

from config import LLM_MODEL

logger = logging.getLogger("reviewer_agent")

# 已知误报模式（从 bad-cases.md 总结）
KNOWN_FALSE_POSITIVE_PATTERNS = [
    "Canvas/WebGL/3D 页面 AX Tree 元素稀疏不是 Bug，是技术限制",
    "新标签页打开下载链接后页面看起来'无响应'不是 Bug",
    "手机号输入框显示 +86 前缀是正常行为",
    "Popover/下拉菜单需要先点击触发按钮才能看到内容，不点击时隐藏是正常的",
    "页面跳转后 DOM 重新渲染导致 ref 编号变化不是 Bug",
    "assert_text 只能检测 DOM 可见文本，document.title 不可见不代表页面标题错误",
    "搜索/过滤操作后数据为空可能是测试数据问题，不一定是功能 Bug",
    "iframe 内容不在当前 AX Tree 中显示是技术限制，不是页面空白 Bug",
]

REVIEW_SYSTEM_PROMPT = """你是一个专业的测试审查员（QA Reviewer）。你的职责是独立判断测试执行者报告的 Bug 是否为真。

## 判断原则

1. **证据优先**：只基于提供的客观证据（页面状态、Console 错误、Network 错误）做判断
2. **排除技术限制**：以下情况不是 Bug：
   - AX Tree 无法穿透 iframe/Canvas/WebGL 导致的"页面空白"
   - 新标签页下载/打开外部链接后主页面看似无响应
   - Popover/下拉菜单在未触发时不可见
   - 页面跳转后元素 ref 编号变化
3. **怀疑操作问题**：如果 Agent 的操作本身可能不正确（如 fill 未触发 Vue 事件），优先怀疑操作问题而非产品 Bug
4. **严格标准**：只有在有明确证据（错误码、异常文本、功能明确缺失）时才确认为 Bug
5. **区分环境问题**：测试环境数据不足、网络超时等不算产品 Bug

## 已知误报模式（历史经验）

""" + "\n".join(f"- {p}" for p in KNOWN_FALSE_POSITIVE_PATTERNS)


class ReviewerAgent:
    """独立于 Executor 的测试审查员"""

    def __init__(self, api_key=None, base_url=None, model=None):
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model or LLM_MODEL
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.review_count = 0
        self.rejected_count = 0

    def review_bug(self, issue: dict, page_state: dict, evidence: dict,
                   action_context: str = "") -> dict:
        """
        审查单个 Bug 报告是否为真。

        Args:
            issue: Agent 报告的 issue {severity, title, description}
            page_state: 当前页面状态 {url, title, refs, text}
            evidence: 证据 {new_console_errors, new_network_errors}
            action_context: 最近操作的简要描述

        Returns:
            {
                "is_valid": bool,       # 是否为真 Bug
                "confidence": str,      # high / medium / low
                "reason": str,          # 判断理由
                "revised_severity": str  # 修正后的严重等级（可能降级）
            }
        """
        self.review_count += 1

        # 构建精简的审查 prompt
        refs_summary = json.dumps(page_state.get("refs", [])[:20], ensure_ascii=False)
        page_text = (page_state.get("text", "") or "")[:800]
        url = page_state.get("url", "")

        evidence_part = ""
        if evidence.get("new_console_errors"):
            evidence_part += f"\n- Console 错误: {json.dumps(evidence['new_console_errors'][:5], ensure_ascii=False)}"
        if evidence.get("new_network_errors"):
            evidence_part += f"\n- Network 错误: {json.dumps(evidence['new_network_errors'][:5], ensure_ascii=False)}"

        prompt = f"""请审查以下 Bug 报告是否为真实产品缺陷。

## Bug 报告
- 严重等级: {issue.get('severity', 'P2')}
- 标题: {issue.get('title', '')}
- 描述: {issue.get('description', '')}

## 当前页面状态
- URL: {url}
- 页面标题: {page_state.get('title', '')}
- 可交互元素(前20个): {refs_summary}
- 页面文本(前800字): {page_text}

## 客观证据{evidence_part or chr(10) + "- 无 Console/Network 错误"}

## 操作上下文
{action_context if action_context else "无"}

请判断这个 Bug 是否为真。只返回 JSON：
{{"is_valid": true/false, "confidence": "high/medium/low", "reason": "简短判断理由", "revised_severity": "P0/P1/P2/P3"}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=200,
                response_format={"type": "json_object"},
            )
            if response.usage:
                self.total_input_tokens += response.usage.prompt_tokens
                self.total_output_tokens += response.usage.completion_tokens

            content = response.choices[0].message.content.strip()
            result = json.loads(content)

            # 确保必要字段存在
            result.setdefault("is_valid", True)
            result.setdefault("confidence", "medium")
            result.setdefault("reason", "")
            result.setdefault("revised_severity", issue.get("severity", "P2"))

            if not result["is_valid"]:
                self.rejected_count += 1
                logger.info(f"Reviewer 否决 Bug: [{issue.get('severity')}] {issue.get('title')} | 原因: {result['reason']}")

            return result

        except Exception as e:
            logger.warning(f"Reviewer 调用失败: {e}")
            # 失败时保守放行（不阻止 Bug 报告）
            return {
                "is_valid": True,
                "confidence": "low",
                "reason": f"审查失败({str(e)[:50]})，保守放行",
                "revised_severity": issue.get("severity", "P2"),
            }

    def review_flow_completion(self, flow_name: str, flow_steps: list,
                               spec_excerpt: str = "") -> dict:
        """
        审查一个流程是否被充分测试。

        Args:
            flow_name: 流程名称
            flow_steps: 该流程的操作历史 [{step, action, result, checklist_item}]
            spec_excerpt: spec 中该流程相关的描述片段

        Returns:
            {
                "sufficiently_tested": bool,
                "coverage_estimate": str,  # e.g. "7/10"
                "missed_items": [str],     # 可能遗漏的测试点
                "false_positive_suspects": [str],  # 疑似误报的 Bug 标题
                "recommendation": str
            }
        """
        # 构建流程操作摘要
        steps_summary = []
        for s in flow_steps[-20:]:  # 最多看最近20步
            action = s.get("action", {})
            result = s.get("result", {})
            ci = s.get("checklist_item", "")
            steps_summary.append(
                f"  [{s.get('step')}] {action.get('type', '?')}: {ci} → {'✓' if result.get('success') else '✗'}"
            )

        prompt = f"""请审查以下测试流程是否被充分测试。

## 流程名称
{flow_name}

## 功能文档描述
{spec_excerpt[:1000] if spec_excerpt else "（无文档摘录）"}

## 实际执行的操作（共 {len(flow_steps)} 步）
{chr(10).join(steps_summary) if steps_summary else "（无操作记录）"}

请判断该流程的测试完整性。只返回 JSON：
{{
    "sufficiently_tested": true/false,
    "coverage_estimate": "N/M",
    "missed_items": ["可能遗漏的测试点1", "..."],
    "false_positive_suspects": ["可能是误报的 Bug 标题"],
    "recommendation": "简要建议"
}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个测试覆盖率审查员。评估测试流程的完整性，识别遗漏和可能的误报。只返回 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
            if response.usage:
                self.total_input_tokens += response.usage.prompt_tokens
                self.total_output_tokens += response.usage.completion_tokens

            content = response.choices[0].message.content.strip()
            result = json.loads(content)
            result.setdefault("sufficiently_tested", True)
            result.setdefault("coverage_estimate", "?/?")
            result.setdefault("missed_items", [])
            result.setdefault("false_positive_suspects", [])
            result.setdefault("recommendation", "")
            return result

        except Exception as e:
            logger.warning(f"Reviewer 流程审查失败: {e}")
            return {
                "sufficiently_tested": True,
                "coverage_estimate": "?/?",
                "missed_items": [],
                "false_positive_suspects": [],
                "recommendation": f"审查失败: {str(e)[:50]}",
            }

    def get_stats(self) -> dict:
        """返回 Reviewer 统计信息"""
        return {
            "review_count": self.review_count,
            "rejected_count": self.rejected_count,
            "rejection_rate": f"{self.rejected_count / max(self.review_count, 1) * 100:.1f}%",
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
        }
