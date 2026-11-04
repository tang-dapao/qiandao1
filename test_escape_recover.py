# -*- coding: utf-8 -*-
"""P8 退穿自愈（2026-10-29）单测。

背景事故：BACK 把 QQ 退出到桌面/系统设置后，`_safe_back_to_robot_list` 仍继续
物理 BACK，之后每台机器人都导航失败 → 9 台连环弃权，单账号只看了 38/100 支广告。

修复后要守住的行为：
  1. `AdbUI.foreground_package()` 能从 `dumpsys activity activities` 正确取包名，
     取不到（无匹配/命令异常）返回 None，绝不猜。
  2. `Flow._foreground_package()` 对 Mock / 无该方法的 fake 一律返回 None ——
     否则 `Mock() != pkg` 恒 True，会把所有单测判成「脱离 QQ」假红。
  3. 前台不是 QQ → **不按 BACK**，直接 am start 拉回；拉回后导航成功即返回 True。
  4. 前台是 QQ → 行为与修复前**完全一致**（照旧物理 BACK）。
  5. `am start` 无效（前台仍被别的 task 压着）→ 先 HOME 清压顶 task 再重试。
  6. `escape_recover: false` → 一次 dumpsys 都不发（一键回退）。
  7. `_nav_recovering` 置位时不触发自愈 → 防 `_nav_robot_list` 回调本函数造成递归。

运行：py -3.13 -m unittest test_escape_recover -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import AdbUI
from flow import Flow

QQ = "com.tencent.mobileqq"
COMP = QQ + "/com.tencent.mobileqq.activity.SplashActivity"

DUMPSYS_QQ = (
    "ACTIVITY MANAGER ACTIVITIES\n"
    "  * Task{194824107 #75 type=standard A=10123:com.tencent.mobileqq}\n"
    "    topResumedActivity=ActivityRecord{5868051 u0 "
    "com.tencent.mobileqq/.activity.SplashActivity t75}\n"
)
DUMPSYS_SETTINGS = (
    "ACTIVITY MANAGER ACTIVITIES\n"
    "    topResumedActivity=ActivityRecord{144368036 u0 "
    "com.android.settings/.homepage.SettingsHomepageActivity t76}\n"
)
DUMPSYS_LAUNCHER_ONLY_MRESUMED = (
    "ACTIVITY MANAGER ACTIVITIES\n"
    "    mResumedActivity: ActivityRecord{194824107 u0 "
    "app.lawnchair/.LawnchairLauncher t2}\n"
)


class TestForegroundPackageParse(unittest.TestCase):
    """`AdbUI.foreground_package()` —— 纯解析，不连设备。"""

    def _ui(self, ret=None, exc=None):
        ui = AdbUI.__new__(AdbUI)
        if exc is not None:
            ui._run = mock.Mock(side_effect=exc)
        else:
            ui._run = mock.Mock(return_value=ret)
        return ui

    def test_parses_top_resumed_qq(self):
        self.assertEqual(self._ui(DUMPSYS_QQ).foreground_package(), QQ)

    def test_parses_settings_package(self):
        self.assertEqual(self._ui(DUMPSYS_SETTINGS).foreground_package(),
                         "com.android.settings")

    def test_falls_back_to_mresumed(self):
        self.assertEqual(
            self._ui(DUMPSYS_LAUNCHER_ONLY_MRESUMED).foreground_package(),
            "app.lawnchair")

    def test_no_match_returns_none(self):
        self.assertIsNone(self._ui("nothing useful here").foreground_package())

    def test_command_error_returns_none(self):
        # adb 挂起/失败绝不能抛到主流程 —— 上层只把它当「读不到」
        self.assertIsNone(
            self._ui(exc=RuntimeError("adb hang")).foreground_package())

    def test_ignores_dotless_tokens_before_component(self):
        # ActivityRecord{5868051 / u0 都没有点号，不能被当包名取走
        txt = ("    topResumedActivity=ActivityRecord{5868051 u0 "
               "com.tencent.mobileqq/.activity.SplashActivity t75}\n")
        self.assertEqual(self._ui(txt).foreground_package(), QQ)


class TestAdbActions(unittest.TestCase):
    """`am_start` / `home` 的 adb 命令形态。"""

    def _ui(self):
        ui = AdbUI.__new__(AdbUI)
        ui._run = mock.Mock(return_value="")
        ui._invalidate_cache = mock.Mock()
        ui._notify_action = mock.Mock()
        return ui

    def test_am_start_passes_component(self):
        ui = self._ui()
        self.assertTrue(ui.am_start(COMP))
        args = ui._run.call_args[0]
        self.assertEqual(args, ("shell", "am", "start", "-n", COMP))
        ui._invalidate_cache.assert_called_once()

    def test_am_start_error_returns_false(self):
        ui = self._ui()
        ui._run.side_effect = RuntimeError("boom")
        self.assertFalse(ui.am_start(COMP))

    def test_home_sends_keycode_3(self):
        ui = self._ui()
        ui.home(pause=0.0)
        self.assertEqual(ui._run.call_args[0],
                         ("shell", "input", "keyevent", "3"))


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {"escape_recover": True, "escape_recover_tries": 2}
        self.f.cfg = {"app": {"package": QQ, "activity":
                              "com.tencent.mobileqq.activity.SplashActivity"}}
        self.f.ui = mock.Mock()
        self.f._page_tc = False
        self.f._at_robot_list = False
        self.f._looks_like_robot_list = mock.Mock(return_value=False)
        self.f._on_qq_main_shell = mock.Mock(return_value=False)
        self.f._nav_robot_list = mock.Mock()
        self.f._dump_stuck = mock.Mock(return_value=False)
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)
        self.addCleanup(setattr, self.f, "_nav_recovering", False)


class TestForegroundProbeGuard(Base):
    """`Flow._foreground_package()` 必须对 Mock / 缺方法的 fake 返回 None。"""

    def test_mock_ui_returns_none(self):
        self.assertIsNone(self.f._foreground_package())

    def test_fake_without_method_returns_none(self):
        class _Fake:
            pass
        self.f.ui = _Fake()
        self.assertIsNone(self.f._foreground_package())

    def test_fake_raising_returns_none(self):
        class _Fake:
            def foreground_package(self):
                raise RuntimeError("boom")
        self.f.ui = _Fake()
        self.assertIsNone(self.f._foreground_package())

    def test_fake_non_str_returns_none(self):
        class _Fake:
            def foreground_package(self):
                return 42
        self.f.ui = _Fake()
        self.assertIsNone(self.f._foreground_package())


class TestRecoverQQForeground(Base):
    def test_first_start_works(self):
        self.f.ui.foreground_package.side_effect = [QQ]
        self.assertTrue(self.f._recover_qq_foreground())
        self.f.ui.am_start.assert_called_once_with(COMP)
        self.f.ui.home.assert_not_called()

    def test_home_then_retry_when_start_ineffective(self):
        # 实测：task 已存在时 am start 只把 QQ task 前置，前台仍被 Settings 压着
        self.f.ui.foreground_package.side_effect = [
            "com.android.settings", QQ]
        self.assertTrue(self.f._recover_qq_foreground())
        self.assertEqual(self.f.ui.am_start.call_count, 2)
        self.f.ui.home.assert_called_once()

    def test_gives_up_after_tries(self):
        self.f.ui.foreground_package.return_value = "app.lawnchair"
        self.assertFalse(self.f._recover_qq_foreground())
        self.assertEqual(self.f.ui.am_start.call_count, 2)
        self.assertEqual(self.f.ui.home.call_count, 2)

    def test_tries_knob_respected(self):
        self.f.wf["escape_recover_tries"] = 1
        self.f.ui.foreground_package.return_value = "app.lawnchair"
        self.assertFalse(self.f._recover_qq_foreground())
        self.assertEqual(self.f.ui.am_start.call_count, 1)

    def test_am_start_absent_does_not_crash(self):
        class _Fake:
            def foreground_package(self):
                return "app.lawnchair"
        self.f.ui = _Fake()
        self.assertFalse(self.f._recover_qq_foreground())


class TestSafeBackEscape(Base):
    """`_safe_back_to_robot_list` 的退穿处置。"""

    def test_not_in_qq_recovers_without_back(self):
        # 前置检查就发现脱离 QQ → 一次 BACK 都不按
        self.f.ui.foreground_package.side_effect = ["app.lawnchair", QQ]
        self.f._looks_like_robot_list.return_value = True
        self.assertTrue(self.f._safe_back_to_robot_list(max_back=6))
        self.f.ui.back.assert_not_called()
        self.f.ui.am_start.assert_called_once_with(COMP)
        self.f._nav_robot_list.assert_called_once()

    def test_in_qq_keeps_old_back_behavior(self):
        self.f.ui.foreground_package.return_value = QQ
        self.assertFalse(self.f._safe_back_to_robot_list(max_back=2))
        self.assertEqual(self.f.ui.back.call_count, 2)
        self.f.ui.am_start.assert_not_called()

    def test_in_loop_escape_detected_and_recovered(self):
        self.f.ui.foreground_package.side_effect = [
            QQ,              # 前置检查：还在 QQ
            QQ,              # i=0 BACK 前：还在 QQ
            "app.lawnchair",  # i=1 BACK 前：已脱离 → 自愈
            QQ,              # am start 后复核
        ]
        self.f._looks_like_robot_list.side_effect = [False, False, True]
        self.assertTrue(self.f._safe_back_to_robot_list(max_back=6))
        self.assertEqual(self.f.ui.back.call_count, 1)   # 只按了 i=0 那一次
        self.f.ui.am_start.assert_called_once_with(COMP)

    def test_knob_off_sends_no_dumpsys(self):
        self.f.wf["escape_recover"] = False
        self.f.ui.foreground_package.return_value = "app.lawnchair"
        self.assertFalse(self.f._safe_back_to_robot_list(max_back=1))
        self.f.ui.foreground_package.assert_not_called()
        self.f.ui.am_start.assert_not_called()
        self.f.ui.back.assert_called_once()

    def test_recursion_guard_skips_recovery(self):
        # _nav_robot_list 在 dump 卡死分支会回调 _safe_back_to_robot_list，
        # 闸门必须挡住自愈，否则无限递归
        self.f._nav_recovering = True
        self.f.ui.foreground_package.return_value = "app.lawnchair"
        self.assertFalse(self.f._safe_back_to_robot_list(max_back=1))
        self.f.ui.am_start.assert_not_called()
        self.f.ui.back.assert_called_once()

    def test_recovery_failure_falls_through_to_back(self):
        # 拉不回来也不能卡死：照旧按 BACK，把决定权交回上层
        self.f.ui.foreground_package.return_value = "app.lawnchair"
        self.assertFalse(self.f._safe_back_to_robot_list(max_back=2))
        self.assertGreaterEqual(self.f.ui.am_start.call_count, 1)
        self.assertGreaterEqual(self.f.ui.back.call_count, 1)


if __name__ == "__main__":
    unittest.main()
