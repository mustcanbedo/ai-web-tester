"""
AI Web Tester - 脚本化测试执行器
按预定义 YAML 脚本顺序执行操作，AI 只负责结果验证（YES/NO）。
替代 LLM 全权决策的探索式模式，大幅提升稳定性。

核心理念：
  - 操作路径由脚本决定（确定性）
  - 元素定位由策略函数决定（确定性）
  - 只有结果验证调用 LLM（简单的 YES/NO 判定，准确率高）
"""

import json
import time
import datetime
import base64
import traceback
from pathlib import Path

import yaml
from openai import OpenAI

from playwright_bridge import PlaywrightBridge
from element_resolver import resolve_element
from evidence import EvidenceCollector
from action_executor import ActionExecutor
from config import SCREENSHOT_DIR, REPORT_DIR, LOG_DIR, VIDEO_DIR, LLM_MODEL, BLOCKED_URL_PATTERNS


# ============================================================
# AI 验证器（只做 YES/NO 判定）
# ============================================================

class AIVerifier:
    """轻量 LLM 调用，只做结果验证，不做决策"""

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

    def verify(self, question, page_state, evidence_summary=None):
        """
        向 LLM 提问一个 YES/NO 验证问题。

        Returns:
            {"passed": bool, "reason": str, "confidence": str}
        """
        refs_str = json.dumps(page_state.get("refs", [])[:40], ensure_ascii=False)
        page_text = (page_state.get("text", "") or "")[:1000]
        url = page_state.get("url", "")
        title = page_state.get("title", "")

        evidence_part = ""
        if evidence_summary:
            if evidence_summary.get("new_console_errors"):
                evidence_part += f"\nConsole 错误: {json.dumps(evidence_summary['new_console_errors'], ensure_ascii=False)}"
            if evidence_summary.get("new_network_errors"):
                evidence_part += f"\nNetwork 错误: {json.dumps(evidence_summary['new_network_errors'], ensure_ascii=False)}"

        action_result_part = ""
        if page_state.get("action_result"):
            action_result_part = f"\n- 上一步操作返回值: {json.dumps(page_state['action_result'], ensure_ascii=False, default=str)}"

        prompt = f"""你是一个测试验证器。请根据当前页面状态判断以下验证条件是否满足。

**当前页面**:
- URL: {url}
- 标题: {title}
- 可交互元素: {refs_str}
- 页面文本(前1000字): {page_text}{action_result_part}
{evidence_part}

**验证问题**: {question}

只返回 JSON，格式：
{{"passed": true/false, "reason": "简短判断理由", "confidence": "high/medium/low"}}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是一个测试验证器。只回答验证问题，只返回 JSON。不要编造信息，基于给定的页面状态做判断。如果信息不足以判断，confidence 设为 low。"},
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
            return {
                "passed": result.get("passed", False),
                "reason": result.get("reason", ""),
                "confidence": result.get("confidence", "medium"),
            }
        except Exception as e:
            print(f"[WARN] AI 验证调用失败: {e}")
            return {"passed": False, "reason": f"AI验证调用失败(非项目问题): {e}", "confidence": "low", "blame": "TOOL"}


# ============================================================
# 确定性验证函数（不需要 LLM）
# ============================================================

def _verify_deterministic(rule, page_state):
    """
    执行不需要 LLM 的确定性验证。
    返回 {"passed": bool, "reason": str}
    """
    rule_type = rule.get("type", "")
    url = page_state.get("url", "")
    title = page_state.get("title", "")
    refs = page_state.get("refs", [])
    text = page_state.get("text", "") or ""

    if rule_type == "url_contains":
        value = rule["value"]
        passed = value in url
        return {"passed": passed, "reason": f"URL {'包含' if passed else '不包含'} '{value}' (当前: {url})"}

    elif rule_type == "title_contains":
        expected = rule["expected"]
        passed = expected in title
        return {"passed": passed, "reason": f"标题 {'包含' if passed else '不包含'} '{expected}' (当前: {title})"}

    elif rule_type == "text_visible":
        target = rule["text"]
        passed = target in text
        return {"passed": passed, "reason": f"页面文本 {'包含' if passed else '不包含'} '{target}'"}

    elif rule_type == "text_not_visible":
        target = rule["text"]
        passed = target not in text
        return {"passed": passed, "reason": f"页面文本 {'不包含' if passed else '仍包含'} '{target}'"}

    elif rule_type == "element_exists":
        role = rule.get("role", "")
        name = rule.get("name", "")
        found = any(
            r.get("role") == role and (not name or name in r.get("name", ""))
            for r in refs
        ) if role else len(refs) > 0
        return {"passed": found, "reason": f"{'找到' if found else '未找到'} role={role} 的元素"}

    elif rule_type == "elements_count_gte":
        role = rule.get("role", "")
        min_count = rule.get("min", 1)
        count = sum(1 for r in refs if r.get("role") == role) if role else len(refs)
        passed = count >= min_count
        return {"passed": passed, "reason": f"role={role} 元素数量={count}, 要求>={min_count}"}

    else:
        return None  # 非确定性规则，需要 AI 判定


# ============================================================
# 脚本执行器
# ============================================================

class ScriptRunner:
    """按 YAML 脚本顺序执行测试步骤"""

    def __init__(self, bridge: PlaywrightBridge, verifier: AIVerifier,
                 evidence: EvidenceCollector, executor: ActionExecutor,
                 task_id: str, log_queue=None):
        self.bridge = bridge
        self.verifier = verifier
        self.evidence = evidence
        self.executor = executor  # 共享操作执行层（含安全防护）
        self.task_id = task_id
        self.log_queue = log_queue
        self.results = []  # 每步的执行结果
        self.issues = []   # 发现的问题
        self._evidence_console_idx = 0  # evidence 增量计数器
        self._evidence_network_idx = 0

    def emit(self, event_type, data):
        if self.log_queue:
            self.log_queue.put({
                "type": event_type,
                "data": data,
                "timestamp": datetime.datetime.now().isoformat(),
            })

    def run_script(self, script_path, base_url="", variables=None):
        """
        执行一个 YAML 测试脚本。

        Args:
            script_path: YAML 文件路径
            base_url: 被测应用的基础 URL
            variables: 额外变量（如 {timestamp}）

        Returns:
            {"flow": str, "total_steps": int, "passed": int, "failed": int, "skipped": int, "issues": list}
        """
        with open(script_path, "r", encoding="utf-8") as f:
            script = yaml.safe_load(f)

        flow_name = script.get("flow", "未命名流程")
        steps = script.get("steps", [])
        vars_ = variables or {}
        vars_["timestamp"] = datetime.datetime.now().strftime("%H%M%S")
        vars_["base_url"] = base_url

        self.emit("log", {"message": f"📋 开始脚本化测试: {flow_name} ({len(steps)} 步)"})
        self.emit("status", {"status": "running", "flow": flow_name})

        passed = 0
        failed = 0
        skipped = 0

        for i, step in enumerate(steps):
            step_id = step.get("id", f"step_{i}")
            step_name = step.get("name", "")
            action_type = step.get("action", "")
            params = step.get("params", {})
            verify_rules = step.get("verify", [])

            # 变量替换
            params = _substitute_vars(params, vars_)

            self.emit("step_start", {"step": i + 1, "id": step_id, "name": step_name, "max_steps": len(steps)})
            self.emit("log", {"message": f"[{step_id}] {step_name}"})

            # 执行操作
            action_result = self._execute_step(action_type, params, step_id)

            if not action_result["success"]:
                # 操作本身失败
                failed += 1
                self.results.append({
                    "step_id": step_id, "name": step_name,
                    "status": "action_failed", "message": action_result["message"],
                })
                self.emit("step_result", {
                    "step": i + 1, "id": step_id, "success": False,
                    "message": f"操作失败: {action_result['message']}",
                    "blame": action_result.get("blame", "TOOL"),
                })
                # 截图记录失败现场
                self._take_screenshot(i + 1, f"failed_{step_id}")

                # 判断是否继续：操作失败不一定要终止整个脚本
                if step.get("critical", False):
                    self.emit("log", {"message": f"❌ 关键步骤失败，终止脚本: {step_id}"})
                    break
                else:
                    self.emit("log", {"message": f"⚠️ 步骤失败但非关键，继续: {action_result['message']}"})
                    continue

            # 操作成功，执行验证
            if verify_rules:
                time.sleep(0.5)  # 等待页面渲染
                self.evidence.collect()
                page_state = self.bridge.get_page_state()
                # 将 action 返回值注入 page_state，供验证使用（如 execute_js 的结果）
                if action_result.get("result"):
                    page_state["action_result"] = action_result["result"]
                verify_result = self._run_verifications(verify_rules, page_state, step_id)

                if verify_result["all_passed"]:
                    passed += 1
                    self.results.append({
                        "step_id": step_id, "name": step_name,
                        "status": "passed", "verifications": verify_result["details"],
                    })
                    self.emit("step_result", {
                        "step": i + 1, "id": step_id, "success": True,
                        "message": f"✅ 验证通过 ({len(verify_result['details'])} 项)",
                    })
                else:
                    failed += 1
                    failed_checks = [d for d in verify_result["details"] if not d["passed"]]
                    self.results.append({
                        "step_id": step_id, "name": step_name,
                        "status": "verify_failed", "verifications": verify_result["details"],
                    })
                    self.emit("step_result", {
                        "step": i + 1, "id": step_id, "success": False,
                        "message": f"❌ 验证失败: {failed_checks[0]['reason']}",
                        "blame": "PROJECT",
                    })
                    # 验证失败 = 项目可能有 bug，记录 issue
                    for fc in failed_checks:
                        self.issues.append({
                            "severity": "P1",
                            "title": f"[{step_id}] {step_name} - 验证失败",
                            "description": fc["reason"],
                            "step_id": step_id,
                            "blame": "PROJECT",
                        })
            else:
                # 无验证规则，操作成功即通过
                passed += 1
                self.results.append({
                    "step_id": step_id, "name": step_name, "status": "passed",
                })
                self.emit("step_result", {
                    "step": i + 1, "id": step_id, "success": True,
                    "message": f"✅ 操作成功",
                })

            # 每步截图
            self._take_screenshot(i + 1, step_id)

        summary = {
            "flow": flow_name,
            "total_steps": len(steps),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "issues": self.issues,
            "results": self.results,
            "token_usage": {
                "input": self.verifier.total_input_tokens,
                "output": self.verifier.total_output_tokens,
            },
        }
        self.emit("log", {"message": f"📊 脚本执行完成: {passed} 通过 / {failed} 失败 / {skipped} 跳过"})
        return summary

    # ------------------------------------------------------------------
    # 步骤执行
    # ------------------------------------------------------------------

    # 脚本化特有的 action 类型，不委托给 ActionExecutor
    _SCRIPT_ONLY_ACTIONS = {"wait_for", "snapshot", "find_and_click", "find_and_fill"}

    def _execute_step(self, action_type, params, step_id):
        """执行单个操作步骤，返回 {success, message, blame, result?}"""
        try:
            # ── 脚本化特有操作 ──
            if action_type == "wait_for":
                return self._wait_for(params)

            elif action_type == "snapshot":
                self.bridge.get_page_state()
                return {"success": True, "message": "已刷新页面快照"}

            elif action_type == "find_and_click":
                return self._find_and_click(params)

            elif action_type == "find_and_fill":
                return self._find_and_fill(params)

            # ── 基础操作：委托给 ActionExecutor（共享安全防护） ──
            else:
                action = {"type": action_type, "params": params}
                result = self.executor.execute(action)
                # 统一返回格式
                ret = {
                    "success": result.get("success", False),
                    "message": result.get("message", ""),
                }
                # 传递 execute_js 的返回值供 verify 使用
                if result.get("js_result") is not None:
                    ret["result"] = result["js_result"]
                if not ret["success"]:
                    ret["blame"] = "TOOL"
                return ret

        except Exception as e:
            error_msg = str(e)
            blame = "TOOL"

            if "no element" in error_msg.lower() or "could not find" in error_msg.lower():
                msg = f"元素未找到（ref 可能已失效）: {error_msg[:100]}"
            elif "timeout" in error_msg.lower():
                blame = "UNKNOWN"
                msg = f"操作超时: {error_msg[:100]}"
            elif "intercepted" in error_msg.lower() or "overlay" in error_msg.lower():
                msg = f"元素被遮挡: {error_msg[:100]}"
            else:
                msg = f"执行异常: {error_msg[:150]}"

            print(f"[ERROR][{step_id}] {msg}")
            traceback.print_exc()
            return {"success": False, "message": msg, "blame": blame}

    def _wait_for(self, params):
        """等待特定条件满足"""
        wait_type = params.get("type", "")
        value = params.get("value", "")
        timeout = params.get("timeout", 10000)
        interval = 500
        elapsed = 0

        while elapsed < timeout:
            page_state = self.bridge.get_page_state()
            if wait_type == "url_contains":
                if value in page_state.get("url", ""):
                    return {"success": True, "message": f"URL 包含 '{value}'"}
            elif wait_type == "text_visible":
                if value in (page_state.get("text", "") or ""):
                    return {"success": True, "message": f"页面文本包含 '{value}'"}
            elif wait_type == "element_exists":
                role = params.get("role", "")
                found = any(r.get("role") == role for r in page_state.get("refs", []))
                if found:
                    return {"success": True, "message": f"找到 role={role} 的元素"}
            time.sleep(interval / 1000.0)
            elapsed += interval

        return {"success": False, "message": f"等待超时({timeout}ms): {wait_type}={value}", "blame": "UNKNOWN"}

    def _find_and_click(self, params):
        """通过策略定位元素并点击"""
        # 先刷新 AX Tree
        page_state = self.bridge.get_page_state()
        refs = page_state.get("refs", [])

        ref_id, match_info = resolve_element(refs, params)
        if not ref_id:
            return {"success": False, "message": f"元素定位失败: {match_info}", "blame": "TOOL"}

        self.emit("log", {"message": f"  定位: {match_info} → {ref_id}"})

        try:
            self.bridge.click(ref_id)
            time.sleep(0.5)
            return {"success": True, "message": f"点击 {ref_id} ({match_info})"}
        except Exception as e:
            return {"success": False, "message": f"点击失败: {e}", "blame": "TOOL"}

    def _find_and_fill(self, params):
        """通过策略定位输入框并填写"""
        page_state = self.bridge.get_page_state()
        refs = page_state.get("refs", [])

        text = params.get("text", "")
        # 把 text 从 params 中临时移除，不影响元素定位
        find_params = {k: v for k, v in params.items() if k != "text"}
        ref_id, match_info = resolve_element(refs, find_params)
        if not ref_id:
            return {"success": False, "message": f"输入框定位失败: {match_info}", "blame": "TOOL"}

        self.emit("log", {"message": f"  定位: {match_info} → {ref_id}"})

        try:
            self.bridge.fill(ref_id, text)
            time.sleep(0.3)
            return {"success": True, "message": f"填写 '{text[:30]}' → {ref_id}"}
        except Exception as e:
            return {"success": False, "message": f"填写失败: {e}", "blame": "TOOL"}

    # ------------------------------------------------------------------
    # 验证
    # ------------------------------------------------------------------

    def _run_verifications(self, rules, page_state, step_id):
        """执行验证规则列表"""
        details = []
        all_passed = True

        evidence_summary = self.evidence.get_new_evidence_since(
            self._evidence_console_idx, self._evidence_network_idx
        )
        self._evidence_console_idx = len(self.evidence.console_errors)
        self._evidence_network_idx = len(self.evidence.network_errors)

        for rule in rules:
            rule_type = rule.get("type", "")

            if rule_type == "ai_judge":
                # AI 验证
                question = rule.get("question", "")
                result = self.verifier.verify(question, page_state, evidence_summary)
                details.append({
                    "type": "ai_judge",
                    "question": question,
                    "passed": result["passed"],
                    "reason": result["reason"],
                    "confidence": result.get("confidence", "medium"),
                })
                if not result["passed"]:
                    all_passed = False
                self.emit("log", {
                    "message": f"  🤖 AI验证: {'✅' if result['passed'] else '❌'} {result['reason']} (置信度: {result.get('confidence', '?')})"
                })
            else:
                # 确定性验证
                result = _verify_deterministic(rule, page_state)
                if result is None:
                    details.append({"type": rule_type, "passed": True, "reason": f"未知验证类型 {rule_type}，跳过"})
                    continue
                details.append({
                    "type": rule_type,
                    "passed": result["passed"],
                    "reason": result["reason"],
                })
                if not result["passed"]:
                    all_passed = False
                desc = rule.get("description", "")
                self.emit("log", {
                    "message": f"  📐 验证: {'✅' if result['passed'] else '❌'} {result['reason']}" + (f" ({desc})" if desc else "")
                })

        return {"all_passed": all_passed, "details": details}

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _take_screenshot(self, step, label):
        try:
            b64 = self.bridge.screenshot(quality=80)
            if b64:
                filename = f"script_{self.task_id}_step{step}.jpg"
                path = SCREENSHOT_DIR / filename
                with open(path, "wb") as f:
                    f.write(base64.b64decode(b64))
                self.emit("screenshot", {
                    "step": step, "filename": filename,
                    "base64": b64, "label": label,
                })
        except Exception:
            pass


# ============================================================
# 工具函数
# ============================================================

def _substitute_vars(params, vars_):
    """递归替换 params 中的 {variable} 占位符"""
    if isinstance(params, str):
        for k, v in vars_.items():
            params = params.replace(f"{{{k}}}", str(v))
        return params
    elif isinstance(params, dict):
        return {k: _substitute_vars(v, vars_) for k, v in params.items()}
    elif isinstance(params, list):
        return [_substitute_vars(item, vars_) for item in params]
    return params


def generate_script_report(summary):
    """生成脚本化测试的 Markdown 报告"""
    flow = summary["flow"]
    total = summary["total_steps"]
    passed = summary["passed"]
    failed = summary["failed"]
    issues = summary.get("issues", [])
    results = summary.get("results", [])

    lines = [
        f"# 脚本化测试报告",
        f"",
        f"## 测试概要",
        f"- **流程**: {flow}",
        f"- **总步骤**: {total}",
        f"- **通过**: {passed} ✅",
        f"- **失败**: {failed} ❌",
        f"- **通过率**: {passed/max(total,1)*100:.1f}%",
        f"- **Token 消耗**: 输入 {summary.get('token_usage', {}).get('input', 0)}, 输出 {summary.get('token_usage', {}).get('output', 0)}",
        f"",
    ]

    if issues:
        lines.append("## 发现的问题")
        lines.append("")
        for issue in issues:
            blame_tag = f"[{issue.get('blame', '?')}]" if issue.get("blame") else ""
            lines.append(f"- **{issue.get('severity', 'P2')}** {blame_tag} {issue.get('title', '')}")
            lines.append(f"  - {issue.get('description', '')}")
        lines.append("")

    lines.append("## 执行详情")
    lines.append("")
    lines.append("| 步骤 | 名称 | 状态 | 说明 |")
    lines.append("|------|------|------|------|")
    for r in results:
        status_icon = "✅" if r["status"] == "passed" else "❌"
        msg = r.get("message", "")
        if not msg and r.get("verifications"):
            failed_v = [v for v in r["verifications"] if not v["passed"]]
            msg = failed_v[0]["reason"] if failed_v else "全部验证通过"
        lines.append(f"| {r['step_id']} | {r['name']} | {status_icon} {r['status']} | {msg[:60]} |")

    return "\n".join(lines)
