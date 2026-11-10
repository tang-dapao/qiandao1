# -*- coding: utf-8 -*-
"""试玩跳过（2026-11-10 用户需求）与账号间内存回收的单测。

背景：
  - 模拟器**没装微信** → 试玩广告点「点击进入」必落到「授权使用微信小程序」，
    白等 ad_minigame_wait(19s) 且拿不到奖励页（11-08/11-09 实测奖励页 0/9）。
  - 用户要求：遇到试玩 → 直接退回 → 重看一支；仍是试玩 → 退出本支、继续流程，
    留待后续轮次/回补再试。
  - 宿主物理内存仅 3.92GB → 账号间要清可再生缓存并记录 RSS。

覆盖：
  1. `_exit_minigame_landing`：有「返回」→ 点一次 + dump 确认任务中心
  2. `_exit_minigame_landing`：无「返回」→ 回退 `_close_ad` 兜底；确认不了 → False
  3. `_ad_play_and_close` 命中试玩：不进 `_play_minigame_ad`、置 `_minigame_skipped`
  4. 开关 `ad_minigame_skip: false` → 回退旧链路（走 `_play_minigame_ad`）
  5. `_watch_ad_once`：试玩→重看→普通广告成功 ⇒ True、不计跳过
  6. `_watch_ad_once`：连续两次试玩 ⇒ False + `_minigame_skip_final` + skipped=1
  7. `_rotate_watch_once`：试玩跳过不计 fail；连续 3 次才置 fail=max_fail
  8. `_watch_ads_session`：试玩跳过不计 fail，连续 3 次后结束会话（防死循环）
  9. `_release_memory`：清缓存 + RSS 日志；`mem_release: false` 只观测不清理

运行：py -3.13 -m unittest test_minigame_skip -v
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
        self.f.wf = {"ad_minigame": True, "ad_minigame_skip": True,
                     "ad_minigame_wait": 0, "ad_minigame_backs": 3,
                     "ad_minigame_skip_close": True,
                     "ad_times_per_robot": 10, "ad_cooldown": 60,
                     "ad_wait_min": 0.1, "ad_wait_max": 0.1,
                     "ad_open_retry": True, "ad_end_verify": False,
                     "mem_release": True}
        self.f.t = {"click_min": 0, "click_max": 0, "page_wait": 0,
                    "ad_close_wait": 0}
        self.f.ui = mock.Mock()
        self.f.ui.nodes.return_value = []
        self.f._watch = mock.Mock()
        self.f._watch_ad_once = mock.Mock(return_value=False)
        self.f._tap_node = mock.Mock()
        self.f._find = mock.Mock(return_value=None)
        self.f._find_row = mock.Mock(return_value=None)
        self.f._at_taskcenter_by_dump = mock.Mock(return_value=False)
        self.f._close_ad = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        self.f._dismiss_badcase = mock.Mock()
        self.f._prefetch_close = None
        self.f._ad_close_suspicious = False
        self.f._ad_not_opened = False
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


class TestExitLanding(Base):
    def test_back_node_found_and_confirmed(self):
        node = _n("返回")
        self.f._find = mock.Mock(return_value=node)
        self.f._at_taskcenter_by_dump = mock.Mock(return_value=True)
        self.assertTrue(self.f._exit_minigame_landing())
        self.f._tap_node.assert_called_once_with(node, pause=1.5)
        self.f._close_ad.assert_not_called()

    def test_no_back_node_falls_back_to_close_ad(self):
        self.f._find = mock.Mock(return_value=None)
        self.f._close_ad = mock.Mock(return_value=True)
        self.assertTrue(self.f._exit_minigame_landing())
        self.f._close_ad.assert_called_once()
        self.f._tap_node.assert_not_called()

    def test_back_tap_not_confirmed_falls_back_and_fails(self):
        self.f._find = mock.Mock(return_value=_n("返回"))
        self.f._at_taskcenter_by_dump = mock.Mock(return_value=False)
        self.f._close_ad = mock.Mock(return_value=False)
        self.assertFalse(self.f._exit_minigame_landing())
        self.f._close_ad.assert_called_once()


class TestPlayAndCloseSkip(Base):
    def _prep(self):
        self.f._probe_minigame_ad = mock.Mock(return_value=_n("点击进入"))
        self.f._play_minigame_ad = mock.Mock(return_value=True)
        self.f._exit_minigame_landing = mock.Mock(return_value=True)

    def test_skip_on_does_not_enter_minigame(self):
        self._prep()
        ok = self.f._ad_play_and_close()
        self.assertFalse(ok)
        self.assertTrue(self.f._minigame_skipped)
        self.f._play_minigame_ad.assert_not_called()
        self.f._exit_minigame_landing.assert_called_once()

    def test_skip_off_keeps_old_path(self):
        self.f.wf["ad_minigame_skip"] = False
        self._prep()
        self.assertTrue(self.f._ad_play_and_close())
        self.f._play_minigame_ad.assert_called_once()
        self.f._exit_minigame_landing.assert_not_called()
        self.assertFalse(self.f._minigame_skipped)

    def test_skip_on_but_exit_failed_returns_false(self):
        self._prep()
        self.f._exit_minigame_landing = mock.Mock(return_value=False)
        self.assertFalse(self.f._ad_play_and_close())
        self.assertTrue(self.f._minigame_skipped)


class TestWatchAdOnceRetry(Base):
    def setUp(self):
        super().setUp()
        # Base 给 `_watch_ad_once` 装了 Mock（供会话/轮换测试用）；
        # 本类要测**真方法**，必须把实例属性删掉让类方法生效。
        del self.f._watch_ad_once

    def _row(self):
        return (_n("看广告", 800, 900, 1000, 980), "看广告", (0, 10))

    def test_skip_then_normal_ad_succeeds(self):
        self.f._find_row = mock.Mock(side_effect=[self._row(), self._row()])
        # 第一次：试玩跳过；第二次：普通广告成功。
        # 注意 `_watch_ad_once` 每次重看前会把 `_minigame_skipped` 复位，
        # 所以脚本必须按**调用次数**推进，不能读那个标记。
        calls = {"n": 0}

        def play():
            calls["n"] += 1
            if calls["n"] == 1:
                self.f._minigame_skipped = True
                return False
            return True
        self.f._ad_play_and_close = mock.Mock(side_effect=play)
        self.assertTrue(self.f._watch_ad_once())
        self.assertEqual(self.f._ad_play_and_close.call_count, 2)
        self.assertFalse(self.f._minigame_skip_final)
        self.assertEqual(self.f._minigame_stats.get("skipped", 0), 0)

    def test_two_minigames_give_up_without_fail_flag(self):
        self.f._find_row = mock.Mock(side_effect=[self._row(), self._row()])

        def play():
            self.f._minigame_skipped = True
            return False
        self.f._ad_play_and_close = mock.Mock(side_effect=play)
        self.assertFalse(self.f._watch_ad_once())
        self.assertTrue(self.f._minigame_skip_final)
        self.assertEqual(self.f._minigame_stats.get("skipped"), 1)
        self.assertEqual(self.f._minigame_skip_streak, 1)

    def test_skip_off_no_retry(self):
        self.f.wf["ad_minigame_skip"] = False
        self.f._find_row = mock.Mock(return_value=self._row())

        def play():
            self.f._minigame_skipped = True
            return False
        self.f._ad_play_and_close = mock.Mock(side_effect=play)
        self.assertFalse(self.f._watch_ad_once())
        self.assertEqual(self.f._ad_play_and_close.call_count, 1)
        self.assertFalse(self.f._minigame_skip_final)


class TestRotateSkip(Base):
    def _prep(self, skip_final=True):
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._capture_energy_start = mock.Mock()
        self.f._stat = mock.Mock(return_value={})
        self.f._tc_entry_ratio = (0, 10)
        self.f._watch_ad_once = mock.Mock(return_value=False)
        self.f._minigame_skip_final = skip_final

    def _st(self):
        return {"done": 0, "fail": 0, "base": None, "reanchor": 0,
                "last_ts": 0.0, "backfill": 0, "mg_skip": 0}

    def test_skip_does_not_increment_fail(self):
        self._prep()
        st = self._st()
        self.f._rotate_watch_once("代柯", {"代柯": st}, 10, 10, 3)
        self.assertEqual(st["fail"], 0)
        self.assertEqual(st["mg_skip"], 1)
        self.assertFalse(self.f._minigame_skip_final)

    def test_three_skips_force_out_of_window(self):
        self._prep()
        st = self._st()
        st["mg_skip"] = 2
        self.f._rotate_watch_once("代柯", {"代柯": st}, 10, 10, 3)
        self.assertEqual(st["fail"], 3, "连续 3 次跳过应判出窗，防死循环")
        self.assertEqual(st["mg_skip"], 3)


class TestSessionSkip(Base):
    def test_skip_not_counted_as_fail_and_breaks_after_three(self):
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._tc_entry_ratio = (0, 10)
        self.f._last_ad_ts = 0
        self.f._last_ad_robot = ""

        def once(row=None):
            self.f._minigame_skip_final = True
            return False
        self.f._watch_ad_once = mock.Mock(side_effect=once)
        got = self.f._watch_ads_session("代柯", 10, 0, start_done=0)
        self.assertEqual(got, 0)
        self.assertEqual(self.f._watch_ad_once.call_count, 3,
                         "试玩跳过 3 次后必须结束会话（不得空转）")
        self.f._exit_taskcenter.assert_called_once()


class TestReleaseMemory(Base):
    def test_release_clears_caches_and_logs(self):
        self.f._shot_cache = object()
        self.f._ocr_result_cache = {"k": (None, 0)}
        self.f.ui._nodes_cache = [object()]
        with mock.patch.object(flow_mod.logger, "info") as log:
            self.f._release_memory("账号 T 收尾")
        self.assertIsNone(self.f._shot_cache)
        self.assertEqual(self.f._ocr_result_cache, {})
        self.assertIsNone(self.f.ui._nodes_cache)
        self.assertTrue(any("[内存]" in str(c) for c in log.call_args_list))

    def test_switch_off_only_observes(self):
        self.f.wf["mem_release"] = False
        self.f._shot_cache = "keep"
        self.f._ocr_result_cache = {"k": (None, 0)}
        self.f._release_memory()
        self.assertEqual(self.f._shot_cache, "keep")
        self.assertEqual(self.f._ocr_result_cache, {"k": (None, 0)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
