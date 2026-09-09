"""flow.py 广告关闭 3 处修复的静态回归测试（mock 驱动，不依赖真机）。

覆盖：
1. _ocr_find：ymax 过滤 + region 偏移 + 与原逻辑等价性 + y==400 边界
2. _back_at_taskcenter：穿透误判防护（顶部关闭按钮 vs 正文关闭字样）
3. _close_ad：4 条路径路由 + 步骤顺序 + max_tries 读取容错 + 最坏循环次数

运行：py -3.13 -m unittest test_flow_ocr_close -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import Flow


def _ocr_data(words):
    """按 (text, left, top, width, height) 列表构造 image_to_data 返回的 DICT。"""
    d = {"text": [], "left": [], "top": [], "width": [], "height": []}
    for t, l, tp, w, h in words:
        d["text"].append(t)
        d["left"].append(l)
        d["top"].append(tp)
        d["width"].append(w)
        d["height"].append(h)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        # 强制 OCR 可用 + 替换 pytesseract 为 fake + 去掉真实 sleep
        self._has_ocr = mock.patch.object(flow_mod, "_HAS_OCR", True)
        self._has_ocr.start()
        self.addCleanup(self._has_ocr.stop)

        self.fake_pt = mock.Mock()
        self.fake_pt.Output.DICT = "dict"
        self._pt = mock.patch.object(flow_mod, "pytesseract",
                                     self.fake_pt, create=True)
        self._pt.start()
        self.addCleanup(self._pt.stop)

        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

        self.f = Flow.__new__(Flow)
        self.f.wf = {}
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f._ocr_lang = "chi_sim+eng"


class TestOcrFind(Base):
    def _shot(self):
        img = mock.Mock()
        self.f._ocr_shot = mock.Mock(return_value=img)
        return img

    def test_no_region_no_ymax_returns_center(self):
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭广告", 100, 140, 44, 24)])
        self.assertEqual(self.f._ocr_find("关闭广告", "关闭"), (122, 152))

    def test_ymax_skips_below_and_matches_above(self):
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭", 100, 500, 40, 20), ("关闭广告", 100, 140, 44, 24)])
        self.assertEqual(
            self.f._ocr_find("关闭广告", "关闭", "跳过", ymax=400), (122, 152))

    def test_ymax_all_below_returns_none(self):
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭", 100, 500, 40, 20), ("跳过", 200, 600, 40, 20)])
        self.assertIsNone(self.f._ocr_find("关闭", "跳过", ymax=400))

    def test_region_offsets_coordinates(self):
        img = self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭广告", 100, 140, 44, 24)])
        pos = self.f._ocr_find("关闭广告", region=(10, 20, 300, 300))
        self.assertEqual(pos, (132, 172))
        img.crop.assert_called_once_with((10, 20, 300, 300))

    def test_ymax_exact_boundary_is_accepted(self):
        # 记录当前实现行为：ymax 用 ">"，y==ymax 时仍会被接受
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭", 100, 390, 40, 20)])  # y = 390 + 10 = 400
        self.assertEqual(self.f._ocr_find("关闭", ymax=400), (120, 400))

    def test_no_ymax_keeps_old_fullscreen_behavior(self):
        # ymax=None 等价旧行为：不裁剪，命中 y>400 的正文也算（回归等价性）
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭", 100, 500, 40, 20)])
        self.assertEqual(self.f._ocr_find("关闭"), (120, 510))

    def test_keyword_substring_order_independent_coord(self):
        # x,y 已提到外层，keyword 命中哪个都不改变坐标
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭广告", 100, 140, 44, 24)])
        self.assertEqual(self.f._ocr_find("关闭", "关闭广告"), (122, 152))


class TestBackAtTaskcenter(Base):
    def test_penetration_plus_top_close_means_still_in_ad(self):
        self.f._find = mock.Mock(return_value=Node("每日签到", 0, 0, 100, 100))
        self.f._ocr_find = mock.Mock(return_value=(120, 152))
        self.assertFalse(self.f._back_at_taskcenter())
        self.f._ocr_find.assert_called_once_with(
            "关闭广告", "关闭", "跳过", ymax=400)

    def test_taskcenter_and_no_close_button_means_back(self):
        self.f._find = mock.Mock(return_value=Node("每日签到", 0, 0, 100, 100))
        self.f._ocr_find = mock.Mock(return_value=None)
        self.assertTrue(self.f._back_at_taskcenter())

    def test_no_taskcenter_text_means_not_back(self):
        self.f._find = mock.Mock(side_effect=[None, None])
        self.f._ocr_find = mock.Mock()
        self.assertFalse(self.f._back_at_taskcenter())
        self.f._ocr_find.assert_not_called()

    def test_fallback_on_taskcenter_title(self):
        # 只有「任务中心」命中时同样进入 OCR 二次确认
        self.f._find = mock.Mock(side_effect=[None, Node("任务中心", 0, 0, 100, 100)])
        self.f._ocr_find = mock.Mock(return_value=(120, 152))
        self.assertFalse(self.f._back_at_taskcenter())


class TestCloseAd(Base):
    def _stub(self):
        f = self.f
        f._find = mock.Mock(return_value=None)
        f.ui = mock.Mock()
        f.ui.nodes.return_value = []
        f._tap_node = mock.Mock()
        f._tap = mock.Mock()
        f._ocr_find = mock.Mock(return_value=None)
        f._back_at_taskcenter = mock.Mock(return_value=False)
        return f

    def test_path_uiautomator(self):
        f = self.f
        node = Node("关闭广告", 0, 140, 200, 164)
        f._find = mock.Mock(return_value=node)
        f.ui = mock.Mock()
        f._tap_node = mock.Mock()
        f._back_at_taskcenter = mock.Mock(return_value=True)
        f._ocr_find = mock.Mock()
        f._tap = mock.Mock()
        self.assertTrue(f._close_ad())
        f._tap_node.assert_called_once_with(node, pause=1.5)
        f._ocr_find.assert_not_called()
        f._tap.assert_not_called()

    def test_path_auto_ended(self):
        f = self._stub()
        f._back_at_taskcenter = mock.Mock(return_value=True)
        self.assertTrue(f._close_ad())
        f._tap_node.assert_not_called()
        f._ocr_find.assert_not_called()
        f._tap.assert_not_called()

    def test_path_ocr(self):
        f = self._stub()
        f._back_at_taskcenter = mock.Mock(side_effect=[False, True])
        f._ocr_find = mock.Mock(return_value=(120, 152))
        self.assertTrue(f._close_ad())
        f.ui.tap.assert_called_once_with(120, 152, pause=1.5)
        f._tap.assert_not_called()

    def test_path_fixed_coordinate_fallback(self):
        f = self._stub()
        f.wf = {"ad_close_retries": 3}
        f._back_at_taskcenter = mock.Mock(return_value=False)
        self.assertFalse(f._close_ad())
        self.assertEqual(f._tap.call_count, 3)
        f._tap.assert_called_with(*flow_mod.AD_CLOSE, pause=1.5)
        # step2 每轮一次 + 循环后最终确认一次
        self.assertEqual(f._back_at_taskcenter.call_count, 4)

    def test_step2_before_step3_order(self):
        f = self._stub()
        calls = []
        f._back_at_taskcenter = mock.Mock(
            side_effect=lambda: calls.append("back") or False)
        f._ocr_find = mock.Mock(
            side_effect=lambda *a, **k: calls.append("ocr") or None)
        f.wf = {"ad_close_retries": 1}
        f._close_ad()
        # 首轮顺序：uiautomator(未命中) -> back(自动结束判断) -> ocr
        self.assertEqual(calls[0], "back")
        self.assertEqual(calls[1], "ocr")

    def test_max_tries_config_wins_over_default(self):
        f = self._stub()
        f.wf = {"ad_close_retries": 5}
        f._close_ad()  # 参数默认 6，但 config 5 应生效
        self.assertEqual(f._tap.call_count, 5)

    def test_max_tries_missing_key_falls_back_to_default(self):
        f = self._stub()
        f.wf = {}  # 缺 ad_close_retries
        f._close_ad()  # 默认 max_tries=6
        self.assertEqual(f._tap.call_count, 6)

    def test_max_tries_zero_returns_final_check_only(self):
        f = self._stub()
        f.wf = {"ad_close_retries": 0}
        f._back_at_taskcenter = mock.Mock(return_value=True)
        self.assertTrue(f._close_ad())
        f._tap.assert_not_called()
        f._ocr_find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
