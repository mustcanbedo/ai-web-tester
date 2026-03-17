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
import base64

from config import (
    SCREENSHOT_DIR, REPORT_DIR, LOG_DIR, SNAPSHOT_DIR, VIDEO_DIR,
    COOKIES_FILE, MAX_STEPS, STUCK_THRESHOLD, MIN_STEPS_BEFORE_FINISH,
)
from playwright_bridge import PlaywrightBridge
from evidence import EvidenceCollector
from llm_engine import LLMEngine
from action_executor import ActionExecutor


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
        print(f"[INFO] 已保存会话 cookies → {COOKIES_FILE}")
    except Exception as e:
        print(f"[WARN] 保存 cookies 失败: {e}")


def restore_session_cookies(bridge, target_url):
    """测试开始前恢复上次保存的 cookies，复用登录态"""
    if not COOKIES_FILE.exists():
        return False
    try:
        data = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
        context_cookies = data.get("context_cookies", [])
        if context_cookies and bridge._context:
            bridge._context.add_cookies(context_cookies)
            print(f"[INFO] 已恢复 {len(context_cookies)} 个 cookies")
            return True
    except Exception as e:
        print(f"[WARN] 恢复 cookies 失败: {e}")
    return False


# ============================================================
# 公共测试循环
# ============================================================

def _test_loop(
    task_id, task, llm, bridge, executor, evidence,
    action_history, all_issues, start_step, extra_context,
    augmented_spec, login_info, api_doc,
    current_flow_name, completed_flows,
    config,
):
    """
    公共测试主循环，由 run_test_task 和 resume_test_task 共用。
    返回 (step, action_history, all_issues, data_checks)
    """
    log_queue = task["log_queue"]
    human_input_queue = task["human_input_queue"]

    def emit(event_type, data):
        log_queue.put({"type": event_type, "data": data, "timestamp": datetime.datetime.now().isoformat()})

    last_cc, last_nc, last_api = 0, 0, 0
    step = start_step
    consecutive_fails = 0
    consecutive_same = 0
    consecutive_waits = 0
    last_action_key = ""

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
        # 把上一步结果也告诉 LLM
        if action_history:
            last_entry = action_history[-1]
            last_result = last_entry.get("result", {})
            if not last_result.get("success"):
                extra_context += f"\n上一步操作失败: {last_result.get('message', '')}"

        # 思考
        emit("thinking", {"step": step, "message": "LLM 正在分析页面状态并决策..."})
        decision = llm.decide(page_state, new_evidence, step, extra_context, recent_api if recent_api else None)
        extra_context = ""
        emit("token_update", {"input": llm.total_input_tokens, "output": llm.total_output_tokens})

        thinking = decision.get("thinking", "")
        action = decision.get("action", {})
        if isinstance(action, list) and len(action) > 0:
            remaining = action[1:]
            action = action[0] if isinstance(action[0], dict) else {"type": "wait", "params": {"ms": 1000}}
            if remaining:
                extra_context = f"你上一步计划了 {len(remaining)+1} 个操作，系统只执行了第一个。请在后续步骤中依次执行剩余操作：{json.dumps(remaining, ensure_ascii=False)[:500]}"
        elif not isinstance(action, dict):
            action = {"type": "wait", "params": {"ms": 1000}}
        issues = decision.get("found_issues", [])
        if not isinstance(issues, list):
            issues = []
        flow = decision.get("current_flow", "")
        flow_status = decision.get("flow_status", "testing")
        should_continue = decision.get("should_continue", True)
        next_plan = decision.get("next_plan", "")
        checklist_item = decision.get("checklist_item", "")

        # 流程切换检测：当 flow 名称变化时重置 LLM 上下文
        if flow and current_flow_name and flow != current_flow_name:
            if current_flow_name not in completed_flows:
                completed_flows.append(current_flow_name)
            print(f"[DEBUG][Step {step}] 流程切换: '{current_flow_name}' → '{flow}'")
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
            print(f"[FATAL][Step {step}] {summary}")
            emit("log", {"message": f"❌ 致命错误: {summary}，测试终止"})
            all_issues.append({"severity": "P0", "title": "API 致命错误", "description": summary})
            action_history.append({"step": step, "thinking": thinking, "action": action, "result": {"success": False, "message": summary}, "flow": flow, "flow_status": flow_status, "checklist_item": checklist_item})
            emit("step_result", {"step": step, "success": False, "message": summary, "action_type": "fatal_error"})
            break

        # 处理 finish
        if action.get("type") == "finish":
            if step < MIN_STEPS_BEFORE_FINISH:
                print(f"[WARN][Step {step}] LLM 尝试过早结束（< {MIN_STEPS_BEFORE_FINISH} 步），要求继续测试")
                extra_context = "系统拒绝了你的 finish 请求。功能预期文档中还有未测试的功能流程。请回顾文档，找到还没测试的流程继续测试。如果某个功能无法访问，请跳过它并测试下一个功能，不要结束整个测试。"
                action_history.append({"step": step, "thinking": thinking, "action": action, "result": {"success": False, "message": "系统拒绝提前结束，还有未测流程"}, "flow": flow, "flow_status": flow_status, "checklist_item": checklist_item})
                emit("step_result", {"step": step, "success": False, "message": "还有未测试的功能流程，继续测试", "action_type": "finish"})
                step += 1
                continue
            # 最终截图
            ss = executor.take_screenshot(step, "finish")
            emit("screenshot", {"step": step, "filename": ss["filename"], "base64": ss["base64"], "label": "测试完成"})
            emit("checklist_update", {"step": step, "status": "passed", "item": checklist_item})
            action_history.append({"step": step, "thinking": thinking, "action": action, "result": {"success": True, "message": "测试结束"}, "flow": flow, "flow_status": flow_status, "checklist_item": checklist_item})
            emit("step_result", {"step": step, "success": True, "message": action.get("params", {}).get("summary", "测试结束"), "action_type": "finish"})
            break

        # 执行操作
        print(f"[DEBUG][Step {step}] EXECUTE: {action.get('type')} | params={json.dumps(action.get('params',{}), ensure_ascii=False)}")
        result = executor.execute(action)
        print(f"[DEBUG][Step {step}] RESULT: success={result['success']} | {result['message']}")

        # 每步操作后自动截图
        ss = executor.take_screenshot(step, action.get("type", ""))
        emit("screenshot", {
            "step": step, "filename": ss["filename"],
            "base64": ss["base64"], "label": checklist_item or result["message"],
        })
        # 实时推送当前页面截图到前端直播面板
        emit("live_screenshot", {"step": step, "base64": ss["base64"], "action": action.get("type", ""), "label": checklist_item or result["message"]})

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
            "result": {"success": result["success"], "message": result["message"]},
            "flow": flow, "flow_status": flow_status, "next_plan": next_plan,
            "checklist_item": checklist_item,
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
                print(f"[WARN][Step {step}] 疑似卡死: {stuck_reason}，注入警告")
                emit("log", {"message": f"⚠️ 检测到疑似卡死: {stuck_reason}，给予最后机会"})
                extra_context = f"⚠️ 系统检测到你陷入了循环：{stuck_reason}。请立即停止重复操作！分析一下为什么之前的操作没有达到预期效果。如果是弹出对话框后 ref 编号变了，请注意：弹窗/对话框会导致页面 DOM 重新渲染，所有 ref 编号会重新分配。你必须根据当前步骤给你的最新 refs 列表来操作，不要用之前记住的 ref。如果某个功能反复尝试无法完成，请跳过该功能，继续测试下一个流程。"
            else:
                print(f"[WARN][Step {step}] 确认卡死: {stuck_reason}，强制终止")
                emit("log", {"message": f"⚠️ 智能终止: {stuck_reason}，停止测试"})
                all_issues.append({"severity": "P2", "title": f"测试卡死: {stuck_reason}", "description": f"在 Step {step} 检测到 {stuck_reason}，系统自动终止测试"})
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
        }
        snapshot_path = SNAPSHOT_DIR / f"snapshot_{task_id}.json"
        try:
            snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        task["snapshot_file"] = str(snapshot_path)

        if not should_continue:
            print(f"[WARN][Step {step}] LLM should_continue=false，强制跳过当前流程继续测试")
            emit("log", {"message": f"⚠️ 当前流程遇到问题，自动跳过并继续下一个流程"})
            extra_context = "系统拒绝了你的终止请求。当前流程失败不代表整个测试结束。请立即跳过当前失败的流程，继续测试功能预期文档中的下一个流程。不要因为一个流程无法完成就放弃整个测试。"
    else:
        emit("log", {"message": f"已达到安全上限 {MAX_STEPS} 步，强制终止"})

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
    except:
        pass

    # 保存 cookies + 关闭浏览器 + 保存视频
    save_session_cookies(bridge)
    video_path = bridge.get_video_path()
    bridge.close_tab()
    bridge.stop_instance()
    video_filename = None
    if video_path:
        try:
            video_filename = f"video_{task_id}.webm"
            dest = VIDEO_DIR / video_filename
            shutil.move(str(video_path), str(dest))
            print(f"[INFO] 测试视频已保存 → {dest}")
        except Exception as e:
            print(f"[WARN] 视频保存失败: {e}")
            video_filename = None

    # 生成报告
    spec_content = config.get("spec_content", "")
    need_login = config.get("need_login", False)
    login_type = config.get("login_type", "")

    emit("status", {"status": "generating_report", "message": "正在生成测试报告..."})
    report = llm.generate_report(action_history, all_issues, evidence.get_summary(), spec_content, login_type if need_login else "", data_checks if data_checks else None)

    report_filename = f"report_{task_id}.md"
    report_path = REPORT_DIR / report_filename
    report_path.write_text(report, encoding="utf-8")

    log_filename = f"log_{task_id}.json"
    log_path = LOG_DIR / log_filename
    log_data = {
        "task_id": task_id, "config": {k: v for k, v in config.items() if k != "password"},
        "start_time": task["start_time"], "end_time": datetime.datetime.now().isoformat(),
        "total_steps": step, "action_history": action_history, "all_issues": all_issues,
        "evidence_summary": evidence.get_summary(), "data_checks": data_checks,
        "token_usage": {"input_tokens": llm.total_input_tokens, "output_tokens": llm.total_output_tokens},
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

            step, action_history, all_issues, data_checks = _test_loop(
                task_id=task_id, task=task, llm=llm, bridge=bridge,
                executor=executor, evidence=evidence,
                action_history=action_history, all_issues=all_issues,
                start_step=0, extra_context="",
                augmented_spec=augmented_spec, login_info=login_info, api_doc=api_doc,
                current_flow_name="", completed_flows=[],
                config=config,
            )
        finally:
            _finalize_test(task_id, task, llm, bridge, executor, evidence,
                           step, action_history, all_issues, data_checks, config)

    except Exception as e:
        print(f"\n[FATAL] 测试异常终止:")
        traceback.print_exc()
        task["status"] = "error"
        task["error"] = str(e)
        emit("error", {"message": f"测试异常终止: {str(e)}"})


# ============================================================
# 入口二：从快照恢复测试
# ============================================================

def resume_test_task(task_id: str, snapshot: dict, rollback_steps: int, tasks: dict):
    """从快照恢复测试，回退 rollback_steps 步后继续"""
    task = tasks[task_id]
    log_queue = task["log_queue"]
    config = task["config"]
    original_task_id = snapshot["task_id"]

    def emit(event_type, data):
        log_queue.put({"type": event_type, "data": data, "timestamp": datetime.datetime.now().isoformat()})

    try:
        # 恢复状态
        saved_messages = snapshot["messages"]
        saved_action_history = snapshot["action_history"]
        saved_issues = snapshot.get("all_issues", [])
        saved_step = snapshot["step"]
        saved_url = snapshot.get("last_url", "")
        saved_tokens = snapshot.get("token_usage", {})

        # 回退 rollback_steps 步
        resume_step = max(0, saved_step - rollback_steps)
        action_history = [a for a in saved_action_history if a["step"] <= resume_step]
        all_issues = saved_issues

        # 回退 LLM messages
        msgs_to_remove = rollback_steps * 2
        if msgs_to_remove > 0 and len(saved_messages) > msgs_to_remove:
            restored_messages = saved_messages[:-msgs_to_remove]
        else:
            restored_messages = saved_messages

        emit("status", {"status": "running", "message": f"从快照恢复测试（原任务 {original_task_id}，回退到 Step {resume_step}）"})
        emit("log", {"message": f"🔄 恢复测试：从 Step {saved_step} 回退 {rollback_steps} 步到 Step {resume_step}，继续执行"})

        # 回放已完成的 checklist 给前端
        for entry in action_history:
            emit("checklist", {
                "step": entry["step"],
                "flow": entry.get("flow", ""),
                "item": entry.get("checklist_item", ""),
                "status": "passed" if entry.get("result", {}).get("success") else "failed",
                "action_type": entry.get("action", {}).get("type", ""),
            })

        # 初始化 LLM（恢复对话历史）
        llm_api_key = config.get("llm_api_key", "").strip() or None
        llm_base_url = config.get("llm_base_url", "").strip() or None
        llm_model = config.get("llm_model", "").strip() or None
        llm = LLMEngine(api_key=llm_api_key, base_url=llm_base_url, model=llm_model)
        llm.restore_messages(restored_messages, saved_tokens.get("input", 0), saved_tokens.get("output", 0))

        evidence = EvidenceCollector()
        bridge = PlaywrightBridge(headless=True, video_dir=str(VIDEO_DIR))
        try:
            bridge.start_server()
            emit("log", {"message": "Playwright 浏览器已就绪（视频录制已开启）"})
            if restore_session_cookies(bridge, saved_url or config.get("target_url", "")):
                emit("log", {"message": "已恢复上次登录态（cookies）"})

            navigate_url = saved_url or config.get("target_url", "about:blank")
            bridge.open_tab(navigate_url)
            evidence.attach(bridge)
            executor = ActionExecutor(bridge, SCREENSHOT_DIR, task_id)
            # SSRF 防护
            target_url = config.get("target_url", "")
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
                    print(f"[WARN] 恢复浏览器状态失败: {e}")
                    emit("log", {"message": f"⚠️ 恢复浏览器状态失败: {e}"})

            emit("log", {"message": f"浏览器已导航到 {navigate_url}，从 Step {resume_step + 1} 继续测试"})

            # 构建上下文
            spec_content = config.get("spec_content", "")
            login_info = ""
            need_login = config.get("need_login", False)
            login_type = config.get("login_type", "password")
            if need_login:
                if login_type == "sms":
                    login_info = f"手机验证码登录，手机号：{config.get('phone', '')}"
                else:
                    login_info = f"账号密码登录，用户名：{config.get('username', '')}"
            api_doc = config.get("api_doc", "")
            augmented_spec = spec_content
            if target_url and "http" not in spec_content[:200]:
                augmented_spec = f"## 测试入口\n- URL：{target_url}\n\n{spec_content}"

            # 恢复已完成的流程名称
            current_flow_name = ""
            completed_flows = []
            for entry in action_history:
                f = entry.get("flow", "")
                if f:
                    current_flow_name = f
                    if entry.get("flow_status") == "passed" and f not in completed_flows:
                        completed_flows.append(f)

            if session_restored:
                extra_context = f"测试已从 Step {resume_step} 恢复。浏览器的登录状态（cookies/localStorage）已从快照恢复，你应该仍然处于登录状态。请观察当前页面确认登录状态，然后继续之前未完成的测试流程。不要重复已测试过的流程。"
            else:
                extra_context = f"测试已从 Step {resume_step} 恢复。注意：浏览器是全新启动的，登录状态可能已丢失。请先观察当前页面状态，如果需要重新登录则先完成登录，然后继续之前未完成的测试流程。"

            step, action_history, all_issues, data_checks = _test_loop(
                task_id=task_id, task=task, llm=llm, bridge=bridge,
                executor=executor, evidence=evidence,
                action_history=action_history, all_issues=all_issues,
                start_step=resume_step, extra_context=extra_context,
                augmented_spec=augmented_spec, login_info=login_info, api_doc=api_doc,
                current_flow_name=current_flow_name, completed_flows=completed_flows,
                config=config,
            )
        finally:
            _finalize_test(task_id, task, llm, bridge, executor, evidence,
                           step, action_history, all_issues, data_checks, config)

    except Exception as e:
        print(f"\n[FATAL] 恢复测试异常终止:")
        traceback.print_exc()
        task["status"] = "error"
        task["error"] = str(e)
        emit("error", {"message": f"恢复测试异常终止: {str(e)}"})
