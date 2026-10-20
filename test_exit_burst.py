# -*- coding: utf-8 -*-
"""P1（2026-10-19）回归测试：退台「三层 BACK 连发 + 尾一次校验」。

背景（probe_exit_timing，3 trial 实测）：3 次 BACK 之后 0.07~0.08s 列表即可被
`_looks_like_robot_list()` 强判据检出；而**前两层** BACK 后 3.5s 窗口内 20 次
检查全不命中 —— 逐层校验的价值只在最后一层，前两层的 pause+dump（每层 ~1.1s）
是纯盲等。连发把退台从 ~3.3s 压到 ~0.8s。

覆盖：
- 成功路径：恰好 3 次 back、**1 次**校验、置 `_at_robot_list`、不走逐层链
- 失败回落：连发未命中 → 原逐层链照常兜底，语义不变
- 开关：`exit_back_burst: false` / `nav_fast_wait: false` → 完全走旧行为
- pause 落在 EXIT_BURST_PAUSE_* 区间内

运行：py -3.13 -m unittest test_exit_burst -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import EXIT_BURST_PAUSE_MAX, EXIT_BURST_PAUSE_MIN, Flow


def nd(text, y=500, x1=100):
    return Node(text, x1, y, x1 + 200, y + 80)


def make_flow(wf_extra=None, hits=None):
    """hits: `_looks_like_robot_list` 的依次返回（用尽后沿用最后一个）。"""
    f = Flow.__new__(Flow)
    f._init_cache_state()
    f.ui = mock.Mock()
    f.t = {"click_min": 1.5, "click_max": 3.0, "page_wait": 2.0,
           "taskcenter_wait": 3.0}
    f.wf = {"nav_fast_wait": True, "exit_back_burst": True}
    f.wf.update(wf_extra or {})
    f._find = mock.Mock(return_value=nd("每日签到"))
    f._taskcenter_confirmed_by_ocr = mock.Mock(return_value=True)
    f._on_qq_main_shell = mock.Mock(return_value=True)
    f._safe_back_to_robot_list = mock.Mock(return_value=True)
    f._at_robot_list = False
    seq = list(hits or [True])

    def _hit():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    f._looks_like_robot_list = mock.Mock(side_effect=_hit)
    return f


class TestExitBackBurst(unittest.TestCase):

    def test_burst_success_single_check(self):
        """连发 3 次 BACK + 1 次校验即返回 —— 这是省时核心，不能被逐层逻辑绕过。"""
        f = make_flow(hits=[True])
        self.assertTrue(f._exit_taskcenter())
        self.assertEqual(f.ui.back.call_count, 3)
        self.assertEqual(f._looks_like_robot_list.call_count, 1)
        self.assertTrue(f._at_robot_list)

    def test_burst_fail_falls_back_to_layers(self):
        """连发未命中 → 逐层链兜底（第 1 层就命中），总 back=4、校验=2。"""
        f = make_flow(hits=[False, True])
        self.assertTrue(f._exit_taskcenter())
        self.assertEqual(f.ui.back.call_count, 4)
        self.assertEqual(f._looks_like_robot_list.call_count, 2)
        self.assertTrue(f._at_robot_list)

    def test_burst_fail_all_layers_fail_goes_safe_back(self):
        """连发 + 逐层全失败 → 落到安全返回/导航兜底，不静默返回 True。"""
        f = make_flow(hits=[False])
        f._on_qq_main_shell = mock.Mock(return_value=False)
        f._safe_back_to_robot_list = mock.Mock(return_value=False)
        f._scroll_to_top_of_taskcenter = mock.Mock()
        f._dump_stuck = mock.Mock(return_value=False)
        self.assertFalse(f._exit_taskcenter())
        f._safe_back_to_robot_list.assert_called_once()

    def test_pause_within_burst_range(self):
        """连发的 pause 必须落在抖动区间内（既是给系统的余量，也是防风控节奏）。"""
        f = make_flow(hits=[True])
        f._exit_taskcenter()
        for call in f.ui.back.call_args_list:
            p = call.kwargs.get("pause")
            self.assertIsNotNone(p)
            self.assertGreaterEqual(p, EXIT_BURST_PAUSE_MIN)
            self.assertLessEqual(p, EXIT_BURST_PAUSE_MAX)

    def test_switch_off_uses_layers_only(self):
        """exit_back_burst: false → 逐层校验（每层 1 次 back + 1 次校验）。"""
        f = make_flow(wf_extra={"exit_back_burst": False}, hits=[True])
        self.assertTrue(f._exit_taskcenter())
        self.assertEqual(f.ui.back.call_count, 1)
        self.assertEqual(f._looks_like_robot_list.call_count, 1)

    def test_nav_fast_wait_off_disables_burst(self):
        """nav_fast_wait: false = 整体回退旧慢链（连发也不启用，层间还 sleep）。"""
        f = make_flow(wf_extra={"nav_fast_wait": False}, hits=[True])
        with mock.patch.object(flow_mod.time, "sleep") as _sleep:
            self.assertTrue(f._exit_taskcenter())
        self.assertEqual(f.ui.back.call_count, 1)
        self.assertTrue(_sleep.called, "旧链必须保留 EXIT_LAYER_WAIT 的层间 sleep")

    def test_precondition_fail_goes_safe_back(self):
        """不在任务中心（无 每日签到/任务中心 且 OCR 也不认）→ 安全返回，不发 BACK。"""
        f = make_flow(hits=[True])
        f._find = mock.Mock(return_value=None)
        f._taskcenter_confirmed_by_ocr = mock.Mock(return_value=False)
        f._safe_back_to_robot_list = mock.Mock(return_value=True)
        self.assertTrue(f._exit_taskcenter())
        self.assertEqual(f.ui.back.call_count, 0)
        f._safe_back_to_robot_list.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
