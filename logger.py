"""
AI Web Tester - 统一日志模块
所有模块统一使用 get_logger() 获取 logger，替代 print 调试输出。
日志同时输出到 console 和文件（logs/ai-web-tester.log）。
"""

import logging
import sys
from pathlib import Path
from config import LOG_DIR, LOG_LEVEL

_LOG_FILE = LOG_DIR / "ai-web-tester.log"
_INITIALIZED = False


def get_logger(name: str = "ai-web-tester") -> logging.Logger:
    """获取或创建一个 logger。各模块用自己的名字调用，如 get_logger("playwright_bridge")"""
    logger = logging.getLogger(name)

    global _INITIALIZED
    if not _INITIALIZED:
        _setup_root_logger()
        _INITIALIZED = True

    return logger


def _setup_root_logger():
    """初始化根 logger：console + 文件双输出"""
    root = logging.getLogger("ai-web-tester")
    root.setLevel(getattr(logging, LOG_LEVEL, logging.DEBUG))

    # 避免重复添加 handler
    if root.handlers:
        return

    fmt = logging.Formatter(
        "[%(asctime)s][%(name)s][%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File handler（追加模式，每次启动不清空）
    fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
