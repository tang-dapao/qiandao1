# -*- coding: utf-8 -*-
"""签到链路校验（D1/D2/D3，2026-10-28 用户需求）单测。

覆盖：
  D1 `_verify_signin_effective`：任务行 n/n 判生效 / 0/n 判未生效 / 读不到不判
  D2 `_verify_signin_ad_reward`：电量差值 ≥ 承诺值判到账 / 差额不足判未到账 /
     读数缺失跳过（绝不把「读不到」当「没到账」）
  D3 `_watch_signin_ad`：点击被吞 → 重取按钮补点一次；补点仍失败 → 告警
  开关 `signin_verify: false` → 三项全部不执行（一键回退）

运行：py -3.13 -m unittest test_signin_verify -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import Flow


def _n(text, y1=1700, y2=1800, x1=60, x2=900):
    return Node(text, x1, y1, x2, y2)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {"signin_verify": True, "signin_ad_reward": 10}
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f.ui = mock.Mock()
        self.f.ui.nodes.return_value = []
        self.f._watch = mock.Mock()
        self.f._find = mock.Mock(return_value=None)
        self.f._tap = mock.Mock()
        self.f._tap_node = mock.Mock()
        self.f._ocr_find = mock.Mock(return_value=None)
        self.f._read_energy = mock.Mock(return_value=None)
        self.f._ad_play_and_close = mock.Mock(return_value=True)
        self.f._find_task_row = mock.Mock(return_value=None)
        self.f._ad_not_opened = False
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


class TestD1SigninReadback(Base):
    def test_row_n_over_n_marks_ok(self):
        self.f._find_task_row.return_value = (
            _n("去签到"), "去签到", (1, 1))
        self.f._verify_signin_effective()
        self.f._watch.signin_verify.assert_called_once_with("signin_row", True)

    def test_row_not_done_warns_and_alerts(self):
        self.f._find_task_row.return_value = (
            _n("去签到"), "去签到", (0, 1))
        self.f._verify_signin_effective()
        args, kwargs = self.f._watch.signin_verify.call_args
        self.assertEqual(args[0], "signin_row")
        self.assertFalse(args[1])
        self.assertIn("0/1", kwargs.get("detail", ""))

    def test_row_missing_skips_without_alert(self):
        # 坑 17：任务行常被顶出可见区 —— 读不到 ≠ 未生效，必须跳过
        self.f._find_task_row.return_value = None
        self.f._verify_signin_effective()
        self.f._watch.signin_verify.assert_not_called()

    def test_row_without_ratio_skips(self):
        self.f._find_task_row.return_value = (_n("去签到"), "去签到", None)
        self.f._verify_signin_effective()
        self.f._watch.signin_verify.assert_not_called()

    def test_knob_off_does_nothing(self):
        self.f.wf["signin_verify"] = False
        self.f._verify_signin_effective()
        self.f._find_task_row.assert_not_called()
        self.f._watch.signin_verify.assert_not_called()


class TestD2AdReward(Base):
    def test_gain_reaches_promise_marks_ok(self):
        before, self.f._read_energy.return_value = 3400, 3410
        self.f._verify_signin_ad_reward(before)
        self.f._watch.signin_verify.assert_called_once_with("signin_ad", True)

    def test_gain_below_promise_alerts(self):
        before, self.f._read_energy.return_value = 3400, 3400
        self.f._verify_signin_ad_reward(before)
        args, kwargs = self.f._watch.signin_verify.call_args
        self.assertEqual(args[0], "signin_ad")
        self.assertFalse(args[1])
        self.assertIn("+0", kwargs.get("detail", ""))

    def test_first_zero_then_grace_read_hits(self):
        # 10-29 实测：15 次「恰好 +0」怀疑是电量显示未刷新。
        # 首读 3400（未变）、宽限期重读 3410 → 按「延迟到账」通过，不告警。
        self.f._read_energy.side_effect = [3400, 3410]
        self.f._verify_signin_ad_reward(3400)
        self.f._watch.signin_verify.assert_called_once_with("signin_ad", True)
        self.assertEqual(self.f._read_energy.call_count, 2)

    def test_grace_read_still_zero_alerts(self):
        # 重读后仍 +0 → 才判「确实未到账」，detail 明确说明已排除刷新因素
        self.f._read_energy.side_effect = [3400, 3400]
        self.f._verify_signin_ad_reward(3400)
        args, kwargs = self.f._watch.signin_verify.call_args
        self.assertFalse(args[1])
        self.assertIn("已排除", kwargs.get("detail", ""))

    def test_reread_disabled_only_reads_once(self):
        self.f.wf["signin_ad_reread"] = 0
        self.f._read_energy.return_value = 3400
        self.f._verify_signin_ad_reward(3400)
        self.assertEqual(self.f._read_energy.call_count, 1)
        self.assertFalse(self.f._watch.signin_verify.call_args[0][1])

    def test_missing_before_skips(self):
        self.f._read_energy.return_value = 3410
        self.f._verify_signin_ad_reward(None)
        self.f._watch.signin_verify.assert_not_called()

    def test_missing_after_skips(self):
        self.f._read_energy.return_value = None
        self.f._verify_signin_ad_reward(3400)
        self.f._watch.signin_verify.assert_not_called()

    def test_knob_off_does_nothing(self):
        self.f.wf["signin_verify"] = False
        self.f._verify_signin_ad_reward(3400)
        self.f._read_energy.assert_not_called()


class TestD3TapSwallowed(Base):
    def _sheet_visible(self):
        self.f._find.side_effect = lambda *a, **k: _n("每日免费领")

    def test_not_opened_retaps_once_and_recovers(self):
        self._sheet_visible()
        self.f.ui.nodes.return_value = [_n("看视频 + 10")]
        self.f._read_energy.side_effect = [3400, 3410]
        _calls = {"n": 0}

        def _play(**kw):
            _calls["n"] += 1
            if _calls["n"] == 1:      # 第一次：点了但广告没打开
                self.f._ad_not_opened = True
                return False
            self.f._ad_not_opened = False
            return True

        self.f._ad_play_and_close = mock.Mock(side_effect=_play)

        ok = self.f._watch_signin_ad()
        self.assertTrue(ok)
        self.assertEqual(self.f._tap_node.call_count, 2)   # 首次 + 补点
        self.assertEqual(self.f._ad_play_and_close.call_count, 2)
        # 补点成功 → 仍要走 D2 到账校验（3400 → 3410 判到账）
        self.f._watch.signin_verify.assert_called_with("signin_ad", True)

    def test_not_opened_retry_fails_alerts(self):
        self._sheet_visible()
        self.f.ui.nodes.return_value = [_n("看视频 + 10")]
        self.f._ad_play_and_close = mock.Mock(return_value=False)
        self.f._ad_not_opened = True

        ok = self.f._watch_signin_ad()
        self.assertFalse(ok)
        args, kwargs = self.f._watch.signin_verify.call_args
        self.assertEqual(args[0], "signin_ad")
        self.assertFalse(args[1])
        self.assertIn("没打开", kwargs.get("detail", ""))

    def test_no_button_skips_quietly(self):
        self._sheet_visible()
        self.f.ui.nodes.return_value = []
        self.f._ocr_find.return_value = None
        ok = self.f._watch_signin_ad()
        self.assertFalse(ok)
        self.f._ad_play_and_close.assert_not_called()
        self.f._watch.signin_verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
