"""
AI Web Tester - 轻量评估模块
在测试完成后对测试质量进行评估，计算关键指标。

指标体系：
  - goal_completion: 目标完成率（覆盖了多少文档中定义的流程）
  - action_efficiency: 操作效率（实际步数 vs 文档估算的最小步数）
  - bug_report_quality: Bug 报告质量（有 severity/flow/step 的 Bug 占比）
  - stuck_rate: 卡死率（因卡死被强制跳过或终止的比例）
  - flow_pass_rate: 流程通过率
"""

import re
from logger import get_logger

logger = get_logger("eval_engine")


def evaluate_test_run(
    spec_content: str,
    action_history: list,
    all_issues: list,
    completed_flows: list,
    failed_flows: list,
    total_steps: int,
    min_steps: int,
    token_usage: dict = None,
) -> dict:
    """
    评估一次测试运行的质量，返回指标字典。

    Args:
        spec_content: 功能预期文档原文
        action_history: 完整操作历史
        all_issues: 所有发现的 issue
        completed_flows: 已通过的流程名列表
        failed_flows: 失败的流程列表 [{"flow": ..., "reason": ...}]
        total_steps: 实际执行的总步数
        min_steps: 文档估算的最小步数
        token_usage: {"input_tokens": N, "output_tokens": N}

    Returns:
        包含各指标的 dict
    """
    metrics = {}

    # ---- 1. 目标完成率 ----
    spec_flows = _extract_flows_from_spec(spec_content)
    total_spec_flows = len(spec_flows) if spec_flows else 1
    touched_flows = set(completed_flows) | set(f["flow"] for f in failed_flows)
    # 从 action_history 补充
    for entry in action_history:
        f = entry.get("flow", "")
        if f and f != "initial_actions":
            touched_flows.add(f)

    metrics["goal_completion"] = {
        "spec_flows": spec_flows,
        "spec_flow_count": total_spec_flows,
        "touched_flows": list(touched_flows),
        "touched_count": len(touched_flows),
        "completed_flows": completed_flows,
        "completed_count": len(completed_flows),
        "coverage_ratio": round(len(touched_flows) / total_spec_flows, 2) if total_spec_flows else 0,
        "completion_ratio": round(len(completed_flows) / total_spec_flows, 2) if total_spec_flows else 0,
    }

    # ---- 2. 操作效率 ----
    effective_steps = sum(
        1 for a in action_history
        if a.get("action", {}).get("type") not in ("wait", "screenshot", "finish")
        and a.get("flow") != "initial_actions"
    )
    metrics["action_efficiency"] = {
        "total_steps": total_steps,
        "effective_steps": effective_steps,
        "min_steps_estimated": min_steps,
        "efficiency_ratio": round(min_steps / effective_steps, 2) if effective_steps else 0,
    }

    # ---- 3. Bug 报告质量 ----
    well_formed = 0
    for issue in all_issues:
        has_severity = bool(issue.get("severity"))
        has_flow = bool(issue.get("flow"))
        has_desc = len(issue.get("description", "")) > 10
        if has_severity and has_flow and has_desc:
            well_formed += 1

    total_issues = len(all_issues)
    # 排除系统自动生成的卡死 issue
    user_issues = [i for i in all_issues if "卡死" not in i.get("title", "")]
    metrics["bug_report_quality"] = {
        "total_issues": total_issues,
        "user_issues": len(user_issues),
        "well_formed_issues": well_formed,
        "quality_ratio": round(well_formed / total_issues, 2) if total_issues else 1.0,
    }

    # ---- 4. 卡死率 ----
    stuck_events = sum(
        1 for a in action_history
        if "卡死" in a.get("thinking", "") or "卡住" in a.get("thinking", "")
    )
    stuck_flows = [f for f in failed_flows if "卡死" in f.get("reason", "")]
    metrics["stuck_rate"] = {
        "stuck_events": stuck_events,
        "stuck_flows": len(stuck_flows),
        "stuck_ratio": round(stuck_events / total_steps, 2) if total_steps else 0,
    }

    # ---- 5. 流程通过率 ----
    all_known_flows = len(completed_flows) + len(failed_flows)
    metrics["flow_pass_rate"] = {
        "passed": len(completed_flows),
        "failed": len(failed_flows),
        "total": all_known_flows,
        "pass_ratio": round(len(completed_flows) / all_known_flows, 2) if all_known_flows else 0,
    }

    # ---- 6. Token 效率（如有） ----
    if token_usage:
        total_tokens = token_usage.get("input_tokens", 0) + token_usage.get("output_tokens", 0)
        metrics["token_efficiency"] = {
            "input_tokens": token_usage.get("input_tokens", 0),
            "output_tokens": token_usage.get("output_tokens", 0),
            "total_tokens": total_tokens,
            "tokens_per_step": round(total_tokens / total_steps) if total_steps else 0,
            "tokens_per_issue": round(total_tokens / total_issues) if total_issues else 0,
        }

    # ---- 综合评分 ----
    score = _compute_overall_score(metrics)
    metrics["overall_score"] = score

    logger.info(
        f"评估完成: score={score}/100 | "
        f"coverage={metrics['goal_completion']['coverage_ratio']} | "
        f"efficiency={metrics['action_efficiency']['efficiency_ratio']} | "
        f"pass_rate={metrics['flow_pass_rate']['pass_ratio']}"
    )

    return metrics


def _extract_flows_from_spec(spec_content: str) -> list:
    """从功能预期文档中提取流程名称列表（流程级别，非子步骤级别）"""
    flows = []
    # 匹配 "## 流程N：xxx" 或 "## 流程 N: xxx"（支持中文数字和阿拉伯数字）
    patterns = [
        r"^##\s*流程\s*[\d一二三四五六七八九十]+[：:]\s*(.+)",
        r"^##\s*(?:Flow|Test)\s*\d+[：:]\s*(.+)",
        r"^##\s+\d+\.\s*(.+)",  # 仅匹配 ## 开头（两个 #），不匹配 ###
    ]
    for pattern in patterns:
        matches = re.findall(pattern, spec_content, re.MULTILINE)
        if matches:
            flows = [m.strip() for m in matches]
            break

    # fallback: 所有二级标题（精确匹配 ## 而非 ###）
    if not flows:
        flows = re.findall(r"^##\s+(?!#)(.+)", spec_content, re.MULTILINE)
        flows = [f.strip() for f in flows if not f.startswith("⛔") and len(f) > 2
                 and not f.startswith("测试入口")]

    return flows


def _compute_overall_score(metrics: dict) -> int:
    """基于各指标计算 0-100 综合评分"""
    weights = {
        "coverage": 30,
        "completion": 25,
        "efficiency": 15,
        "pass_rate": 20,
        "bug_quality": 10,
    }

    coverage = metrics["goal_completion"]["coverage_ratio"]
    completion = metrics["goal_completion"]["completion_ratio"]
    efficiency = min(metrics["action_efficiency"]["efficiency_ratio"], 1.0)
    pass_rate = metrics["flow_pass_rate"]["pass_ratio"]
    bug_quality = metrics["bug_report_quality"]["quality_ratio"]

    # 卡死惩罚
    stuck_penalty = min(metrics["stuck_rate"]["stuck_ratio"] * 50, 15)

    raw = (
        coverage * weights["coverage"]
        + completion * weights["completion"]
        + efficiency * weights["efficiency"]
        + pass_rate * weights["pass_rate"]
        + bug_quality * weights["bug_quality"]
        - stuck_penalty
    )

    return max(0, min(100, round(raw)))


def format_eval_report(metrics: dict) -> str:
    """将评估结果格式化为 Markdown 报告片段"""
    lines = [
        "## 📊 测试质量评估",
        "",
        f"**综合评分：{metrics['overall_score']}/100**",
        "",
        "| 指标 | 数值 | 说明 |",
        "|------|------|------|",
        f"| 流程覆盖率 | {metrics['goal_completion']['coverage_ratio']*100:.0f}% | "
        f"触及 {metrics['goal_completion']['touched_count']}/{metrics['goal_completion']['spec_flow_count']} 个流程 |",
        f"| 流程完成率 | {metrics['goal_completion']['completion_ratio']*100:.0f}% | "
        f"通过 {metrics['goal_completion']['completed_count']}/{metrics['goal_completion']['spec_flow_count']} 个流程 |",
        f"| 流程通过率 | {metrics['flow_pass_rate']['pass_ratio']*100:.0f}% | "
        f"{metrics['flow_pass_rate']['passed']} 通过 / {metrics['flow_pass_rate']['failed']} 失败 |",
        f"| 操作效率 | {metrics['action_efficiency']['efficiency_ratio']*100:.0f}% | "
        f"有效步数 {metrics['action_efficiency']['effective_steps']}（估算最小 {metrics['action_efficiency']['min_steps_estimated']}）|",
        f"| Bug 报告质量 | {metrics['bug_report_quality']['quality_ratio']*100:.0f}% | "
        f"{metrics['bug_report_quality']['well_formed_issues']}/{metrics['bug_report_quality']['total_issues']} 条格式完整 |",
        f"| 卡死率 | {metrics['stuck_rate']['stuck_ratio']*100:.0f}% | "
        f"{metrics['stuck_rate']['stuck_events']} 次卡死事件 |",
    ]

    if "token_efficiency" in metrics:
        te = metrics["token_efficiency"]
        lines.append(
            f"| Token 消耗 | {te['total_tokens']:,} | "
            f"~{te['tokens_per_step']:,}/步 |"
        )

    lines.append("")
    return "\n".join(lines)
