"""测试 llm_engine.py 核心函数（不涉及实际 LLM 调用）"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import patch, MagicMock
from llm_engine import LLMEngine


def _make_engine():
    """创建一个不连接 LLM 的 engine 实例"""
    with patch("llm_engine.OpenAI"):
        engine = LLMEngine(api_key="fake", base_url="http://fake", model="test-model")
    return engine


class TestRobustJsonParse:
    """测试 _robust_json_parse 的各种 LLM 输出格式"""

    def setup_method(self):
        self.engine = _make_engine()

    def test_normal_json(self):
        content = json.dumps({"thinking": "ok", "action": {"type": "click", "params": {"ref": "r1"}}})
        result = self.engine._robust_json_parse(content)
        assert result["thinking"] == "ok"
        assert result["action"]["type"] == "click"

    def test_markdown_fenced(self):
        content = '```json\n{"thinking": "test", "action": {"type": "wait", "params": {"ms": 1000}}}\n```'
        result = self.engine._robust_json_parse(content)
        assert result["thinking"] == "test"
        assert result["action"]["type"] == "wait"

    def test_trailing_comma(self):
        content = '{"thinking": "test", "action": {"type": "click", "params": {"ref": "r1"},},}'
        result = self.engine._robust_json_parse(content)
        assert result["action"]["type"] == "click"

    def test_truncated_json(self):
        content = '{"thinking": "test", "action": {"type": "wait", "params": {"ms": 1000}}'
        # Missing closing brace
        result = self.engine._robust_json_parse(content)
        assert result["thinking"] == "test"

    def test_js_comment(self):
        content = '{"thinking": "test", "action": {"type": "click", "params": {"ref": "r1"}} // this is a comment\n}'
        result = self.engine._robust_json_parse(content)
        assert result["action"]["type"] == "click"

    def test_totally_broken_falls_back_to_regex(self):
        content = 'This is not JSON at all but has "thinking": "fallback" and "action": {"type": "wait"} somewhere'
        result = self.engine._robust_json_parse(content)
        assert result["thinking"] == "fallback"
        assert result["action"]["type"] == "wait"


class TestSmartTruncateRefs:
    """测试 _smart_truncate_refs"""

    def setup_method(self):
        self.engine = _make_engine()

    def test_no_truncation_needed(self):
        refs = [{"ref": f"r{i}", "role": "button", "name": f"btn{i}"} for i in range(10)]
        result_refs, note = self.engine._smart_truncate_refs(refs, max_total=50)
        assert len(result_refs) == 10

    def test_truncation_preserves_non_option(self):
        refs = [{"ref": "r0", "role": "button", "name": "Submit"}]
        refs += [{"ref": f"r{i}", "role": "option", "name": f"opt{i}"} for i in range(1, 100)]
        result_refs, note = self.engine._smart_truncate_refs(refs, max_total=20)
        assert len(result_refs) <= 20
        # Non-option should be preserved
        assert any(r["ref"] == "r0" for r in result_refs)
