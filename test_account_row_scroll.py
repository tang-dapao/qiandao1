# -*- coding: utf-8 -*-
"""账号面板滚动查找（2026-10-30 王账号切号失败）单测。

事故事实（10-30 11:37 失败现场 dump，5 个账号）：
    唐六爻(616)/3462182422(679)、凌津渡(778)/3102582976(841)、
    唐灵(940)/2593292352(1003)、觅夏(1102)/2774054368(1165)、
    **王(1250) 但 UIN 1766775822 不在树里**（被面板视口切掉）
⇒ `_wait_for_text(uin)` 6s 打空 × 3 次重试 → 整账号跳过。

⚠️ 修法形态被真机探针纠正过两次：面板是**居中对话框**（列表区 y≈780~1470），
`swipe_up()` 起点固定 y=1500 在对话框**外** → 手势被 ROM 当成「点面板外关闭」
→ 面板消失（v2/v4 探针各翻车一次）。**只有面板内小幅拖**可用：v5 实测
`(540,1440)→(540,1140)` / 700ms 一次即读出 UIN，面板仍开着。

修复后要守住：
  1. 首屏能读到 UIN → **一次拖动都不发**（成功路径零成本）。
  2. 首屏读不到 → 面板内**拖动**再找，找到即继续切号。
  3. **绝不回退到 swipe_up / swipe_down**（会把面板关掉）—— 用例直接炸。
  4. `accounts.row_scroll: false` → 恢复旧行为（不拖，直接判失败）。
  5. 拖动次数由 `accounts.row_scroll_rounds` 控制；面板被关掉则提前收手。
  6. 拖动坐标可由 `row_scroll_x / from_y / to_y / duration_ms` 覆盖。
  7. 后端没有 `swipe_from_to`（或 ui 是裸 Mock）→ 返回 None，**不许假通过**。

运行：py -3.13 -m unittest test_account_row_scroll -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import (ACCOUNT_ENTRY_TEXT, CONTACT_PAGE_HINT, SWITCH_ACCOUNT_TEXT,
                  Flow)

UIN = "1766775822"
NICK = "王"


def _n(text, y1, y2=None, x1=300, x2=480):
    return Node(text, x1, y1, x2, y2 if y2 is not None else y1 + 60)


def _first_screen():
    """10-30 失败现场复刻：第 5 个账号只有昵称、UIN 不在树上。"""
    return [
        _n("唐六爻", 616), _n("3462182422", 679),
        _n("凌津渡", 778), _n("3102582976", 841),
        _n("唐灵", 940), _n("2593292352", 1003),
        _n("觅夏", 1102), _n("2774054368", 1165),
        _n(NICK, 1250),
    ]


class FakePanelUI:
    """模拟「切换账号」居中对话框：每次 `swipe_from_to` 换下一屏。

    `swipe_up` / `swipe_down` 故意 **抛断言** —— 它们起点固定 y=1500，落在
    对话框外，会把面板关掉。任何代码若退回用它们做面板滚动，用例立刻炸掉。
    """

    def __init__(self, screens):
        self.screens = [list(s) for s in screens]
        self.drags = []
        self.dump_fail_streak = 0

    def nodes(self, include_desc=True, timeout=None):
        return list(self.screens[0]) if self.screens else []

    def swipe_from_to(self, x1, y1, x2, y2, duration_ms=600, pause=1.0):
        self.drags.append((x1, y1, x2, y2, duration_ms))
        if len(self.screens) > 1:
            self.screens.pop(0)

    def swipe_up(self, *a, **k):
        raise AssertionError(
            "面板内滚动禁止用 swipe_up：起点 y=1500 在对话框外，会把面板关掉")

    def swipe_down(self, *a, **k):
        raise AssertionError(
            "面板内滚动禁止用 swipe_down：同样落到对话框外，会关掉面板")


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {}
        self.f.acc = {"settle": 0, "row_scroll": True, "row_scroll_rounds": 2}
        self.f.ui = mock.Mock()
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)


class TestFindAccountRowScroll(Base):
    """`_find_account_row_with_scroll` 主体行为。"""

    def test_knob_off_returns_none_without_drag(self):
        self.f.acc["row_scroll"] = False
        ui = FakePanelUI([_first_screen()])
        self.f.ui = ui
        self.assertIsNone(self.f._find_account_row_with_scroll(UIN))
        self.assertEqual(ui.drags, [])

    def test_hit_after_one_drag(self):
        ui = FakePanelUI([
            _first_screen(),
            _first_screen()[:8] + [_n(NICK, 1328), _n(UIN, 1399)],
        ])
        self.f.ui = ui
        got = self.f._find_account_row_with_scroll(UIN)
        self.assertIsNotNone(got)
        self.assertEqual(got.text, UIN)
        self.assertEqual(got.y1, 1399)
        self.assertEqual(len(ui.drags), 1)

    def test_default_drag_geometry(self):
        """默认拖着必须落在对话框列表区内（from_y 1440 < 1470 底边）。"""
        ui = FakePanelUI([_first_screen(), [_n(UIN, 1399)]])
        self.f.ui = ui
        self.f._find_account_row_with_scroll(UIN)
        self.assertEqual(ui.drags[0], (540, 1440, 540, 1140, 700))

    def test_custom_drag_geometry(self):
        self.f.acc.update({"row_scroll_x": 520, "row_scroll_from_y": 1400,
                           "row_scroll_to_y": 1000,
                           "row_scroll_duration_ms": 650})
        ui = FakePanelUI([_first_screen(), [_n(UIN, 1399)]])
        self.f.ui = ui
        self.f._find_account_row_with_scroll(UIN)
        self.assertEqual(ui.drags[0], (520, 1400, 520, 1000, 650))

    def test_gives_up_after_rounds(self):
        self.f.acc["row_scroll_rounds"] = 3
        ui = FakePanelUI([_first_screen()])   # 永远读不到 UIN
        self.f.ui = ui
        self.assertIsNone(self.f._find_account_row_with_scroll(UIN))
        self.assertEqual(len(ui.drags), 3)

    def test_panel_closed_aborts_early(self):
        """拖完面板消失（节点全空）→ 立刻收手，不空拖剩余轮次。"""
        self.f.acc["row_scroll_rounds"] = 3
        ui = FakePanelUI([_first_screen(), []])
        self.f.ui = ui
        self.assertIsNone(self.f._find_account_row_with_scroll(UIN))
        self.assertEqual(len(ui.drags), 1)

    def test_still_open_without_title_node(self):
        """「面板还在」不能靠『切换账号』标题判 —— 该面板 dump 里只有账号行。

        中间那屏故意**不含**标题，若判据错写成标题匹配，这里会提前收手。
        """
        mid = [_n("唐六爻", 400), _n("3462182422", 463),
               _n(NICK, 600)]
        ui = FakePanelUI([_first_screen(), mid, [_n(UIN, 700)]])
        self.f.ui = ui
        got = self.f._find_account_row_with_scroll(UIN)
        self.assertIsNotNone(got)
        self.assertEqual(len(ui.drags), 2)

    def test_backend_without_swipe_from_to_returns_none(self):
        class NoDrag:
            dump_fail_streak = 0

            def nodes(self, include_desc=True, timeout=None):
                return _first_screen()

        self.f.ui = NoDrag()
        self.assertIsNone(self.f._find_account_row_with_scroll(UIN))

    def test_bare_mock_ui_does_not_fake_pass(self):
        """裸 Mock：`nodes()` 返回 Mock 且 `swipe_from_to` 是自动生成的 Mock。

        若不校验类型，会「拖了、没找到、也不报错」混过去 —— 关键是**不能返回
        一个节点**（假通过），必须 None。
        """
        self.assertIsNone(self.f._find_account_row_with_scroll(UIN))

    def test_missing_acc_attr_defaults_on(self):
        del self.f.acc
        ui = FakePanelUI([_first_screen(), [_n(UIN, 1399)]])
        self.f.ui = ui
        self.assertIsNotNone(self.f._find_account_row_with_scroll(UIN))
        self.assertEqual(len(ui.drags), 1)


class TestNodeMatching(Base):
    """`_match_uin_node` / `_panel_still_open` 细节。"""

    def test_exact_match(self):
        nd = self.f._match_uin_node([_n("觅夏", 100), _n(UIN, 160)], UIN)
        self.assertIsNotNone(nd)
        self.assertEqual(nd.y1, 160)

    def test_tolerates_short_suffix_glue(self):
        """dump 偶发把 UIN 与少量后缀粘在一个节点里。"""
        nd = self.f._match_uin_node([_n(UIN + " (当前)", 160)], UIN)
        self.assertIsNotNone(nd)

    def test_rejects_long_text_containing_uin(self):
        """长文本里含 UIN 不算（避免命中「签名里写了 QQ 号」这类节点）。"""
        self.assertIsNone(self.f._match_uin_node(
            [_n("我的QQ号是 " + UIN + " 欢迎加好友呀", 160)], UIN))

    def test_nick_only_is_not_a_hit(self):
        self.assertIsNone(self.f._match_uin_node([_n(NICK, 1250)], UIN))

    def test_panel_open_true_on_uin_rows(self):
        self.assertTrue(self.f._panel_still_open([_n("觅夏", 100),
                                                 _n("2774054368", 160)]))

    def test_panel_open_false_when_no_uin_row(self):
        self.assertFalse(self.f._panel_still_open([_n(NICK, 100)]))
        self.assertFalse(self.f._panel_still_open([]))

    def test_panel_nodes_rejects_non_list(self):
        self.f.ui = mock.Mock()      # nodes() 返回 Mock
        self.assertEqual(self.f._panel_nodes(), [])


class TestSwitchAccountWiring(Base):
    """`_switch_account` 必须在首屏 miss 时走拖动兜底。"""

    def setUp(self):
        super().setUp()
        self.f._page_tc = False
        self.f._at_robot_list = False
        self.f._find = mock.Mock(return_value=_n(ACCOUNT_ENTRY_TEXT, 100, 160))
        self.f._on_qq_main_shell = mock.Mock(return_value=True)
        self.f._contacts_tab_active = mock.Mock(return_value=True)
        self.f._tap_node = mock.Mock()
        self.f._sidebar_nick = mock.Mock(return_value=NICK)
        self.f._log_page_snapshot = mock.Mock()
        self.f._safe_back_to_robot_list = mock.Mock(return_value=True)
        self.f._recover_to_contacts_after_switch = mock.Mock(return_value=True)
        self.uin_node = _n(UIN, 1300, 1360)

    def _wt_factory(self, uin_first_screen):
        def _wt(text, timeout=6.0, interval=0.4):
            if text == SWITCH_ACCOUNT_TEXT:
                return _n(SWITCH_ACCOUNT_TEXT, 460, 510)
            if text == UIN:
                return self.uin_node if uin_first_screen else None
            if text == CONTACT_PAGE_HINT:
                return _n(CONTACT_PAGE_HINT, 380, 440)
            return _n(text, 100, 160)
        return _wt

    def test_first_screen_hit_does_not_scroll(self):
        self.f._wait_for_text = mock.Mock(side_effect=self._wt_factory(True))
        self.f._find_account_row_with_scroll = mock.Mock()
        self.assertTrue(self.f._switch_account(UIN, NICK))
        self.f._find_account_row_with_scroll.assert_not_called()

    def test_miss_falls_back_to_scroll_and_succeeds(self):
        self.f._wait_for_text = mock.Mock(side_effect=self._wt_factory(False))
        self.f._find_account_row_with_scroll = mock.Mock(
            return_value=self.uin_node)
        self.assertTrue(self.f._switch_account(UIN, NICK))
        self.f._find_account_row_with_scroll.assert_called_once_with(UIN)
        self.f._log_page_snapshot.assert_not_called()

    def test_miss_and_scroll_miss_fails_with_snapshot(self):
        self.f._wait_for_text = mock.Mock(side_effect=self._wt_factory(False))
        self.f._find_account_row_with_scroll = mock.Mock(return_value=None)
        self.assertFalse(self.f._switch_account(UIN, NICK))
        self.f._log_page_snapshot.assert_called_once_with("switch_no_uin")

    def test_knob_off_miss_fails_without_drag(self):
        self.f.acc["row_scroll"] = False
        ui = FakePanelUI([_first_screen()])
        self.f.ui = ui
        self.f._wait_for_text = mock.Mock(side_effect=self._wt_factory(False))
        self.assertFalse(self.f._switch_account(UIN, NICK))
        self.assertEqual(ui.drags, [])
        self.f._log_page_snapshot.assert_called_once_with("switch_no_uin")


if __name__ == "__main__":
    unittest.main()
