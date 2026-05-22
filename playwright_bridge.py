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
from logger import get_logger
from exceptions import BridgeError, ElementNotFoundError, ElementInteractionError, NavigationError, PageCrashedError

logger = get_logger("playwright_bridge")


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
        # 多标签页支持
        self._pages: list = []  # 所有打开的页面
        self._page_index: int = 0  # 当前活跃页面索引
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
        self._pages = [self._page]
        self._page_index = 0
        # 创建 CDP session 用于获取 Accessibility Tree
        self._cdp = self._context.new_cdp_session(self._page)
        # 监听 window.open 打开的新页面
        self._context.on("page", self._on_new_page)
        return True

    def _on_new_page(self, page: Page):
        """window.open 打开新页面时自动捕获"""
        page.wait_for_load_state("domcontentloaded")
        self._pages.append(page)
        new_idx = len(self._pages) - 1
        logger.debug(f"检测到新标签页: index={new_idx} url={page.url}")
        # 自动切换到新页面
        self._switch_to_page(new_idx)
        logger.debug(f"已自动切换到新标签页 index={new_idx}")

    def _switch_to_page(self, index: int):
        """内部方法：切换到指定索引的页面"""
        if index < 0 or index >= len(self._pages):
            raise ValueError(f"无效的标签页索引: {index}，当前共 {len(self._pages)} 个标签页")
        self._page = self._pages[index]
        self._page_index = index
        # 重建 CDP session
        try:
            if self._cdp:
                self._cdp.detach()
        except:
            pass
        self._cdp = self._context.new_cdp_session(self._page)
        self._ref_map = {}
        self._ref_counter = 0

    def switch_tab(self, index: int = -1):
        """切换到指定标签页。index=-1 表示最新打开的标签页"""
        if index == -1:
            index = len(self._pages) - 1
        self._switch_to_page(index)
        self._page.bring_to_front()
        return {"success": True, "tab_index": index, "url": self._page.url, "total_tabs": len(self._pages)}

    def close_tab(self, index: int = -1):
        """关闭指定标签页并切回上一个。index=-1 表示当前标签页"""
        if index == -1:
            index = self._page_index
        if len(self._pages) <= 1:
            raise ValueError("不能关闭最后一个标签页")
        page_to_close = self._pages.pop(index)
        page_to_close.close()
        # 切换到前一个标签页
        new_index = min(index, len(self._pages) - 1)
        if new_index < 0:
            new_index = 0
        self._switch_to_page(new_index)
        return {"success": True, "closed_index": index, "current_index": new_index, "total_tabs": len(self._pages)}

    def get_tab_info(self):
        """获取所有标签页信息"""
        tabs = []
        for i, p in enumerate(self._pages):
            try:
                tabs.append({"index": i, "url": p.url, "title": p.title(), "active": i == self._page_index})
            except:
                tabs.append({"index": i, "url": "(closed)", "title": "", "active": i == self._page_index})
        return tabs

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

        try:
            title = self._page.title()
        except Exception:
            title = ""
        return {"url": self._page.url, "title": title}

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
        ax_backend_ids = set()  # 记录 AX Tree 已包含的 backendNodeId
        for ax_node in ax_nodes:
            role = ax_node.get("role", {}).get("value", "")
            name = ax_node.get("name", {}).get("value", "")
            ignored = ax_node.get("ignored", False)

            if ignored:
                continue

            if filter_interactive and role not in self.INTERACTIVE_ROLES:
                # 额外检查：元素是否 focusable（如有 tabindex 的元素）
                props = ax_node.get("properties", [])
                is_focusable = any(
                    p.get("name") == "focusable" and p.get("value", {}).get("value") == True
                    for p in props
                )
                if not is_focusable:
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

            # 对 name 为空的交互元素，尝试从 DOM 子元素补充名称
            backend_node_id = ax_node.get("backendDOMNodeId")
            if not name and backend_node_id and role in ("button", "link", "tab"):
                try:
                    resolve_result = self._cdp.send("DOM.resolveNode", {"backendNodeId": backend_node_id})
                    object_id = resolve_result.get("object", {}).get("objectId")
                    if object_id:
                        fn_result = self._cdp.send("Runtime.callFunctionOn", {
                            "objectId": object_id,
                            "functionDeclaration": """function() {
                                // 0) aria-label / title 最高优先级（比 haspopup 泛化标注更精确）
                                var label = this.getAttribute('aria-label') || this.getAttribute('title') || '';
                                if (label) return label;
                                // 1) aria-haspopup 且无 aria-label → 标注为 [更多操作]（Popover/Dropdown trigger）
                                var haspopup = this.getAttribute('aria-haspopup');
                                if (haspopup && (haspopup === 'dialog' || haspopup === 'menu' || haspopup === 'true')) {
                                    return '[更多操作]';
                                }
                                // 2) Iconify v4: <svg data-icon="mdi:xxx">
                                var svgV4 = this.querySelector('svg[data-icon]');
                                if (svgV4) {
                                    var iconName = svgV4.getAttribute('data-icon') || '';
                                    if (iconName) return '[' + iconName + ']';
                                }
                                // 3) 子元素的 icon 属性（未渲染的 Iconify）
                                var iconEl = this.querySelector('[icon]');
                                if (iconEl) {
                                    var iName = iconEl.getAttribute('icon') || '';
                                    if (iName) return '[' + iName + ']';
                                }
                                // 4) SVG title / img alt
                                var svgTitle = this.querySelector('svg title');
                                if (svgTitle && svgTitle.textContent) return svgTitle.textContent;
                                var img = this.querySelector('img');
                                if (img && img.alt) return img.alt;
                                // 5) 位序 + 上下文推断
                                var el = this;
                                // 5a) 向上找最近的「卡片级」容器（通过 class 特征或语义结构）
                                var card = el.parentElement;
                                for (var up = 0; up < 10 && card; up++) {
                                    var cls = card.className || '';
                                    // 卡片特征：class 含 card/rounded+shadow/item 等
                                    if (/\bcard\b|\brounded.*shadow|\bshadow.*rounded|\blist-item\b/.test(cls)) break;
                                    // 或者有 h1-h6 标题 + 按钮（语义卡片）
                                    var hasHeading = card.querySelector('h1,h2,h3,h4,h5,h6');
                                    var hasBtns = card.querySelectorAll('button').length >= 1;
                                    if (hasHeading && hasBtns && card.querySelectorAll('button').length <= 8) break;
                                    card = card.parentElement;
                                }
                                if (!card) return '';
                                // 5b) 在卡片内收集按钮（排除弹层内的）
                                var allBtns = Array.from(card.querySelectorAll('button'));
                                allBtns = allBtns.filter(function(b) {
                                    var layer = b.closest('[role=dialog],[role=menu],[data-radix-popper-content-wrapper]');
                                    return !layer || layer === card;
                                });
                                var idx = allBtns.indexOf(el);
                                if (idx === -1) return '';
                                // 5c) 提取上下文：卡片内的标题文本
                                var ctx = '';
                                var h = card.querySelector('h1,h2,h3,h4,h5,h6,[class*=title],[class*=name],[class*=card-header]');
                                if (h && h.textContent.trim()) {
                                    ctx = h.textContent.trim().substring(0, 30);
                                }
                                // 5d) 构建结果
                                if (allBtns.length === 1) {
                                    return ctx ? '[唯一按钮 @ ' + ctx + ']' : '[唯一按钮]';
                                }
                                var result = '[按钮' + (idx+1) + '/' + allBtns.length;
                                if (ctx) result += ' @ ' + ctx;
                                result += ']';
                                return result;
                            }""",
                            "returnByValue": True,
                        })
                        inferred_name = fn_result.get("result", {}).get("value", "")
                        if inferred_name:
                            name = inferred_name
                            ref_entry["name"] = name
                            logger.debug(f"补充按钮名称: ref={ref_id} role={role} → name='{name}' (via CDP)")
                except Exception as ex:
                    logger.debug(f"补充按钮名称失败: ref={ref_id} backendNodeId={backend_node_id} error={ex}")

            refs.append(ref_entry)

            # 存储 backendDOMNodeId 用于后续精确定位
            self._ref_map[ref_id] = {
                "role": role,
                "name": name,
                "backendDOMNodeId": backend_node_id,
            }

        # 后处理：一次 JS 调用发现所有非标准可点击元素（div/@click, h3/@click 等）
        # 检测方式：cursor:pointer / el.onclick / Vue3 _vei / React props
        # 全部在浏览器进程内执行，无 CDP round-trip，耗时 ~50ms
        if filter_interactive:
            try:
                clickable_elements = self._page.evaluate("""() => {
                    const nativeTags = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','LABEL','OPTION']);
                    const results = [];
                    const markedParents = new Set();
                    let scanned = 0, skippedNative = 0, skippedHidden = 0, skippedSmall = 0;
                    let skippedNotClickable = 0, skippedChild = 0, skippedNoText = 0;
                    for (const el of document.querySelectorAll('*')) {
                        scanned++;
                        if (nativeTags.has(el.tagName)) { skippedNative++; continue; }
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') { skippedHidden++; continue; }
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 10 || rect.height < 10) { skippedSmall++; continue; }
                        if (rect.bottom < 0 || rect.top > window.innerHeight) { skippedSmall++; continue; }
                        // 检测是否真正可点击
                        let clickable = false;
                        let reason = '';
                        // 1) cursor: pointer
                        if (style.cursor === 'pointer') { clickable = true; reason = 'cursor'; }
                        // 2) 内联 onclick
                        if (!clickable && el.onclick) { clickable = true; reason = 'onclick'; }
                        // 3) Vue 3: @click 编译为 _vei (Vue Event Invoker)
                        if (!clickable && el._vei) {
                            if (el._vei.onClick || el._vei.onClickCapture) { clickable = true; reason = 'vue_vei'; }
                        }
                        // 3b) Vue 3 备选：检查 __vueParentComponent 上的事件
                        if (!clickable) {
                            for (const key of Object.keys(el)) {
                                if (key.startsWith('__vueParentComponent') || key.startsWith('__vue')) {
                                    // Vue 组件实例存在，检查 cursor:pointer 即可
                                    break;
                                }
                            }
                        }
                        // 4) React: onClick 存储在 __reactProps$ 或 __reactEvents$
                        if (!clickable) {
                            for (const key of Object.keys(el)) {
                                if ((key.startsWith('__reactProps$') || key.startsWith('__reactEvents$')) && el[key] && el[key].onClick) {
                                    clickable = true; reason = 'react'; break;
                                }
                            }
                        }
                        if (!clickable) { skippedNotClickable++; continue; }
                        // 避免子元素重复：如果父级已被标记，跳过
                        let parent = el.parentElement;
                        let skip = false;
                        while (parent) {
                            if (markedParents.has(parent)) { skip = true; break; }
                            parent = parent.parentElement;
                        }
                        if (skip) { skippedChild++; continue; }
                        // 获取有意义的文本
                        let text = (el.getAttribute('alt') || el.getAttribute('title') || '').trim();
                        if (!text) {
                            text = el.innerText ? el.innerText.trim().substring(0, 80) : '';
                        }
                        if (!text) { skippedNoText++; continue; }
                        markedParents.add(el);
                        // 将文本编码到属性中，避免 querySelectorAll 顺序不一致
                        const idx = results.length;
                        el.setAttribute('data-ai-ref', 'c' + idx);
                        results.push({ tag: el.tagName.toLowerCase(), text: text, reason: reason });
                    }
                    return {
                        items: results,
                        stats: { scanned, skippedNative, skippedHidden, skippedSmall, skippedNotClickable, skippedChild, skippedNoText, found: results.length }
                    };
                }""")
                items = clickable_elements.get("items", []) if isinstance(clickable_elements, dict) else []
                stats = clickable_elements.get("stats", {}) if isinstance(clickable_elements, dict) else {}
                logger.debug(f"clickable 检测统计: {stats}")
                if items:
                    # 逐个通过 data-ai-ref='cN' 精确匹配，避免顺序问题
                    doc = self._cdp.send("DOM.getDocument", {"depth": 0})
                    root_id = doc["root"]["nodeId"]
                    added = 0
                    existing_bids = {v.get("backendDOMNodeId") for v in self._ref_map.values()}
                    for i, info in enumerate(items):
                        try:
                            query_result = self._cdp.send("DOM.querySelectorAll", {
                                "nodeId": root_id,
                                "selector": f"[data-ai-ref='c{i}']",
                            })
                            node_ids = query_result.get("nodeIds", [])
                            if not node_ids:
                                continue
                            desc = self._cdp.send("DOM.describeNode", {"nodeId": node_ids[0]})
                            bid = desc.get("node", {}).get("backendNodeId")
                            if not bid or bid in existing_bids:
                                continue
                            ref_id = f"e{self._ref_counter}"
                            self._ref_counter += 1
                            refs.append({"ref": ref_id, "role": "clickable", "name": info["text"]})
                            self._ref_map[ref_id] = {
                                "role": "clickable",
                                "name": info["text"],
                                "backendDOMNodeId": bid,
                            }
                            existing_bids.add(bid)
                            added += 1
                        except Exception as ex:
                            logger.debug(f"clickable element {i} ({info.get('text','')[:30]}) 获取 nodeId 失败: {ex}")
                    # 清理标记
                    self._page.evaluate("document.querySelectorAll('[data-ai-ref]').forEach(el => el.removeAttribute('data-ai-ref'))")
                    if added:
                        logger.debug(f"snapshot: 额外添加 {added} 个可点击元素（Vue/React/cursor:pointer）")
                    else:
                        logger.debug(f"snapshot: JS 检测到 {len(items)} 个可点击元素，但 0 个成功添加到 refs（全部 backendNodeId 冲突或获取失败）")
                        for info in items[:5]:
                            logger.debug(f"  - <{info['tag']}> '{info['text'][:40]}' reason={info['reason']}")
            except Exception as e:
                logger.debug(f"snapshot: 可点击元素检测失败: {e}", exc_info=True)

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
            raise ElementNotFoundError(f"未知的 ref: {ref}，请先调用 snapshot 刷新元素列表", action="locate", ref=ref)

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
                    logger.debug(f"_get_locator({ref}): CDP 精确定位成功")
                    return locator
                else:
                    logger.debug(f"_get_locator({ref}): CDP 注入属性后 count={locator.count()}, fallback")
            except Exception as e:
                logger.debug(f"_get_locator({ref}): CDP 定位异常: {e}, fallback")

        # Fallback: 使用 get_by_role 定位
        role = info["role"]
        name = info["name"]

        # 自定义角色（如 clickable）无法用 get_by_role，使用文本定位
        if role == "clickable":
            if name:
                locator = self._page.get_by_text(name, exact=False)
            else:
                raise ElementNotFoundError(f"ref {ref} 角色为 clickable 但无名称，无法 fallback 定位", action="locate", ref=ref)
            logger.debug(f"_get_locator({ref}): clickable fallback, get_by_text('{name[:30]}')")
        elif name:
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
                logger.debug(f"_get_locator({ref}): get_by_role fallback, 多个匹配({count}), 取第 {ref_idx} 个")
            else:
                locator = locator.first
                logger.debug(f"_get_locator({ref}): get_by_role fallback, 多个匹配({count}), idx={ref_idx} 超出, 取 first")
        else:
            logger.debug(f"_get_locator({ref}): get_by_role fallback, 唯一匹配")

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
        elif kind == "go_back":
            return self.go_back()
        elif kind == "send_keys":
            return self.send_keys(key or "")
        else:
            raise BridgeError(f"未知的动作类型: {kind}", action=kind)

    def _cdp_click_element(self, locator):
        """通过 CDP Input.dispatchMouseEvent 发送原生鼠标事件（比 JS dispatchEvent 更底层）"""
        # 确保元素在视口内，否则 bounding_box 坐标可能超出 viewport 导致点击偏移
        try:
            locator.evaluate("el => el.scrollIntoViewIfNeeded()")
        except Exception:
            pass
        # 获取元素中心坐标
        box = locator.bounding_box(timeout=3000)
        if not box:
            raise ElementInteractionError("无法获取元素 bounding box", action="cdp_click")
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        # 完整的鼠标事件序列：mouseMoved → mousePressed → mouseReleased
        self._cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": x, "y": y,
        })
        time.sleep(0.05)
        self._cdp.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        time.sleep(0.05)
        self._cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })

    def _get_popup_state(self, locator):
        """检查元素或其最近 3 层祖先的 data-state 属性"""
        return locator.evaluate("""el => {
            let s = el.getAttribute('data-state');
            if (!s) {
                let p = el.parentElement;
                for (let i = 0; i < 3 && p; i++) {
                    s = p.getAttribute('data-state');
                    if (s) break;
                    p = p.parentElement;
                }
            }
            return s;
        }""")

    def click(self, ref):
        locator = self._get_locator(ref)

        # 检测目标是否为 Popover / DropdownMenu trigger（检查自身和最近 3 层祖先）
        is_popup_trigger = False
        try:
            popup_info = locator.evaluate("""el => {
                // 检查自身
                let haspopup = el.getAttribute('aria-haspopup');
                let dataState = el.getAttribute('data-state');
                // 检查祖先（某些组件库把 aria-haspopup 放在包裹元素上）
                if (!haspopup) {
                    let p = el.parentElement;
                    for (let i = 0; i < 3 && p; i++) {
                        haspopup = p.getAttribute('aria-haspopup');
                        if (haspopup) { dataState = p.getAttribute('data-state'); break; }
                        p = p.parentElement;
                    }
                }
                return { haspopup, dataState, tag: el.tagName, role: el.getAttribute('role') };
            }""")
            is_popup_trigger = popup_info.get('haspopup') in ('dialog', 'menu', 'true')
            if is_popup_trigger:
                logger.debug(f"click {ref}: 检测到 popup trigger (haspopup={popup_info.get('haspopup')}, state={popup_info.get('dataState')}, tag={popup_info.get('tag')})")
        except Exception as e:
            logger.debug(f"click {ref}: popup 检测异常: {e}")

        clicked_ok = False
        # ---- 降级链 ----
        # Level 1: Playwright 原生 click（最可靠，有 auto-wait + actionability check）
        try:
            locator.click(timeout=5000)
            clicked_ok = True
        except Exception as e:
            logger.debug(f"click {ref}: locator.click 失败({e})，降级为 CDP 原生点击")

        # Level 2: CDP Input.dispatchMouseEvent（原生鼠标事件，绕过 hit-testing）
        if not clicked_ok:
            try:
                self._cdp_click_element(locator)
                clicked_ok = True
                logger.debug(f"click {ref}: CDP 原生点击成功")
            except Exception as e:
                logger.debug(f"click {ref}: CDP 点击失败({e})，降级为 el.click()")

        # Level 3: JS el.click()（最后手段，不触发 pointer 事件）
        if not clicked_ok:
            try:
                locator.evaluate("el => el.click()")
                clicked_ok = True
                logger.debug(f"click {ref}: el.click() 降级成功")
            except Exception as e2:
                logger.debug(f"click {ref}: el.click() 也失败: {e2}")

        # ---- Popover/DropdownMenu trigger 验证 ----
        if clicked_ok and is_popup_trigger:
            time.sleep(0.3)
            try:
                state = self._get_popup_state(locator)
                if state != 'open':
                    logger.debug(f"click {ref}: popup trigger 点击后 data-state={state}，用 CDP 重试")
                    try:
                        self._cdp_click_element(locator)
                    except Exception:
                        locator.evaluate("el => el.click()")
                    time.sleep(0.3)
                    state2 = self._get_popup_state(locator)
                    if state2 != 'open':
                        # 最后尝试：keyboard Enter
                        logger.debug(f"click {ref}: CDP 重试后 data-state={state2}，尝试 focus+Enter")
                        try:
                            locator.focus()
                            self._page.keyboard.press("Enter")
                            time.sleep(0.3)
                        except Exception:
                            pass
                        state3 = self._get_popup_state(locator)
                        logger.debug(f"click {ref}: focus+Enter 后 data-state={state3}")
                    else:
                        logger.debug(f"click {ref}: CDP 重试成功 data-state=open")
            except Exception as e:
                logger.debug(f"click {ref}: data-state 检查异常: {e}")

        if not clicked_ok:
            return {"success": False, "message": f"click {ref}: 三级降级均失败"}
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
        # 补充触发 input+change 事件，确保 Vue v-model / 组件库正确更新响应式状态
        try:
            locator.evaluate("""el => {
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""")
        except:
            pass
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

    def go_back(self):
        """浏览器后退"""
        if not self._page:
            return {"success": False, "message": "无活跃页面"}
        try:
            self._page.go_back(wait_until="domcontentloaded", timeout=10000)
        except Exception as e:
            logger.debug(f"go_back 异常（可能无历史记录）: {e}")
        return {"success": True, "url": self._page.url, "title": self._page.title()}

    def send_keys(self, keys):
        """发送键盘按键序列，支持空格分隔的多个按键（如 "Tab Tab Enter"）"""
        if not self._page:
            return {"success": False, "message": "无活跃页面"}
        key_list = keys.split()
        for key in key_list:
            self._page.keyboard.press(key)
            time.sleep(0.1)
        return {"success": True, "keys": keys}

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------

    def screenshot(self, quality=80):
        """获取页面截图，返回 base64 编码的 JPEG（含重试）"""
        if not self._page:
            return ""
        for attempt in range(3):
            try:
                raw = self._page.screenshot(type="jpeg", quality=quality, full_page=False)
                return base64.b64encode(raw).decode("utf-8")
            except Exception as ex:
                if attempt < 2:
                    time.sleep(0.5)
                else:
                    logger.warning(f"screenshot 失败(3次重试后): {ex}")
                    return ""

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

        result = {
            "url": page_url,
            "title": page_title,
            "refs": refs,
            "text": page_text[:2000] if page_text else "",
        }
        # 多标签页信息
        if len(self._pages) > 1:
            result["tabs"] = self.get_tab_info()
        return result
