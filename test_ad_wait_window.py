# -*- coding: utf-8 -*-
"""P0-a 回归测试：广告等待窗必须吸收「关闭按钮预定位」的 OCR 耗时。

背景（2026-10-19 真机 + 日志实测）：旧实现是

    sleep(prefetch_at); _prefetch_ad_close_pos(); sleep(wait - prefetch_at)

预定位内部要做一次**顶条 OCR**（取图 0.08~0.12 + 识别 ~1.0 ≈ 1.1s），这段
耗时落在两个 sleep **之外** ⇒ 实际观看时长恒为 `wait + OCR ≈ wait + 1.1s`，
每支广告白等 1.1s（10-19 共 447 支 ≈ 8min）。修复后按「已消耗时间」补差，
总时长严格 = wait。

注：原计划的 P0-c（关闭确认改走 u2 dump 快通道）已被真机实测**否决**
（任务中心 WebView 上 u2 dump 要 1.89s，比全屏 OCR 1.68s 还慢），相关测试
随代码一并回退，详见 .workbuddy/memory/2026-10-19.md。

运行：py -3.13 -m unittest test_ad_wait_window -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from flow import Flow


class _Clock:
    """假时钟命名空间：sleep 推进虚拟时间，time() 返回当前虚拟时刻。"""

    def __init__(self):
        self.t = 1000.0
        self.ns = mock.Mock()
        self.ns.time = self._time
        self.ns.sleep = self._sleep

    def _time(self):
        return self.t

    def _sleep(self, s):
        if s and s > 0:
            self.t += s


def make_flow(wf_extra=None):
    f = Flow.__new__(Flow)
    f._init_cache_state()
    f.ui = mock.Mock()
    f.t = {"click_min": 1.5, "click_max": 3.0, "page_wait": 2.0,
           "taskcenter_wait": 3.0, "ad_close_wait": 0.0}
    f.wf = {"ad_cooldown": 60, "ad_times_per_robot": 10,
            "ad_wait_min": 15, "ad_wait_max": 17, "ad_prefetch_at": 13.0,
            "ad_prefetch_pos": True}
    f.wf.update(wf_extra or {})
    return f


class TestAdWaitWindow(unittest.TestCase):

    def _run(self, prefetch_cost, wait=16.0, extra=None):
        clock = _Clock()
        f = make_flow(extra)
        seen = {}

        def _pf():
            clock.t += prefetch_cost     # 模拟 OCR 真实耗时

        def _close(**kw):
            seen["elapsed"] = clock.t - start
            return True

        pf = mock.Mock(side_effect=_pf)
        f._prefetch_ad_close_pos = pf
        f._close_ad = _close
        start = clock.t
        with mock.patch.object(flow_mod, "time", clock.ns), \
                mock.patch.object(flow_mod.random, "uniform",
                                  lambda a, b: wait):
            f._ad_play_and_close()
        return seen.get("elapsed"), pf

    def test_prefetch_cost_absorbed_in_window(self):
        """旧实现实际观看 = wait + OCR(1.1) = 17.1s；修复后必须 = wait。"""
        elapsed, _ = self._run(prefetch_cost=1.1, wait=16.0)
        self.assertAlmostEqual(elapsed, 16.0, places=2)

    def test_zero_cost_still_exact(self):
        elapsed, _ = self._run(prefetch_cost=0.0, wait=15.5)
        self.assertAlmostEqual(elapsed, 15.5, places=2)

    def test_cost_larger_than_remaining(self):
        """OCR 比剩余等待还久（prefetch_at 过于靠后）时总时长 = at + cost ——
        `max(0.0, ...)` 只保证不**负睡**，时间不可能倒流。
        真实路径 cost ≈ 1.1s、at = 13.0 ⇒ 14.1s < wait_min(15)，不触发本边界。"""
        elapsed, _ = self._run(prefetch_cost=4.0, wait=16.0)
        self.assertAlmostEqual(elapsed, 17.0, places=2)

    def test_prefetch_at_13_covers_full_wait_range(self):
        """P0-b：wait ∈ [15,17] 全窗都应触发预定位（条件 wait > at + 1.0）。"""
        for wait in (15.0, 15.4, 16.0, 16.9, 17.0):
            _, pf = self._run(prefetch_cost=0.0, wait=wait)
            self.assertTrue(pf.called,
                            "wait=%.1f 时未触发预定位，覆盖面出现缺口" % wait)

    def test_prefetch_switch_off_is_plain_sleep(self):
        """ad_prefetch_pos: false → 纯 sleep(wait)，不得调用预定位。"""
        elapsed, pf = self._run(prefetch_cost=1.1, wait=16.0,
                                extra={"ad_prefetch_pos": False})
        self.assertAlmostEqual(elapsed, 16.0, places=2)
        pf.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
