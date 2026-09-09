"""改动 A（--no-ad 拆分启动）的静态回归测试（mock 驱动，不依赖真机/adb）。

分两层验证：
1. main.py 的 argparse 组合 → run_all(do_signin, do_feedback, ad_times) 映射正确
   （重点：--no-ad ⇒ ad_times=0；--no-ad + --ad-times 5 时 no-ad 优先）
2. flow.run_all：
   - ad_times=0 / None(配置=0) ⇒ 签到/反馈循环照跑，然后提前 return，
     完全不进入看广告轮转（不调用 _enter_taskcenter/_watch_ad_once）
   - ad_times=2 ⇒ 进入轮转并执行 2 次
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
class FlowAllBase(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}  # cd=0 避免时间死循环
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f.run_robot = mock.Mock()
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._watch_ad_once = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        self._sleep = mock.patch.object(flow_mod.time, "sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


class TestRunAllNoAd(FlowAllBase):
    def test_zero_skips_ad_rotation_but_still_runs_robots(self):
        self.f.run_all(["R1", "R2"], True, True, 0)
        # 签到/反馈循环照跑
        self.assertEqual(self.f.run_robot.call_count, 2)
        self.f.run_robot.assert_any_call("R1", True, True)
        self.f.run_robot.assert_any_call("R2", True, True)
        # 看广告轮转一次都不进入
        self.f._enter_taskcenter.assert_not_called()
        self.f._watch_ad_once.assert_not_called()

    def test_none_config_zero_also_skips(self):
        self.f.wf = {"ad_times_per_robot": 0, "ad_cooldown": 0}
        self.f.run_all(["R1"], False, False, None)
        self.f.run_robot.assert_called_once_with("R1", False, False)
        self.f._enter_taskcenter.assert_not_called()

    def test_explicit_zero_with_ad_only_style_is_noop_not_error(self):
        # 对应 main --no-ad --ad-only：run_robot(False,False) 空走一遍后返回
        self.f.run_all(["R1"], False, False, 0)
        self.f.run_robot.assert_called_once_with("R1", False, False)
        self.f._enter_taskcenter.assert_not_called()


class TestRunAllAdRotation(FlowAllBase):
    def test_explicit_target_runs_exact_count(self):
        self.f.run_all(["R1"], False, False, 2)
        self.assertEqual(self.f._enter_taskcenter.call_count, 2)
        self.assertEqual(self.f._watch_ad_once.call_count, 2)
        self.assertEqual(self.f._exit_taskcenter.call_count, 2)

    def test_none_uses_config_default(self):
        self.f.wf = {"ad_times_per_robot": 2, "ad_cooldown": 0}
        self.f.run_all(["R1"], False, False, None)
        self.assertEqual(self.f._enter_taskcenter.call_count, 2)

    def test_failed_watch_ad_counts_fail_not_loop_forever(self):
        self.f._watch_ad_once = mock.Mock(return_value=False)
        self.f.run_all(["R1"], False, False, 5)   # max_fail=3 会先踢出
        self.assertEqual(self.f._enter_taskcenter.call_count, 3)
        self.assertEqual(self.f._watch_ad_once.call_count, 3)


if __name__ == "__main__":
    unittest.main()
