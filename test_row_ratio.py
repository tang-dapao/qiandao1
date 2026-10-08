"""任务行 X/Y 计数解析回归测试（2026-09-30，修 /1000 串扰）。

背景：`_row_completion` 取 label 行 ±30px 的 y band 拼接所有节点文本后
`re.search(r"(\\d+)/(\\d+)")`。**同 y band 会拼入邻近数字**（实测电量等），
把 "…2/10" 吃成 (2,1000)：旧贪婪正则 `(\\d+)` 会把 "10" 后面的 "00" 一起吞。

修复：分子/分母限 2 位 `\\d{1,2}`（本 app 任务行配额恒为 1 或 X/10，2 位足矣）；
并在 `_read_ad_ratio` 加分母校验（必须 == ad_times_per_robot），不符即视为读不到。

运行：py -3.13 -m unittest test_row_ratio -v
"""
import unittest
from unittest import mock

from adb_ui import Node
from flow import Flow


def nd(text, x1, x2, y1=800, y2=860):
    return Node(text, x1, y1, x2, y2)


class RowRatioBase(unittest.TestCase):
    def _flow(self, nodes):
        f = Flow.__new__(Flow)
        f.ui = mock.Mock()
        f.ui.nodes.return_value = nodes
        return f


class TestRowCompletion(RowRatioBase):
    def test_polluted_band_does_not_read_1000(self):
        """电量串扰 "2/10" + "00" → 旧代码得 (2,1000)，修复后须为 (2,10)。"""
        label = nd("看视频赚电量", 100, 300)
        nodes = [label,
                 nd("2", 400, 420),
                 nd("/", 425, 435),
                 nd("10", 440, 480),
                 nd("00", 900, 920)]      # 邻近串扰数字（电量等）
        f = self._flow(nodes)
        self.assertEqual(f._row_completion(label), (2, 10))

    def test_polluted_band_3digit_tail(self):
        """分母只串进一个 "0" → "2/100" 也须收敛为 (2,10)。"""
        label = nd("看视频赚电量", 100, 300)
        nodes = [label, nd("2", 400, 420), nd("/", 425, 435),
                 nd("10", 440, 480), nd("0", 900, 920)]
        f = self._flow(nodes)
        self.assertEqual(f._row_completion(label), (2, 10))

    def test_single_node_fullmatch(self):
        """MuMu 合并为单节点 "10/10" → 走第 1 遍整词。"""
        label = nd("看视频赚电量", 100, 300)
        f = self._flow([label, nd("10/10", 400, 480)])
        self.assertEqual(f._row_completion(label), (10, 10))

    def test_signin_split_one_of_one(self):
        """1/1 拆成三个节点 → (1,1)。"""
        label = nd("每日签到", 100, 300)
        f = self._flow([label, nd("1", 400, 420), nd("/", 425, 435),
                        nd("1", 440, 460)])
        self.assertEqual(f._row_completion(label), (1, 1))

    def test_no_ratio_returns_none(self):
        label = nd("看视频赚电量", 100, 300)
        f = self._flow([label, nd("获取随机", 400, 600)])
        self.assertIsNone(f._row_completion(label))


class TestReadAdRatioGuard(RowRatioBase):
    def _flow2(self):
        f = Flow.__new__(Flow)
        f.wf = {"ad_times_per_robot": 10}
        return f

    def test_rejects_wrong_denominator(self):
        f = self._flow2()
        f._find_row = mock.Mock(return_value=(None, None, (2, 1000)))
        self.assertIsNone(f._read_ad_ratio())

    def test_accepts_correct_denominator(self):
        f = self._flow2()
        f._find_row = mock.Mock(return_value=(None, None, (2, 10)))
        self.assertEqual(f._read_ad_ratio(), (2, 10))

    def test_none_ratio_is_none(self):
        f = self._flow2()
        f._find_row = mock.Mock(return_value=(None, None, None))
        self.assertIsNone(f._read_ad_ratio())


if __name__ == "__main__":
    unittest.main()
