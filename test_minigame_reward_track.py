# -*- coding: utf-8 -*-
"""试玩广告「奖励页」计数（坑 44，2026-11-03）单测。

背景（证据见 MEMORY.md 坑 41 / 坑 44）：
  全期 251 支签到广告回溯 —— 「经过试玩奖励页」18/18 到账、「未过奖励页」0/8
  到账，26 样本零例外 ⇒ 奖励页是**零成本的到账先行信号**。2026-11-01 03:39 起
  试玩创意整体不出奖励页，11-03 试玩占比又从 0% 跳到 ~70%，但日志里看不到损失。

本文件覆盖：
  - `_record_minigame_reward`：✓/✗ 计数、懒初始化、`_watch` 缺失、告警抛异常吞掉、
    开关 `minigame_reward_track: false` 一键回退
  - `_play_minigame_ad` 的三条链路形态（奖励页→任务中心 / 直接任务中心 / 未确认）：
    奖励页是否被记准，**且退出行为一字不变**（返回次数、是否走 `_close_ad`）
  - 第一条「返回」就直接回任务中心时，仍补读一次奖励页（新增的零成本分支）
  - `_log_summary` 输出试玩统计行

运行：py -3.13 -m unittest test_minigame_reward_track -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import Flow


def _n(text, x1=60, y1=144, x2=204, y2=276):
    return Node(text, x1, y1, x2, y2)


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {"minigame_reward_track": True,
                     "ad_minigame_wait": 0,
                     "ad_minigame_backs": 3,
                     "ad_minigame_skip_close": True}
        self.f.t = {"click_min": 0, "click_max": 0,
                    "page_wait": 0, "ad_close_wait": 0}
        self.f.ui = mock.Mock()
        self.f.ui.nodes.return_value = []
        self.f._watch = mock.Mock()
        self.f._tap_node = mock.Mock()
        self.f._minigame_cta_node = mock.Mock(return_value=None)
        self.f._prefetch_close = None
        self.f._ad_close_suspicious = False
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

    # ---------------------------------------------------------------
    def script(self, steps):
        """按顺序脚本化退出循环。

        steps：[(tc_now, reward_visible), ...]，每次点「返回」后依次消费一组。
        「返回」节点由 `_find` 返回；步数用尽即视为无「返回」节点。
        """
        seq = list(steps)

        def find(text, *a, **k):
            if text != "返回":
                return None
            return _n("返回") if seq else None

        tc_vals = []
        rw_vals = []
        for tc, rw in steps:
            tc_vals.append(tc)
            rw_vals.append(rw)

        self.f._find = mock.Mock(side_effect=find)
        self.f._at_taskcenter_by_dump = mock.Mock(side_effect=tc_vals)
        self.f._minigame_reward_visible = mock.Mock(side_effect=rw_vals)
        self.f._close_ad = mock.Mock(return_value=True)
        return seq

    def play(self):
        cta = _n("点击进入", 174, 1218, 906, 1389)
        return self.f._play_minigame_ad(cta, sheet_ok=False)


class TestPlayMinigameChain(Base):
    def test_reward_page_then_taskcenter_counts_reward(self):
        # 10-30 形态：第1次返回 → 奖励页；第2次返回 → 任务中心
        self.script([(False, True), (True, False)])
        self.assertTrue(self.play())
        st = self.f._minigame_stats
        self.assertEqual(st["total"], 1)
        self.assertEqual(st["reward"], 1)
        self.assertNotIn("no_reward", st)
        self.f._watch.signin_verify.assert_called_once_with(
            "minigame_reward", True)

    def test_no_reward_page_counts_missing(self):
        # 11-03 形态：两次返回都不见奖励页，直接回任务中心
        self.script([(False, False), (True, False)])
        self.assertTrue(self.play())
        st = self.f._minigame_stats
        self.assertEqual(st["total"], 1)
        self.assertEqual(st.get("reward", 0), 0)
        self.assertEqual(st["no_reward"], 1)
        kind, ok = self.f._watch.signin_verify.call_args[0][:2]
        self.assertEqual(kind, "minigame_reward")
        self.assertFalse(ok)

    def test_taskcenter_on_first_back_still_reads_reward_page(self):
        """第一条「返回」就直接命中任务中心时，新增的补读分支必须生效。

        同时锁死「退出行为不变」：只有 1 次「返回」点击（+1 次 CTA 点击），
        且走 `ad_minigame_skip_close` 的快路径、不调 `_close_ad`。
        """
        self.script([(True, True)])
        self.assertTrue(self.play())
        self.assertEqual(self.f._minigame_stats.get("reward"), 1)
        self.assertEqual(self.f._tap_node.call_count, 2)
        self.f._close_ad.assert_not_called()

    def test_reward_missing_when_exit_not_confirmed(self):
        """3 次返回都没确认回任务中心 → 仍计入统计（统计与关闭结果解耦）。"""
        self.script([(False, False), (False, False), (False, False)])
        self.f._close_ad.return_value = False
        self.assertFalse(self.play())
        self.assertEqual(self.f._minigame_stats["no_reward"], 1)

    def test_no_back_node_immediately(self):
        self.script([])
        self.assertTrue(self.play())
        self.assertEqual(self.f._minigame_stats["no_reward"], 1)


class TestKnobAndLazy(Base):
    def test_knob_off_records_nothing(self):
        self.f.wf["minigame_reward_track"] = False
        self.script([(False, False), (True, False)])
        self.play()
        self.assertEqual(self.f._minigame_stats["total"], 0)
        self.f._watch.signin_verify.assert_not_called()

    def test_default_on_when_key_absent(self):
        self.f.wf.pop("minigame_reward_track")
        self.script([(False, False), (True, False)])
        self.play()
        self.assertEqual(self.f._minigame_stats["total"], 1)

    def test_lazy_init_without_init(self):
        """Flow.__new__ 绕过 __init__ 也不能炸（沿用全仓既有约定）。"""
        del self.f._minigame_stats
        self.f._record_minigame_reward(True)
        self.assertEqual(self.f._minigame_stats, {"total": 1, "reward": 1})

    def test_watch_missing_does_not_crash(self):
        self.f._watch = None
        self.f._record_minigame_reward(False)
        self.assertEqual(self.f._minigame_stats["no_reward"], 1)

    def test_watch_exception_swallowed(self):
        self.f._watch.signin_verify.side_effect = RuntimeError("boom")
        self.f._record_minigame_reward(False)
        self.f._record_minigame_reward(True)
        self.assertEqual(self.f._minigame_stats["total"], 2)

    def test_consecutive_missing_keeps_counting(self):
        for _ in range(4):
            self.f._record_minigame_reward(False)
        self.assertEqual(self.f._minigame_stats["no_reward"], 4)
        self.assertEqual(self.f._watch.signin_verify.call_count, 4)

    def test_reward_then_missing_resets_via_watch(self):
        self.f._record_minigame_reward(False)
        self.f._record_minigame_reward(True)
        last = self.f._watch.signin_verify.call_args[0]
        self.assertEqual(last[:2], ("minigame_reward", True))


class TestLogSummaryLine(unittest.TestCase):
    def test_summary_contains_minigame_line(self):
        f = Flow.__new__(Flow)
        f.stats = {}
        f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}
        f._minigame_stats = {"total": 7, "reward": 2, "no_reward": 5}
        f._stat("甲").update({"signin": "成功", "feedback": "已反馈", "ad": 10})
        with mock.patch.object(flow_mod, "time") as tmod, \
                self.assertLogs("flow", level="INFO") as cm:
            tmod.strftime.return_value = "20261103_100000"
            f._log_summary(do_signin=True, do_feedback=True)
        out = "\n".join(cm.output)
        self.assertIn("试玩广告：7 支，奖励页 ✓2 / ✗5", out)

    def test_summary_omits_line_when_no_minigame(self):
        f = Flow.__new__(Flow)
        f.stats = {}
        f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}
        f._minigame_stats = {"total": 0, "reward": 0}
        f._stat("甲").update({"signin": "成功", "feedback": "已反馈", "ad": 10})
        with mock.patch.object(flow_mod, "time") as tmod, \
                self.assertLogs("flow", level="INFO") as cm:
            tmod.strftime.return_value = "20261103_100000"
            f._log_summary(do_signin=True, do_feedback=True)
        self.assertNotIn("试玩广告：", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
