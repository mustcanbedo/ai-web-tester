"""
AI Web Tester - 全局配置
"""

from pathlib import Path

BASE_DIR = Path(__file__).parent
SCREENSHOT_DIR = BASE_DIR / "screenshots"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR = BASE_DIR / "logs"
SPECS_DIR = BASE_DIR / "specs"
SNAPSHOT_DIR = BASE_DIR / "snapshots"
VIDEO_DIR = BASE_DIR / "videos"

for d in [SCREENSHOT_DIR, REPORT_DIR, LOG_DIR, SPECS_DIR, SNAPSHOT_DIR, VIDEO_DIR]:
    d.mkdir(exist_ok=True)

MAX_STEPS = 500  # 安全上限，正常情况下由智能终止检测决定何时停止
STUCK_THRESHOLD = 5  # 连续相同操作 / 连续失败 / 连续 wait 达到此数则强制终止
MIN_STEPS_BEFORE_FINISH = 20  # LLM 至少跑这么多步才允许 finish
LLM_MODEL = "deepseek-ai/DeepSeek-V3"

# 跨测试会话复用登录态
COOKIES_FILE = BASE_DIR / "session_cookies.json"

# tasks 过期清理：保留最近 MAX_TASKS 个任务
MAX_TASKS = 20
