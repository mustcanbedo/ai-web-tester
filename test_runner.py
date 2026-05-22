"""
AI Web Tester - 测试运行器
包含公共测试循环，run_test_task 和 resume_test_task 共用。
"""

import json
import shutil
import time
import queue
import datetime
import traceback
import re
import base64
import hashlib

from config import (
    SCREENSHOT_DIR, REPORT_DIR, LOG_DIR, SNAPSHOT_DIR, VIDEO_DIR,
    COOKIES_FILE, MAX_STEPS, STUCK_THRESHOLD, MIN_STEPS_BEFORE_FINISH,
    SUB_FLOW_STUCK_WINDOW, SUB_FLOW_STUCK_CLICK_RATIO, MAX_ACTIONS_PER_STEP,
)
from logger import get_logger
from playwright_bridge import PlaywrightBridge
from evidence import EvidenceCollector
from llm_engine import LLMEngine
from action_executor import ActionExecutor
from eval_engine import evaluate_test_run, format_eval_report

logger = get_logger("test_runner")


# ============================================================
# 页面状态提取
# ============================================================

def extract_page_state(bridge: PlaywrightBridge) -> dict:
    """通过 Playwright 获取页面状态，返回 Accessibility Tree refs + 可读文本"""
    try:
        return bridge.get_page_state()
    except Exception as e:
        return {"url": "", "title": "ERROR", "refs": [], "text": str(e)}


# ============================================================
# Cookie 管理
# ============================================================

def save_session_cookies(bridge):
    """测试结束后保存 cookies 到文件，下次测试可恢复登录态"""
    try:
        cookies = bridge.evaluate("JSON.stringify(document.cookie)")
        storage = bridge.evaluate("JSON.stringify(localStorage)")
        url = bridge.evaluate("window.location.origin")
        data = {"cookies_str": cookies, "localStorage": storage, "origin": url}
        if bridge._context:
            data["context_cookies"] = bridge._context.cookies()
        COOKIES_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        logger.info(f"已保存会话 cookies → {COOKIES_FILE}")
    except Exception as e:
        logger.warning(f"保存 cookies 失败: {e}")


def restore_session_cookies(bridge, target_url):
    """测试开始前恢复上次保存的 cookies，复用登录态"""
    if not COOKIES_FILE.exists():
        return False
    try:
        data = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        context_cookies = data.get("context_cookies", [])
        if context_cookies and bridge._context:
            bridge._context.add_cookies(context_cookies)
            logger.info(f"已恢复 {len(context_cookies)} 个 cookies")
            return True
    except Exception as e:
        logger.warning(f"恢复 cookies 失败: {e}")
    return False


# ============================================================
# 动态最小步数估算
# ============================================================

def estimate_min_steps(spec_content: str) -> int:
    """
    从功能预期文档中解析流程结构，动态计算最小步数。
    解析规则：
    - `## 流程N` → 流程
    - `### N.M` → 子节
    - `N. ` 开头的行 → 实际操作步骤（排除"预期结果"、"异常"等描述行）
    - 标有 [SKIP] 的章节不计入
    最终取：所有子节的操作步骤总数 × 0.6（并非每步都需要独立 action，
    如"预期结果"是观察而非操作），下限为 MIN_STEPS_BEFORE_FINISH 兜底值。
    """
    lines = spec_content.split("\n")
    total_action_steps = 0
    flow_count = 0
    in_skip = False
    in_result_section = False  # 在“结果预期”或“失败条件”下的编号行不计入
    current_section_steps = 0

    for line in lines:
        stripped = line.strip()
        # 检测流程级标题
        if stripped.startswith("## "):
            in_skip = "[SKIP]" in stripped or "[skip]" in stripped
            in_result_section = False
            if current_section_steps > 0:
                total_action_steps += current_section_steps
                current_section_steps = 0
            continue
        # 检测子节标题
        if stripped.startswith("### "):
            in_skip = in_skip or "[SKIP]" in stripped or "[skip]" in stripped
            in_result_section = False
            if current_section_steps > 0:
                total_action_steps += current_section_steps
                current_section_steps = 0
            flow_count += 1
            continue
        if in_skip:
            continue
        # 检测是否进入结果/失败描述区域（加粗标题行）
        if stripped.startswith("**") and any(kw in stripped for kw in ["结果预期", "失败条件", "预期结果", "异常"]):
            in_result_section = True
            continue
        if stripped.startswith("**") and any(kw in stripped for kw in ["预期步骤", "步骤", "操作"]):
            in_result_section = False
            continue
        if in_result_section:
            continue
        # 统计编号步骤行（如 "1. xxx"、"2. xxx"）
        if re.match(r"^\d+\.\s+", stripped):
            current_section_steps += 1

    # 别忘了最后一个子节
    total_action_steps += current_section_steps

    # 每个流程有基础开销（导航、等待、截图、验证），每流程 +3
    flow_overhead = flow_count * 3
    estimated = total_action_steps + flow_overhead
    fallback = 15  # 兖底值：再简单的测试也至少 15 步
    result = max(estimated, fallback)
    logger.info(f"文档解析：{total_action_steps} 个操作步骤 + {flow_count} 个流程×3 → 动态最小步数 = {result}（估算 {estimated}，兖底 {fallback}）")
    return result


# ============================================================
# 公共测试循环
# ============================================================

def _test_loop(
    task_id, task, llm, bridge, executor, evidence,
    action_history, all_issues, start_step, extra_context,
    augmented_spec, login_info, api_doc,
    current_flow_name, completed_flows,
    config, min_steps=None,
):
    """
    公共测试主循环，由 run_test_task 和 resume_test_task 共用。
    返回 (step, action_history, all_issues, data_checks)
    """
    log_queue = task["log_queue"]
    human_input_queue = task["human_input_queue"]

    def emit(event_type, data):
        log_queue.put({"type": event_type, "data": data, "timestamp": datetime.datetime.now().isoformat()})

    # 动态最小步数：优先用传入值，否则用配置兜底值
    effective_min_steps = min_steps if min_steps else MIN_STEPS_BEFORE_FINISH

    last_cc, last_nc, last_api = 0, 0, 0
    step = start_step
    consecutive_fails = 0
    consecutive_same = 0
    consecutive_waits = 0
    last_action_key = ""
    failed_flows = task.get("_failed_flows", [])  # [{"flow": "...", "reason": "..."}]
    consecutive_finish_rejects = 0  # 连续拒绝 finish 的次数（安全阀）

    # 子流程卡死检测（基于操作模式而非 checklist_item 文本）
    # SUB_FLOW_STUCK_WINDOW 和 SUB_FLOW_STUCK_CLICK_RATIO 从 config.py 导入
    stuck_skip_cooldown = 0  # 触发跳过后的冷却步数，避免连续注入
    stuck_flow_hit_count = {}  # {flow_name: int} 每个流程被检测到卡死的次数
    # State Hash：基于页面状态（URL + refs 签名）检测「操作无效果」
    recent_state_hashes = []  # 最近 N 步的 (url, refs_hash) 元组
    STATE_HASH_STUCK_THRESHOLD = 3  # 连续 N 步页面状态无变化视为卡死

    while step < MAX_STEPS:
        # 检查是否被取消
        if task.get("cancel_event") and task["cancel_event"].is_set():
            emit("log", {"message": "测试已被用户终止"})
            break
        # 检查是否暂停，阻塞等待恢复
        if task.get("pause_event"):
            task["pause_event"].wait()
        # 再次检查取消（从暂停恢复后可能已取消）
        if task.get("cancel_event") and task["cancel_event"].is_set():
            emit("log", {"message": "测试已被用户终止"})
            break

        step += 1
        emit("step_start", {"step": step, "max_steps": 0})

        # 采集证据
        evidence.collect()

        # 观察
        time.sleep(1)  # 等待页面渲染稳定
        page_state = extract_page_state(bridge)
        emit("log", {"message": f"页面状态: url={page_state.get('url','')}, refs={len(page_state.get('refs',[]))}个, text={len(page_state.get('text',''))}字符"})

        # State Hash：记录页面状态签名，用于检测「操作无效果」
        refs_sig = hashlib.md5(
            json.dumps([(r.get('role',''), r.get('name','')) for r in page_state.get('refs', [])], sort_keys=True).encode()
        ).hexdigest()[:8]
        current_state_hash = (page_state.get('url', ''), refs_sig)
        recent_state_hashes.append(current_state_hash)
        if len(recent_state_hashes) > 10:
            recent_state_hashes = recent_state_hashes[-10:]
        # 检测连续 N 步页面状态完全无变化 → 注入提醒
        if len(recent_state_hashes) >= STATE_HASH_STUCK_THRESHOLD:
            last_n = recent_state_hashes[-STATE_HASH_STUCK_THRESHOLD:]
            if len(set(last_n)) == 1 and step > start_step + STATE_HASH_STUCK_THRESHOLD:
                extra_context += (
                    f"\n⚠️ 系统检测到最近 {STATE_HASH_STUCK_THRESHOLD} 步操作后页面状态完全没有变化（URL 和元素列表均相同）。"
                    f"你的操作可能没有产生任何效果。请分析原因并尝试完全不同的操作方式，或跳过当前子步骤。"
                )
        new_evidence = evidence.get_new_evidence_since(last_cc, last_nc)
        last_cc = len(evidence.console_errors)
        last_nc = len(evidence.network_errors)

        recent_api = evidence.get_recent_api_responses(last_api)
        last_api = len(evidence.api_responses)

        # 检测连续重复失败，注入提醒
        if len(action_history) >= 2:
            last_two = action_history[-2:]
            if (not last_two[-1].get("result", {}).get("success")
                and not last_two[-2].get("result", {}).get("success")
                and last_two[-1].get("action") == last_two[-2].get("action")):
                extra_context += "\n⚠️ 你已经连续两次执行相同的操作且都失败了。请不要重复相同操作，分析失败原因后尝试不同的方法。"
        # 检测同一流程花费过多步数，提醒快速失败
        if current_flow_name and len(action_history) >= 15:
            flow_steps = sum(1 for a in action_history[-20:] if a.get("flow", "") == current_flow_name)
            if flow_steps >= 15:
                extra_context += f"\n⚠️ 你已经在「{current_flow_name}」上花费了 {flow_steps} 步以上。如果核心功能确实无法工作（如页面空白、按钮无响应），请将其标记为失败（flow_status=failed），记录 Bug，然后立即跳到下一个流程。不要反复尝试。"
        # 把上一步结果也告诉 LLM
        if action_history:
            last_entry = action_history[-1]
            last_result = last_entry.get("result", {})
            if not last_result.get("success"):
                extra_context += f"\n上一步操作失败: {last_result.get('message', '')}"

        # 思考
        emit("thinking", {"step": step, "message": "LLM 正在分析页面状态并决策..."})
        # 从 action_history 提取当前流程已完成的子节（去重）
        done_items_in_flow = []
        seen_items = set()
        for a in action_history:
            if a.get("flow") == (current_flow_name or flow):
                ci = a.get("checklist_item", "")
                ok = a.get("result", {}).get("success")
                if ci and ok and ci not in seen_items:
                    seen_items.add(ci)
                    done_items_in_flow.append(ci)
        flow_progress = {
            "completed_flows": completed_flows,
            "failed_flows": failed_flows,
            "current_flow": current_flow_name,
            "done_items_in_current_flow": done_items_in_flow[-20:],  # 最近 20 个
        }
        decision = llm.decide(page_state, new_evidence, step, extra_context, recent_api if recent_api else None, flow_progress=flow_progress)
        extra_context = ""
        emit("token_update", {"input": llm.total_input_tokens, "output": llm.total_output_tokens})

        thinking = decision.get("thinking", "")
        # 支持批量操作：优先读取 actions 数组，fallback 到 action 单字段
        action_list = decision.get("actions") or []
        if not isinstance(action_list, list) or not action_list:
            single = decision.get("action", {})
            if isinstance(single, list) and single:
                action_list = [a for a in single if isinstance(a, dict)]
            elif isinstance(single, dict):
                action_list = [single]
            else:
                action_list = [{"type": "wait", "params": {"ms": 1000}}]
        # 限制最多 4 个，防止 LLM 输出过多
        action_list = action_list[:MAX_ACTIONS_PER_STEP]
        action = action_list[0]  # 主 action 用于后续 flow/status 处理
        issues = decision.get("found_issues", [])
        if not isinstance(issues, list):
            issues = []
        flow = decision.get("current_flow", "")
        flow_status = decision.get("flow_status", "testing")
        should_continue = decision.get("should_continue", True)
        next_plan = decision.get("next_plan", "")
        checklist_item = decision.get("checklist_item", "")

        # ---- 子流程卡死检测（基于操作模式） ----
        # 不依赖 checklist_item 文本匹配（Agent 每步描述都不同），
        # 而是分析最近 N 步的实际行为模式：同一流程内反复 click 且页面无变化
        if stuck_skip_cooldown > 0:
            stuck_skip_cooldown -= 1
        elif len(action_history) >= SUB_FLOW_STUCK_WINDOW:
            recent = action_history[-SUB_FLOW_STUCK_WINDOW:]
            # 条件 1：最近 N 步都在同一个流程
            recent_flows = [a.get("flow", "") for a in recent]
            same_flow = len(set(f for f in recent_flows if f)) <= 1
            # 条件 2：大部分是 click 操作（排除 fill/assert 等有意义的操作）
            click_count = sum(1 for a in recent if a.get("action", {}).get("type") in ("click", "close_tab", "switch_tab"))
            click_ratio = click_count / len(recent)
            # 条件 3：有失败或包含 finish 被拒绝
            fail_count = sum(1 for a in recent if not a.get("result", {}).get("success"))
            finish_rejects = sum(1 for a in recent if a.get("action", {}).get("type") == "finish" and not a.get("result", {}).get("success"))
            # 条件 4：页面 URL 基本没变（Agent 没有导航到新页面）
            recent_urls = [a.get("result", {}).get("url", "") for a in recent if a.get("result", {}).get("url")]
            url_unchanged = len(set(recent_urls)) <= 2  # 允许最多 2 个不同 URL（如新标签页跳转后切回）

            # 条件 5（新增）：导航循环检测 — Agent 反复在 2 个 URL 间来回跳（即使全部成功）
            nav_loop = False
            if len(recent_urls) >= 4:
                # 检测 A→B→A→B 模式：URL 只有 2 种且交替出现
                unique_urls = set(recent_urls)
                nav_loop = len(unique_urls) == 2 and click_ratio >= 0.6

            is_stuck = (same_flow
                        and click_ratio >= SUB_FLOW_STUCK_CLICK_RATIO
                        and (fail_count >= 2 or finish_rejects >= 1 or nav_loop)
                        and url_unchanged)

            if is_stuck:
                stuck_flow = current_flow_name or flow
                stuck_flow_hit_count[stuck_flow] = stuck_flow_hit_count.get(stuck_flow, 0) + 1
                hits = stuck_flow_hit_count[stuck_flow]
                skip_msg = f"在「{stuck_flow}」中检测到操作模式卡死（第 {hits} 次，最近 {SUB_FLOW_STUCK_WINDOW} 步：{click_count} 次点击，{fail_count} 次失败）"
                logger.warning(f"[Step {step}] {skip_msg}")
                emit("log", {"message": f"🚫 {skip_msg}，系统强制跳过"})
                if hits >= 2:
                    # 同一流程第二次卡死，强制跳到下一个流程
                    extra_context += (
                        f"\n\n🚫🚫 **系统第 {hits} 次检测到你在「{stuck_flow}」卡死！**\n"
                        f"**你必须立即放弃整个「{stuck_flow}」**，执行以下操作：\n"
                        f"1. 将 flow_status 设为 failed，在 found_issues 中记录阻塞原因\n"
                        f"2. **立即切换到下一个流程**（如从流程三跳到流程四），更新 current_flow\n"
                        f"3. 不要再回到「{stuck_flow}」，不要 finish，开始执行下一个流程的第一个子节\n"
                    )
                else:
                    # 第一次卡死，建议跳子节
                    extra_context += (
                        f"\n\n🚫 **系统检测到你卡住了**（最近 {SUB_FLOW_STUCK_WINDOW} 步都在重复操作且无进展）。\n"
                        f"**你必须立即停止当前子节的尝试**，执行以下操作：\n"
                        f"1. 如果尚未记录，将当前阻塞问题记录到 found_issues\n"
                        f"2. 保持 flow_status=testing（单个子节失败不等于整个流程失败）\n"
                        f"3. **立即跳到当前流程的下一个子节**（如从 3.6 跳到 3.7）\n"
                        f"4. 不要再尝试当前子节，不要再点击同类按钮，不要 finish\n"
                        f"把剩余步数用于测试其他未覆盖的功能。"
                    )
                stuck_skip_cooldown = SUB_FLOW_STUCK_WINDOW  # 冷却 N 步

        # 流程切换检测：当 flow 名称变化时重置 LLM 上下文
        if flow and current_flow_name and flow != current_flow_name:
            # 统计当前流程实际执行了多少步
            flow_step_count = sum(1 for a in action_history if a.get("flow") == current_flow_name)
            if flow_status == "failed" or any(i.get("flow") == current_flow_name for i in all_issues):
                if not any(f["flow"] == current_flow_name for f in failed_flows):
                    reason = next((i.get("title", "") for i in all_issues if i.get("flow") == current_flow_name), "未知原因")
                    failed_flows.append({"flow": current_flow_name, "reason": reason})
            elif current_flow_name not in completed_flows:
                # LLM 标记流程为 passed 且至少执行了 1 步就标记为 completed
                if flow_step_count >= 1:
                    completed_flows.append(current_flow_name)
                    if flow_step_count < 3:
                        logger.info(f"[Step {step}] 流程 '{current_flow_name}' 仅执行了 {flow_step_count} 步，标记 completed（可能测试不够深入）")
            logger.debug(f"[Step {step}] 流程切换: '{current_flow_name}' → '{flow}'")
            emit("log", {"message": f"🔄 流程切换: {current_flow_name} → {flow}（上下文已重置）"})
            current_page_url = page_state.get("url", "")
            llm.reset_for_new_flow(augmented_spec, completed_flows, all_issues, current_page_url, login_info, api_doc)
        current_flow_name = flow if flow else current_flow_name

        # 发送 checklist 事件
        emit("checklist", {
            "step": step, "flow": flow, "item": checklist_item,
            "status": "running", "action_type": action.get("type", ""),
        })

        emit("decision", {
            "step": step, "thinking": thinking, "action": action,
            "flow": flow, "flow_status": flow_status, "next_plan": next_plan,
            "issues": issues, "page_url": page_state.get("url", ""),
        })

        if issues:
            existing_titles = {i.get("title", "") for i in all_issues}
            for issue in issues:
                title = issue.get("title", "")
                if title not in existing_titles:
                    issue["flow"] = current_flow_name
                    issue["step"] = step
                    all_issues.append(issue)
                    existing_titles.add(title)
                    emit("bug", {"severity": issue.get("severity"), "title": title, "description": issue.get("description")})

        # 处理人工输入请求
        if action.get("type") == "request_human_input":
            params = action.get("params", {})
            emit("waiting_human_input", {
                "message": f"Agent 请求人工输入: {params.get('title', '')}",
                "title": params.get("title", "需要您的输入"),
                "description": params.get("description", "请输入所需信息"),
                "placeholder": params.get("placeholder", "请输入"),
            })
            ss = executor.take_screenshot(step, "waiting_input")
            emit("screenshot", {"step": step, "filename": ss["filename"], "base64": ss["base64"], "label": "等待人工输入"})
            emit("checklist_update", {"step": step, "status": "waiting", "item": checklist_item})

            action_history.append({"step": step, "thinking": thinking, "action": action, "result": {"success": True, "message": "等待人工输入"}, "flow": flow, "flow_status": flow_status, "checklist_item": checklist_item})
            task["status"] = "waiting_input"
            try:
                human_value = human_input_queue.get(timeout=300)
                emit("log", {"message": f"收到人工输入，继续测试"})
                extra_context = f"用户已提供输入值：{human_value}。你的下一步操作必须是用 fill 动作将「{human_value}」填入对应的输入框（如验证码框），然后勾选必要的 checkbox，最后点击提交按钮。不要执行 wait。"
                task["status"] = "running"
            except queue.Empty:
                emit("log", {"message": "等待人工输入超时（5分钟），终止测试"})
                task["status"] = "running"
                break
            continue

        # 处理致命错误（余额不足等）— 立即终止
        if action.get("type") == "fatal_error":
            summary = action.get("params", {}).get("summary", "致命错误")
            logger.critical(f"[Step {step}] {summary}")
            emit("log", {"message": f"❌ 致命错误: {summary}，测试终止"})
            all_issues.append({"severity": "P0", "title": "API 致命错误", "description": summary, "flow": flow, "step": step})
            action_history.append({"step": step, "thinking": thinking, "action": action, "result": {"success": False, "message": summary}, "flow": flow, "flow_status": flow_status, "checklist_item": checklist_item})
            emit("step_result", {"step": step, "success": False, "message": summary, "action_type": "fatal_error"})
            break

        # 处理 finish
        if action.get("type") == "finish":
            # 检查步数下限（使用动态计算的最小步数）
            reject_reason = ""
            if step < effective_min_steps:
                reject_reason = f"步数不足（{step}/{effective_min_steps}，基于文档结构动态计算）"
            else:
                # 检查 1：从文档中提取所有流程名，对比是否有完全未触及的流程
                from eval_engine import _extract_flows_from_spec
                spec_flows = _extract_flows_from_spec(augmented_spec)
                # 排除 [SKIP] 标记的流程
                spec_flows = [f for f in spec_flows if '[SKIP]' not in f and '[skip]' not in f]
                touched_flows = set(completed_flows) | set(f.get("flow", "") for f in failed_flows) | set(a.get("flow", "") for a in action_history if a.get("flow"))
                untouched = [f for f in spec_flows if not any(f in t or t in f for t in touched_flows)]
                if untouched:
                    reject_reason = f"以下流程完全未测试：{', '.join(untouched)}。必须先测试这些流程才能结束"
                else:
                    # 检查 2：已触及的流程中是否有操作不足的（仅检查 spec 定义的流程）
                    from collections import Counter
                    flow_step_counts = Counter(a.get("flow", "") for a in action_history if a.get("flow"))
                    # 只检查与 spec 流程名有关联的流程，忽略 LLM 自创的流程名
                    if spec_flows:
                        relevant_flows = {f: c for f, c in flow_step_counts.items()
                                          if any(sf in f or f in sf for sf in spec_flows)}
                    else:
                        relevant_flows = dict(flow_step_counts)
                    # 排除已标记为 completed 或 failed 的流程（它们已经被充分处理）
                    done_flow_names = set(completed_flows) | set(ff.get("flow", "") for ff in failed_flows)
                    shallow_flows = [f for f, c in relevant_flows.items()
                                     if c < 3 and not any(f in d or d in f for d in done_flow_names)]
                    untested_ratio = len(shallow_flows) / max(len(relevant_flows), 1)
                    if shallow_flows and untested_ratio > 0.3:
                        reject_reason = f"流程覆盖不足：以下流程操作不足3步，疑似未深入测试：{', '.join(shallow_flows)}"
            if reject_reason:
                consecutive_finish_rejects += 1
                # 安全阀：连续拒绝 finish 超过 5 次，强制放行
                if consecutive_finish_rejects > 5:
                    logger.warning(f"[Step {step}] 连续拒绝 finish {consecutive_finish_rejects} 次，安全阀触发，强制允许终止")
                    emit("log", {"message": f"⚠️ 安全阀触发：连续 {consecutive_finish_rejects} 次拒绝 finish，强制终止测试"})
                    action_history.append({"step": step, "thinking": thinking, "action": action, "result": {"success": True, "message": "安全阀触发，强制终止"}, "flow": flow, "flow_status": flow_status, "checklist_item": checklist_item})
                    break
                logger.warning(f"[Step {step}] LLM 尝试过早结束: {reject_reason} (第 {consecutive_finish_rejects} 次拒绝)")
                # 统计当前流程已完成的子节数量
                done_in_flow = set()
                for a in action_history:
                    if a.get("flow") == flow and a.get("result", {}).get("success") and a.get("checklist_item"):
                        done_in_flow.add(a["checklist_item"])
                # 判断：当前流程是否已标记为 failed 且子节充分（>=5 个不同子节）
                current_flow_in_failed = any(f.get("flow") == flow for f in failed_flows)
                flow_exhausted = current_flow_in_failed and len(done_in_flow) >= 5
                if flow_exhausted or (flow_status == "failed" and len(done_in_flow) >= 5):
                    # 当前流程确实做了足够多的子节且被标记失败，允许跳到下一个流程
                    extra_context = (
                        f"系统拒绝了你的 finish 请求。原因：{reject_reason}。\n"
                        f"当前流程「{flow}」已标记失败且已测试 {len(done_in_flow)} 个子节。\n"
                        f"请跳到**下一个流程**（更新 current_flow），从该流程的第一个子节开始测试。"
                    )
                else:
                    # 当前流程还有未完成的子节，不要跳走
                    extra_context = (
                        f"系统拒绝了你的 finish 请求。原因：{reject_reason}。\n"
                        f"当前流程「{flow}」只完成了 {len(done_in_flow)} 个子节，还有很多子节未测试。\n"
                        f"**不要跳到下一个流程**，请继续测试当前流程中尚未完成的子节。\n"
                        f"遇到某个子节的问题（如页面加载失败），记录 Bug 后跳过该子节，继续测试同一流程的下一个子节。\n"
                        f"回顾功能预期文档，找到当前流程中还没执行的子步骤，逐一操作验证。"
                    )
                action_history.append({"step": step, "thinking": thinking, "action": action, "result": {"success": False, "message": f"系统拒绝: {reject_reason}"}, "flow": flow, "flow_status": flow_status, "checklist_item": checklist_item})
                emit("step_result", {"step": step, "success": False, "message": f"finish 被拒绝: {reject_reason}", "action_type": "finish"})
                step += 1
                continue
            # 最终截图
            ss = executor.take_screenshot(step, "finish")
            emit("screenshot", {"step": step, "filename": ss["filename"], "base64": ss["base64"], "label": "测试完成"})
            emit("checklist_update", {"step": step, "status": "passed", "item": checklist_item})
            action_history.append({"step": step, "thinking": thinking, "action": action, "result": {"success": True, "message": "测试结束"}, "flow": flow, "flow_status": flow_status, "checklist_item": checklist_item})
            emit("step_result", {"step": step, "success": True, "message": action.get("params", {}).get("summary", "测试结束"), "action_type": "finish"})
            break

        # 执行操作（支持批量：action_list 可含 1~4 个 action）
        url_before = page_state.get("url", "")
        batch_results = []
        for act_idx, act in enumerate(action_list):
            logger.debug(f"[Step {step}] EXECUTE[{act_idx+1}/{len(action_list)}]: {act.get('type')} | params={json.dumps(act.get('params',{}), ensure_ascii=False)}")
            result = executor.execute(act)
            logger.debug(f"[Step {step}] RESULT[{act_idx+1}/{len(action_list)}]: success={result['success']} | {result['message']}")
            batch_results.append((act, result))
            # 批量中某个失败则停止后续
            if not result['success']:
                break
            # 如果 URL 发生变化（导航/跳转），停止后续批量操作
            try:
                url_now = bridge._page.url
                if url_now != url_before:
                    if act_idx < len(action_list) - 1:
                        logger.debug(f"[Step {step}] 批量操作中检测到 URL 变化，停止后续操作")
                    break
            except Exception:
                pass
        # 用最后一个实际执行的 action 和 result 作为本步的代表
        action, result = batch_results[-1]
        if len(batch_results) > 1:
            batch_msg = " → ".join(f"{a.get('type')}({'✓' if r['success'] else '✗'})" for a, r in batch_results)
            result["message"] = f"[批量{len(batch_results)}步] {batch_msg} | {result['message']}"

        # 每步操作后自动截图
        ss = executor.take_screenshot(step, action.get("type", ""))
        emit("screenshot", {
            "step": step, "filename": ss["filename"],
            "base64": ss["base64"], "label": checklist_item or result["message"],
        })
        # 实时推送当前页面截图到前端直播面板
        emit("live_screenshot", {"step": step, "base64": ss["base64"], "action": action.get("type", ""), "label": checklist_item or result["message"]})

        # 操作成功时重置连续 finish 拒绝计数器
        if result["success"]:
            consecutive_finish_rejects = 0

        # 更新 checklist 状态
        cl_status = "passed" if result["success"] else "failed"
        emit("checklist_update", {"step": step, "status": cl_status, "item": checklist_item})

        if action.get("type") == "extract_data" and result.get("extracted_data"):
            extra_context = f"数据提取结果：{json.dumps(result['extracted_data'], ensure_ascii=False)[:2000]}"
        if action.get("type") == "call_api" and result.get("api_response"):
            extra_context = f"API 调用结果：{json.dumps(result['api_response'], ensure_ascii=False)[:2000]}"
        if action.get("type") == "verify_data":
            emit("data_check", {"check_type": action.get("params", {}).get("check_type", ""), "description": action.get("params", {}).get("description", ""), "step": step})

        emit("step_result", {
            "step": step, "success": result["success"], "message": result["message"],
            "action_type": action.get("type"), "screenshot": result.get("screenshot"),
        })

        action_history.append({
            "step": step, "thinking": thinking, "action": action,
            "result": {"success": result["success"], "message": result["message"],
                       "url": bridge._page.url if bridge._page else ""},
            "flow": flow, "flow_status": flow_status, "next_plan": next_plan,
            "checklist_item": checklist_item,
            "batch_size": len(batch_results),
        })

        # ---- 智能终止检测 ----
        action_key = json.dumps(action, sort_keys=True, ensure_ascii=False)
        if action_key == last_action_key:
            consecutive_same += 1
        else:
            consecutive_same = 0
            last_action_key = action_key
        if not result["success"]:
            consecutive_fails += 1
        else:
            consecutive_fails = 0
        if action.get("type") == "wait":
            consecutive_waits += 1
        else:
            consecutive_waits = 0

        # 交替循环检测：检查最近 N 步是否形成 A→B→A→B 模式
        alternating_loop = False
        recent_keys = [json.dumps(h["action"], sort_keys=True, ensure_ascii=False) for h in action_history[-8:]]
        if len(recent_keys) >= 6:
            # 检测周期为 2 的循环（A→B→A→B→A→B）
            if (recent_keys[-1] == recent_keys[-3] == recent_keys[-5]
                and recent_keys[-2] == recent_keys[-4] == recent_keys[-6]
                and recent_keys[-1] != recent_keys[-2]):
                alternating_loop = True
            # 检测周期为 3 的循环（A→B→C→A→B→C）
            if (len(recent_keys) >= 6
                and recent_keys[-1] == recent_keys[-4]
                and recent_keys[-2] == recent_keys[-5]
                and recent_keys[-3] == recent_keys[-6]):
                alternating_loop = True

        stuck_reason = ""
        if consecutive_same >= STUCK_THRESHOLD:
            stuck_reason = f"连续 {consecutive_same} 次执行相同操作"
        elif consecutive_fails >= STUCK_THRESHOLD:
            stuck_reason = f"连续 {consecutive_fails} 次操作失败"
        elif consecutive_waits >= STUCK_THRESHOLD:
            stuck_reason = f"连续 {consecutive_waits} 次 wait"
        elif alternating_loop:
            stuck_reason = "检测到交替循环操作模式（如 click→fill→click→fill 反复）"

        if stuck_reason:
            # 第一次检测到卡死时，先给 LLM 一次警告机会，而非直接终止
            if not task.get("_stuck_warned"):
                task["_stuck_warned"] = True
                logger.warning(f"[Step {step}] 疑似卡死: {stuck_reason}，注入警告")
                emit("log", {"message": f"⚠️ 检测到疑似卡死: {stuck_reason}，给予最后机会"})
                extra_context = f"⚠️ 系统检测到你陷入了循环：{stuck_reason}。请立即停止重复操作！分析一下为什么之前的操作没有达到预期效果。如果是弹出对话框后 ref 编号变了，请注意：弹窗/对话框会导致页面 DOM 重新渲染，所有 ref 编号会重新分配。你必须根据当前步骤给你的最新 refs 列表来操作，不要用之前记住的 ref。如果某个功能反复尝试无法完成，请跳过该功能，继续测试下一个流程。"
            else:
                logger.warning(f"[Step {step}] 确认卡死: {stuck_reason}，强制终止")
                emit("log", {"message": f"⚠️ 智能终止: {stuck_reason}，停止测试"})
                all_issues.append({"severity": "P2", "title": f"测试卡死: {stuck_reason}", "description": f"在 Step {step} 检测到 {stuck_reason}，系统自动终止测试", "flow": current_flow_name, "step": step})
                break

        # 保存快照（用于断点续测）
        browser_state = {}
        try:
            cookie_result = bridge.evaluate("document.cookie")
            browser_state["cookies"] = cookie_result.get("result", "") if isinstance(cookie_result, dict) else str(cookie_result)
            ls_result = bridge.evaluate("JSON.stringify(localStorage)")
            browser_state["localStorage"] = ls_result.get("result", "{}") if isinstance(ls_result, dict) else str(ls_result)
        except Exception:
            pass
        snapshot = {
            "task_id": task_id, "config": {k: v for k, v in config.items() if k != "password"},
            "step": step, "messages": llm.messages, "action_history": action_history,
            "all_issues": all_issues, "last_url": page_state.get("url", ""),
            "token_usage": {"input": llm.total_input_tokens, "output": llm.total_output_tokens},
            "browser_state": browser_state,
            "completed_flows": completed_flows,
            "failed_flows": failed_flows,
        }
        snapshot_path = SNAPSHOT_DIR / f"snapshot_{task_id}.json"
        try:
            snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        task["snapshot_file"] = str(snapshot_path)

        if not should_continue:
            logger.warning(f"[Step {step}] LLM should_continue=false，强制跳过当前流程继续测试")
            emit("log", {"message": f"⚠️ 当前流程遇到问题，自动跳过并继续下一个流程"})
            extra_context = "系统拒绝了你的终止请求。当前流程失败不代表整个测试结束。请立即跳过当前失败的流程，继续测试功能预期文档中的下一个流程。不要因为一个流程无法完成就放弃整个测试。"
    else:
        emit("log", {"message": f"已达到安全上限 {MAX_STEPS} 步，强制终止"})

    # 将流程状态保存到 task，供 _finalize_test 评估使用
    task["_completed_flows"] = completed_flows
    task["_failed_flows"] = failed_flows
    task["_min_steps"] = min_steps or 0

    return step, action_history, all_issues, executor.data_checks


# ============================================================
# 公共的 finally 收尾：截图、保存视频、生成报告、emit complete
# ============================================================

def _finalize_test(task_id, task, llm, bridge, executor, evidence,
                   step, action_history, all_issues, data_checks, config):
    """公共的测试收尾逻辑"""
    log_queue = task["log_queue"]

    def emit(event_type, data):
        log_queue.put({"type": event_type, "data": data, "timestamp": datetime.datetime.now().isoformat()})

    # 最终截图
    try:
        final_b64 = bridge.screenshot(quality=80)
        final_path = SCREENSHOT_DIR / f"final_{task_id}.jpg"
        with open(final_path, "wb") as f:
            f.write(base64.b64decode(final_b64))
        emit("screenshot", {"step": step, "filename": f"final_{task_id}.jpg", "base64": final_b64, "label": "最终页面状态"})
    except Exception:
        pass

    # 保存 cookies + 关闭浏览器 + 保存视频
    save_session_cookies(bridge)
    video_path = bridge.get_video_path()
    try:
        bridge.close_tab()
    except (ValueError, Exception):
        pass  # 只剩一个标签页时跳过，stop_instance 会统一关闭
    bridge.stop_instance()
    video_filename = None
    if video_path:
        try:
            video_filename = f"video_{task_id}.webm"
            dest = VIDEO_DIR / video_filename
            shutil.move(str(video_path), str(dest))
            logger.info(f"测试视频已保存 → {dest}")
        except Exception as e:
            logger.warning(f"视频保存失败: {e}")
            video_filename = None

    # 生成报告
    spec_content = config.get("spec_content", "")
    need_login = config.get("need_login", False)
    login_type = config.get("login_type", "")

    emit("status", {"status": "generating_report", "message": "正在生成测试报告..."})
    report = llm.generate_report(action_history, all_issues, evidence.get_summary(), spec_content, login_type if need_login else "", data_checks if data_checks else None)

    report_filename = f"report_{task_id}.md"
    report_path = REPORT_DIR / report_filename

    # ---- 测试质量评估 ----
    try:
        eval_metrics = evaluate_test_run(
            spec_content=spec_content,
            action_history=action_history,
            all_issues=all_issues,
            completed_flows=task.get("_completed_flows", []),
            failed_flows=task.get("_failed_flows", []),
            total_steps=step,
            min_steps=task.get("_min_steps", 0),
            token_usage={"input_tokens": llm.total_input_tokens, "output_tokens": llm.total_output_tokens},
        )
        eval_report_section = format_eval_report(eval_metrics)
        report = report + "\n\n" + eval_report_section
        emit("log", {"message": f"📊 测试质量评分：{eval_metrics['overall_score']}/100"})
    except Exception as e:
        logger.warning(f"评估模块执行失败: {e}", exc_info=True)
        eval_metrics = {}

    report_path.write_text(report, encoding="utf-8")

    log_filename = f"log_{task_id}.json"
    log_path = LOG_DIR / log_filename
    log_data = {
        "task_id": task_id, "config": {k: v for k, v in config.items() if k != "password"},
        "start_time": task["start_time"], "end_time": datetime.datetime.now().isoformat(),
        "total_steps": step, "action_history": action_history, "all_issues": all_issues,
        "evidence_summary": evidence.get_summary(), "data_checks": data_checks,
        "token_usage": {"input_tokens": llm.total_input_tokens, "output_tokens": llm.total_output_tokens},
        "eval_metrics": eval_metrics,
    }
    log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")

    task["status"] = "completed"
    task["report"] = report
    task["report_file"] = report_filename
    task["total_steps"] = step
    task["issues_count"] = len(all_issues)
    task["token_usage"] = {"input": llm.total_input_tokens, "output": llm.total_output_tokens}
    task["video_file"] = video_filename

    emit("complete", {
        "status": "completed", "report_file": report_filename,
        "total_steps": step, "issues_count": len(all_issues),
        "token_usage": task["token_usage"],
        "report_content": report,
        "video_file": video_filename,
        "eval_score": eval_metrics.get("overall_score"),
    })


# ============================================================
# 入口一：全新测试
# ============================================================

def run_test_task(task_id: str, config: dict, tasks: dict):
    task = tasks[task_id]
    log_queue = task["log_queue"]

    spec_content = config["spec_content"]
    target_url = config.get("target_url", "")
    need_login = config.get("need_login", False)
    login_type = config.get("login_type", "password")
    login_url = config.get("login_url", "") or target_url
    username = config.get("username", "")
    password = config.get("password", "")
    phone = config.get("phone", "")

    def emit(event_type, data):
        log_queue.put({"type": event_type, "data": data, "timestamp": datetime.datetime.now().isoformat()})

    try:
        task["status"] = "running"
        emit("status", {"status": "running", "message": "测试启动中..."})

        llm_api_key = config.get("llm_api_key", "").strip() or None
        llm_base_url = config.get("llm_base_url", "").strip() or None
        llm_model = config.get("llm_model", "").strip() or None
        llm = LLMEngine(api_key=llm_api_key, base_url=llm_base_url, model=llm_model)
        evidence = EvidenceCollector()
        action_history = []
        all_issues = []

        login_info = ""
        if need_login:
            if login_type == "password":
                login_info = f"""登录方式：账号密码登录
登录页地址：{login_url}
用户名：{username}
密码：{password}
请先导航到登录页，找到用户名和密码输入框，填入上述信息并提交登录。
登录成功后再开始测试核心功能。"""
            elif login_type == "sms":
                login_info = f"""登录方式：手机验证码登录
国家/地区：中国（+86）
手机号（纯数字，不含区号前缀）：{phone}
目标：完成手机验证码登录。
注意事项：
- 如果页面有国家/区号选择器，确认当前区号是 +86（中国），如果不是则需要先切换
- 手机号输入框中只填纯数字 {phone}，不要加任何 + 号或区号前缀
- 验证码需要通过 request_human_input 向用户获取
- 每一步操作后观察页面变化，根据实际页面状态决定下一步"""

        augmented_spec = spec_content
        if target_url and "http" not in spec_content[:200]:
            augmented_spec = f"## 测试入口\n- URL：{target_url}\n\n{spec_content}"

        api_doc = config.get("api_doc", "")
        llm.init_conversation(augmented_spec, login_info, api_doc)
        emit("log", {"message": f"LLM 对话已初始化 | 目标: {target_url} | 登录: {'需要(' + login_type + ')' if need_login else '不需要'}"})

        # 每次测试创建独立浏览器实例，开启视频录制
        bridge = PlaywrightBridge(headless=True, video_dir=str(VIDEO_DIR))
        try:
            bridge.start_server()
            emit("log", {"message": "Playwright 浏览器已就绪（视频录制已开启）"})
            if restore_session_cookies(bridge, target_url):
                emit("log", {"message": "已恢复上次登录态（cookies）"})

            bridge.open_tab(target_url or "about:blank")
            evidence.attach(bridge)
            executor = ActionExecutor(bridge, SCREENSHOT_DIR, task_id)
            # SSRF 防护：设置允许的 origin
            if target_url:
                from urllib.parse import urlparse
                parsed = urlparse(target_url)
                executor.allowed_origin = f"{parsed.scheme}://{parsed.netloc}"

            emit("log", {"message": "浏览器已启动（Playwright headless 模式），开始测试循环"})

            # ---- initial_actions: 确定性前置步骤（登录等），跳过 LLM ----
            initial_actions = config.get("initial_actions", [])
            init_summary_parts = []
            if initial_actions:
                emit("log", {"message": f"⚡ 执行 {len(initial_actions)} 个前置确定性步骤（跳过 LLM）"})
                for ia_idx, ia in enumerate(initial_actions):
                    ia_type = ia.get("type", "unknown")
                    ia_params = ia.get("params", {})
                    ia_action = {"type": ia_type, "params": ia_params}
                    logger.info(f"[initial_action {ia_idx+1}/{len(initial_actions)}] {ia_type} | {ia_params}")
                    ia_result = executor.execute(ia_action)
                    emit("log", {"message": f"  [{ia_idx+1}] {ia_type}: {ia_result['message']}"})
                    action_history.append({
                        "step": ia_idx, "thinking": "前置确定性步骤（跳过 LLM）",
                        "action": ia_action, "result": ia_result,
                        "flow": "initial_actions", "flow_status": "testing",
                        "checklist_item": f"前置步骤: {ia_type}",
                    })
                    init_summary_parts.append(f"{ia_type}({ia_result['message']})")
                    if not ia_result["success"]:
                        emit("log", {"message": f"⚠️ 前置步骤 [{ia_idx+1}] 失败，继续执行后续步骤"})
                    time.sleep(0.3)
                emit("log", {"message": f"✅ 前置步骤执行完毕（{len(initial_actions)} 步）"})

            # 若有 initial_actions，告知 LLM 哪些步骤已完成
            extra_context = ""
            if init_summary_parts:
                extra_context = (
                    f"系统已自动完成以下前置操作（登录/导航等），你不需要重复执行：\n"
                    + "\n".join(f"- {s}" for s in init_summary_parts)
                    + "\n请直接开始测试核心功能。"
                )

            dynamic_min_steps = estimate_min_steps(spec_content)
            emit("log", {"message": f"📊 文档动态分析：最小测试步数 = {dynamic_min_steps}"})

            start_step = len(initial_actions) if initial_actions else 0
            step, action_history, all_issues, data_checks = _test_loop(
                task_id=task_id, task=task, llm=llm, bridge=bridge,
                executor=executor, evidence=evidence,
                action_history=action_history, all_issues=all_issues,
                start_step=start_step, extra_context=extra_context,
                augmented_spec=augmented_spec, login_info=login_info, api_doc=api_doc,
                current_flow_name="", completed_flows=[],
                config=config, min_steps=dynamic_min_steps,
            )
        finally:
            if 'executor' in dir() or 'executor' in locals():
                _finalize_test(task_id, task, llm, bridge, executor, evidence,
                               step if 'step' in locals() else 0,
                               action_history, all_issues,
                               data_checks if 'data_checks' in locals() else [],
                               config)
            else:
                # executor 未初始化，只清理 bridge
                try:
                    bridge.close()
                except Exception:
                    pass
                task["status"] = "error"
                task["error"] = "测试初始化失败（浏览器/页面加载异常）"

    except Exception as e:
        logger.critical("测试异常终止", exc_info=True)
        task["status"] = "error"
        task["error"] = str(e)
        emit("error", {"message": f"测试异常终止: {str(e)}"})


# ============================================================
# 入口二：从快照恢复测试
# ============================================================

def resume_test_task(task_id: str, snapshot: dict, rollback_steps: int, tasks: dict):
    """从快照恢复测试：精简上下文，只注入流程级状态，不恢复旧对话历史"""
    task = tasks[task_id]
    log_queue = task["log_queue"]
    config = task["config"]
    original_task_id = snapshot["task_id"]

    def emit(event_type, data):
        log_queue.put({"type": event_type, "data": data, "timestamp": datetime.datetime.now().isoformat()})

    try:
        # 从快照读取流程级状态（精简）
        saved_action_history = snapshot["action_history"]
        saved_issues = snapshot.get("all_issues", [])
        saved_step = snapshot["step"]
        saved_url = snapshot.get("last_url", "")
        completed_flows = snapshot.get("completed_flows", [])
        failed_flows = snapshot.get("failed_flows", [])

        # 如果快照没有流程状态（旧格式快照），从 action_history 推断
        if not completed_flows and not failed_flows:
            for entry in saved_action_history:
                f = entry.get("flow", "")
                if f and entry.get("flow_status") == "passed" and f not in completed_flows:
                    completed_flows.append(f)

        # action_history 保留用于流程状态判断和 checklist 回放，但不注入 LLM 上下文
        action_history = saved_action_history
        # 清空旧 issues：恢复测试会重新验证失败流程，旧 Bug 可能已修复，不应沿用
        all_issues = []

        # 将 failed_flows 存入 task 供 _test_loop 继续追踪
        task["_failed_flows"] = failed_flows

        task["status"] = "running"
        emit("status", {"status": "running", "message": f"从快照恢复测试（原任务 {original_task_id}，Step {saved_step}）"})

        # 构建精简的流程状态摘要
        flow_summary_parts = []
        if completed_flows:
            flow_summary_parts.append(f"已通过的流程（跳过）：{', '.join(completed_flows)}")
        if failed_flows:
            failed_desc = "; ".join([f"{f['flow']}（原因：{f['reason']}）" for f in failed_flows])
            flow_summary_parts.append(f"未通过需复测的流程：{failed_desc}")
        flow_summary = "\n".join(flow_summary_parts) if flow_summary_parts else "无历史流程记录"

        emit("log", {"message": f"🔄 恢复测试 | 已通过: {len(completed_flows)} 个流程 | 需复测: {len(failed_flows)} 个流程"})

        # 回放已完成的 checklist 给前端
        for entry in action_history:
            emit("checklist", {
                "step": entry["step"],
                "flow": entry.get("flow", ""),
                "item": entry.get("checklist_item", ""),
                "status": "passed" if entry.get("result", {}).get("success") else "failed",
                "action_type": entry.get("action", {}).get("type", ""),
            })

        # 构建上下文
        spec_content = config.get("spec_content", "")
        target_url = config.get("target_url", "")
        need_login = config.get("need_login", False)
        login_type = config.get("login_type", "password")
        login_info = ""
        if need_login:
            if login_type == "sms":
                login_info = f"手机验证码登录，手机号：{config.get('phone', '')}"
            else:
                login_info = f"账号密码登录，用户名：{config.get('username', '')}"
        api_doc = config.get("api_doc", "")
        augmented_spec = spec_content
        if target_url and "http" not in spec_content[:200]:
            augmented_spec = f"## 测试入口\n- URL：{target_url}\n\n{spec_content}"

        # 初始化 LLM：全新对话，不恢复旧历史，只注入流程级状态
        llm_api_key = config.get("llm_api_key", "").strip() or None
        llm_base_url = config.get("llm_base_url", "").strip() or None
        llm_model = config.get("llm_model", "").strip() or None
        llm = LLMEngine(api_key=llm_api_key, base_url=llm_base_url, model=llm_model)
        llm.init_conversation(augmented_spec, login_info, api_doc)

        evidence = EvidenceCollector()
        bridge = PlaywrightBridge(headless=True, video_dir=str(VIDEO_DIR))
        try:
            bridge.start_server()
            emit("log", {"message": "Playwright 浏览器已就绪（视频录制已开启）"})
            if restore_session_cookies(bridge, saved_url or target_url):
                emit("log", {"message": "已恢复上次登录态（cookies）"})

            navigate_url = saved_url or target_url or "about:blank"
            bridge.open_tab(navigate_url)
            evidence.attach(bridge)
            executor = ActionExecutor(bridge, SCREENSHOT_DIR, task_id)
            # SSRF 防护
            if target_url:
                from urllib.parse import urlparse
                parsed = urlparse(target_url)
                executor.allowed_origin = f"{parsed.scheme}://{parsed.netloc}"

            # 恢复浏览器状态（cookies + localStorage）以保持登录态
            browser_state = snapshot.get("browser_state", {})
            session_restored = False
            if browser_state:
                try:
                    ls_data = browser_state.get("localStorage", "{}")
                    if ls_data and ls_data != "{}":
                        escaped = ls_data.replace("\\", "\\\\").replace("'", "\\'")
                        bridge.evaluate(f"try {{ var d = JSON.parse('{escaped}'); for (var k in d) localStorage.setItem(k, d[k]); }} catch(e) {{}}")
                        emit("log", {"message": "✅ 已恢复 localStorage（登录 token 等）"})
                        session_restored = True
                    cookies_str = browser_state.get("cookies", "")
                    if cookies_str:
                        for cookie in cookies_str.split("; "):
                            if "=" in cookie:
                                bridge.evaluate(f"document.cookie = '{cookie}; path=/';")
                        emit("log", {"message": "✅ 已恢复 cookies"})
                        session_restored = True
                    if session_restored:
                        bridge.evaluate("location.reload()")
                        time.sleep(2)
                        emit("log", {"message": "页面已刷新，登录态应已恢复"})
                except Exception as e:
                    logger.warning(f"恢复浏览器状态失败: {e}")
                    emit("log", {"message": f"⚠️ 恢复浏览器状态失败: {e}"})

            emit("log", {"message": f"浏览器已导航到 {navigate_url}，开始复测"})

            # 构建精简的恢复上下文（注入给 LLM 的第一条 extra_context）
            resume_parts = [f"这是一次恢复测试（复测）。上次测试在 Step {saved_step} 结束。"]
            resume_parts.append(f"\n{flow_summary}")
            if session_restored:
                resume_parts.append("\n浏览器登录状态已从快照恢复，你应该仍然处于登录状态。")
            else:
                resume_parts.append("\n注意：浏览器是全新启动的，登录状态可能已丢失。如需要请先重新登录。")
            if failed_flows:
                resume_parts.append("\n请从第一个未通过的流程开始复测。已通过的流程可以跳过。")
            else:
                resume_parts.append("\n请继续测试上次未完成的流程。已通过的流程可以跳过。")
            extra_context = "".join(resume_parts)

            # 确定起始流程
            current_flow_name = ""
            if failed_flows:
                current_flow_name = failed_flows[0]["flow"]
            elif completed_flows:
                current_flow_name = completed_flows[-1]

            dynamic_min_steps = estimate_min_steps(spec_content)
            emit("log", {"message": f"📊 文档动态分析：最小测试步数 = {dynamic_min_steps}"})

            step, action_history, all_issues, data_checks = _test_loop(
                task_id=task_id, task=task, llm=llm, bridge=bridge,
                executor=executor, evidence=evidence,
                action_history=action_history, all_issues=all_issues,
                start_step=saved_step, extra_context=extra_context,
                augmented_spec=augmented_spec, login_info=login_info, api_doc=api_doc,
                current_flow_name=current_flow_name, completed_flows=completed_flows,
                config=config, min_steps=dynamic_min_steps,
            )
        finally:
            _finalize_test(task_id, task, llm, bridge, executor, evidence,
                           step, action_history, all_issues, data_checks, config)

    except Exception as e:
        logger.critical("恢复测试异常终止", exc_info=True)
        task["status"] = "error"
        task["error"] = str(e)
        emit("error", {"message": f"恢复测试异常终止: {str(e)}"})


# ============================================================
# 入口三：脚本化测试（确定性操作 + AI 验证）
# ============================================================

def run_script_task(task_id: str, config: dict, tasks: dict):
    """
    脚本化测试入口：按 YAML 脚本顺序执行操作，AI 只做结果验证。
    操作路径确定，元素定位由策略函数决定，大幅提升稳定性。
    """
    from script_runner import ScriptRunner, AIVerifier, generate_script_report

    task = tasks[task_id]
    log_queue = task["log_queue"]

    target_url = config.get("target_url", "")
    script_file = config.get("script_file", "")

    def emit(event_type, data):
        log_queue.put({"type": event_type, "data": data, "timestamp": datetime.datetime.now().isoformat()})

    try:
        emit("status", {"status": "running", "message": "脚本化测试启动中..."})

        # 检查脚本文件
        from pathlib import Path
        script_path = Path(__file__).parent / "test_scripts" / script_file
        if not script_path.exists():
            raise FileNotFoundError(f"测试脚本不存在: {script_path}")

        # 初始化 AI 验证器
        llm_api_key = config.get("llm_api_key", "").strip() or None
        llm_base_url = config.get("llm_base_url", "").strip() or None
        llm_model = config.get("llm_model", "").strip() or None
        verifier = AIVerifier(api_key=llm_api_key, base_url=llm_base_url, model=llm_model)

        evidence = EvidenceCollector()

        # 启动浏览器
        bridge = PlaywrightBridge(headless=True, video_dir=str(VIDEO_DIR))
        bridge.start_server()
        emit("log", {"message": "Playwright 浏览器已就绪（视频录制已开启）"})

        if restore_session_cookies(bridge, target_url):
            emit("log", {"message": "已恢复上次登录态（cookies）"})

        bridge.open_tab(target_url or "about:blank")
        evidence.attach(bridge)

        # 创建共享的 ActionExecutor（含 SSRF 防护 + URL 黑名单）
        executor = ActionExecutor(bridge, SCREENSHOT_DIR, task_id)
        if target_url:
            from urllib.parse import urlparse
            parsed = urlparse(target_url)
            executor.allowed_origin = f"{parsed.scheme}://{parsed.netloc}"

        emit("log", {"message": f"📋 加载测试脚本: {script_file}"})

        # 提取 base_url
        base_url = target_url.rstrip("/") if target_url else ""

        # 执行脚本
        runner = ScriptRunner(bridge, verifier, evidence, executor, task_id, log_queue)
        task["status"] = "running"
        summary = runner.run_script(str(script_path), base_url=base_url)

        # 保存 cookies
        save_session_cookies(bridge)

        # 保存视频
        video_path = bridge.get_video_path()
        try:
            bridge.close_tab()
        except (ValueError, Exception):
            pass
        bridge.stop_instance()

        video_filename = None
        if video_path:
            try:
                video_filename = f"video_{task_id}.webm"
                dest = VIDEO_DIR / video_filename
                shutil.move(str(video_path), str(dest))
                logger.info(f"测试视频已保存 → {dest}")
            except Exception as e:
                logger.warning(f"视频保存失败: {e}")

        # 生成报告
        report = generate_script_report(summary)
        report_filename = f"report_{task_id}.md"
        report_path = REPORT_DIR / report_filename
        report_path.write_text(report, encoding="utf-8")

        # 保存 log
        log_filename = f"log_{task_id}.json"
        log_path = LOG_DIR / log_filename
        log_data = {
            "task_id": task_id, "mode": "script",
            "config": {k: v for k, v in config.items() if k != "password"},
            "start_time": task["start_time"],
            "end_time": datetime.datetime.now().isoformat(),
            "summary": summary,
        }
        log_path.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")

        task["status"] = "completed"
        task["report"] = report
        task["report_file"] = report_filename
        task["total_steps"] = summary["total_steps"]
        task["issues_count"] = len(summary["issues"])
        task["token_usage"] = summary.get("token_usage", {})
        task["video_file"] = video_filename

        emit("complete", {
            "status": "completed", "report_file": report_filename,
            "total_steps": summary["total_steps"],
            "passed": summary["passed"], "failed": summary["failed"],
            "issues_count": len(summary["issues"]),
            "token_usage": summary.get("token_usage", {}),
            "report_content": report,
            "video_file": video_filename,
        })

    except Exception as e:
        logger.critical("脚本化测试异常终止", exc_info=True)
        task["status"] = "error"
        task["error"] = str(e)
        emit("error", {"message": f"脚本化测试异常终止: {str(e)}"})
