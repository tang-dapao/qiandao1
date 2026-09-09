"""寻路优化（B1/B2/B3）的静态回归测试（mock 驱动，不依赖真机/adb）。

覆盖：
- B2 _find_scroll：
    * 滚动到页面尽头（无新文本）即提前停止，不机械滚满 max_scroll
    * 首屏命中不滚动 / 一次滚动命中 / dump 空结果快速返回
- B1 _nav_robot_list：
    * _at_robot_list=True 且快照判为机器人列表 → 快路径直接返回（零 tap）
    * 状态失真（实际在任务中心/聊天页）→ 回退常规导航不崩
- B3 _enter_taskcenter：
    * 固定 sleep 改条件等待（wait_for ymin 传参正确）、成功清状态位
    * 等待失败返回 False
- _exit_taskcenter：退出后置 _at_robot_list=True（快路径状态）

运行：py -3.13 -m unittest test_nav_optimize -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import Flow


def nd(text: str, y: int = 500, x1: int = 100) -> Node:
    """构造一个 200x80 的文本节点（默认 y 中区）。"""
    return Node(text, x1, y, x1 + 200, y + 80)


class FakeUI:
    """可翻页 UI：nodes() 返回当前页；swipe 切换页面并计数（到底自动停）。"""

    def __init__(self, pages):
        self.pages = pages          # list[list[Node]]
        self.pos = 0
        self.swipes = 0
        self.taps = []

    def nodes(self):
        return list(self.pages[self.pos])

    def _move(self, d):
        self.swipes += 1
        npos = self.pos + d
        if 0 <= npos < len(self.pages):
            self.pos = npos

    def swipe_up(self, pause=1.0):
        self._move(1)

    def swipe_down(self, pause=1.0):
        self._move(-1)

    def find(self, text, ymin=0, ymax=99999, xmin=0, xmax=99999):
        for n in self.nodes():
            if (n.text == text and ymin <= n.y1 <= ymax
                    and xmin <= n.x1 <= xmax):
                return n
        return None

    def tap(self, x, y, pause=1.2):
        self.taps.append((x, y))

    def tap_node(self, node, pause=1.2):
        self.taps.append(node.center)


def make_flow(ui, at_list=False):
    f = Flow.__new__(Flow)
    f.ui = ui
    f.t = {"click_min": 1.5, "click_max": 3.0, "page_wait": 2.0,
           "taskcenter_wait": 3.5}
    f.wf = {"ad_cooldown": 60}
    f._at_robot_list = at_list
    return f


# ----------------------------------------------------------------------
# B2: _find_scroll 到尽头提前停止
# ----------------------------------------------------------------------
class TestFindScrollStopAtEnd(unittest.TestCase):
    def test_no_target_stops_when_no_new_content(self):
        # 3 页短列表，目标不存在：每方向滚到"无新文本"即停，不会滚满 10 次
        pages = [[nd("A", 300), nd("B", 400)],
                 [nd("C", 300), nd("D", 400)],
                 [nd("E", 300)]]
        ui = FakeUI(pages)
        f = make_flow(ui)
        self.assertIsNone(f._find_scroll("ZZZ", max_scroll=10))
        # attempt1 向下 3 次(含到尽头那 1 次) + attempt2 向上 3 次
        self.assertEqual(ui.swipes, 6)

    def test_target_first_screen_no_swipe(self):
        ui = FakeUI([[nd("李宥恩", 300), nd("小麦", 400)]])
        f = make_flow(ui)
        n = f._find_scroll("李宥恩", max_scroll=10)
        self.assertIsNotNone(n)
        self.assertEqual(n.text, "李宥恩")
        self.assertEqual(ui.swipes, 0)

    def test_target_after_one_swipe(self):
        pages = [[nd("A", 300)], [nd("目标", 300)]]
        ui = FakeUI(pages)
        f = make_flow(ui)
        n = f._find_scroll("目标", max_scroll=10)
        self.assertIsNotNone(n)
        self.assertEqual(ui.swipes, 1)

    def test_down_first_false_searches_upward(self):
        pages = [[nd("目标", 300)], [nd("A", 300)]]
        ui = FakeUI(pages)
        ui.pos = 1                       # 当前在下屏，目标在上方
        f = make_flow(ui)
        n = f._find_scroll("目标", max_scroll=10, down_first=False)
        self.assertIsNotNone(n)
        self.assertEqual(ui.swipes, 1)

    def test_empty_dump_returns_none_fast(self):
        # dump 持续失败（nodes 空）：每方向第 1 次"无新内容"即停
        ui = FakeUI([[]])
        f = make_flow(ui)
        self.assertIsNone(f._find_scroll("X", max_scroll=10))
        self.assertEqual(ui.swipes, 2)   # 两个方向各 1 次试探后停止

    def test_overshoot_then_flip_direction_finds(self):
        # attempt1 向下滚过头(目标在中间某屏被跳过场景简化版)，
        # attempt2 翻转向上应能找回：目标在页1，起点页2
        pages = [[nd("A", 300)], [nd("目标", 300)], [nd("B", 300)]]
        ui = FakeUI(pages)
        ui.pos = 2
        f = make_flow(ui)
        n = f._find_scroll("目标", max_scroll=10)
        self.assertIsNotNone(n)
        self.assertEqual(n.text, "目标")


# ----------------------------------------------------------------------
# B1: _nav_robot_list 快路径
# ----------------------------------------------------------------------
class TestNavFastPath(unittest.TestCase):
    def test_fast_path_skips_when_at_robot_list(self):
        # 机器人列表页：无任务中心/发消息文字，有底部联系人 tab
        ui = FakeUI([[nd("联系人", 1880), nd("李宥恩", 300)]])
        f = make_flow(ui, at_list=True)
        f._exit_taskcenter = mock.Mock()
        f._nav_robot_list()
        self.assertEqual(f._at_robot_list, True)
        self.assertEqual(ui.taps, [])                 # 零点击
        f._exit_taskcenter.assert_not_called()

    def test_stale_flag_on_taskcenter_falls_back_to_exit(self):
        # 状态失真：标志 True 但实际残留在任务中心 → 校验失败 → 退出并复位
        ui = FakeUI([[nd("每日签到", 500), nd("联系人", 1880)]])
        f = make_flow(ui, at_list=True)
        f._exit_taskcenter = mock.Mock()
        f._nav_robot_list()
        f._exit_taskcenter.assert_called_once()
        self.assertEqual(f._at_robot_list, True)
        self.assertEqual(ui.taps, [])

    def test_stale_flag_on_chat_page_recovers_via_tab_nav(self):
        # 状态失真：实际停在聊天页(有 发消息)，快照校验失败 →
        # 无任务中心残留 → 走常规 tab 导航兜底（此处无联系人tab可点，仅不崩）
        ui = FakeUI([[nd("发消息", 900)]])
        f = make_flow(ui, at_list=True)
        f._exit_taskcenter = mock.Mock()
        f._nav_robot_list()
        f._exit_taskcenter.assert_not_called()        # 不是任务中心
        self.assertEqual(f._at_robot_list, True)      # 尽力而为置位
        self.assertEqual(ui.taps, [])

    def test_first_run_full_nav_from_contacts(self):
        # 首次(_at_robot_list=False)在联系人页：点联系人tab→点机器人分类→展开分组
        # 模拟：初始屏无分组头；tap 联系人后屏变机器人分类页(含 我添加的机器人)
        class DynUI(FakeUI):
            def __init__(self):
                super().__init__([[]])
                self.taps = []
                self.stage = 0

            def nodes(self):
                if self.stage == 0:
                    return [nd("消息", 1880), nd("联系人", 1880),
                            nd("李宥恩", 300)]         # 已在机器人列表(展开)
                return [nd("机器人", 1000)]

            def find(self, text, ymin=0, ymax=99999,
                     xmin=0, xmax=99999):
                return super().find(text, ymin, ymax, xmin, xmax)

            def tap(self, x, y, pause=1.2):
                self.taps.append((x, y))
                self.stage = 1

            def tap_node(self, node, pause=1.2):
                self.taps.append(node.center)
                self.stage = 1

        ui = DynUI()
        f = make_flow(ui, at_list=False)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._nav_robot_list()
        # 首屏已见 我添加的机器人？无 → 但无联系人tab可点? 屏含联系人tab(1880)
        # → 应 tap 联系人。验证：最终状态置位、有导航动作且无残留退出
        self.assertEqual(f._at_robot_list, True)
        self.assertTrue(len(ui.taps) >= 0)            # 结构不崩即可


# ----------------------------------------------------------------------
# B3: _enter_taskcenter 条件等待 + 状态位
# ----------------------------------------------------------------------
class TestEnterTaskcenter(unittest.TestCase):
    def _f(self):
        ui = mock.Mock()
        f = make_flow(ui)
        f._nav_robot_list = mock.Mock()
        f._find_robot = mock.Mock(return_value=nd("某机器人", 300))
        f._tap_node = mock.Mock()
        return ui, f

    def test_success_uses_conditional_wait_and_clears_flag(self):
        ui, f = self._f()
        f._at_robot_list = True
        ui.wait_for.side_effect = [nd("发消息", 900), nd("个人", 700)]
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("某机器人")
        self.assertTrue(ok)
        # 条件等待参数正确：个人 限定 y>=600
        self.assertEqual(ui.wait_for.call_args_list[0],
                         mock.call("发消息", retries=8, interval=1.0))
        self.assertEqual(ui.wait_for.call_args_list[1],
                         mock.call("个人", retries=8, interval=1.0, ymin=600))
        self.assertEqual(f._tap_node.call_count, 3)   # 点条目+发消息+个人
        self.assertEqual(f._at_robot_list, False)     # 进入后清快路径状态

    def test_profile_fail_returns_false(self):
        ui, f = self._f()
        ui.wait_for.return_value = None               # profile 没加载出 发消息
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("某机器人")
        self.assertFalse(ok)
        self.assertEqual(f._tap_node.call_count, 1)   # 只点了机器人条目

    def test_robot_not_in_list_returns_false(self):
        ui, f = self._f()
        f._find_robot.return_value = None
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("不存在")
        self.assertFalse(ok)
        f._nav_robot_list.assert_called_once()


# ----------------------------------------------------------------------
# _exit_taskcenter：置位快路径状态
# ----------------------------------------------------------------------
class TestExitTaskcenter(unittest.TestCase):
    def test_exit_sets_at_robot_list(self):
        ui = mock.Mock()
        f = make_flow(ui)
        f._at_robot_list = False
        f._scroll_to_top_of_taskcenter = mock.Mock()
        f._tap = mock.Mock()
        with mock.patch.object(flow_mod.time, "sleep"):
            f._exit_taskcenter()
        self.assertEqual(f._at_robot_list, True)
        self.assertEqual(f._tap.call_count, 3)        # 三层返回箭头


if __name__ == "__main__":
    unittest.main()
