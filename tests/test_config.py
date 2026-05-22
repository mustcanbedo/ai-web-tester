"""测试 config.py 配置完整性"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def test_directories_exist():
    """所有配置目录应已创建"""
    assert config.SCREENSHOT_DIR.exists()
    assert config.REPORT_DIR.exists()
    assert config.LOG_DIR.exists()
    assert config.SPECS_DIR.exists()
    assert config.SNAPSHOT_DIR.exists()
    assert config.VIDEO_DIR.exists()


def test_thresholds_are_positive():
    assert config.MAX_STEPS > 0
    assert config.STUCK_THRESHOLD > 0
    assert config.MIN_STEPS_BEFORE_FINISH > 0
    assert config.SUB_FLOW_STUCK_WINDOW > 0
    assert config.SUB_FLOW_STUCK_CLICK_RATIO > 0
    assert config.MAX_ACTIONS_PER_STEP > 0


def test_llm_config():
    assert config.LLM_MAX_RETRIES >= 1
    assert 0 <= config.LLM_TEMPERATURE <= 2
    assert config.LLM_MAX_TOKENS > 0
    assert config.LLM_RATE_LIMIT_BASE_WAIT > 0


def test_history_config():
    assert config.HISTORY_COMPRESS_THRESHOLD > 0
    assert config.HISTORY_COMPRESS_THRESHOLD_L2 > config.HISTORY_COMPRESS_THRESHOLD
    assert config.HISTORY_KEEP_RECENT > 0
    assert config.HISTORY_KEEP_RECENT_L2 > 0
    assert config.HISTORY_KEEP_RECENT_L2 < config.HISTORY_KEEP_RECENT


def test_log_level_valid():
    assert config.LOG_LEVEL in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
