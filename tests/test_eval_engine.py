"""测试 eval_engine.py 评估模块"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval_engine import evaluate_test_run, format_eval_report, _extract_flows_from_spec


class TestExtractFlows:

    def test_chinese_flow_format(self):
        spec = """
## 流程1：创建订单
步骤...
## 流程2：查看订单列表
步骤...
## 流程3：删除订单
步骤...
"""
        flows = _extract_flows_from_spec(spec)
        assert len(flows) == 3
        assert "创建订单" in flows[0]

    def test_numbered_format(self):
        spec = """
## 1. Create Order
steps...
## 2. View Orders
steps...
"""
        flows = _extract_flows_from_spec(spec)
        assert len(flows) == 2

    def test_fallback_h2(self):
        spec = """
## 登录功能
步骤...
## 搜索功能
步骤...
"""
        flows = _extract_flows_from_spec(spec)
        assert len(flows) == 2


class TestEvaluateTestRun:

    def _sample_eval(self, **overrides):
        defaults = dict(
            spec_content="## 流程1：创建\n## 流程2：查看\n## 流程3：删除",
            action_history=[
                {"step": 0, "action": {"type": "navigate"}, "flow": "创建", "thinking": "ok", "result": {"success": True}},
                {"step": 1, "action": {"type": "click"}, "flow": "创建", "thinking": "ok", "result": {"success": True}},
                {"step": 2, "action": {"type": "click"}, "flow": "查看", "thinking": "ok", "result": {"success": True}},
            ],
            all_issues=[
                {"severity": "P1", "flow": "创建", "title": "Bug1", "description": "描述一段足够长的bug说明"},
            ],
            completed_flows=["创建"],
            failed_flows=[{"flow": "查看", "reason": "元素不存在"}],
            total_steps=10,
            min_steps=5,
            token_usage={"input_tokens": 5000, "output_tokens": 2000},
        )
        defaults.update(overrides)
        return evaluate_test_run(**defaults)

    def test_returns_all_keys(self):
        m = self._sample_eval()
        assert "goal_completion" in m
        assert "action_efficiency" in m
        assert "bug_report_quality" in m
        assert "stuck_rate" in m
        assert "flow_pass_rate" in m
        assert "token_efficiency" in m
        assert "overall_score" in m

    def test_overall_score_range(self):
        m = self._sample_eval()
        assert 0 <= m["overall_score"] <= 100

    def test_coverage_ratio(self):
        m = self._sample_eval()
        # touched: 创建 + 查看 = 2 out of 3
        assert m["goal_completion"]["coverage_ratio"] >= 0.5

    def test_flow_pass_rate(self):
        m = self._sample_eval()
        # 1 passed, 1 failed
        assert m["flow_pass_rate"]["pass_ratio"] == 0.5

    def test_stuck_rate_zero_when_no_stuck(self):
        m = self._sample_eval()
        assert m["stuck_rate"]["stuck_events"] == 0

    def test_stuck_rate_nonzero(self):
        actions = [
            {"step": 0, "action": {"type": "click"}, "flow": "f", "thinking": "卡死了", "result": {"success": False}},
            {"step": 1, "action": {"type": "click"}, "flow": "f", "thinking": "ok", "result": {"success": True}},
        ]
        m = self._sample_eval(action_history=actions, total_steps=2)
        assert m["stuck_rate"]["stuck_events"] == 1

    def test_token_efficiency(self):
        m = self._sample_eval()
        assert m["token_efficiency"]["total_tokens"] == 7000
        assert m["token_efficiency"]["tokens_per_step"] == 700

    def test_perfect_score_scenario(self):
        """所有流程都通过、无卡死、有效步数=最小步数"""
        m = self._sample_eval(
            completed_flows=["创建", "查看", "删除"],
            failed_flows=[],
            total_steps=5,
            min_steps=5,
        )
        assert m["overall_score"] >= 80

    def test_no_issues_quality_is_1(self):
        m = self._sample_eval(all_issues=[])
        assert m["bug_report_quality"]["quality_ratio"] == 1.0


class TestFormatEvalReport:

    def test_format_returns_markdown(self):
        m = evaluate_test_run(
            spec_content="## 流程1：测试",
            action_history=[],
            all_issues=[],
            completed_flows=[],
            failed_flows=[],
            total_steps=5,
            min_steps=3,
        )
        report = format_eval_report(m)
        assert "## 📊 测试质量评估" in report
        assert "综合评分" in report
        assert "/100" in report
