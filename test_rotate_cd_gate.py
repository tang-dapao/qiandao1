# -*- coding: utf-8 -*-
"""回落同机连看前补等 CD（2026-10-31 游迦 8/10 事故）单测。

事故时序（10-31 03:44，觅夏号·游迦）：
    03:44:11  游迦 轮换看广告 +1（今日 8/10）   ← 上一支关闭
    03:44:12  回落同机连看（日志写「CD 等待语义」，其实没等）
    03:44:17  点广告 → 广告页不出
    03:44:35 / :54 / 03:45:14  连败 3 次 → 弃权，8/10 收工（丢 2 支）
根因：`_watch_ads_session` 只保证**支与支之间**等到 `t_close + ad_cooldown`，
**首支没有任何 CD 门槛**（正常单机路径第一支离上一支天然很远，回落路径则不然）。

修复后要守住：
  1. 上一支就是这台、且距关闭不足 cd 秒 → **只补余量**（≤cd 秒）；
  2. 上一支是别的台 / 已经等够 / 没有记录 → **一秒都不等**；
  3. `workflow.rotate_cd_gate: false` → 回到旧行为（不等）；
  4. 回落分支必须在进入会话**之前**调用门控；
  5. `_rotate_watch_once` 每成功一支都要刷新「最近一支」标记。

运行：py -3.13 -m unittest test_rotate_cd_gate -v
"""
import time
import unittest
from unittest import mock

import flow as flow_mod
from flow import Flow


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f.wf = {"ad_cooldown": 60, "rotate_cd_gate": True}
        self.f.ui = mock.Mock()
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self.sleep = self._sleep.start()
        self.addCleanup(self._sleep.stop)

    def _mark(self, robot, ago):
        self.f._last_ad_robot = robot
        self.f._last_ad_ts = time.time() - ago


class WaitSameDeviceCdTest(Base):
    def test_waits_remaining_cd(self):
        """5 秒前刚看完 → 补等 ~55 秒。"""
        self._mark("游迦", 5.0)
        got = self.f._wait_same_device_cd("游迦", 60)
        self.assertAlmostEqual(got, 55.0, delta=0.6)
        self.assertAlmostEqual(self.sleep.call_args[0][0], 55.0, delta=0.6)

    def test_no_wait_for_other_robot(self):
        """上一支是别的台 → 窗口切走本来就吸收了 CD，不必等。"""
        self._mark("裴旖", 5.0)
        self.assertEqual(self.f._wait_same_device_cd("游迦", 60), 0.0)
        self.sleep.assert_not_called()

    def test_no_wait_when_already_elapsed(self):
        """距上一支已超过 cd → 不重复等。"""
        self._mark("游迦", 70.0)
        self.assertEqual(self.f._wait_same_device_cd("游迦", 60), 0.0)
        self.sleep.assert_not_called()

    def test_no_wait_without_marker(self):
        """从没记录过（进程刚起/单机模式）→ 不等。"""
        self.f._last_ad_robot = None
        self.f._last_ad_ts = 0.0
        self.assertEqual(self.f._wait_same_device_cd("游迦", 60), 0.0)
        self.sleep.assert_not_called()

    def test_knob_off(self):
        self.f.wf["rotate_cd_gate"] = False
        self._mark("游迦", 5.0)
        self.assertEqual(self.f._wait_same_device_cd("游迦", 60), 0.0)
        self.sleep.assert_not_called()

    def test_cd_zero_disabled(self):
        self._mark("游迦", 5.0)
        self.assertEqual(self.f._wait_same_device_cd("游迦", 0), 0.0)
        self.sleep.assert_not_called()

    def test_tiny_remainder_skipped(self):
        """只差 0.2s → 不折腾（阈值 0.5s）。"""
        self._mark("游迦", 59.8)
        self.assertEqual(self.f._wait_same_device_cd("游迦", 60), 0.0)
        self.sleep.assert_not_called()

    def test_capped_at_cd(self):
        """时钟回拨/异常（上一支在未来）→ 最多等 cd 秒，不无限等。"""
        self._mark("游迦", -100.0)
        self.assertEqual(self.f._wait_same_device_cd("游迦", 60), 60.0)
        self.assertAlmostEqual(self.sleep.call_args[0][0], 60.0, delta=0.01)


class RotateFallbackWiringTest(Base):
    """回落分支必须「先等 CD、再进会话」，且顺序不能反。"""

    def _mk_flow(self, robots, first_done, quota_left, order):
        f = Flow.__new__(Flow)
        f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 60,
                "ad_rotate_group": 5, "rotate_cd_gate": True}
        f.ui = mock.Mock()
        # 配额从 st 的 done 现算，`_rotate_watch_once` 每次真推进 done ——
        # 否则 while window 永不收敛（第一版就因为固定返回值把测试跑成死循环）。
        f._quota_left = mock.Mock(
            side_effect=lambda s, t, q: max(0, q - int(s["done"])))

        def _once(n, st, t, q, mf):
            order.append(("once", n))
            st[n]["done"] += 1
            return 1
        f._wait_same_device_cd = mock.Mock(
            side_effect=lambda n, cd: order.append(("gate", n)) or 55.0)
        f._watch_ads_session = mock.Mock(
            side_effect=lambda n, t, cd, start_done=0:
            order.append(("session", n)) or 2)
        f._rotate_watch_once = mock.Mock(side_effect=_once)
        f._stat = mock.Mock(return_value={})
        return f

    def test_fallback_waits_cd_before_session(self):
        order = []
        f = self._mk_flow(["游迦"], {"游迦": 8}, 2, order)
        f._run_rotate_phase(["游迦"], {"游迦": 8}, 10, 5)
        self.assertEqual(order, [("gate", "游迦"), ("session", "游迦")],
                         "回落必须先补等 CD 再进会话")
        f._wait_same_device_cd.assert_called_once_with("游迦", 60)

    def test_no_gate_when_window_has_peers(self):
        """窗口 2 台且同一圈都收满 → 走正常轮换，不触发回落门控。"""
        order = []
        first = {"游迦": 9, "裴旖": 9}
        f = self._mk_flow(["游迦", "裴旖"], first, 2, order)
        f._run_rotate_phase(["游迦", "裴旖"], first, 10, 5)
        f._wait_same_device_cd.assert_not_called()
        f._watch_ads_session.assert_not_called()
        self.assertEqual([k for k, _ in order], ["once", "once"])


class MarkerUpdateTest(Base):
    """`_rotate_watch_once` 成功一支后必须刷新「最近一支」标记。"""

    def test_success_updates_marker(self):
        f = Flow.__new__(Flow)
        f.wf = {"ad_times_per_robot": 10}
        f.ui = mock.Mock()
        f._last_ad_robot = None
        f._last_ad_ts = 0.0
        f._enter_taskcenter = mock.Mock(return_value=True)
        f._capture_energy_start = mock.Mock()
        f._dismiss_badcase = mock.Mock()
        f._read_ad_ratio = mock.Mock(return_value=(8, 10))
        f._tc_entry_ratio = (8, 10)
        f._ad_quota_done = mock.Mock(return_value=False)
        f._watch_ad_once = mock.Mock(return_value=True)
        f._exit_taskcenter = mock.Mock()
        st = {"游迦": {"done": 0, "fail": 0, "base": None, "reanchor": 0}}
        got = f._rotate_watch_once("游迦", st, 10, 10, 3)
        self.assertEqual(got, 1)
        self.assertEqual(f._last_ad_robot, "游迦")
        self.assertGreater(f._last_ad_ts, 0.0)


if __name__ == "__main__":
    unittest.main()
