# -*- coding: utf-8 -*-
"""「满额跳过不计支数」+「漏看点名告警」+「minigame 告警降噪」单测（2026-11-03）。

三件事对应用户三条诉求：
1. 修计数 bug：`_watch_ad_once` 在「屏幕 X/10 且 X>=target」时跳过，
   旧版调用方一律 +1 → 满额机器人白记 1 支（11-02 续跑 100 虚报的成因）。
2. 漏看告警要点名：用户原话「把那个账号下的那个机器人漏看的告警出来」，
   并且「这种告警我分辨不出来是那个机器人异常」→ 账号推送标题带「N 台漏看」，
   正文逐台列出「机器人：实看 a/应看 n，缺 n-a 支」。
3. minigame 告警降噪：11-03 一天推了 7 条同类 → `signin_verify(once=True)`
   改成整轮只推一次。

运行：py -3.13 -m unittest test_ad_count_missed -v
"""
import threading
import unittest
from unittest import mock

import flow as flow_mod
import watch as watch_mod
from flow import Flow


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0,
                     "minigame_reward_track": True,
                     "minigame_reward_alert_once": True}
        self.f.t = {"click_min": 0, "click_max": 0, "page_wait": 0}
        self.f.stats = {}
        self.f._watch = None
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


# ---------------------------------------------------------------- 1. 满额跳过
class TestFullSkipNotCounted(Base):
    def test_watch_ad_once_marks_skip_when_full(self):
        self.f._find_row = mock.Mock(return_value=(mock.Mock(), "看广告", (10, 10)))
        self.assertTrue(self.f._watch_ad_once())
        self.assertTrue(self.f._ad_skipped_full)

    def test_watch_ad_once_clears_flag_when_no_row(self):
        """进入时先把标记清 False —— 否则上一支的「跳过」会污染这一支。"""
        self.f._ad_skipped_full = True
        self.f._find_row = mock.Mock(return_value=None)
        self.f._ad_close_pos = mock.Mock(return_value=None)
        self.f._note_landing_fail = mock.Mock()
        self.assertFalse(self.f._watch_ad_once())
        self.assertFalse(self.f._ad_skipped_full)

    def roll_setup(self, skipped):
        """搭 `_rotate_watch_once` 的最小环境。skipped=True 模拟满额跳过。"""
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        self.f._dismiss_badcase = mock.Mock()
        self.f._back_at_taskcenter = mock.Mock(return_value=True)
        self.f._read_ad_ratio = mock.Mock(return_value=None)
        self.f.ui = mock.Mock()
        self.f._read_energy = mock.Mock(return_value=None)
        self.f._last_ad_ts = 0.0
        self.f._last_ad_robot = ""
        self.f._wait_same_device_cd = mock.Mock()

        def fake_watch():
            self.f._ad_skipped_full = skipped
            return True

        self.f._watch_ad_once = mock.Mock(side_effect=fake_watch)

    def test_rotation_full_skip_does_not_count(self):
        self.roll_setup(skipped=True)
        st = {"R1": {"done": 0, "base": 0, "fail": 0}}
        n = self.f._rotate_watch_once("R1", st, 10, 10, 3)
        self.assertEqual(n, 0)
        self.assertEqual(st["R1"]["done"], 0)
        self.assertEqual(self.f._stat("R1")["ad"], 0)
        self.assertEqual(self.f._stat("R1")["ad_need"], 0)

    def test_rotation_full_skip_forces_quota_zero(self):
        """防死循环：base 未校准时（配额读不到）也要让 `_quota_left` 变 0。"""
        self.roll_setup(skipped=True)
        st = {"R1": {"done": 0, "base": None, "fail": 0}}
        self.f._rotate_watch_once("R1", st, 10, 10, 3)
        self.assertEqual(self.f._quota_left(st["R1"], 10, 10), 0)

    def test_rotation_normal_counts_one(self):
        self.roll_setup(skipped=False)
        st = {"R1": {"done": 0, "base": 0, "fail": 0}}
        n = self.f._rotate_watch_once("R1", st, 10, 10, 3)
        self.assertEqual(n, 1)
        self.assertEqual(st["R1"]["done"], 1)
        # 轮换段 `_stat()["ad"]` 由 `_run_rotate_phase` 按 done 镜像同步
        # （flow.py 里 `_stat(n)["ad"] = s["done"]`），本函数内不改统计；
        # 这里只锁「正常支不写 ad_need」（写了会污染漏看口径）。
        self.assertNotIn("ad_need", self.f._stat("R1"))

    def test_first_round_full_skip_not_counted(self):
        self.f.wf["signin_ad"] = False
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        self.f._dismiss_badcase = mock.Mock()
        self.f._signin = mock.Mock(return_value=True)
        self.f._feedback = mock.Mock(return_value=True)
        self.f._feedback_already = False

        def fake_watch():
            self.f._ad_skipped_full = True
            return True

        self.f._watch_ad_once = mock.Mock(side_effect=fake_watch)
        r = self.f.run_robot("R1", True, True, first_ad=True)
        # 返回码必须不是 2 —— 2 会让轮换阶段 first_done +1，又白记一支
        self.assertEqual(r, 1)
        self.assertEqual(self.f._stat("R1")["ad"], 0)
        self.assertEqual(self.f._stat("R1")["ad_need"], 0)


# ---------------------------------------------------------------- 2. 漏看清单
class TestMissedRobots(Base):
    def test_explicit_need_respected(self):
        self.f._stat("A").update({"ad": 2, "ad_need": 10})
        self.f._stat("B").update({"ad": 10, "ad_need": 10})
        out = self.f._missed_ad_robots()
        self.assertEqual([(n, a, nd) for n, a, nd, _ in out], [("A", 2, 10)])

    def test_full_skip_is_not_missed(self):
        """满额跳过的台 ad_need=0、ad=0 → 不能算漏看（否则续跑全是假告警）。"""
        self.f._stat("A").update({"ad": 0, "ad_need": 0})
        self.assertEqual(self.f._missed_ad_robots(), [])

    def test_default_need_when_unset(self):
        """顺序连看路径没写 ad_need → 退化为 ad_times_per_robot。"""
        self.f._stat("A").update({"ad": 3})
        out = self.f._missed_ad_robots()
        self.assertEqual(out[0][2], 10)
        self.assertEqual(out[0][1], 3)

    def test_entered_false_marked(self):
        self.f._stat("A").update({"ad": 0, "entered": False})
        out = self.f._missed_ad_robots()
        self.assertEqual(out[0][3], "进台失败")

    def test_note_carried(self):
        self.f._stat("古禹").update({"ad": 2, "ad_need": 10,
                                     "note": "连败3次弃权，余8支未看"})
        out = self.f._missed_ad_robots()
        self.assertIn("弃权", out[0][3])

    def test_sorted_by_gap_desc(self):
        self.f._stat("小缺口").update({"ad": 9, "ad_need": 10})
        self.f._stat("大缺口").update({"ad": 2, "ad_need": 10})
        self.assertEqual([x[0] for x in self.f._missed_ad_robots()],
                         ["大缺口", "小缺口"])

    def test_empty_stats(self):
        self.assertEqual(self.f._missed_ad_robots(), [])


class TestSummaryLine(Base):
    def test_summary_names_robots(self):
        self.f._stat("古禹").update({"ad": 2, "ad_need": 10,
                                     "note": "连败3次弃权，余8支未看"})
        self.f._stat("代柯").update({"ad": 10, "ad_need": 10})
        with mock.patch.object(flow_mod, "time") as tmod, \
                self.assertLogs("flow", level="INFO") as cm:
            tmod.strftime.return_value = "20261103_160000"
            self.f._log_summary(do_signin=True, do_feedback=True,
                                title="账号 唐灵 (1000000004)")
        out = "\n".join(cm.output)
        self.assertIn("漏看 1 台：古禹 2/10（缺 8）", out)

    def test_no_line_when_nothing_missed(self):
        self.f._stat("代柯").update({"ad": 10, "ad_need": 10})
        with mock.patch.object(flow_mod, "time") as tmod, \
                self.assertLogs("flow", level="INFO") as cm:
            tmod.strftime.return_value = "20261103_160000"
            self.f._log_summary()
        self.assertNotIn("漏看", "\n".join(cm.output))


# ---------------------------------------------------------------- 3. 账号推送点名
class TestAccountPushNames(Base):
    def make_watch(self):
        w = mock.Mock()
        w.notifier.enabled = True
        w.push = mock.Mock()
        self.f._watch = w
        self.f.cfg = {"notify": {"account_summary": True}}
        return w

    def test_head_and_body_name_robot(self):
        w = self.make_watch()
        self.f._stat("古禹").update({"ad": 2, "ad_need": 10,
                                     "note": "连败3次弃权，余8支未看"})
        self.f._stat("代柯").update({"ad": 10, "ad_need": 10})
        self.f._push_account_summary("唐灵", "1000000004", 4, 5, 3600.0, True,
                                     ["古禹", "代柯"])
        title = w.push.call_args[0][0]
        body = w.push.call_args[0][1]
        self.assertIn("1 台漏看", title)
        self.assertIn("唐灵", title)
        self.assertIn("漏看 1 台 · 共缺 8 支", body)
        self.assertIn("**古禹**：实看 2/10，缺 8 支", body)
        self.assertIn("弃权", body)
        self.assertNotIn("代柯", body)

    def test_no_missed_no_marker(self):
        w = self.make_watch()
        self.f._stat("代柯").update({"ad": 10, "ad_need": 10})
        self.f._push_account_summary("唐灵", "1", 1, 1, 100.0, True, ["代柯"])
        title = w.push.call_args[0][0]
        body = w.push.call_args[0][1]
        self.assertNotIn("漏看", title)
        self.assertNotIn("漏看", body)

    def test_more_than_six_truncated(self):
        w = self.make_watch()
        for i in range(8):
            self.f._stat("R%d" % i).update({"ad": 0, "ad_need": 10})
        self.f._push_account_summary("唐灵", "1", 1, 1, 100.0, True, [])
        body = w.push.call_args[0][1]
        self.assertIn("等共 8 台", body)


# ---------------------------------------------------------------- 4. 告警降噪
class TestAlertOnce(unittest.TestCase):
    def make_watch(self):
        w = watch_mod.Watch.__new__(watch_mod.Watch)
        w.enabled = True
        w.conf = {"task_fail": 3}
        w.robot = "游迦"
        w.notifier = mock.Mock()
        w._c = {}
        w._lock = threading.Lock()
        w._once_done = set()
        w._shot = mock.Mock(return_value="")
        w._screen_texts = mock.Mock(return_value="屏文")
        w._emit = mock.Mock()
        return w

    def test_once_emits_only_first_time(self):
        w = self.make_watch()
        for _ in range(12):        # 4 轮「连续 3 支」
            w.signin_verify("minigame_reward", False, detail="x", once=True)
        self.assertEqual(w._emit.call_count, 1)

    def test_without_once_emits_every_streak(self):
        w = self.make_watch()
        for _ in range(12):
            w.signin_verify("minigame_reward", False, detail="x")
        self.assertEqual(w._emit.call_count, 4)

    def test_once_does_not_block_other_kinds(self):
        w = self.make_watch()
        for _ in range(3):
            w.signin_verify("minigame_reward", False, detail="x", once=True)
        for _ in range(3):
            w.signin_verify("signin_ad", False, detail="y", once=True)
        self.assertEqual(w._emit.call_count, 2)

    def test_ok_still_resets_counter_before_once(self):
        """有奖励页时计数照常归零（不影响其它 key/后续判断）。"""
        w = self.make_watch()
        w.signin_verify("minigame_reward", False, once=True)
        w.signin_verify("minigame_reward", True, once=True)
        self.assertEqual(w._c.get("verify|minigame_reward", 0), 0)
        self.assertEqual(w._emit.call_count, 0)

    def test_flow_passes_once_knob(self):
        f = Flow.__new__(Flow)
        f._init_cache_state()
        f.wf = {"minigame_reward_alert_once": True}
        f._watch = mock.Mock()
        f._record_minigame_reward(False)
        self.assertTrue(f._watch.signin_verify.call_args[1]["once"])

    def test_flow_can_disable_once(self):
        f = Flow.__new__(Flow)
        f._init_cache_state()
        f.wf = {"minigame_reward_alert_once": False}
        f._watch = mock.Mock()
        f._record_minigame_reward(False)
        self.assertFalse(f._watch.signin_verify.call_args[1]["once"])


if __name__ == "__main__":
    unittest.main()
