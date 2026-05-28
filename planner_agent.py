"""
PlannerAgent - 将自然语言 spec 转为结构化测试计划

在测试开始前运行一次，产出 JSON 格式的测试计划，供 Executor 按计划逐步执行。
核心收益：
  1. 约束 Executor 只能按 spec 定义的流程执行，不创建额外流程
  2. 每步提供明确的 verify 条件，供 Reviewer 独立判断
  3. 减少 Executor prompt 中的 spec 原文注入，降低 token 消耗
"""

from __future__ import annotations

import json
import re
from openai import OpenAI
from logger import get_logger
from config import LLM_MODEL

logger = get_logger("planner_agent")

PLANNER_PROMPT = """你是一个测试计划生成器。你的任务是将功能预期文档转化为结构化的 JSON 测试计划。

## 输出格式

你必须返回一个严格的 JSON 对象，格式如下：

```json
{
  "flows": [
    {
      "id": "flow_1",
      "name": "流程一：商品浏览与分类筛选",
      "preconditions": ["已打开首页"],
      "steps": [
        {
          "id": "1.1",
          "description": "查看所有商品",
          "action_hint": "观察页面上的商品卡片列表",
          "verify": "页面显示多个商品卡片，每个卡片包含商品图片、名称和价格；页面上方有分类导航",
          "priority": "required"
        },
        {
          "id": "1.2",
          "description": "按分类筛选商品",
          "action_hint": "点击 Phones 分类链接",
          "verify": "商品列表只显示手机类商品，切换分类后商品列表正确更新",
          "priority": "required"
        }
      ]
    }
  ]
}
```

## 规则

1. **每个流程对应文档中的一个二级标题**（## 流程N）
2. **每个步骤对应文档中的一个三级标题**（### N.M）
3. `verify` 字段必须是**可观察的验证条件**，来自文档的"结果预期"部分
4. `action_hint` 是操作提示，帮助 Executor 快速理解要做什么
5. `priority` 为 "required"（必测）或 "optional"（有条件才测）
6. 如果文档标注了 [SKIP]，该步骤 priority 设为 "skip"
7. 保持文档中的原始流程顺序
8. 不要添加文档中没有的流程或步骤

只返回 JSON，不要其他内容。"""


class PlannerAgent:
    """将 spec 文档转化为结构化测试计划"""

    def __init__(self, api_key=None, base_url=None, model=None):
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        kwargs["timeout"] = 120
        self.client = OpenAI(**kwargs)
        self.model = model or LLM_MODEL
        self.total_tokens = 0

    def plan(self, spec_content: str) -> dict:
        """
        将 spec 文档转为结构化测试计划。
        返回 {"flows": [...], "total_steps": N} 或 fallback 的空计划。
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": PLANNER_PROMPT},
                    {"role": "user", "content": f"请将以下功能预期文档转化为结构化测试计划：\n\n{spec_content}"},
                ],
                temperature=0.1,
                max_tokens=8000,
            )
            content = response.choices[0].message.content or ""
            if hasattr(response, "usage") and response.usage:
                self.total_tokens += (response.usage.prompt_tokens or 0) + (response.usage.completion_tokens or 0)

            plan = self._parse_plan(content)
            total_steps = sum(len(f.get("steps", [])) for f in plan.get("flows", []))
            plan["total_steps"] = total_steps
            logger.info(f"Planner 生成计划: {len(plan.get('flows', []))} 个流程, {total_steps} 个步骤, tokens={self.total_tokens}")
            return plan

        except Exception as e:
            logger.error(f"Planner 调用失败: {e}")
            return {"flows": [], "total_steps": 0, "error": str(e)}

    def _repair_truncated_json(self, text: str) -> dict | None:
        """尝试修复被截断的 JSON（补全缺失的括号和引号）"""
        # 策略：逐步移除尾部不完整的内容，然后补全括号
        # 先移除尾部不完整的键值对（找最后一个完整的 } 或 ]）
        for i in range(len(text) - 1, max(0, len(text) - 200), -1):
            if text[i] in ('}', ']', '"'):
                candidate = text[:i+1]
                # 计算需要补全的括号
                open_braces = candidate.count('{') - candidate.count('}')
                open_brackets = candidate.count('[') - candidate.count(']')
                if open_braces >= 0 and open_brackets >= 0:
                    candidate += ']' * open_brackets + '}' * open_braces
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        continue
        return None

    def _parse_plan(self, content: str) -> dict:
        """健壮地解析 LLM 返回的计划 JSON"""
        cleaned = content.strip()
        # 去除 Markdown 代码围栏
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[\w]*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        # 修复 trailing comma
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)

        try:
            plan = json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试提取 JSON 对象
            match = re.search(r'\{[\s\S]*\}', cleaned)
            if match:
                try:
                    plan = json.loads(match.group())
                except json.JSONDecodeError:
                    # 可能是截断的 JSON，尝试修复（补全缺失的括号）
                    truncated = match.group()
                    plan = self._repair_truncated_json(truncated)
                    if plan is None:
                        logger.error(f"Planner JSON 解析失败: {cleaned[:500]}")
                        return {"flows": []}
            else:
                logger.error(f"Planner 输出无 JSON: {cleaned[:500]}")
                return {"flows": []}

        # 校验结构
        if "flows" not in plan or not isinstance(plan["flows"], list):
            logger.warning("Planner 输出缺少 flows 字段")
            return {"flows": []}

        # 确保每个 flow 和 step 有必要字段
        for flow in plan["flows"]:
            if "id" not in flow:
                flow["id"] = f"flow_{plan['flows'].index(flow) + 1}"
            if "steps" not in flow:
                flow["steps"] = []
            for step in flow["steps"]:
                if "verify" not in step:
                    step["verify"] = ""
                if "priority" not in step:
                    step["priority"] = "required"

        return plan

    def get_flow_names(self) -> list:
        """获取计划中的流程名称列表（用于约束 Agent 的 current_flow 字段）"""
        if not hasattr(self, '_last_plan'):
            return []
        return [f.get("name", f.get("id", "")) for f in self._last_plan.get("flows", [])]

    @staticmethod
    def get_current_step_context(plan: dict, flow_name: str, done_items: list) -> str:
        """
        根据当前流程名和已完成子节，返回当前应执行步骤的上下文。
        用于注入 Executor prompt，替代全文 spec。
        """
        if not plan or not plan.get("flows"):
            return ""

        target_flow = None
        for f in plan["flows"]:
            if f.get("name") == flow_name or f.get("id") == flow_name:
                target_flow = f
                break
        if not target_flow:
            return ""

        parts = [f"## 当前流程: {target_flow.get('name', '')}"]
        if target_flow.get("preconditions"):
            parts.append(f"前置条件: {', '.join(target_flow['preconditions'])}")

        # 标记已完成和待执行的步骤
        done_set = set(done_items)
        next_found = False
        next_step_id = ""
        for step in target_flow.get("steps", []):
            if step.get("priority") == "skip":
                parts.append(f"  ⏭ {step['id']} {step.get('description', '')} [SKIP]")
                continue
            # 简单匹配：done_items 中包含步骤描述的关键词
            is_done = any(step.get("description", "???") in d or step.get("id", "???") in d for d in done_set)
            if is_done:
                parts.append(f"  ✅ {step['id']} {step.get('description', '')}")
            elif not next_found:
                parts.append(f"  👉 {step['id']} {step.get('description', '')} ← 当前必须执行这一步")
                parts.append(f"     操作提示: {step.get('action_hint', '')}")
                parts.append(f"     验证条件: {step.get('verify', '')}")
                next_found = True
                next_step_id = step.get('id', '')
            else:
                parts.append(f"  ⬜ {step['id']} {step.get('description', '')}")

        # 强制顺序执行提示
        if next_step_id:
            parts.append(f"\n⚠️ 你必须严格按照上面的步骤顺序执行。当前应执行 {next_step_id}，不可跳过。")
            parts.append(f"在 checklist_item 中，请以步骤编号开头（如 \"{next_step_id} ...\"），以便系统追踪进度。")

        return "\n".join(parts)

    @staticmethod
    def get_current_pending_step(plan: dict, flow_name: str, done_items: list) -> dict | None:
        """
        返回当前流程中第一个未完成的步骤信息 (id, description, verify, action_hint)。
        如果全部完成则返回 None。
        """
        if not plan or not plan.get("flows"):
            return None
        target_flow = None
        for f in plan["flows"]:
            if f.get("name") == flow_name or f.get("id") == flow_name:
                target_flow = f
                break
        if not target_flow:
            return None

        done_set = set(done_items)
        for step in target_flow.get("steps", []):
            if step.get("priority") == "skip":
                continue
            is_done = any(step.get("description", "???") in d or step.get("id", "???") in d for d in done_set)
            if not is_done:
                return step
        return None  # 全部完成

    @staticmethod
    def count_remaining_steps(plan: dict, flow_name: str, done_items: list) -> int:
        """返回当前流程还剩多少步未完成"""
        if not plan or not plan.get("flows"):
            return 0
        target_flow = None
        for f in plan["flows"]:
            if f.get("name") == flow_name or f.get("id") == flow_name:
                target_flow = f
                break
        if not target_flow:
            return 0
        done_set = set(done_items)
        count = 0
        for step in target_flow.get("steps", []):
            if step.get("priority") == "skip":
                continue
            is_done = any(step.get("description", "???") in d or step.get("id", "???") in d for d in done_set)
            if not is_done:
                count += 1
        return count

    @staticmethod
    def get_allowed_flow_names(plan: dict) -> list:
        """返回计划中所有合法的流程名称"""
        if not plan or not plan.get("flows"):
            return []
        return [f.get("name", f.get("id", "")) for f in plan["flows"]]

    @staticmethod
    def get_next_flow(plan: dict, completed_flows: list, failed_flows: list) -> str:
        """根据已完成和失败的流程，返回下一个应测试的流程名"""
        if not plan or not plan.get("flows"):
            return ""
        done_set = set(completed_flows)
        fail_set = set(f.get("flow", "") if isinstance(f, dict) else f for f in failed_flows)
        for f in plan["flows"]:
            name = f.get("name", f.get("id", ""))
            if name not in done_set and name not in fail_set:
                return name
        return ""  # 全部完成
