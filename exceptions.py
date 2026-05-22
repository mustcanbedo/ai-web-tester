"""
AI Web Tester - 统一异常体系
所有 bridge / executor 层的可预见异常统一使用此模块的异常类。
"""


class BridgeError(Exception):
    """playwright_bridge 层面的异常基类"""

    def __init__(self, message: str, action: str = "", ref: str = "", recoverable: bool = True):
        self.action = action
        self.ref = ref
        self.recoverable = recoverable
        super().__init__(message)

    def to_result(self) -> dict:
        """转换为标准 result dict，供 action_executor 直接返回"""
        return {
            "success": False,
            "message": str(self),
            "error_type": self.__class__.__name__,
            "recoverable": self.recoverable,
        }


class ElementNotFoundError(BridgeError):
    """元素未找到（ref 不存在或已过期）"""
    pass


class ElementInteractionError(BridgeError):
    """元素存在但交互失败（点击被遮挡、不可见等）"""
    pass


class NavigationError(BridgeError):
    """导航相关错误（超时、URL 无效等）"""
    pass


class PageCrashedError(BridgeError):
    """页面崩溃或浏览器上下文已销毁"""

    def __init__(self, message: str = "页面崩溃或浏览器上下文不可用"):
        super().__init__(message, recoverable=False)
