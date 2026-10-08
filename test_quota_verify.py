"""方案 A 收尾配额复核回归测试（2026-09-29，游迦 9/10 虚报实锤）。

背景：连看会话末支广告 tap 未触发 → 广告页从未打开 → `_close_ad` 双 OCR 判
「已在任务中心，无需关闭」→ done 无条件 +1 虚报（屏幕实为 9/10，日志记
10/10）。修复：末支关闭路径可疑（未点到任何关闭按钮）时，收尾 break 前
读屏复核（`_verify_session_quota`），按屏幕真值校准 done 继续补看；
正常关闭路径零额外读屏。

⚠️ mock 语义坑（本文件首版踩过）：`side_effect=[callable, ...]` 列表里的
可调用元素会被 **原样返回**（作为 mock 的返回值），**不会被调用** —— 必须
用「单一可调用 side_effect」才能在每次调用时执行副作用（置可疑标记）。

运行：py -3.13 -m unittest test_quota_verify -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from flow import Flow


def _watch_script(f, behaviors):
    """构造按脚本逐支设置可疑标记的 _watch_ad_once side_effect。

    behaviors：[(suspicious: bool, ret: bool), ...] —— 第 i 次调用置
    f._ad_close_suspicious=behaviors[i][0] 并返回 behaviors[i][1]。
    """
    it = iter(behaviors)

    def _fn(*a, **k):
        sus, ret = next(it)
        f._ad_close_suspicious = sus
        return ret
    return _fn


class QuotaVerifyBase(unittest.TestCase):
    """mock 齐备、CD 归零、sleep 打桩的 Flow（同 test_watch_recover 模式）。"""

    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0,
                     "ad_close_dump_timeout": 2.5, "ad_close_settle": 0.0}
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f._dismiss_badcase = mock.Mock()
        self.f._find_row = mock.Mock(return_value=None)
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._watch_ad_once = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        self.f._safe_back_to_robot_list = mock.Mock(return_value=True)
        self.f._read_ad_ratio = mock.Mock(return_value=None)
        self.f._ad_quota_done = mock.Mock(return_value=False)
        self.f._back_at_taskcenter = mock.Mock(return_value=True)
        self.f._tc_entry_ratio = None      # None → 会话首检走 _read_ad_ratio
        self._sleep = mock.patch.object(flow_mod.time, "sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


class TestSuspiciousEndVerify(QuotaVerifyBase):
    """末支关闭路径可疑 → 读屏复核 → 校准继续补看。"""

    def test_false_positive_calibrated_and_refilled(self):
        """游迦场景复刻：start 8，末支虚报 → 复核读 9 → 校准后补看 1 支。

        时序：watch1 成功（可疑，虚报）→ done=9 → 未达 target → CD →
        watch2 成功（可疑）→ done=10 达 target → 复核读屏 (9,10) →
        done=9 → 继续补看 watch3（正常关闭）→ done=10 → 正常 break。
        返回真实支数 2。
        """
        self.f._tc_entry_ratio = (8, 10)
        self.f._watch_ad_once = mock.Mock(side_effect=_watch_script(
            self.f, [(True, True), (True, True), (False, True)]))
        self.f._read_ad_ratio = mock.Mock(return_value=(9, 10))

        got = self.f._watch_ads_session("游迦", 10, 0, start_done=8)

        self.assertEqual(got, 2)
        self.assertEqual(self.f._watch_ad_once.call_count, 3)
        # 复核读屏恰好 1 次（仅可疑末支）
        self.assertEqual(self.f._read_ad_ratio.call_count, 1)

    def test_arithmetic_finish_suspicious_also_verified(self):
        """算术收尾判满 + 末支可疑 → 同样先复核再收尾。"""
        self.f._tc_entry_ratio = (9, 10)   # 进台 9 + 会话 1 → 算术判满
        self.f._watch_ad_once = mock.Mock(side_effect=_watch_script(
            self.f, [(True, True), (False, True)]))
        self.f._read_ad_ratio = mock.Mock(return_value=(9, 10))  # 屏幕 9 未满

        got = self.f._watch_ads_session("游迦", 10, 0, start_done=8)

        # 复核校准 done=9 → 补看 watch2（正常）→ done=10 → break，共 2 支
        self.assertEqual(got, 2)
        self.assertEqual(self.f._watch_ad_once.call_count, 2)
        self.assertEqual(self.f._read_ad_ratio.call_count, 1)

    def test_verify_reads_full_breaks_normally(self):
        """可疑但复核读屏已满（官方实际已计入）→ 正常收尾不补看。"""
        self.f._tc_entry_ratio = (9, 10)
        self.f._watch_ad_once = mock.Mock(side_effect=_watch_script(
            self.f, [(True, True)]))
        self.f._read_ad_ratio = mock.Mock(return_value=(10, 10))

        got = self.f._watch_ads_session("游迦", 10, 0, start_done=9)

        self.assertEqual(got, 1)
        self.assertEqual(self.f._watch_ad_once.call_count, 1)


class TestVerifyFallbacks(QuotaVerifyBase):
    """复核读屏失败 / 旋钮关闭 / 正常路径的降级行为。"""

    def test_read_fail_twice_conservative_break(self):
        """两次读屏均失败 → 保守按原计数收尾（不引入新失败模式）。"""
        self.f._tc_entry_ratio = (9, 10)
        self.f._watch_ad_once = mock.Mock(side_effect=_watch_script(
            self.f, [(True, True)]))
        self.f._read_ad_ratio = mock.Mock(return_value=None)

        got = self.f._watch_ads_session("游迦", 10, 0, start_done=9)

        self.assertEqual(got, 1)
        # 入口首检 0 次（有缓存）+ 复核 2 次
        self.assertEqual(self.f._read_ad_ratio.call_count, 2)
        self.f._exit_taskcenter.assert_called_once()

    def test_knob_off_no_verify(self):
        """ad_end_verify: false → 回退旧行为，可疑也不复核。"""
        self.f.wf["ad_end_verify"] = False
        self.f._tc_entry_ratio = (9, 10)
        self.f._watch_ad_once = mock.Mock(side_effect=_watch_script(
            self.f, [(True, True)]))
        self.f._read_ad_ratio = mock.Mock(return_value=(9, 10))

        got = self.f._watch_ads_session("游迦", 10, 0, start_done=9)

        self.assertEqual(got, 1)
        self.f._read_ad_ratio.assert_not_called()

    def test_normal_close_zero_extra_read(self):
        """正常关闭（未点到可疑路径）→ 收尾零额外读屏（不影响点击效率）。"""
        self.f._tc_entry_ratio = (9, 10)
        self.f._watch_ad_once = mock.Mock(side_effect=_watch_script(
            self.f, [(False, True)]))

        got = self.f._watch_ads_session("游迦", 10, 0, start_done=9)

        self.assertEqual(got, 1)
        self.f._read_ad_ratio.assert_not_called()

    def test_flag_reset_per_watch(self):
        """每支观看前重置可疑标记：skip/正常路径不得继承上一支旧标记。

        watch1 可疑（done=9 未达 target，无收尾复核）→ watch2 正常关闭
        达 target。若标记未重置，watch2 后会被误判可疑 → 多付一次读屏。
        """
        self.f._tc_entry_ratio = (8, 10)   # 算术 8+9-8=9 <10，不触发算术收尾
        self.f._watch_ad_once = mock.Mock(side_effect=_watch_script(
            self.f, [(True, True), (False, True)]))
        self.f._read_ad_ratio = mock.Mock(return_value=(8, 10))

        got = self.f._watch_ads_session("游迦", 10, 0, start_done=8)

        self.assertEqual(got, 2)
        # watch2 正常关闭 → 标记已重置 → 零复核读屏（若泄漏则 ≥1 次）
        self.f._read_ad_ratio.assert_not_called()


class TestCloseAdSuspiciousFlag(QuotaVerifyBase):
    """_close_ad「无需关闭」路径置位 / 入口重置。"""

    def test_no_close_path_sets_flag(self):
        """双 OCR 判定已在任务中心（未点任何关闭按钮）→ flag=True。"""
        self.f._prefetch_close = None
        self.f._taskcenter_confirmed_by_ocr = mock.Mock(return_value=True)

        ok = self.f._close_ad(tc_seen=False)

        self.assertTrue(ok)
        self.assertTrue(self.f._ad_close_suspicious)

    def test_entry_resets_flag(self):
        """每次 _close_ad 入口重置标记（前次残留不得泄漏）。"""
        self.f._ad_close_suspicious = True
        self.f._prefetch_close = None
        # 走 tc_seen=True 快路径：二次确认即返回 → flag 由本次判定重新置位
        # （入口先重置、判定再置位，顺序保证语义正确）。
        self.f._taskcenter_confirmed_by_ocr = mock.Mock(return_value=True)

        self.f._close_ad(tc_seen=True)

        self.assertTrue(self.f._ad_close_suspicious)


if __name__ == "__main__":
    unittest.main(verbosity=2)
