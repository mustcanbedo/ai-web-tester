"""
AI Web Tester - 结构化记忆系统
独立于 LLM 上下文窗口的持久化记忆，不随消息裁剪而丢失。

三层记忆模型（参考 Mem0 2026）：
  - Episodic Memory：发生了什么（流程摘要、关键事件）
  - Semantic Memory：已知什么（已确认 Bug、页面模式）
  - Procedural Memory：应该怎么做（学到的操作策略）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("test_memory")


class TestMemory:
    """测试过程中的结构化记忆，永不随 LLM 上下文裁剪而丢失"""

    def __init__(self):
        # ---- Episodic Memory（情节记忆）— 发生了什么 ----
        self.flow_summaries: dict[str, str] = {}  # {flow_name: "流程摘要"}
        self.key_events: list[dict] = []           # 关键事件时间线

        # ---- Semantic Memory（语义记忆）— 已知什么 ----
        self.confirmed_bugs: list[dict] = []       # 已确认的 Bug
        self.rejected_bugs: list[dict] = []        # 被 Reviewer 否决的 Bug（避免重复报告）
        self.page_patterns: dict[str, str] = {}    # {url_pattern: "页面特征"}

        # ---- Procedural Memory（过程记忆）— 应该怎么做 ----
        self.learned_strategies: list[dict] = []   # 学到的操作策略

    def on_flow_complete(self, flow_name: str, steps: list, result: str,
                         issues_in_flow: list = None):
        """流程完成时记录摘要"""
        step_count = len(steps)
        successful = sum(1 for s in steps if s.get("result", {}).get("success"))
        checklist_items = [s.get("checklist_item", "") for s in steps if s.get("checklist_item")]
        unique_items = list(dict.fromkeys(checklist_items))  # 去重保序

        summary_parts = [f"共{step_count}步(成功{successful}步)"]
        if unique_items:
            summary_parts.append(f"测试项: {', '.join(unique_items[:8])}")
        if issues_in_flow:
            bug_titles = [i.get("title", "") for i in issues_in_flow]
            summary_parts.append(f"发现问题: {', '.join(bug_titles[:3])}")
        summary_parts.append(f"结果: {result}")

        self.flow_summaries[flow_name] = " | ".join(summary_parts)
        logger.debug(f"Memory: 记录流程摘要 [{flow_name}] = {self.flow_summaries[flow_name]}")

    def on_bug_confirmed(self, issue: dict):
        """Bug 经 Reviewer 确认后记录"""
        # 避免重复
        title = issue.get("title", "")
        if not any(b.get("title") == title for b in self.confirmed_bugs):
            self.confirmed_bugs.append(issue)

    def on_bug_rejected(self, issue: dict, reason: str):
        """Bug 被 Reviewer 否决后记录（避免 Agent 重复报告同类问题）"""
        self.rejected_bugs.append({
            "title": issue.get("title", ""),
            "reason": reason,
        })

    def on_action_failed_and_recovered(self, action: dict, error: str, recovery: str):
        """操作失败并成功恢复后，记录过程知识"""
        strategy = {
            "trigger": f"{action.get('type', '?')} 失败: {error[:100]}",
            "strategy": recovery[:200],
        }
        # 避免重复
        if not any(s["trigger"] == strategy["trigger"] for s in self.learned_strategies):
            self.learned_strategies.append(strategy)
            logger.debug(f"Memory: 学到新策略 - {strategy['trigger']} → {strategy['strategy']}")

    def on_key_event(self, step: int, event: str):
        """记录关键事件（如登录成功、页面跳转、发现重要信息）"""
        self.key_events.append({"step": step, "event": event})
        # 只保留最近 30 个关键事件
        if len(self.key_events) > 30:
            self.key_events = self.key_events[-30:]

    def get_context_for_step(self, current_flow: str = "", max_chars: int = 1500) -> str:
        """
        生成当前步骤需要的精简记忆上下文。
        这段文本会被注入到 LLM 的观察中，即使历史消息被裁剪也能保留关键信息。
        """
        parts = []

        # 已完成流程的摘要（核心：防止遗忘）
        if self.flow_summaries:
            parts.append("## 记忆：已完成流程")
            for name, summary in self.flow_summaries.items():
                parts.append(f"- ✅ {name}: {summary}")

        # 已确认的 Bug（避免重复报告）
        if self.confirmed_bugs:
            parts.append("## 记忆：已确认 Bug（不要重复报告）")
            for bug in self.confirmed_bugs[-8:]:
                parts.append(f"- [{bug.get('severity', 'P2')}] {bug.get('title', '')}")

        # 被否决的 Bug（避免重复踩坑）
        if self.rejected_bugs:
            parts.append("## 记忆：已否决的误报（这些不是 Bug）")
            for rb in self.rejected_bugs[-5:]:
                parts.append(f"- ✗ {rb['title']}: {rb['reason']}")

        # 学到的策略
        if self.learned_strategies:
            parts.append("## 记忆：已知操作策略")
            for s in self.learned_strategies[-5:]:
                parts.append(f"- {s['trigger']} → {s['strategy']}")

        result = "\n".join(parts)

        # 截断保护
        if len(result) > max_chars:
            result = result[:max_chars] + "\n（记忆已截断）"

        return result

    def get_rejected_bug_titles(self) -> set:
        """返回所有被否决的 Bug 标题集合，用于快速过滤"""
        return {rb["title"] for rb in self.rejected_bugs}

    def save(self, path: str):
        """持久化到文件（跨会话复用）"""
        data = {
            "flow_summaries": self.flow_summaries,
            "key_events": self.key_events,
            "confirmed_bugs": self.confirmed_bugs,
            "rejected_bugs": self.rejected_bugs,
            "page_patterns": self.page_patterns,
            "learned_strategies": self.learned_strategies,
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Memory: 已保存到 {path}")

    def load(self, path: str) -> bool:
        """从文件加载历史记忆"""
        p = Path(path)
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            self.flow_summaries = data.get("flow_summaries", {})
            self.key_events = data.get("key_events", [])
            self.confirmed_bugs = data.get("confirmed_bugs", [])
            self.rejected_bugs = data.get("rejected_bugs", [])
            self.page_patterns = data.get("page_patterns", {})
            self.learned_strategies = data.get("learned_strategies", [])
            logger.info(f"Memory: 已从 {path} 加载（{len(self.flow_summaries)} 个流程摘要, {len(self.learned_strategies)} 条策略）")
            return True
        except Exception as e:
            logger.warning(f"Memory: 加载失败 - {e}")
            return False

    def get_stats(self) -> dict:
        """返回记忆统计"""
        return {
            "flow_summaries": len(self.flow_summaries),
            "confirmed_bugs": len(self.confirmed_bugs),
            "rejected_bugs": len(self.rejected_bugs),
            "learned_strategies": len(self.learned_strategies),
            "key_events": len(self.key_events),
        }
