"""
Playwright Bridge - 封装 Playwright API 调用
提供浏览器控制、页面快照、动作执行、截图等功能

接口与 PinchTabBridge 完全兼容，可直接替换。
使用 Playwright 的 Accessibility Tree 生成与 PinchTab 格式一致的 refs 列表。
"""

import json
import time
import base64
import re
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


class PlaywrightBridge:
    """Playwright 浏览器控制客户端，接口兼容 PinchTabBridge"""

    def __init__(self, headless=True, video_dir=None, **kwargs):
        self.headless = headless
        self._video_dir = video_dir
        self._playwright = None
        self._browser = None  # type: Browser | None
        self._context = None  # type: BrowserContext | None
        self._page = None  # type: Page | None
        self._cdp = None
        # ref -> CDP backendDOMNodeId 映射（每次 snapshot 刷新）
        self._ref_map: dict = {}
        self._ref_counter = 0

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    def start_server(self):
        """启动 Playwright 浏览器"""
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        ctx_kwargs = {
            "viewport": {"width": 1280, "height": 800},
            "locale": "zh-CN",
        }
        if self._video_dir:
            ctx_kwargs["record_video_dir"] = str(self._video_dir)
            ctx_kwargs["record_video_size"] = {"width": 1280, "height": 800}
        self._context = self._browser.new_context(**ctx_kwargs)
        self._page = self._context.new_page()
        # 创建 CDP session 用于获取 Accessibility Tree
        self._cdp = self._context.new_cdp_session(self._page)
        return True

    def get_video_path(self):
        """获取录制视频的路径（必须在 context 关闭后才能访问完整视频）"""
        try:
            if self._page and self._page.video:
                return self._page.video.path()
        except Exception:
            pass
        return None

    def stop_server(self):
        """关闭浏览器和 Playwright"""
        try:
            if self._cdp:
                self._cdp.detach()
        except:
            pass
        # 获取视频路径（必须在关闭 context 之前）
        self._final_video_path = self.get_video_path()
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except:
            pass
        self._cdp = None
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    def is_running(self):
        """检查浏览器是否运行中"""
        return self._browser is not None and self._browser.is_connected()

    # ------------------------------------------------------------------
    # 实例管理（兼容接口，Playwright 不需要实例管理）
    # ------------------------------------------------------------------

    def start_instance(self):
        """兼容接口：启动浏览器"""
        if not self.is_running():
            self.start_server()

    def stop_instance(self):
        """兼容接口：关闭浏览器"""
        self.stop_server()

    # ------------------------------------------------------------------
    # Tab 管理
    # ------------------------------------------------------------------

    def open_tab(self, url="about:blank"):
        """打开页面并导航到指定 URL"""
        if not self._page:
            self.start_server()

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # 等待网络空闲
            self._page.wait_for_load_state("networkidle", timeout=15000)
        except:
            # 超时也继续，页面可能部分加载
            pass

        return {"url": self._page.url, "title": self._page.title()}

    def close_tab(self):
        """兼容接口"""
        pass  # Playwright 由 stop_server 统一关闭

    # ------------------------------------------------------------------
    # 导航
    # ------------------------------------------------------------------

    def navigate(self, url):
        """导航到指定 URL"""
        if not self._page:
            return self.open_tab(url)

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass

        # SPA 路由守卫可能重定向，等待 URL 稳定
        try:
            self._page.wait_for_timeout(1000)
        except:
            pass

        actual_url = self._page.url
        result = {"url": actual_url, "title": self._page.title()}
        if actual_url != url and not actual_url.startswith(url):
            result["redirected"] = True
            result["message"] = f"页面被重定向到 {actual_url}（可能未登录或权限不足）"
        return result

    # ------------------------------------------------------------------
    # 快照（Accessibility Tree）
    # ------------------------------------------------------------------

    # 可交互角色集合
    INTERACTIVE_ROLES = {
        "link", "button", "textbox", "checkbox", "radio",
        "combobox", "listbox", "option", "menuitem", "menu",
        "switch", "slider", "spinbutton", "tab", "searchbox",
        "menuitemcheckbox", "menuitemradio", "treeitem",
    }

    def snapshot(self, filter_interactive=True, diff=False):
        """通过 CDP 获取 Accessibility Tree，返回与 PinchTab 兼容的 refs 格式"""
        if not self._page or not self._cdp:
            return {"nodes": [], "url": "", "title": "", "count": 0}

        # 重置 ref 映射
        self._ref_map = {}
        self._ref_counter = 0

        try:
            result = self._cdp.send("Accessibility.getFullAXTree")
            ax_nodes = result.get("nodes", [])
        except Exception:
            return {"nodes": [], "url": self._page.url, "title": self._page.title(), "count": 0}

        refs = []
        for ax_node in ax_nodes:
            role = ax_node.get("role", {}).get("value", "")
            name = ax_node.get("name", {}).get("value", "")
            ignored = ax_node.get("ignored", False)

            if ignored:
                continue

            if filter_interactive and role not in self.INTERACTIVE_ROLES:
                continue

            ref_id = f"e{self._ref_counter}"
            self._ref_counter += 1

            ref_entry = {
                "ref": ref_id,
                "role": role,
                "name": name,
            }

            # 提取额外属性
            props = ax_node.get("properties", [])
            for prop in props:
                pname = prop.get("name", "")
                pval = prop.get("value", {}).get("value")
                if pname == "checked" and pval is not None:
                    ref_entry["checked"] = pval
                elif pname == "disabled" and pval:
                    ref_entry["disabled"] = True
                elif pname == "focused" and pval:
                    ref_entry["focused"] = True

            val_obj = ax_node.get("value", {})
            if val_obj and val_obj.get("value"):
                ref_entry["value"] = val_obj["value"]

            refs.append(ref_entry)

            # 存储 backendDOMNodeId 用于后续精确定位
            backend_node_id = ax_node.get("backendDOMNodeId")
            self._ref_map[ref_id] = {
                "role": role,
                "name": name,
                "backendDOMNodeId": backend_node_id,
            }

        return {
            "nodes": refs,
            "url": self._page.url,
            "title": self._page.title(),
            "count": len(refs),
        }

    def get_text(self):
        """获取页面可读文本"""
        if not self._page:
            return {"text": "", "url": "", "title": ""}

        try:
            text = self._page.inner_text("body", timeout=5000)
        except:
            text = ""

        return {
            "text": text,
            "url": self._page.url,
            "title": self._page.title(),
        }

    # ------------------------------------------------------------------
    # 元素定位：通过 ref ID 找到 Playwright Locator
    # ------------------------------------------------------------------

    def _get_locator(self, ref):
        """通过 ref ID 获取 Playwright Locator（优先 CDP 精确定位，fallback get_by_role）"""
        if ref not in self._ref_map:
            raise ValueError(f"未知的 ref: {ref}，请先调用 snapshot 刷新元素列表")

        info = self._ref_map[ref]
        backend_node_id = info.get("backendDOMNodeId")

        # 优先通过 CDP backendDOMNodeId 精确定位
        if backend_node_id and self._cdp:
            try:
                unique_attr = f"pw-ref-{ref}"
                # 通过 backendNodeId 获取 JS 对象引用
                obj = self._cdp.send("DOM.resolveNode", {"backendNodeId": backend_node_id})
                object_id = obj["object"]["objectId"]
                # 通过 JS 直接设置 data 属性（绕过 DOM.requestNode / DOM.setAttributeValue）
                self._cdp.send("Runtime.callFunctionOn", {
                    "objectId": object_id,
                    "functionDeclaration": f'function() {{ this.setAttribute("data-pw-ref", "{unique_attr}"); }}',
                })
                locator = self._page.locator(f'[data-pw-ref="{unique_attr}"]')
                if locator.count() == 1:
                    print(f"[DEBUG] _get_locator({ref}): CDP 精确定位成功")
                    return locator
                else:
                    print(f"[DEBUG] _get_locator({ref}): CDP 注入属性后 count={locator.count()}, fallback")
            except Exception as e:
                print(f"[DEBUG] _get_locator({ref}): CDP 定位异常: {e}, fallback")

        # Fallback: 使用 get_by_role 定位
        role = info["role"]
        name = info["name"]

        if name:
            locator = self._page.get_by_role(role, name=name)
        else:
            locator = self._page.get_by_role(role)

        count = locator.count()
        # 如果有多个匹配，根据 ref 的索引取对应的
        if count > 1:
            # 从 ref_map 中找出所有同 role+name 的 ref，确定当前 ref 的顺序
            ref_idx = 0
            for r, r_info in self._ref_map.items():
                if r_info["role"] == role and r_info["name"] == name:
                    if r == ref:
                        break
                    ref_idx += 1
            if ref_idx < count:
                locator = locator.nth(ref_idx)
                print(f"[DEBUG] _get_locator({ref}): get_by_role fallback, 多个匹配({count}), 取第 {ref_idx} 个")
            else:
                locator = locator.first
                print(f"[DEBUG] _get_locator({ref}): get_by_role fallback, 多个匹配({count}), idx={ref_idx} 超出, 取 first")
        else:
            print(f"[DEBUG] _get_locator({ref}): get_by_role fallback, 唯一匹配")

        return locator

    # ------------------------------------------------------------------
    # 动作执行
    # ------------------------------------------------------------------

    def action(self, kind, ref=None, text=None, key=None, selector=None, direction=None, pixels=None):
        """执行浏览器动作，接口兼容 PinchTabBridge"""
        if kind == "click":
            return self.click(ref)
        elif kind == "fill":
            return self.fill(ref, text or "")
        elif kind == "type":
            return self.type_text(ref, text or "")
        elif kind == "press":
            return self.press(key or "Enter", ref=ref)
        elif kind == "hover":
            return self.hover(ref)
        elif kind == "select":
            return self.select(ref, text or "")
        elif kind == "scroll":
            return self.scroll(direction or "down", pixels or 500)
        elif kind == "focus":
            locator = self._get_locator(ref)
            locator.focus()
            return {"success": True}
        else:
            raise ValueError(f"未知的动作类型: {kind}")

    def click(self, ref):
        locator = self._get_locator(ref)
        try:
            locator.click(timeout=5000)
        except Exception as e:
            # 遮罩层拦截 pointer events 时，用 force=True 跳过遮挡检查
            print(f"[DEBUG] click {ref} 被拦截，降级为 force click: {e}")
            try:
                locator.click(force=True, timeout=5000)
            except Exception as e2:
                # force 也失败时，用 JS 直接触发 click
                print(f"[DEBUG] click {ref} force 也失败，降级为 JS click: {e2}")
                locator.evaluate("el => el.click()")
        return {"success": True}

    def type_text(self, ref, text):
        locator = self._get_locator(ref)
        # 先清空已有内容，避免追加到旧文本后面
        locator.fill("", timeout=5000)
        locator.press_sequentially(text, delay=50)
        return {"success": True}

    def fill(self, ref, text):
        locator = self._get_locator(ref)
        locator.fill(text, timeout=5000)
        return {"success": True}

    def press(self, key, ref=None):
        if ref:
            locator = self._get_locator(ref)
            locator.press(key)
        else:
            self._page.keyboard.press(key)
        return {"success": True}

    def hover(self, ref):
        locator = self._get_locator(ref)
        locator.hover(timeout=5000)
        return {"success": True}

    def select(self, ref, value):
        locator = self._get_locator(ref)
        locator.select_option(value, timeout=5000)
        return {"success": True}

    def scroll(self, direction="down", pixels=500):
        delta = pixels if direction == "down" else -pixels
        self._page.mouse.wheel(0, delta)
        return {"success": True}

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------

    def screenshot(self, quality=80):
        """获取页面截图，返回 base64 编码的 JPEG"""
        if not self._page:
            return ""
        raw = self._page.screenshot(type="jpeg", quality=quality, full_page=False)
        return base64.b64encode(raw).decode("utf-8")

    # ------------------------------------------------------------------
    # JavaScript 执行
    # ------------------------------------------------------------------

    def evaluate(self, expression):
        """在当前页面执行 JavaScript"""
        if not self._page:
            return {"result": None}
        try:
            result = self._page.evaluate(expression)
            return {"result": result}
        except Exception as e:
            return {"result": None, "error": str(e)}

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def get_page_state(self):
        """
        获取页面状态：结合 snapshot + text，返回适合 LLM 消费的格式

        返回:
        {
            "url": "...",
            "title": "...",
            "refs": [...],          # 可交互元素列表
            "text": "...",          # 页面可读文本
        }
        """
        snap = self.snapshot(filter_interactive=True)
        refs = snap.get("nodes", [])
        page_url = snap.get("url", "")
        page_title = snap.get("title", "")

        text_data = self.get_text()
        page_text = text_data.get("text", "")
        if not page_url:
            page_url = text_data.get("url", "")
        if not page_title:
            page_title = text_data.get("title", "")

        return {
            "url": page_url,
            "title": page_title,
            "refs": refs,
            "text": page_text[:2000] if page_text else "",
        }
