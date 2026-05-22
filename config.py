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
MIN_STEPS_BEFORE_FINISH = 30  # 兜底默认值，实际由 estimate_min_steps() 从文档动态计算
LLM_MODEL = "deepseek-ai/DeepSeek-V3"

# Guardrail：禁止 Agent 访问的 URL 模式
BLOCKED_URL_PATTERNS = [
    "projectId=25",
]

# 跨测试会话复用登录态
COOKIES_FILE = BASE_DIR / "session_cookies.json"

# tasks 过期清理：保留最近 MAX_TASKS 个任务
MAX_TASKS = 20

# ---------- 子流程卡死检测 ----------
SUB_FLOW_STUCK_WINDOW = 4        # 回看最近 N 步检测卡死
SUB_FLOW_STUCK_CLICK_RATIO = 0.75  # click 操作占比 >= 此值视为重复

# ---------- LLM 消息历史压缩 ----------
HISTORY_COMPRESS_THRESHOLD = 80000    # 总字符超过此值触发压缩
HISTORY_COMPRESS_THRESHOLD_L2 = 120000  # 二级压缩阈值
HISTORY_KEEP_RECENT = 20              # 一级压缩保留最近 N 条
HISTORY_KEEP_RECENT_L2 = 12          # 二级压缩保留最近 N 条
HISTORY_MAX_SUMMARY_LINES = 30       # 压缩摘要最多保留的操作行数

# ---------- LLM 调用 ----------
LLM_MAX_RETRIES = 3                  # LLM API 调用最大重试次数
LLM_TEMPERATURE = 0.2
LLM_MAX_TOKENS = 1500
LLM_RATE_LIMIT_BASE_WAIT = 15       # 限流时基础等待秒数（实际 = base * attempt）

# ---------- 批量执行 ----------
MAX_ACTIONS_PER_STEP = 4            # 每步最多批量执行的 action 数

# ---------- 日志 ----------
LOG_LEVEL = "DEBUG"                  # DEBUG / INFO / WARNING / ERROR
