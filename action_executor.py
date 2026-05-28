"""
AI Web Tester - 动作执行器
通过 Playwright ref 引用执行浏览器操作
"""

import json
import time
import base64
import datetime

import requests as http_requests

from playwright_bridge import PlaywrightBridge
from config import SCREENSHOT_DIR, BLOCKED_URL_PATTERNS
from logger import get_logger
from exceptions import BridgeError

logger = get_logger("action_executor")


class ActionExecutor:
    """通过 Playwright ref 引用执行浏览器操作"""

    def __init__(self, bridge: PlaywrightBridge, screenshot_dir, task_id):
        self.bridge = bridge
        self.screenshot_dir = screenshot_dir
        self.task_id = task_id
        self.screenshot_count = 0
        self.data_checks = []
        # SSRF 防护：允许访问的 origin（由 test_runner 设置）
        self.allowed_origin = None

    def _check_blocked_url(self, url):
        """Guardrail：检查 URL 是否命中禁止模式"""
        for pattern in BLOCKED_URL_PATTERNS:
            if pattern in url:
                return pattern
        return None

    def take_screenshot(self, step: int, label: str = "") -> dict:
        """每步操作后自动截图，返回 base64 和文件名"""
        name = f"step_{step}_{self.task_id}"
        path = self.screenshot_dir / f"{name}.jpg"
        try:
            b64 = self.bridge.screenshot(quality=80)
            if not b64:
                logger.warning(f"take_screenshot step={step}: bridge.screenshot 返回空")
                return {"filename": "", "base64": ""}
            with open(path, "wb") as f:
                f.write(base64.b64decode(b64))
            self.screenshot_count += 1
            return {"filename": f"{name}.jpg", "base64": b64}
        except Exception as ex:
            logger.warning(f"take_screenshot step={step} 失败: {ex}")
            return {"filename": "", "base64": ""}

    def _check_api_url(self, url):
        """SSRF 防护：校验 call_api 的 URL 是否属于允许的 origin"""
        if not self.allowed_origin:
            return True  # 未设置限制则放行
        from urllib.parse import urlparse
        parsed = urlparse(url)
        allowed = urlparse(self.allowed_origin)
        # 允许同 scheme+host+port
        if parsed.scheme == allowed.scheme and parsed.hostname == allowed.hostname:
            if parsed.port == allowed.port:
                return True
            # 默认端口处理
            if not parsed.port and not allowed.port:
                return True
        return False

    def execute(self, action):
        action_type = action.get("type", "unknown")
        params = action.get("params", {})
        # 兼容扁平格式：某些 LLM 不输出 params 包装层，直接把 ref/url/text 放顶层
        if not params:
            flat_keys = {k: v for k, v in action.items() if k not in ("type", "params", "description")}
            if flat_keys:
                params = flat_keys
        result = {"success": False, "message": "", "screenshot": None}
        try:
            if action_type == "navigate":
                url = params.get("url", "")
                blocked = self._check_blocked_url(url)
                if blocked:
                    result.update(success=False, message=f"⛔ Guardrail 拦截：禁止访问包含 '{blocked}' 的 URL")
                    return result
                self.bridge.navigate(url)
                result.update(success=True, message=f"已导航到 {url}")
            elif action_type == "click":
                ref = params.get("ref", "")
                tabs_before = len(self.bridge._pages)
                click_result = self.bridge.click(ref)
                time.sleep(0.5)
                # 检查 bridge.click 返回值（三级降级均失败时返回 success=False）
                if isinstance(click_result, dict) and not click_result.get("success", True):
                    result.update(success=False, message=click_result.get("message", f"click {ref} 失败"))
                    return result
                # Guardrail：click 后检查是否意外进入禁区
                try:
                    current_url = self.bridge._page.url
                    blocked = self._check_blocked_url(current_url)
                    if blocked:
                        self.bridge._page.go_back()
                        time.sleep(0.5)
                        result.update(success=False, message=f"⛔ Guardrail 拦截：点击后进入禁区('{blocked}')，已自动返回")
                        return result
                except Exception:
                    pass
                # 检测是否有新标签页打开（window.open 下载等场景）
                tabs_after = len(self.bridge._pages)
                if tabs_after > tabs_before:
                    new_tab_url = ""
                    try:
                        new_tab_url = self.bridge._pages[-1].url
                    except Exception:
                        pass
                    result.update(success=True, message=f"已点击: {ref}（触发了新标签页打开: {new_tab_url[:120]}）")
                else:
                    result.update(success=True, message=f"已点击: {ref}")
            elif action_type == "fill":
                ref = params.get("ref", "")
                text = params.get("text", "")
                self.bridge.fill(ref, text)
                time.sleep(0.3)
                result.update(success=True, message=f"已填写 '{text}' → {ref}")
            elif action_type == "type":
                ref = params.get("ref", "")
                text = params.get("text", "")
                self.bridge.type_text(ref, text)
                time.sleep(0.3)
                result.update(success=True, message=f"已输入 '{text}' → {ref}")
            elif action_type == "press":
                key = params.get("key", "Enter")
                ref = params.get("ref", None)
                self.bridge.press(key, ref=ref)
                time.sleep(0.5)
                result.update(success=True, message=f"已按下 {key}")
            elif action_type == "hover":
                ref = params.get("ref", "")
                self.bridge.hover(ref)
                time.sleep(0.3)
                result.update(success=True, message=f"已悬停: {ref}")
            elif action_type == "select":
                ref = params.get("ref", "")
                value = params.get("value", "")
                self.bridge.select(ref, value)
                time.sleep(0.3)
                result.update(success=True, message=f"已选择 '{value}' → {ref}")
            elif action_type == "wait":
                time.sleep(params.get("ms", 1000) / 1000.0)
                result.update(success=True, message=f"已等待 {params.get('ms', 1000)}ms")
            elif action_type == "screenshot":
                name = params.get("name", f"step_{self.screenshot_count}")
                path = self.screenshot_dir / f"{name}.jpg"
                b64 = self.bridge.screenshot(quality=80)
                with open(path, "wb") as f:
                    f.write(base64.b64decode(b64))
                self.screenshot_count += 1
                result.update(success=True, message=f"已截图: {path.name}", screenshot=path.name)
            elif action_type == "assert_text":
                text = params.get("text", "")
                text_data = self.bridge.get_text()
                page_text = text_data.get("text", "")
                if text in page_text:
                    result.update(success=True, message=f"断言通过: '{text}'")
                else:
                    result.update(success=False, message=f"断言失败: '{text}'")
            elif action_type == "scroll":
                direction = params.get("direction", "down")
                pixels = params.get("pixels", 500)
                self.bridge.scroll(direction, pixels)
                time.sleep(0.5)
                result.update(success=True, message=f"已滚动 {direction} {pixels}px")
            elif action_type == "extract_data":
                desc = params.get("description", "提取数据")
                try:
                    js = """JSON.stringify((() => {
                        const text = document.body.innerText.substring(0, 3000);
                        const tables = [];
                        document.querySelectorAll('table').forEach((t, i) => {
                            const rows = [];
                            t.querySelectorAll('tr').forEach(tr => {
                                const cells = [];
                                tr.querySelectorAll('th, td').forEach(c => cells.push(c.textContent.trim()));
                                if (cells.length) rows.push(cells);
                            });
                            if (rows.length) tables.push(rows);
                        });
                        return {text, tables};
                    })())"""
                    eval_result = self.bridge.evaluate(js)
                    raw = eval_result.get("result", "{}")
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    result.update(success=True, message=f"数据提取完成: {desc}")
                    result["extracted_data"] = data
                except Exception as ex:
                    result.update(success=False, message=f"数据提取失败: {str(ex)}")
            elif action_type == "execute_js":
                expression = params.get("expression", "")
                desc = params.get("description", "执行 JS")
                if not expression:
                    result.update(success=False, message="execute_js: expression 参数不能为空")
                else:
                    try:
                        eval_result = self.bridge.evaluate(expression)
                        js_result = eval_result.get("result")
                        js_error = eval_result.get("error")
                        if js_error:
                            result.update(success=False, message=f"JS 执行出错: {js_error}")
                        else:
                            preview = str(js_result)[:500] if js_result is not None else "undefined"
                            result.update(success=True, message=f"JS 执行完成 ({desc}): {preview}")
                            result["js_result"] = js_result
                    except Exception as ex:
                        result.update(success=False, message=f"JS 执行失败: {str(ex)}")
            elif action_type == "verify_data":
                check_type = params.get("check_type", "unknown")
                desc = params.get("description", "数据验证")
                data = params.get("data", {})
                check_record = {"check_type": check_type, "description": desc, "data": data, "timestamp": datetime.datetime.now().isoformat(), "layer": "frontend", "passed": True}
                self.data_checks.append(check_record)
                result.update(success=True, message=f"数据验证已记录: [{check_type}] {desc}")
            elif action_type == "call_api":
                method = params.get("method", "GET").upper()
                url = params.get("url", "")
                headers = params.get("headers", {})
                body = params.get("body", None)
                desc = params.get("description", "API 调用")
                # SSRF 防护
                if not self._check_api_url(url):
                    result.update(success=False, message=f"SSRF 防护：URL {url} 不在允许的 origin 内（仅允许 {self.allowed_origin}）")
                else:
                    try:
                        resp = http_requests.request(method, url, headers=headers, json=body, timeout=10)
                        try:
                            resp_data = resp.json()
                            resp_preview = json.dumps(resp_data, ensure_ascii=False)[:3000]
                        except (ValueError, TypeError):
                            resp_preview = resp.text[:3000]
                        api_record = {"check_type": "api_call", "description": desc, "data": {"method": method, "url": url, "status": resp.status_code, "response_preview": resp_preview[:500]}, "timestamp": datetime.datetime.now().isoformat(), "layer": "backend", "passed": resp.status_code < 400}
                        self.data_checks.append(api_record)
                        result.update(success=True, message=f"API 调用完成: [{method}] {url} → {resp.status_code}")
                        result["api_response"] = {"status": resp.status_code, "data": resp_preview}
                    except Exception as ex:
                        result.update(success=False, message=f"API 调用失败: {str(ex)}")
            elif action_type == "compare_api_vs_page":
                desc = params.get("description", "前后端数据对比")
                api_data = params.get("api_data", "")
                page_data = params.get("page_data", "")
                compare_record = {"check_type": "api_vs_page", "description": desc, "data": {"api_data": str(api_data)[:500], "page_data": str(page_data)[:500]}, "timestamp": datetime.datetime.now().isoformat(), "layer": "api_vs_frontend", "passed": True}
                self.data_checks.append(compare_record)
                result.update(success=True, message=f"前后端数据对比已记录: {desc}")
            elif action_type == "switch_tab":
                index = params.get("index", -1)
                tab_result = self.bridge.switch_tab(index)
                result.update(success=True, message=f"已切换到标签页 {tab_result['tab_index']}（共 {tab_result['total_tabs']} 个），URL: {tab_result['url']}")
            elif action_type == "close_tab":
                index = params.get("index", -1)
                tab_result = self.bridge.close_tab(index)
                result.update(success=True, message=f"已关闭标签页 {tab_result['closed_index']}，切回标签页 {tab_result['current_index']}（共 {tab_result['total_tabs']} 个）")
            elif action_type == "get_tabs":
                tabs = self.bridge.get_tab_info()
                tab_desc = "; ".join([f"[{t['index']}] {'*' if t['active'] else ''}{t['url']}" for t in tabs])
                result.update(success=True, message=f"当前标签页: {tab_desc}")
                result["tabs"] = tabs
            elif action_type == "go_back":
                back_result = self.bridge.go_back()
                time.sleep(0.5)
                result.update(success=True, message=f"已后退到 {back_result.get('url', '')}")
            elif action_type == "send_keys":
                keys = params.get("keys", "")
                if not keys:
                    result.update(success=False, message="send_keys: keys 参数不能为空")
                else:
                    self.bridge.send_keys(keys)
                    time.sleep(0.3)
                    result.update(success=True, message=f"已发送按键: {keys}")
            elif action_type == "finish":
                result.update(success=True, message=f"测试结束: {params.get('summary', '')}")
            elif action_type == "request_human_input":
                result.update(success=True, message="等待人工输入")
            else:
                result["message"] = f"未知操作: {action_type}"
        except BridgeError as e:
            result["message"] = f"执行失败 [{action_type}]: {e}"
            result["error_type"] = e.__class__.__name__
            result["recoverable"] = e.recoverable
            logger.warning(f"BridgeError [{action_type}]: {e}")
            self._capture_error_screenshot(result)
        except Exception as e:
            result["message"] = f"执行失败 [{action_type}]: {str(e)}"
            logger.error(f"未预期异常 [{action_type}]: {e}", exc_info=True)
            self._capture_error_screenshot(result)
        return result

    def _capture_error_screenshot(self, result: dict):
        """尝试在出错时截图，失败时静默忽略"""
        try:
            err_b64 = self.bridge.screenshot(quality=60)
            err_path = self.screenshot_dir / f"error_{self.screenshot_count}_{self.task_id}.jpg"
            with open(err_path, "wb") as f:
                f.write(base64.b64decode(err_b64))
            result["screenshot"] = err_path.name
            self.screenshot_count += 1
        except Exception:
            pass
