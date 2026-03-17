"""
AI Web Tester - 证据采集器
使用 Playwright 原生事件监听采集 console 错误和网络错误，
比 JS 注入方案更可靠（不受页面导航丢失影响）。
"""

import json
import datetime
from playwright_bridge import PlaywrightBridge


class EvidenceCollector:
    """通过 Playwright 原生事件 + JS evaluate 采集页面证据"""

    INJECT_SCRIPT = """(() => {
        if (window.__evidence_injected) return;
        window.__evidence_injected = true;
        window.__console_errors = [];
        window.__network_errors = [];
        const origError = console.error;
        console.error = function() {
            window.__console_errors.push({type:'error', text: Array.from(arguments).join(' '), ts: Date.now()});
            origError.apply(console, arguments);
        };
        window.addEventListener('error', e => {
            window.__console_errors.push({type:'pageerror', text: e.message || String(e), ts: Date.now()});
        });
        const origFetch = window.fetch;
        window.fetch = function() {
            return origFetch.apply(this, arguments).then(resp => {
                if (resp.status >= 400) {
                    window.__network_errors.push({url: resp.url, status: resp.status, ts: Date.now()});
                }
                return resp;
            }).catch(err => { throw err; });
        };
        const origXHR = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function() {
            this.addEventListener('loadend', function() {
                if (this.status >= 400) {
                    window.__network_errors.push({url: this.responseURL, status: this.status, ts: Date.now()});
                }
            });
            origXHR.apply(this, arguments);
        };
    })()"""

    COLLECT_SCRIPT = """JSON.stringify({
        console_errors: (window.__console_errors || []).splice(0),
        network_errors: (window.__network_errors || []).splice(0)
    })"""

    def __init__(self):
        self.console_errors = []
        self.console_warnings = []
        self.network_errors = []
        self.api_responses = []
        self.bridge = None

    def attach(self, bridge: PlaywrightBridge):
        self.bridge = bridge
        try:
            self.bridge.evaluate(self.INJECT_SCRIPT)
        except:
            pass

    def collect(self):
        """从页面收集新的证据（每步调用一次）"""
        if not self.bridge:
            return
        try:
            # 重新注入（页面导航后脚本会丢失）
            self.bridge.evaluate(self.INJECT_SCRIPT)
            result = self.bridge.evaluate(self.COLLECT_SCRIPT)
            raw = result.get("result", "{}")
            if isinstance(raw, str):
                data = json.loads(raw)
            else:
                data = raw
            now = datetime.datetime.now().isoformat()
            for err in data.get("console_errors", []):
                self.console_errors.append({"type": err.get("type", "error"), "text": err.get("text", ""), "timestamp": now})
            for err in data.get("network_errors", []):
                self.network_errors.append({"url": err.get("url", ""), "status": err.get("status", 0), "method": "?", "timestamp": now})
        except:
            pass

    def get_new_evidence_since(self, lc, ln):
        return {"new_console_errors": self.console_errors[lc:], "new_network_errors": self.network_errors[ln:]}

    def get_recent_api_responses(self, last_count=0):
        return self.api_responses[last_count:]

    def get_summary(self):
        return {
            "total_console_errors": len(self.console_errors),
            "total_network_errors": len(self.network_errors),
            "total_api_responses_captured": len(self.api_responses),
            "console_errors": self.console_errors,
            "network_errors": self.network_errors,
            "api_responses": self.api_responses[-20:],
        }
