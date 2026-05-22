"""测试 action_executor.py 核心执行逻辑（mock bridge）"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, PropertyMock, patch
from pathlib import Path
import tempfile

from action_executor import ActionExecutor
from exceptions import ElementNotFoundError, ElementInteractionError


def _make_executor():
    """创建带 mock bridge 的 executor"""
    bridge = MagicMock()
    bridge._pages = [MagicMock()]
    bridge._page = bridge._pages[0]
    bridge._page.url = "http://example.com"
    tmpdir = Path(tempfile.mkdtemp())
    executor = ActionExecutor(bridge, tmpdir, "test-001")
    return executor, bridge


class TestExecuteClick:

    def test_click_success(self):
        executor, bridge = _make_executor()
        bridge.click.return_value = {"success": True}
        result = executor.execute({"type": "click", "params": {"ref": "r1"}})
        assert result["success"] is True
        assert "r1" in result["message"]
        bridge.click.assert_called_once_with("r1")

    def test_click_bridge_returns_failure(self):
        executor, bridge = _make_executor()
        bridge.click.return_value = {"success": False, "message": "click r1: 三级降级均失败"}
        result = executor.execute({"type": "click", "params": {"ref": "r1"}})
        assert result["success"] is False
        assert "三级降级" in result["message"]

    def test_click_element_not_found(self):
        executor, bridge = _make_executor()
        bridge.click.side_effect = ElementNotFoundError("未知的 ref: r99", action="click", ref="r99")
        bridge.screenshot.return_value = ""
        result = executor.execute({"type": "click", "params": {"ref": "r99"}})
        assert result["success"] is False
        assert "r99" in result["message"]
        assert result.get("error_type") == "ElementNotFoundError"
        assert result.get("recoverable") is True

    def test_click_interaction_error(self):
        executor, bridge = _make_executor()
        bridge.click.side_effect = ElementInteractionError("元素被遮挡", action="click", ref="r1")
        bridge.screenshot.return_value = ""
        result = executor.execute({"type": "click", "params": {"ref": "r1"}})
        assert result["success"] is False
        assert result.get("error_type") == "ElementInteractionError"


class TestExecuteNavigate:

    def test_navigate_success(self):
        executor, bridge = _make_executor()
        result = executor.execute({"type": "navigate", "params": {"url": "http://example.com/page"}})
        assert result["success"] is True
        bridge.navigate.assert_called_once_with("http://example.com/page")

    def test_navigate_blocked_url(self):
        executor, bridge = _make_executor()
        result = executor.execute({"type": "navigate", "params": {"url": "http://example.com?projectId=25"}})
        assert result["success"] is False
        assert "Guardrail" in result["message"]
        bridge.navigate.assert_not_called()


class TestExecuteFill:

    def test_fill_success(self):
        executor, bridge = _make_executor()
        result = executor.execute({"type": "fill", "params": {"ref": "r2", "text": "hello"}})
        assert result["success"] is True
        bridge.fill.assert_called_once_with("r2", "hello")


class TestExecuteWait:

    def test_wait_success(self):
        executor, bridge = _make_executor()
        result = executor.execute({"type": "wait", "params": {"ms": 100}})
        assert result["success"] is True


class TestExecuteUnknown:

    def test_unknown_action(self):
        executor, bridge = _make_executor()
        result = executor.execute({"type": "nonexistent_action", "params": {}})
        assert result["success"] is False
        assert "未知操作" in result["message"]


class TestExecuteGuardrail:

    def test_click_guardrail_post_check(self):
        """click 后 URL 进入禁区时应返回失败"""
        executor, bridge = _make_executor()
        bridge.click.return_value = {"success": True}
        # 模拟 click 后 URL 变为禁区
        bridge._page.url = "http://example.com?projectId=25"
        result = executor.execute({"type": "click", "params": {"ref": "r1"}})
        assert result["success"] is False
        assert "Guardrail" in result["message"]
