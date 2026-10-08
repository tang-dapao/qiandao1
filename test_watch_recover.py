"""连看会话「失败后复位重进」回归测试（P0，2026-09-27）。

背景（真机实测，游迦 4 账号全中）：
广告播完后 OCR 在顶条 (0,0,1080,320) 漏读关闭按钮 → `_close_ad` 走物理 BACK
兜底 → BACK 撞 `ad_close_max_backs` 上限后放弃关闭，此时页面已被一路退到 QQ
联系人页。原实现失败后**原地重试**，下一次 `_watch_ad_once` 只会在错误页面上
必然再失败（`未找到 看广告/获取随机 行`），把「1 次关闭失败」放大成「3 连败
弃权」。修复后：重试前先 `_back_at_taskcenter()` 校验，不在则复位 + 重新进台。

本文件只验证该修复路径，不改主程序。

运行：py -3.13 -m unittest test_watch_recover -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from flow import Flow


class WatchSessionRecoverBase(unittest.TestCase):
    """构造 mock 齐备、CD 归零（避免 time.sleep 死循环）的 Flow。"""

    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0,
                     "ad_close_dump_timeout": 2.5}
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f._dismiss_badcase = mock.Mock()
        self.f._find_row = mock.Mock(return_value=None)
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._watch_ad_once = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        self.f._safe_back_to_robot_list = mock.Mock(return_value=True)
        self.f._read_ad_ratio = mock.Mock(return_value=None)
        self.f._ad_quota_done = mock.Mock(return_value=False)
        # 默认「已回任务中心」= 不需要恢复；各用例按需覆写。
        self.f._back_at_taskcenter = mock.Mock(return_value=True)
        self._sleep = mock.patch.object(flow_mod.time, "sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


class TestRecoverWhenPageEscaped(WatchSessionRecoverBase):
    """失败后已不在任务中心 → 复位 + 重新进台，随后重试成功。"""

    def test_fail_then_recover_and_continue(self):
        # 第 1 次失败（页面）；恢复后第 2、3 次成功 → 看满 target=2。
        self.f._watch_ad_once = mock.Mock(side_effect=[False, True, True])
        self.f._back_at_taskcenter = mock.Mock(return_value=False)

        got = self.f._watch_ads_session("游迦", 2, 0)

        self.assertEqual(got, 2)
        # 首次进台 1 次 + 恢复重进 1 次
        self.assertEqual(self.f._enter_taskcenter.call_count, 2)
        self.f._safe_back_to_robot_list.assert_called_once()
        self.f._exit_taskcenter.assert_called_once()
        self.assertEqual(self.f._watch_ad_once.call_count, 3)

    def test_first_iter_reset_after_recover(self):
        """重进后按会话起始语义重跑一次清场（问卷 H5 延迟弹出）。"""
        self.f._watch_ad_once = mock.Mock(side_effect=[False, True])
        self.f._back_at_taskcenter = mock.Mock(return_value=False)

        self.f._watch_ads_session("游迦", 1, 0)

        # 会话进台后首检清场 1 次 + 循环首轮 1 次 + 恢复重进后 1 次 = 3
        self.assertEqual(self.f._dismiss_badcase.call_count, 3)

    def test_dump_timeout_passed_from_config(self):
        """校验用的 dump 超时应取 workflow.ad_close_dump_timeout（快速失败），
        并开启 dump 复核（allow_dump_fallback，2026-09-27 二次修复）。"""
        self.f._watch_ad_once = mock.Mock(side_effect=[False, True])
        self.f._back_at_taskcenter = mock.Mock(return_value=False)

        self.f._watch_ads_session("游迦", 1, 0)

        self.f._back_at_taskcenter.assert_called_once_with(
            timeout=2.5, allow_dump_fallback=True)


class TestNoRecoverWhenStillAtTaskcenter(WatchSessionRecoverBase):
    """仍在任务中心（普通失败）→ 不做复位/重进，避免无谓开销。"""

    def test_still_at_taskcenter_no_reset(self):
        self.f._watch_ad_once = mock.Mock(side_effect=[False, True])
        self.f._back_at_taskcenter = mock.Mock(return_value=True)

        got = self.f._watch_ads_session("游迦", 1, 0)

        self.assertEqual(got, 1)
        self.assertEqual(self.f._enter_taskcenter.call_count, 1)
        self.f._safe_back_to_robot_list.assert_not_called()

    def test_success_path_zero_extra_checks(self):
        """零失败的成功路径不应付出任何额外读屏（不影响点击效率）。"""
        self.f._watch_ad_once = mock.Mock(return_value=True)

        got = self.f._watch_ads_session("游迦", 2, 0)

        self.assertEqual(got, 2)
        self.f._back_at_taskcenter.assert_not_called()
        self.f._safe_back_to_robot_list.assert_not_called()
        self.assertEqual(self.f._enter_taskcenter.call_count, 1)


class TestReenterFailureAborts(WatchSessionRecoverBase):
    """重进任务中心失败 → 终止本会话（不再空转浪费失败预算）。"""

    def test_reenter_fail_breaks_loop(self):
        self.f._watch_ad_once = mock.Mock(return_value=False)
        self.f._back_at_taskcenter = mock.Mock(return_value=False)
        # 首次进台成功、恢复重进失败
        self.f._enter_taskcenter = mock.Mock(side_effect=[True, False])

        got = self.f._watch_ads_session("游迦", 5, 0)

        self.assertEqual(got, 0)
        self.assertEqual(self.f._watch_ad_once.call_count, 1)
        self.f._exit_taskcenter.assert_called_once()


class TestNoCheckOnLastStrike(WatchSessionRecoverBase):
    """最后一次失败（已达 max_fail）不再做校验 —— 反正要弃权，省一次读屏。"""

    def test_no_check_on_last_fail(self):
        self.f._watch_ad_once = mock.Mock(return_value=False)
        self.f._back_at_taskcenter = mock.Mock(return_value=False)

        got = self.f._watch_ads_session("游迦", 5, 0)

        self.assertEqual(got, 0)
        # 3 次失败：前 2 次做校验并恢复重进，第 3 次（= max_fail）跳过校验
        self.assertEqual(self.f._back_at_taskcenter.call_count, 2)
        self.assertEqual(self.f._enter_taskcenter.call_count, 3)  # 1 + 2 次恢复
        self.assertEqual(self.f._watch_ad_once.call_count, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
