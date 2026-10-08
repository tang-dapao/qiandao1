# -*- coding: utf-8 -*-
"""运行汇总记账单测（2026-09-22 用户需求）。

覆盖：
- _stat 懒初始化（兼容 Flow.__new__(Flow) 绕过 __init__ 的单测构造方式）；
- run_robot 记账：签到/反馈结果（成功/已签/失败）、首轮广告 +1、进台失败；
- run_all 顺序模式记账：首轮 + 会话返回值累加，不重复计数；
- _run_rotate_phase 收尾绝对值覆盖 + 弃权备注（真实路径、感知层全桩）；
- _log_summary 输出行包含各台结果与合计。
"""
import unittest
from unittest import mock

import flow


def _bare_flow():
    """Flow.__new__(Flow) 构造：仅补记账所需最小属性。"""
    f = flow.Flow.__new__(flow.Flow)
    f.stats = {}
    f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}
    return f


class TestStatLazyInit(unittest.TestCase):
    def test_stat_defaults_and_lazy_container(self):
        f = flow.Flow.__new__(flow.Flow)   # 不设 stats，验证懒初始化
        s = f._stat("R1")
        self.assertEqual(s, {"entered": True, "signin": None,
                             "feedback": None, "ad": 0, "note": ""})
        self.assertIs(f._stat("R1"), s)    # 同名条目复用
        self.assertIn("R1", f.stats)


class TestRunRobotStats(unittest.TestCase):
    def _patch_ops(self, f, signin_already=False, signin_ok=True,
                   feedback_already=False, feedback_ok=True,
                   first_ad_ok=True):
        def _signin():
            f._signin_already = signin_already
            return signin_ok

        def _feedback():
            f._feedback_already = feedback_already
            return feedback_ok

        return [
            mock.patch.object(f, "_enter_taskcenter", return_value=True),
            mock.patch.object(f, "_dismiss_badcase", return_value=None),
            mock.patch.object(f, "_signin", side_effect=_signin),
            mock.patch.object(f, "_feedback", side_effect=_feedback),
            mock.patch.object(f, "_watch_ad_once", return_value=first_ad_ok),
            mock.patch.object(f, "_exit_taskcenter", return_value=None),
        ]

    def _start(self, patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_all_success_first_ad(self):
        f = _bare_flow()
        self._start(self._patch_ops(f, signin_already=True,
                                    feedback_already=True))
        r = f.run_robot("R1", True, True, first_ad=True)
        self.assertEqual(r, 2)
        s = f.stats["R1"]
        self.assertTrue(s["entered"])
        self.assertEqual(s["signin"], "已签")
        self.assertEqual(s["feedback"], "已反馈")
        self.assertEqual(s["ad"], 1)

    def test_signin_fail_recorded(self):
        f = _bare_flow()
        self._start(self._patch_ops(f, signin_ok=False))
        f.run_robot("R1", True, True, first_ad=False)
        self.assertEqual(f.stats["R1"]["signin"], "失败")

    def test_enter_fail_recorded(self):
        f = _bare_flow()
        with mock.patch.object(f, "_enter_taskcenter", return_value=False), \
             mock.patch.object(f, "_safe_back_to_robot_list",
                               return_value=None):
            r = f.run_robot("R1", True, True, first_ad=False)
        self.assertEqual(r, 0)
        self.assertFalse(f.stats["R1"]["entered"])

    def test_no_signin_no_feedback_ad_only(self):
        f = _bare_flow()
        self._start(self._patch_ops(f))
        f.run_robot("R1", False, False, first_ad=True)
        s = f.stats["R1"]
        self.assertIsNone(s["signin"])
        self.assertIsNone(s["feedback"])
        self.assertEqual(s["ad"], 1)


class TestRunAllSequentialStats(unittest.TestCase):
    def test_first_ad_plus_session_no_double_count(self):
        f = _bare_flow()

        def fake_robot(name, do_signin, do_feedback, first_ad):
            # 模拟真实 run_robot 的首轮广告记账（返回 2 = 首轮已看 1 支）
            f._stat(name)["ad"] += 1
            return 2

        with mock.patch.object(f, "run_robot", side_effect=fake_robot), \
             mock.patch.object(f, "_watch_ads_session", return_value=5) as ps:
            f.run_all(["A"], True, True, 6, rotate=False)
        ps.assert_called_once_with("A", 6, mock.ANY, start_done=0)
        self.assertEqual(f.stats["A"]["ad"], 6)   # 首轮1 + 会话5


class TestRotatePhaseStats(unittest.TestCase):
    """真实调用 _run_rotate_phase，仅桩掉感知层（进台/读屏/看广告/退出）。"""

    def test_quota_fill_and_abandon_note(self):
        f = _bare_flow()
        state = {"cur": None}

        def _enter(name):
            state["cur"] = name
            return True

        def _ratio():
            # A 首访：屏幕 2/10（含签到首轮 1 支 → base=1）；B 读不到
            return (2, 10) if state["cur"] == "A" else None

        def _watch(row=None):
            return state["cur"] == "A"   # A 每轮成功 1 支；B 恒失败

        def _session(name, target, cd, start_done=0):
            # 桩：B 弃权后 A 单独回落连看的路径（真实实现会继续看满），
            # 这里不补看（返回 0），仅验证回落记账与收尾覆盖。
            self.assertEqual(name, "A")
            self.assertEqual(start_done, 4)   # 3 轮轮换 + 首轮 1 支
            return 0

        with mock.patch.object(f, "_enter_taskcenter", side_effect=_enter), \
             mock.patch.object(f, "_safe_back_to_robot_list",
                               return_value=None), \
             mock.patch.object(f, "_dismiss_badcase", return_value=None), \
             mock.patch.object(f, "_read_ad_ratio", side_effect=_ratio), \
             mock.patch.object(f, "_watch_ad_once", side_effect=_watch), \
             mock.patch.object(f, "_exit_taskcenter", return_value=None), \
             mock.patch.object(f, "_watch_ads_session",
                               side_effect=_session):
            f._run_rotate_phase(["A", "B"], first_done={"A": 1, "B": 0},
                                target=10, group=2)
        # A：绝对值覆盖为 st["done"]=4（3 轮轮换 + 首轮，回落桩补看 0），
        #    不是 stats 旧值 1 + 轮换数的重复累计
        self.assertEqual(f.stats["A"]["ad"], 4)
        # B：连败 3 次弃权，带备注
        self.assertEqual(f.stats["B"]["ad"], 0)
        self.assertIn("弃权", f.stats["B"]["note"])


class TestLogSummaryOutput(unittest.TestCase):
    def test_summary_lines_and_totals(self):
        f = _bare_flow()
        f._stat("甲").update({"signin": "成功", "feedback": "已反馈",
                              "ad": 10})
        f._stat("乙").update({"signin": "失败", "feedback": "成功", "ad": 8,
                              "note": "连败3次弃权，余2支未看"})
        f._stat("丙").update({"signin": "已签", "feedback": "失败",
                              "entered": False, "ad": 0})
        with mock.patch.object(flow, "time") as tmod, \
             self.assertLogs("flow", level="INFO") as cm:
            tmod.strftime.return_value = "20260922_170000"
            f._log_summary(do_signin=True, do_feedback=True)
        out = "\n".join(cm.output)
        self.assertIn("本次运行汇总（3 台）", out)
        self.assertIn("甲", out)
        self.assertIn("进台失败", out)
        self.assertIn("合计：签到 2/3，问题反馈 2/3，广告 18 支", out)

    def test_summary_skip_columns_when_disabled(self):
        f = _bare_flow()
        f._stat("A").update({"signin": None, "feedback": None, "ad": 7})
        with mock.patch.object(flow, "time") as tmod, \
             self.assertLogs("flow", level="INFO") as cm:
            tmod.strftime.return_value = "20260922_170000"
            f._log_summary(do_signin=False, do_feedback=False)
        out = "\n".join(cm.output)
        self.assertIn("跳过", out)
        self.assertIn("合计：签到 0/0，问题反馈 0/0，广告 7 支", out)

    def test_summary_empty_no_crash(self):
        f = _bare_flow()
        f.stats = {}
        with self.assertLogs("flow", level="INFO") as cm:
            f._log_summary()
        self.assertIn("无可统计的处理记录", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
