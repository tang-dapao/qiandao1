"""改动 A（--no-ad 拆分启动）的静态回归测试（mock 驱动，不依赖真机/adb）。

分两层验证：
1. main.py 的 argparse 组合 → run_all(do_signin, do_feedback, ad_times) 映射正确
   （重点：--no-ad ⇒ ad_times=0；--no-ad + --ad-times 5 时 no-ad 优先）
2. flow.run_all：
   - ad_times=0 / None(配置=0) ⇒ 签到/反馈循环照跑，然后提前 return，
     完全不进入看广告轮转（不调用 _enter_taskcenter/_watch_ad_once）
   - ad_times=N ⇒ 单次任务中心会话内连看 N 次（F10：每台只进出一次，
     会话内按 CD 间隔连看，替代旧"每广告进出一次"轮转）
   - ad_times=None ⇒ 采用 config workflow.ad_times_per_robot 默认值

运行：py -3.13 -m unittest test_run_all_flags -v
"""
import sys
import unittest
from unittest import mock

import flow as flow_mod
from flow import Flow


def _cfg():
    return {"device": {"udid": "emulator-test"},
            "workflow": {"ad_times_per_robot": 10, "ad_cooldown": 60},
            "logging": {}}


# ----------------------------------------------------------------------
# main.py 层：用真实 argparse + mock 掉重依赖，验证 flag 组合的最终传参
# ----------------------------------------------------------------------
class TestMainArgMapping(unittest.TestCase):
    def _run_main(self, argv_extra):
        patchers = [
            mock.patch("main.load_config", return_value=_cfg()),
            mock.patch("main.setup_logging"),
            mock.patch("main.AdbUI"),
            mock.patch("main.Flow"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        ui_cls = mock.patch("main.AdbUI").start()
        flow_cls = mock.patch("main.Flow").start()
        ui_cls.return_value.is_online.return_value = True
        argv = ["main.py", "--robots", "R1"] + argv_extra
        with mock.patch.object(sys, "argv", argv):
            import main as main_mod
            main_mod.main()
        return flow_cls.return_value.run_all.call_args

    def test_default_all_on(self):
        args = self._run_main([])
        self.assertEqual(args[0][0], ["R1"])
        self.assertEqual(args[0][1:], (True, True, None))  # 签到/反馈/默认次数

    def test_no_ad_zero(self):
        args = self._run_main(["--no-ad"])
        self.assertEqual(args[0][1:], (True, True, 0))

    def test_ad_only_skips_signin_feedback(self):
        args = self._run_main(["--ad-only"])
        self.assertEqual(args[0][1:], (False, False, None))

    def test_no_ad_plus_ad_only_conflict_resolves_to_zero(self):
        # 语义冲突组合：no-ad 赢 → ad_times=0；ad-only 赢 → 不签到不反馈
        # 结果 = 空跑（run_robot(False,False) + 跳过看广告），不抛异常
        args = self._run_main(["--no-ad", "--ad-only"])
        self.assertEqual(args[0][1:], (False, False, 0))

    def test_no_ad_overrides_ad_times(self):
        args = self._run_main(["--no-ad", "--ad-times", "5"])
        self.assertEqual(args[0][1:], (True, True, 0))

    def test_ad_times_passed_through(self):
        args = self._run_main(["--ad-times", "3"])
        self.assertEqual(args[0][1:], (True, True, 3))

    def test_ad_only_with_ad_times(self):
        args = self._run_main(["--ad-only", "--ad-times", "3"])
        self.assertEqual(args[0][1:], (False, False, 3))

    def test_no_signin_no_feedback_keeps_ad(self):
        args = self._run_main(["--no-signin", "--no-feedback"])
        self.assertEqual(args[0][1:], (False, False, None))


# ----------------------------------------------------------------------
# flow.py 层：run_all 提前 return / 轮转计数逻辑
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# main.py 层：白名单过滤（robot_whitelist）——仅处理名单内昵称
# ----------------------------------------------------------------------
class TestMainWhitelist(unittest.TestCase):
    def _run(self, robots_arg, wl):
        cfg = _cfg()
        cfg["workflow"]["robot_whitelist"] = wl
        mock.patch("main.load_config", return_value=cfg).start()
        mock.patch("main.setup_logging").start()
        ui_cls = mock.patch("main.AdbUI").start()
        flow_cls = mock.patch("main.Flow").start()
        self.addCleanup(mock.patch.stopall)
        ui_cls.return_value.is_online.return_value = True
        argv = ["main.py", "--robots", robots_arg]
        with mock.patch.object(sys, "argv", argv):
            import main as main_mod
            main_mod.main()
        return flow_cls.return_value.run_all

    def test_whitelist_keeps_members_drops_others(self):
        run_all = self._run("R1,R2,R3", ["R1", "R3"])
        self.assertEqual(run_all.call_args[0][0], ["R1", "R3"])

    def test_cli_robots_also_subject_to_whitelist(self):
        # 命令行指定的名字同样受白名单约束（用户剔除的机器人不该被绕过）
        run_all = self._run("R1,R2", ["R1", "R3"])
        self.assertEqual(run_all.call_args[0][0], ["R1"])

    def test_all_filtered_out_returns_without_run(self):
        run_all = self._run("R2", ["R1", "R3"])
        self.assertFalse(run_all.called)          # 全被滤掉 → 不进入 run_all

    def test_empty_whitelist_means_no_filter(self):
        # 名单为空/未配置 = 不过滤（保持旧行为，兼容无白名单环境）
        run_all = self._run("R1,R2", [])
        self.assertEqual(run_all.call_args[0][0], ["R1", "R2"])


class FlowAllBase(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}  # cd=0 避免时间死循环
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f.run_robot = mock.Mock()
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._watch_ad_once = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        # F10：单会话连看相关辅助（默认未达配额/进入即成功）
        self.f._ad_quota_done = mock.Mock(return_value=False)
        self.f._safe_back_to_robot_list = mock.Mock(return_value=True)
        # 2026-09-10 CD 重叠优化：进台读一次 X/10 基数（默认读不到 → 走屏幕
        # 复核的旧路径，与 _ad_quota_done 的 None 语义一致）
        self.f._read_ad_ratio = mock.Mock(return_value=None)
        self._sleep = mock.patch.object(flow_mod.time, "sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


class TestRunAllNoAd(FlowAllBase):
    def test_zero_skips_ad_rotation_but_still_runs_robots(self):
        self.f.run_all(["R1", "R2"], True, True, 0)
        # 签到/反馈循环照跑（target=0 → 首轮不带广告）
        self.assertEqual(self.f.run_robot.call_count, 2)
        self.f.run_robot.assert_any_call("R1", True, True, first_ad=False)
        self.f.run_robot.assert_any_call("R2", True, True, first_ad=False)
        # 看广告轮转一次都不进入
        self.f._enter_taskcenter.assert_not_called()
        self.f._watch_ad_once.assert_not_called()

    def test_none_config_zero_also_skips(self):
        self.f.wf = {"ad_times_per_robot": 0, "ad_cooldown": 0}
        self.f.run_all(["R1"], False, False, None)
        self.f.run_robot.assert_not_called()          # 签到/反馈关闭：不空跑
        self.f._enter_taskcenter.assert_not_called()  # 0 次广告：不进入

    def test_explicit_zero_with_ad_only_style_is_noop_not_error(self):
        # 对应 main --no-ad --ad-only：签到/反馈关闭 → run_robot 空跑被跳过，
        # 广告次数 0 → 直接返回，全程无任何页面动作
        self.f.run_all(["R1"], False, False, 0)
        self.f.run_robot.assert_not_called()
        self.f._enter_taskcenter.assert_not_called()

    def test_ad_only_skips_pointless_run_robot(self):
        # --ad-only：不再逐台"空进出"任务中心，直接进入看广告轮转
        self.f.run_all(["R1"], False, False, 1)
        self.f.run_robot.assert_not_called()
        self.assertEqual(self.f._enter_taskcenter.call_count, 1)
        self.assertEqual(self.f._watch_ad_once.call_count, 1)


class TestRunAllAdRotation(FlowAllBase):
    """F10（2026-09-09）：看广告改为"单次任务中心会话连看多次"。

    旧语义：每看 1 次广告进出一次任务中心（enter/watch/exit × target）。
    新语义：每台机器人进一次任务中心（最多 3 次进入尝试），会话内按 CD
    间隔连看直到 target 次/失败上限/已达每日配额，最后退一次。
    """

    def test_explicit_target_runs_in_single_session(self):
        # target=2：只进一次任务中心、会话内看 2 次、退一次（不再 2 进 2 出）
        self.f.run_all(["R1"], False, False, 2)
        self.assertEqual(self.f._enter_taskcenter.call_count, 1)
        self.assertEqual(self.f._watch_ad_once.call_count, 2)
        self.assertEqual(self.f._exit_taskcenter.call_count, 1)

    def test_none_uses_config_default(self):
        self.f.wf = {"ad_times_per_robot": 2, "ad_cooldown": 0}
        self.f.run_all(["R1"], False, False, None)
        self.assertEqual(self.f._enter_taskcenter.call_count, 1)
        self.assertEqual(self.f._watch_ad_once.call_count, 2)

    def test_multi_robot_each_gets_own_session(self):
        # 每台独立会话：R1、R2 各进一次、各看 1 次、各退一次
        self.f.run_all(["R1", "R2"], False, False, 1)
        self.assertEqual(self.f._enter_taskcenter.call_count, 2)
        self.assertEqual(self.f._watch_ad_once.call_count, 2)
        self.assertEqual(self.f._exit_taskcenter.call_count, 2)

    def test_failed_watch_ad_counts_fail_not_loop_forever(self):
        self.f._watch_ad_once = mock.Mock(return_value=False)
        self.f.run_all(["R1"], False, False, 5)   # max_fail=3 先踢出
        # 已进入的会话内连续失败：watch 3 次后 fail=3 退出，只进出一次
        self.assertEqual(self.f._enter_taskcenter.call_count, 1)
        self.assertEqual(self.f._watch_ad_once.call_count, 3)
        self.assertEqual(self.f._exit_taskcenter.call_count, 1)

    def test_enter_failure_three_tries_then_skip(self):
        # 进入失败：同一台最多 3 次进入尝试（每次都 safe_back 复位），
        # 仍失败则跳过该台看广告（不 exit——没进去过）
        self.f._enter_taskcenter = mock.Mock(return_value=False)
        self.f.run_all(["R1"], False, False, 1)
        self.assertEqual(self.f._enter_taskcenter.call_count, 3)
        self.assertEqual(self.f._safe_back_to_robot_list.call_count, 3)
        self.assertEqual(self.f._watch_ad_once.call_count, 0)
        self.assertEqual(self.f._exit_taskcenter.call_count, 0)

    def test_quota_already_done_exits_without_watching(self):
        # 会话内首检：X/10 已达每日配额（跨进程残留/手动已看完）→ 进一次、
        # 不看直接退出 —— 修掉旧实现空转 ~110s/次×10 的隐患
        self.f._read_ad_ratio = mock.Mock(return_value=(10, 10))
        self.f.run_all(["R1"], False, False, 10)
        self.assertEqual(self.f._enter_taskcenter.call_count, 1)
        self.assertEqual(self.f._watch_ad_once.call_count, 0)
        self.assertEqual(self.f._exit_taskcenter.call_count, 1)

    def test_quota_fills_right_after_watch_stops_without_cd(self):
        # 算术收尾：进台 9/10 + 本会话看 1 次 → 必满 → 不读屏、不等 CD 直接
        # 退出（2026-09-10 CD 重叠优化；旧实现此处会读屏复核撞 dump 超时）
        self.f._read_ad_ratio = mock.Mock(return_value=(9, 10))
        self.f._watch_ad_once = mock.Mock(return_value=True)
        self.f.run_all(["R1"], False, False, 5)
        self.assertEqual(self.f._watch_ad_once.call_count, 1)
        self.f._ad_quota_done.assert_not_called()       # 无需屏幕复核
        self.assertEqual(self.f._exit_taskcenter.call_count, 1)

    def test_quota_screen_recheck_in_cd_window_when_base_unknown(self):
        # 基数读不到（进台行未读出）→ 回退 CD 窗口末尾的屏幕复核：
        # 看 1 次后复核命中 → 收尾（屏幕读屏仍能兜底手动/并发补满场景）
        self.f._read_ad_ratio = mock.Mock(return_value=None)
        self.f._ad_quota_done = mock.Mock(return_value=True)
        self.f._watch_ad_once = mock.Mock(return_value=True)
        self.f.run_all(["R1"], False, False, 5)
        self.assertEqual(self.f._watch_ad_once.call_count, 1)
        self.f._ad_quota_done.assert_called_once()      # CD 窗口内复核 1 次
        self.assertEqual(self.f._exit_taskcenter.call_count, 1)


class TestFirstAdInFirstPass(unittest.TestCase):
    """2026-09-10 用户优化：主流程第一轮（签到/反馈）同会话先看 1 次广告。

    语义：
    - run_all：target>0 时 run_robot 收到 first_ad=True；target=0 保持 False
    - run_robot(first_ad=True)：签到 → 反馈 → 巡检 → 看一次 → 退出（同会话）
    - 首轮广告失败不影响 run_robot 成功返回（交轮转阶段补看）
    - 默认 first_ad=False：完全不碰广告路径（向后兼容，--no-ad 行为不变）
    """

    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self._sleep = mock.patch.object(flow_mod.time, "sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

    def test_run_all_passes_first_ad_when_target_positive(self):
        f = self.f
        f.run_robot = mock.Mock()
        f._enter_taskcenter = mock.Mock(return_value=True)
        f._watch_ad_once = mock.Mock(return_value=True)
        f._read_ad_ratio = mock.Mock(return_value=(10, 10))  # 阶段二首检即满
        f._exit_taskcenter = mock.Mock()
        f.run_all(["R1"], True, True, 5)
        f.run_robot.assert_any_call("R1", True, True, first_ad=True)

    def test_run_all_first_ad_disabled_when_target_zero(self):
        f = self.f
        f.run_robot = mock.Mock()
        f.run_all(["R1", "R2"], True, True, 0)
        f.run_robot.assert_any_call("R1", True, True, first_ad=False)
        f.run_robot.assert_any_call("R2", True, True, first_ad=False)

    def _robot_with_mocks(self, watch_ret=True):
        f = self.f
        self.calls = []
        f._enter_taskcenter = mock.Mock(
            return_value=True,
            side_effect=lambda *a: (self.calls.append("enter"), True)[1])
        f._dismiss_badcase = mock.Mock(
            side_effect=lambda *a: (self.calls.append("dismiss"), None)[1])
        f._signin = mock.Mock(
            return_value=True,
            side_effect=lambda *a, **k: (self.calls.append("signin"), True)[1])
        f._feedback = mock.Mock(
            return_value=True,
            side_effect=lambda *a, **k: (self.calls.append("feedback"), True)[1])
        f._watch_ad_once = mock.Mock(
            return_value=watch_ret,
            side_effect=lambda *a: (self.calls.append("ad"), watch_ret)[1])
        f._exit_taskcenter = mock.Mock(
            side_effect=lambda *a: (self.calls.append("exit"), None)[1])
        f._safe_back_to_robot_list = mock.Mock(return_value=True)
        return f

    def test_run_robot_first_ad_order_and_session_reuse(self):
        # 同一会话：进入 → 巡检(A3) → 签到 → 反馈 → 再巡检 → 看一次 → 退出
        f = self._robot_with_mocks()
        self.assertTrue(f.run_robot("R", True, True, first_ad=True))
        self.assertEqual(self.calls,
                         ["enter", "dismiss", "signin", "feedback",
                          "dismiss", "ad", "exit"])
        f._watch_ad_once.assert_called_once_with()

    def test_run_robot_first_ad_failure_still_succeeds(self):
        # 首轮广告失败：不重试、不影响返回值，照常退出（轮转阶段补看）
        f = self._robot_with_mocks(watch_ret=False)
        self.assertTrue(f.run_robot("R", True, True, first_ad=True))
        f._watch_ad_once.assert_called_once_with()
        f._exit_taskcenter.assert_called_once_with()

    def test_run_robot_default_no_first_ad_untouched(self):
        # 向后兼容：不传 first_ad（--no-ad 场景）→ 完全不碰看广告路径
        f = self._robot_with_mocks()
        self.assertTrue(f.run_robot("R", True, True))
        self.assertEqual(self.calls,
                         ["enter", "dismiss", "signin", "feedback", "exit"])
        f._watch_ad_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()
