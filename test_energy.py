# -*- coding: utf-8 -*-
"""电量统计单测（2026-09-29 用户需求）：账号级「本流程获得电量」。

覆盖：
- _read_energy：dump 纯数字节点命中 / dump 无标签走 OCR / OCR 截图失败 /
  旋钮关闭（零读屏）；
- _capture_energy_start：首次读取、幂等（成败都只试一次，防每次进台重读）、
  旋钮关闭；
- _capture_energy_end：正常差额、起始缺失不白跑、进台全失败；
- _log_summary 电量行：正常差额 / 结束缺失 / 起始缺失不输出。
"""
import unittest
from unittest import mock

import flow


def _bare_flow():
    f = flow.Flow.__new__(flow.Flow)
    f.stats = {}
    f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}
    return f


def _node(text, y1, x1=100):
    return flow.Node(text, x1, y1, x1 + 120, y1 + 60)


class TestReadEnergy(unittest.TestCase):
    def test_dump_pure_digit_node_hit(self):
        f = _bare_flow()
        f._find = mock.Mock(return_value=_node("当前电量", 700))
        f.ui = mock.Mock()
        f.ui.nodes.return_value = [
            _node("去充电", 700, x1=800),      # 同条带但无数字
            _node("602", 820),                 # 纯数字 → 命中
        ]
        self.assertEqual(f._read_energy(), 602)

    def test_dump_embedded_digit_fallback(self):
        # 条带内无纯数字节点 → 取含数字节点（2 位起，按 y,x 排序取最上左）
        f = _bare_flow()
        f._find = mock.Mock(return_value=_node("当前电量", 700))
        f.ui = mock.Mock()
        f.ui.nodes.return_value = [
            _node("余额88", 900, x1=300),
            _node("电量75x", 820, x1=200),
        ]
        self.assertEqual(f._read_energy(), 75)

    def test_dump_single_digit_pure_node(self):
        # 纯数字节点接受个位数（新账号电量可能 <10）；混排单词元不收
        f = _bare_flow()
        f._find = mock.Mock(return_value=_node("当前电量", 700))
        f.ui = mock.Mock()
        f.ui.nodes.return_value = [_node("8", 820)]
        self.assertEqual(f._read_energy(), 8)

    def test_no_label_ocr_shot_none(self):
        f = _bare_flow()
        f._find = mock.Mock(return_value=None)
        f._ocr_shot = mock.Mock(return_value=None)
        self.assertIsNone(f._read_energy())
        f._ocr_shot.assert_called_once()

    def test_knob_off_no_screen_read(self):
        f = _bare_flow()
        f.wf["energy_stat"] = False
        f._find = mock.Mock()
        f._ocr_shot = mock.Mock()
        self.assertIsNone(f._read_energy())
        f._find.assert_not_called()
        f._ocr_shot.assert_not_called()

    def test_find_raises_swallowed(self):
        # ui 异常（如 Mock 缺属性）必须吞掉返回 None，绝不阻断主流程
        f = _bare_flow()
        f._find = mock.Mock(side_effect=AttributeError("no ui"))
        f._ocr_shot = mock.Mock(return_value=None)
        self.assertIsNone(f._read_energy())


class TestCaptureEnergyStart(unittest.TestCase):
    def test_first_read_and_idempotent(self):
        f = _bare_flow()
        f._read_energy = mock.Mock(return_value=602)
        f._capture_energy_start()
        self.assertEqual(f._energy_start, 602)
        self.assertTrue(f._energy_start_done)
        f._capture_energy_start()               # 第二次进台：不再读
        self.assertEqual(f._read_energy.call_count, 1)

    def test_read_fail_tried_once(self):
        # 读失败也只试一次 —— 否则每次进台重读，90 支 × 2-4s 纯浪费
        f = _bare_flow()
        f._read_energy = mock.Mock(return_value=None)
        f._capture_energy_start()
        f._capture_energy_start()
        self.assertIsNone(f._energy_start)
        self.assertEqual(f._read_energy.call_count, 1)

    def test_knob_off(self):
        f = _bare_flow()
        f.wf["energy_stat"] = False
        f._read_energy = mock.Mock()
        f._capture_energy_start()
        f._read_energy.assert_not_called()


class TestCaptureEnergyEnd(unittest.TestCase):
    def test_normal_delta(self):
        f = _bare_flow()
        f._energy_start, f._energy_start_done = 602, True
        f._enter_taskcenter = mock.Mock(return_value=True)
        f._dismiss_badcase = mock.Mock()
        f._read_energy = mock.Mock(return_value=812)
        f._exit_taskcenter = mock.Mock()
        f._capture_energy_end(["R1"])
        self.assertEqual(f._energy_end, 812)
        f._enter_taskcenter.assert_called_once_with("R1")
        f._exit_taskcenter.assert_called_once()

    def test_start_missing_skips(self):
        # 起始未读到 → 不为统计白跑一次进台
        f = _bare_flow()
        f._energy_start, f._energy_start_done = None, True
        f._enter_taskcenter = mock.Mock()
        f._capture_energy_end(["R1"])
        f._enter_taskcenter.assert_not_called()

    def test_enter_fail_all_robots(self):
        f = _bare_flow()
        f._energy_start, f._energy_start_done = 602, True
        f._enter_taskcenter = mock.Mock(return_value=False)
        f._capture_energy_end(["R1", "R2"])
        self.assertIsNone(f._energy_end)
        self.assertEqual(f._enter_taskcenter.call_count, 2)


class TestSummaryEnergyLine(unittest.TestCase):
    def _summary_lines(self, f):
        with mock.patch.object(flow, "logger") as lg:
            f._log_summary(True, True, title="账号 T (1)")
        out = []
        for c in lg.info.call_args_list:
            out.append(c.args[0] % c.args[1:] if len(c.args) > 1 else c.args[0])
        return out

    def test_delta_line(self):
        f = _bare_flow()
        f.stats = {"R1": {"entered": True, "signin": "成功",
                          "feedback": "成功", "ad": 10, "note": ""}}
        f._energy_start, f._energy_end = 602, 812
        out = self._summary_lines(f)
        self.assertTrue(any("电量：602 → 812，本流程获得 +210" in l for l in out))

    def test_end_missing_line(self):
        f = _bare_flow()
        f.stats = {"R1": {"entered": True, "signin": "成功",
                          "feedback": "成功", "ad": 3, "note": ""}}
        f._energy_start, f._energy_end = 602, None
        out = self._summary_lines(f)
        self.assertTrue(any("电量：起始 602，结束读数失败" in l for l in out))

    def test_start_missing_no_line(self):
        f = _bare_flow()
        f.stats = {"R1": {"entered": True, "signin": "成功",
                          "feedback": "成功", "ad": 3, "note": ""}}
        f._energy_start, f._energy_end = None, None
        out = self._summary_lines(f)
        self.assertFalse(any("电量" in l for l in out))


if __name__ == "__main__":
    unittest.main()
