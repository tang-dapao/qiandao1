"""签到奖励广告「看广告 +⚡」（2026-09-16）静态回归测试（mock 驱动，不依赖真机）。

覆盖：
- _signin 已签到跳过路径不触发奖励广告；真正签到路径触发一次
- wf.signin_ad=false 不触发
- _watch_signin_ad：浮层内 dump 含匹配「看广告」按钮 → 节点点击 + 共用播放关闭段
- 按钮 dump 未命中 → OCR 兜底（trusted 坐标点击）
- 两者都未命中 → 跳过返回 False，不动广告关闭段
- 奖励广告异常不阻断 _signin 成功返回
- _ad_play_and_close 从 _watch_ad_once 抽出后的行为回归（经由现有用例覆盖）

运行：py -3.13 -m unittest test_signin_ad -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from flow import Flow
from adb_ui import Node


def _node(text, x1, y1, x2, y2):
    return Node(text, x1, y1, x2, y2)


class SigninAdBase(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0,
                     "ad_wait_min": 15, "ad_wait_max": 17,
                     "signin_ad": True}
        self.f.t = {"click_min": 0.1, "click_max": 0.2,
                    "page_wait": 0.1, "ad_close_wait": 0.1}
        self._sleep = mock.patch.object(flow_mod.time, "sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

    # ---- _signin 打桩：真实 _signin + mock 页面交互 ----
    def stub_signin_page(self, signed=False, know=False, sheet=True):
        """构造签到页交互桩。
        signed=True：签到行 1/1（已签，跳过）；否则 None（可签）。
        know：_find("我知道了") 是否命中。
        sheet：_find("每日免费领"/"恭喜获得") 是否命中。
        """
        row = (_node("每日签到", 100, 900, 500, 950), "每日签到",
               (1, 1) if signed else None)
        self.f._find_row = mock.Mock(return_value=row)

        def find(text, *a, **k):
            if text == "我知道了":
                return _node("我知道了", 400, 1150, 680, 1230) if know else None
            if text in ("每日免费领", "恭喜获得"):
                return _node("每日免费领", 40, 1050, 1040, 1900) if sheet else None
            return None
        self.f._find = mock.Mock(side_effect=find)
        self.f.ui = mock.Mock()
        self.f.ui.wait_for.return_value = _node("每日免费领", 40, 1050,
                                                1040, 1900)
        self.f._tap = mock.Mock(return_value=True)
        self.f._tap_node = mock.Mock()
        self.f._reset_after_fail = mock.Mock()
        self.f._watch_signin_ad = mock.Mock(return_value=True)
        self.f._ad_play_and_close = mock.Mock(return_value=True)


class TestSigninAdTrigger(SigninAdBase):
    def test_signed_skip_does_not_trigger(self):
        # 今日已签（1/1）→ 直接跳过，不进浮层、不触发奖励广告
        self.stub_signin_page(signed=True)
        self.assertTrue(self.f._signin())
        self.f._watch_signin_ad.assert_not_called()

    def test_real_signin_triggers_bonus_once(self):
        # 真正签到：浮层出现 → 我知道了 → 奖励广告 1 次 → 关浮层
        self.stub_signin_page(signed=False, know=True, sheet=True)
        self.assertTrue(self.f._signin())
        self.f._watch_signin_ad.assert_called_once_with()
        # 关浮层 ✕ 仍执行（trusted 坐标点击）
        self.assertTrue(any(c.args[2:3] == (True,) or c.kwargs.get("trusted")
                            for c in self.f._tap.call_args_list))

    def test_signin_ad_disabled_by_config(self):
        self.f.wf["signin_ad"] = False
        self.stub_signin_page(signed=False, know=True, sheet=True)
        self.assertTrue(self.f._signin())
        self.f._watch_signin_ad.assert_not_called()

    def test_bonus_exception_does_not_break_signin(self):
        # 奖励广告抛异常 → _signin 仍成功返回（不阻断主流程）
        self.stub_signin_page(signed=False, know=True, sheet=True)
        self.f._watch_signin_ad = mock.Mock(
            side_effect=RuntimeError("boom"))
        self.assertTrue(self.f._signin())


class TestWatchSigninAd(SigninAdBase):
    def setUp(self):
        super().setUp()
        self.f._find = mock.Mock(return_value=_node("每日免费领", 40, 1050,
                                                    1040, 1900))
        self.f.ui = mock.Mock()
        self.f._tap = mock.Mock(return_value=True)
        self.f._tap_node = mock.Mock()
        self.f._ad_play_and_close = mock.Mock(return_value=True)
        self.f._ocr_find = mock.Mock(return_value=None)

    def test_dump_hit_taps_node_and_closes_sheet(self):
        # 浮层底部「看广告 +⚡」节点（y>=1600）→ 节点点击 + 播放关闭 + 关浮层
        self.f.ui.nodes.return_value = [
            _node("看广告", 100, 800, 500, 850),        # 背景 TC 行（y<1600，忽略）
            _node("看广告 +⚡", 60, 1790, 1020, 1880),  # 浮层按钮
        ]
        self.assertTrue(self.f._watch_signin_ad())
        self.f._ad_play_and_close.assert_called_once()
        self.assertEqual(self.f._ad_play_and_close.call_args.kwargs.get(
            "sheet_ok"), True)
        self.f._tap_node.assert_called_once()
        n = self.f._tap_node.call_args[0][0]
        self.assertEqual(n.y1, 1790)                 # 选的是浮层底部按钮
        self.f._tap.assert_called()                  # 关 ✕（trusted）

    def test_dump_miss_ocr_fallback_taps_trusted(self):
        # dump 无按钮节点 → OCR 底部区域命中 → trusted 坐标点击
        self.f.ui.nodes.return_value = [
            _node("看广告", 100, 800, 500, 850)]     # 只有背景行
        self.f._ocr_find = mock.Mock(return_value=(540, 1835))
        self.assertTrue(self.f._watch_signin_ad())
        self.f._ocr_find.assert_called_once()
        self.f._ad_play_and_close.assert_called_once()
        self.assertEqual(self.f._ad_play_and_close.call_args.kwargs.get(
            "sheet_ok"), True)
        args, kwargs = self.f._tap.call_args_list[0]
        self.assertEqual((args[0], args[1]), (540, 1835))
        self.assertTrue(kwargs.get("trusted") or
                        (len(args) > 2 and args[2] is True))

    def test_no_button_anywhere_skips_without_ad(self):
        # 已看过（按钮消失）→ 跳过，不碰广告关闭段
        self.f.ui.nodes.return_value = []
        self.f._ocr_find = mock.Mock(return_value=None)
        self.assertFalse(self.f._watch_signin_ad())
        self.f._ad_play_and_close.assert_not_called()

    def test_confirm_direct_close_accepts_sheet_as_terminal(self):
        # 14:18 代柯实测回归：E1 直关已生效但浮层盖着 TC → OCR 不认；
        # sheet_ok=True 时「浮层在屏」必须判成功（否则 BACK×3 退穿）
        self.f._back_at_taskcenter = mock.Mock(return_value=False)
        self.f._find = mock.Mock(
            return_value=_node("每日免费领", 40, 1050, 1040, 1900))
        self.assertTrue(self.f._confirm_direct_close(0.1, sheet_ok=True))
        # 默认 sheet_ok=False：同样的页面状态判失败（主广告路径语义不变）
        self.assertFalse(self.f._confirm_direct_close(0.1))
        # 浮层不在屏 + OCR 不认 → 仍失败
        self.f._find = mock.Mock(return_value=None)
        self.assertFalse(self.f._confirm_direct_close(0.1, sheet_ok=True))

    def test_sheet_gone_skips(self):
        # 浮层已不在屏 → 直接跳过
        self.f._find = mock.Mock(return_value=None)
        self.f.ui.nodes.return_value = []
        self.assertFalse(self.f._watch_signin_ad())
        self.f._ad_play_and_close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
