"""测试 exceptions.py 异常体系"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exceptions import BridgeError, ElementNotFoundError, ElementInteractionError, NavigationError, PageCrashedError


def test_bridge_error_to_result():
    err = BridgeError("something failed", action="click", ref="r1")
    result = err.to_result()
    assert result["success"] is False
    assert "something failed" in result["message"]
    assert result["error_type"] == "BridgeError"
    assert result["recoverable"] is True


def test_element_not_found_is_bridge_error():
    err = ElementNotFoundError("未知的 ref: r99", action="locate", ref="r99")
    assert isinstance(err, BridgeError)
    assert err.ref == "r99"
    assert err.recoverable is True


def test_page_crashed_not_recoverable():
    err = PageCrashedError()
    assert err.recoverable is False
    result = err.to_result()
    assert result["recoverable"] is False


def test_inheritance_chain():
    """所有自定义异常都是 BridgeError 的子类"""
    for cls in [ElementNotFoundError, ElementInteractionError, NavigationError, PageCrashedError]:
        assert issubclass(cls, BridgeError)
        assert issubclass(cls, Exception)
