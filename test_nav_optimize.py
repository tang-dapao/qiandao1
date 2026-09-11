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
- _collect_robot_names：滚动收集不被"最多 3 屏"死代码截断，连续 3 屏零新增
  才停（2026-09-10 修复）

运行：py -3.13 -m unittest test_nav_optimize -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import Flow


def nd(text: str, y: int = 500, x1: int = 100, selected: bool = False) -> Node:
    """构造一个 200x80 的文本节点（默认 y 中区；可指定 selected）。"""
    return Node(text, x1, y, x1 + 200, y + 80, selected=selected)


def _bottom_tabs(active: str = "联系人"):
    """底部 3 主 tab（频道已停用）：消息/联系人/动态，active 的 selected=True。
    x 取实机 dump 量测值（1080 宽：消息 151、联系人 496、动态 871）。"""
    return [
        nd("消息", 1880, x1=151, selected=(active == "消息")),
        nd("联系人", 1880, x1=496, selected=(active == "联系人")),
        nd("动态", 1880, x1=871, selected=(active == "动态")),
    ]


def _robot_list_page(*rows) -> list:
    """机器人列表视图节点组（F1 强判据通过的标准页）：
    QQ 主壳(联系人 tab 激活) + 中部 机器人 分类 selected + 机器人行。
    rows: (名称, y) 或 ((名称, y, x1), ...) —— 名称默认 x1=183。"""
    nodes = _bottom_tabs("联系人")
    nodes.append(nd("机器人", 500, x1=677, selected=True))
    for item in rows:
        name, y = item[0], item[1]
        x1 = item[2] if len(item) > 2 else 183
        nodes.append(nd(name, y, x1=x1))
    return nodes


def _list_page():
    """旧名兼容：机器人列表页（默认含一个黎小姐行）。"""
    return _robot_list_page(("黎小姐", 300))


class FakeUI:
    """可翻页 UI：nodes() 返回当前页；swipe 切换页面并计数（到底自动停）。"""

    def __init__(self, pages):
        self.pages = pages          # list[list[Node]]
        self.pos = 0
        self.swipes = 0
        self.taps = []
        self.dump_fail_streak = 0   # flow._dump_stuck 读取（0 = 页面正常）

    def nodes(self, timeout=None):
        return list(self.pages[self.pos])

    def refresh(self):
        pass    # FakeUI 无 TTL 缓存，no-op（真实 AdbUI 会失效缓存强制重 dump）

    def _move(self, d):
        self.swipes += 1
        npos = self.pos + d
        if 0 <= npos < len(self.pages):
            self.pos = npos

    def swipe_up(self, pause=1.0):
        self._move(1)

    def swipe_down(self, pause=1.0):
        self._move(-1)

    def find(self, text, ymin=0, ymax=99999, xmin=0, xmax=99999, timeout=None):
        for n in self.nodes():
            if (n.text == text and ymin <= n.y1 <= ymax
                    and xmin <= n.x1 <= xmax):
                return n
        return None

    def back(self, pause=1.2):
        self.taps.append(("BACK",))
        self._move(-1)

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
    # 注：_tap/_tap_node 由各测试按需 stub（TestExitTaskcenter 需真实 _tap；
    # TestEnterTaskcenter/F9 钳位测试需要 mock 以断言 tap 坐标）
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
# B1+F1: _nav_robot_list 快路径 / 强判据 / QQ 主界面完整导航
# ----------------------------------------------------------------------
class TestNavFastPath(unittest.TestCase):
    def test_fast_path_skips_when_at_robot_list(self):
        # 机器人列表：联系人tab激活 + 机器人分类selected + 行 → 快路径零点击
        ui = FakeUI([_robot_list_page(("李宥恩", 300))])
        f = make_flow(ui, at_list=True)
        f._exit_taskcenter = mock.Mock()
        f._nav_robot_list()
        self.assertEqual(f._at_robot_list, True)
        self.assertEqual(ui.taps, [])                 # 零点击
        f._exit_taskcenter.assert_not_called()

    def test_qq_message_home_not_mistaken_as_robot_list(self):
        # F1 关键回归：QQ 主界面【消息】首页（底部有联系人tab 但机器人分类
        # 未选中）绝不能判成机器人列表 —— 旧判据假阳性根因
        ui = FakeUI([_bottom_tabs("消息") + [nd("某会话", 400)]])
        f = make_flow(ui, at_list=True)               # 状态位也真
        self.assertFalse(f._looks_like_robot_list())

    def test_contacts_tab_but_no_robot_cat_not_list(self):
        # 联系人 tab 激活但分类行没选 机器人（如停在 好友）→ 不是机器人列表
        ui = FakeUI([_bottom_tabs("联系人") + [nd("好友", 500, x1=197, selected=True)]])
        f = make_flow(ui, at_list=True)
        self.assertFalse(f._looks_like_robot_list())

    def test_robot_cat_recognized_regardless_of_y_position(self):
        # 实机回归：聊天返回后分类行被吸附到顶部 y≈201（之前误用 y 400-650
        # 窗口导致强判据恒 False，导航循环 2 分钟无效重试）。改用 x 范围后
        # 必须不论 y 在哪儿都能识别
        ui = FakeUI([_bottom_tabs("联系人")
                     + [nd("机器人", 210, x1=677, selected=True)]
                     + [nd("黎小姐", 400)]])
        f = make_flow(ui, at_list=True)
        self.assertTrue(f._looks_like_robot_list())

    def test_stale_flag_on_taskcenter_falls_back_to_exit(self):
        # 状态失真：标志 True 但实际残留在任务中心 → 强判据不过 → 退出
        ui = FakeUI([[nd("每日签到", 500)]])
        f = make_flow(ui, at_list=True)
        f._exit_taskcenter = mock.Mock(return_value=True)
        f._safe_back_to_robot_list = mock.Mock(return_value=True)
        f._nav_robot_list()
        f._exit_taskcenter.assert_called_once()
        # 退出后页面未变（假 UI 单页）→ 强判据仍不过 → 不再盲目置 True
        self.assertEqual(f._at_robot_list, False)

    def test_stale_flag_on_profile_page_recovers_via_back(self):
        # 状态失真：实际停在 profile/聊天页(有 发消息)，非 QQ 主壳 →
        # 物理返回逐层退；本页(索引1)退回即机器人列表(索引0) → 成功
        pages = [_robot_list_page(("黎小姐", 300)), [nd("发消息", 900)]]
        ui = FakeUI(pages)
        ui.pos = 1
        f = make_flow(ui, at_list=True)
        f._exit_taskcenter = mock.Mock()
        f._nav_robot_list()
        f._exit_taskcenter.assert_not_called()        # 不是任务中心
        self.assertEqual(f._at_robot_list, True)
        self.assertIn(("BACK",), ui.taps)             # 确实物理返回过

    def test_full_nav_from_qq_message_home(self):
        # 起点：QQ 主界面【消息】首页(at_list=False) → 完整导航：
        # 点底部 联系人tab -> 点中部 机器人 分类 → 落在机器人列表
        class ContactsNavUI(FakeUI):
            def __init__(self):
                super().__init__([[]])
                self.stage = "home_msg"

            def nodes(self, timeout=None):
                if self.stage == "home_msg":
                    return _bottom_tabs("消息") + [nd("某会话", 400)]
                if self.stage == "contacts_no_cat":
                    return _bottom_tabs("联系人") + [nd("机器人", 500, x1=677)]
                return _robot_list_page(("黎小姐", 400))

            def tap_node(self, node, pause=1.2):
                self.taps.append(node.center)
                if self.stage == "home_msg":
                    self.stage = "contacts_no_cat"    # 点了底部 联系人
                elif self.stage == "contacts_no_cat":
                    self.stage = "robot_list"         # 点了 机器人 分类

        ui = ContactsNavUI()
        f = make_flow(ui, at_list=False)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._nav_robot_list()
        self.assertEqual(f._at_robot_list, True)
        self.assertEqual(ui.stage, "robot_list")
        # 依次点过：底部联系人tab → 中部机器人分类
        self.assertEqual(len(ui.taps), 2)


# ----------------------------------------------------------------------
# B3: _enter_taskcenter 条件等待 + 状态位
# ----------------------------------------------------------------------
class TestEnterTaskcenter(unittest.TestCase):
    def _f(self):
        ui = mock.Mock()
        ui.nodes.return_value = []                    # _screen_texts 读屏用
        f = make_flow(ui)
        f._nav_robot_list = mock.Mock()
        f._find_robot = mock.Mock(return_value=nd("某机器人", 300))
        f._tap_node = mock.Mock()
        f._tap = mock.Mock()                          # F9：钳位测试需断言 tap 坐标
        f._diag_shot = mock.Mock()                    # F5：跳过真实截图
        # F8：点击前新鲜校验默认放行（行仍存在、坐标不变）
        f._reconfirm_click_target = mock.Mock(
            side_effect=lambda name, r: r)
        return ui, f

    def test_success_uses_conditional_wait_and_clears_flag(self):
        ui, f = self._f()
        f._at_robot_list = True
        ui.wait_for.side_effect = [nd("发消息", 900), nd("个人", 700)]
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("某机器人")
        self.assertTrue(ok)
        # 条件等待参数正确：个人 限定 y>=600；发消息 12s（F2 放宽）
        self.assertEqual(ui.wait_for.call_args_list[0],
                         mock.call("发消息", retries=12, interval=1.0))
        self.assertEqual(ui.wait_for.call_args_list[1],
                         mock.call("个人", retries=8, interval=1.0, ymin=600))
        # F9：机器人行 tap 改走 _tap(cx, cy)（带 y 钳位），不走 _tap_node
        self.assertEqual(f._tap_node.call_count, 2)   # profile→发消息 + 聊天页→个人
        self.assertEqual(f._at_robot_list, False)     # 进入后清快路径状态
        f._reconfirm_click_target.assert_called_once()
        # 机器人行 tap 应使用 row center（无贴底钳位）
        # nd("某机器人", 300) → x1=100,x2=300,y1=300,y2=380 → center=(200, 340)
        self.assertEqual(f._tap.call_args_list[0][0][:2], (200, 340))

    def test_profile_fail_returns_false(self):
        ui, f = self._f()
        ui.wait_for.return_value = None               # profile 没加载出 发消息
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("某机器人")
        self.assertFalse(ok)
        self.assertEqual(f._tap_node.call_count, 0)   # 没等到发消息，不点后续
        self.assertEqual(f._tap.call_count, 1)       # 只点了机器人条目
        f._diag_shot.assert_called_once_with("enter_profile")

    def test_robot_not_in_list_returns_false(self):
        ui, f = self._f()
        f._find_robot.return_value = None
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("不存在")
        self.assertFalse(ok)
        f._nav_robot_list.assert_called_once()
        f._diag_shot.assert_called_once_with("enter_norobot")

    def test_click_target_vanished_aborts_without_tap(self):
        # F8：点击前重 dump 发现目标行消失（页面已漂移/被切走）→ 不盲点，
        # 返回 False 交上层 safe_back 复位重试 —— 防点到消息 tab/会话行。
        # （真实方法的截图/日志行为见 TestReconfirmClickTarget）
        ui, f = self._f()
        f._reconfirm_click_target = mock.Mock(return_value=None)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("某机器人")
        self.assertFalse(ok)
        f._reconfirm_click_target.assert_called_once()
        f._tap_node.assert_not_called()               # 不点旧坐标
        f._tap.assert_not_called()                    # F9：也不调 _tap

    # A（2026-09-10）：dump 窗口内未见任务中心特征 → OCR 一次区分
    # 「心动卡会员 H5 错页」（返回 False 交上层复位重试）与「慢加载真任务
    # 中心」（维持原"版式差异，继续流程"放行，行为不变）。
    def _enter_timeout(self):
        """让 _find 恒 None → found_tc=False → 走到 OCR 守卫分支。"""
        ui, f = self._f()
        f._find = mock.Mock(return_value=None)        # dump 窗口内读不到特征
        f._ocr_find = mock.Mock()                      # 守卫用 OCR 单独 stub
        ui.wait_for.side_effect = [nd("发消息", 900), nd("个人", 700)]
        return ui, f

    def test_wrongpage_ocr_hit_returns_false(self):
        # A：OCR 命中心动卡会员页标记 → 判定进入失败（不再"版式差异放行"
        # 后在错页把看广告入口当签到行点 → 弹游戏广告/反馈被挡）
        ui, f = self._enter_timeout()
        f._ocr_find.return_value = (540, 300)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("某机器人")
        self.assertFalse(ok)
        f._diag_shot.assert_called_once_with("enter_wrongpage")
        self.assertFalse(f._at_robot_list)

    def test_wrongpage_ocr_miss_keeps_lenient_continue(self):
        # A：OCR 未命中错页标记（真任务中心 H5 加载慢）→ 维持原"版式差异，
        # 继续流程"放行，返回 True —— 看广告路径行为不变
        ui, f = self._enter_timeout()
        f._ocr_find.return_value = None
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("某机器人")
        self.assertTrue(ok)
        f._diag_shot.assert_not_called()
        self.assertEqual(f._at_robot_list, False)     # 进入后清快路径状态
        # 守卫 OCR 只检索错页标记词
        args = f._ocr_find.call_args.args
        self.assertIn("心动卡", args)


# ----------------------------------------------------------------------
# F9: _enter_taskcenter tap 机器人行 y 上钳（TAP_Y_MAX=1740 防 nav 边缘回弹）
# 实证：藤非 @(183,1771) 3/3 弹回消息 tab（实测 09-09 20:34 f9），
# 上钳到 1740 (TAB_Y-100) 留 100px nav 缓冲；游迦 1684 等低于阈值的行不变。
# ----------------------------------------------------------------------
class TestEnterTaskcenterYClamp(unittest.TestCase):
    def _f(self, row_y):
        ui = mock.Mock()
        ui.nodes.return_value = []
        f = make_flow(ui)
        f._nav_robot_list = mock.Mock()
        # 构造指定 y 的 row（默认 x1=100 仅做 Mock Node 用，reconfirm 被 stub）
        f._find_robot = mock.Mock(return_value=nd("藤非", row_y))
        f._tap_node = mock.Mock()
        f._tap = mock.Mock()                          # F9：钳位测试需断言 tap 坐标
        f._diag_shot = mock.Mock()
        f._reconfirm_click_target = mock.Mock(
            side_effect=lambda name, r: r)             # 直通返回
        # profile→个人 链直接返回 None（钳位测试只看第一 tap 坐标）
        ui.wait_for.return_value = None
        return f

    def test_bottom_edge_row_y1771_clamps_to_1740(self):
        # 藤非实机 y=1771 → 应上钳到 TAP_Y_MAX=1740（防 nav 回弹）
        f = self._f(1771)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._enter_taskcenter("藤非")
        # 第一个 _tap 调用即机器人行；坐标应为 (cx, 1740)
        self.assertEqual(f._tap.call_args_list[0][0][1],
                         flow_mod.TAP_Y_MAX)

    def test_safe_row_y1684_unchanged(self):
        # 游迦实机 y=1684（远低于阈值）→ 应原值 tap，不触发钳位
        f = self._f(1684)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._enter_taskcenter("游迦")
        # nd("游迦", 1684) → y1=1684,y2=1764 → center=(?, 1724)
        # 1724 < 1740，不钳位，tap y 保持 1724
        self.assertEqual(f._tap.call_args_list[0][0][1], 1724)

    def test_top_row_y799_unchanged(self):
        # 代柯实机 y=799 → 原值 tap
        f = self._f(799)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._enter_taskcenter("代柯")
        # nd("代柯", 799) → y1=799,y2=879 → center=(?, 839)
        self.assertEqual(f._tap.call_args_list[0][0][1], 839)

    def test_clamp_exact_boundary_y1741(self):
        # 边界：y=1740 (=TAP_Y_MAX) → 不钳位；y=1741 → 钳到 1740
        f1 = self._f(1690)                             # center=1730, 不钳
        with mock.patch.object(flow_mod.time, "sleep"):
            f1._enter_taskcenter("R")
        self.assertEqual(f1._tap.call_args_list[0][0][1], 1730)

        f2 = self._f(1700)                             # center=1740, 不钳
        with mock.patch.object(flow_mod.time, "sleep"):
            f2._enter_taskcenter("R")
        self.assertEqual(f2._tap.call_args_list[0][0][1], 1740)

        f3 = self._f(1701)                             # center=1741, 钳到 1740
        with mock.patch.object(flow_mod.time, "sleep"):
            f3._enter_taskcenter("R")
        self.assertEqual(f3._tap.call_args_list[0][0][1], 1740)


# ----------------------------------------------------------------------
# F8: _reconfirm_click_target 点击前新鲜坐标校验（真实逻辑直测）
# ----------------------------------------------------------------------
class TestReconfirmClickTarget(unittest.TestCase):
    # nd() 默认 x1=100 不符合机器人行特征(x1 180-195)，一律显式 x1=183
    def test_row_present_returns_fresh_node(self):
        ui = FakeUI([[nd("某机器人", 300, x1=183)]])
        f = make_flow(ui)
        f._diag_shot = mock.Mock()
        r = nd("某机器人", 300, x1=183)
        out = f._reconfirm_click_target("某机器人", r)
        self.assertIsNotNone(out)
        self.assertEqual(out.y1, 300)
        f._diag_shot.assert_not_called()

    def test_row_vanished_returns_none_with_diag(self):
        # 目标行在当前 dump 中消失（页面已切走/滚动位置变化）→ None + 截图
        ui = FakeUI([[nd("别的人", 300, x1=183)]])
        f = make_flow(ui)
        f._diag_shot = mock.Mock()
        r = nd("某机器人", 300, x1=183)
        self.assertIsNone(f._reconfirm_click_target("某机器人", r))
        f._diag_shot.assert_called_once_with("enter_vanished")

    def test_row_moved_uses_new_coordinates(self):
        # 滚动动画中间帧：重 dump 后行坐标已变 → 返回最新坐标防点错
        ui = FakeUI([[nd("某机器人", 500, x1=183)]])   # 重 dump 时 y 300 -> 500
        f = make_flow(ui)
        f._diag_shot = mock.Mock()
        out = f._reconfirm_click_target(
            "某机器人", nd("某机器人", 300, x1=183))
        self.assertEqual(out.y1, 500)                  # 用新坐标
        f._diag_shot.assert_not_called()


# ----------------------------------------------------------------------
# _exit_taskcenter 安全版（P0）：前提校验 + 分层校验 + safe_back 兜底
# ----------------------------------------------------------------------
class _TapAdvanceUI(FakeUI):
    """tap/back 均推进一页：简化模拟「逐层返回」的真实页面切换。"""

    def tap(self, x, y, pause=1.2):
        self.taps.append((x, y))
        self._move(1)

    def back(self, pause=1.2):
        self.taps.append(("BACK",))
        self._move(1)


def _tc_page():
    return [nd("每日签到", 500), nd("任务中心", 200)]       # 任务中心特征


def _chat_page():
    return [nd("发消息", 900)]                              # profile/聊天页特征


class TestExitTaskcenter(unittest.TestCase):
    def _f(self, ui):
        f = make_flow(ui)
        f._scroll_to_top_of_taskcenter = mock.Mock()
        return f

    def test_full_three_layers_from_taskcenter(self):
        # 任务中心 -> 聊天 -> profile -> 列表，三层 BACK 后成功
        pages = [_tc_page(), _chat_page(), _chat_page(), _list_page()]
        ui = _TapAdvanceUI(pages)
        f = self._f(ui)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._exit_taskcenter()
        self.assertTrue(ok)
        # 2026-09-09 23:1x：三层全部改用系统 BACK 键（避开 banner AI 好友
        # 反馈入口），不再用固定坐标 tap（90,138)/(59,139)/(73,133) ——
        # 那些坐标全在 banner 区，QQ 升级后任何 tap 都触发 Badcase 问卷。
        self.assertEqual(ui.taps, [("BACK",), ("BACK",), ("BACK",)])
        self.assertEqual(f._at_robot_list, True)

    def test_early_stop_when_back_to_list_after_first_layer(self):
        # 第1层 BACK 后已到列表 → 提前结束，不再 BACK 后两层
        pages = [_tc_page(), _list_page()]
        ui = _TapAdvanceUI(pages)
        f = self._f(ui)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._exit_taskcenter()
        self.assertTrue(ok)
        self.assertEqual(ui.taps, [("BACK",)])
        self.assertEqual(f._at_robot_list, True)

    def test_precondition_fail_on_foreign_page_uses_safe_back(self):
        # 前提校验失败（不在任务中心，如误入浏览器）→ 直接安全返回兜底
        ui = FakeUI([[nd("常见问题", 300)]])              # QQBrowser 帮助页
        f = self._f(ui)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._exit_taskcenter()
        self.assertFalse(ok)                              # 单页无处可退
        self.assertEqual(len(ui.taps), 6)                 # back 上限 6 次
        self.assertEqual(f._at_robot_list, False)

    def test_three_layers_fail_then_safe_back_recovers(self):
        # 三层固定退出没回列表 → safe_back 物理返回兜底找回列表
        pages = [_tc_page(), _chat_page(), _chat_page(),
                 _chat_page(), _list_page()]              # 第3层仍 profile 页
        ui = _TapAdvanceUI(pages)
        f = self._f(ui)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._exit_taskcenter()
        self.assertTrue(ok)
        self.assertIn(("BACK",), ui.taps)                 # 兜底时发生了物理返回
        self.assertEqual(f._at_robot_list, True)

    def test_nav_stale_taskcenter_exit_verified(self):
        # B1 残留分支：exit 失败会转 safe_back（不裸 return 造成错页）。
        # 此处页面恒停在任务中心（exit/safe_back 都回不去）→ 不强置 True，
        # _at_robot_list 如实为 False，交由上层(_enter/run_robot 复位重试)处理
        ui = FakeUI([_tc_page()])
        f = make_flow(ui, at_list=True)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._nav_robot_list()
        self.assertEqual(f._at_robot_list, False)         # 不再“尽力置位”掩盖错页
        self.assertIn(("BACK",), ui.taps)                 # 确实走了物理返回兜底


# ----------------------------------------------------------------------
# F3: _find_robot 行几何约束 —— 贴底行(被 tab bar 压住)不得直接 tap
# ----------------------------------------------------------------------
class TestRobotRowGuard(unittest.TestCase):
    def test_mid_screen_row_found_without_scroll(self):
        ui = FakeUI([_robot_list_page(("黎小姐", 1000))])
        f = make_flow(ui)
        n = f._find_robot("黎小姐")
        self.assertIsNotNone(n)
        self.assertEqual(ui.swipes, 0)

    def test_bottom_hidden_row_scrolled_into_view_first(self):
        # 实机场景：下一台机器人行贴底(中心 y 落在 tab bar 上) —— 若直接
        # tap 中心会点到【消息】tab。必须滚动后再命中安全位置的行。
        pages = [[nd("藤非", 1896, x1=183)],          # 中心 y=1936，贴底被压
                 [nd("藤非", 1500, x1=183)]]          # 滚一下后到安全区
        ui = FakeUI(pages)
        f = make_flow(ui)
        n = f._find_robot("藤非")
        self.assertIsNotNone(n)
        cy = (n.y1 + n.y2) // 2
        self.assertLess(cy, flow_mod.ROW_SAFE_Y)       # 命中位置必须可安全点
        self.assertEqual(ui.swipes, 1)

    def test_non_row_text_match_ignored(self):
        # 文本命中但几何不像行(如顶部标题/右侧元素) → 忽略，找真正的行
        ui = FakeUI([[nd("黎小姐", 500, x1=700),       # x 不在行带
                      nd("黎小姐", 300, x1=183)]])     # 真正的行
        f = make_flow(ui)
        n = f._find_robot("黎小姐")
        self.assertIsNotNone(n)
        self.assertEqual(n.x1, 183)


# ----------------------------------------------------------------------
# _collect_robot_names 滚动收集：修复「最多只滚 3 屏」死代码（2026-09-10）
# ----------------------------------------------------------------------
def _robot_row(name: str, row: int) -> Node:
    """符合昵称识别规则的机器人行：x1=183、宽 200、y1 落在 350~1850。"""
    return nd(name, y=400 + row * 110, x1=183)


def _rolling_pages(n_pages: int, window: int = 3) -> list:
    """滚动窗口：第 i 屏显示 [R_i, R_{i+1}, R_{i+2}] —— 每滚一屏恰好新增
    1 个新昵称，用于模拟「比 3 屏更长」的机器人列表。"""
    names = [f"机器人{i}" for i in range(n_pages + window - 1)]
    return [[_robot_row(names[i + k], k) for k in range(window)]
            for i in range(n_pages)]


class TestCollectRobotNamesScroll(unittest.TestCase):
    """2026-09-10 修复回归：滚动停止判据曾是死代码。

    before 取值在 swipe 之前、比较在 swipe 之后，而中间没有任何代码修改
    seen → len(seen) == before 恒为真 → no_new 每轮必 +1 → 第 3 屏无条件
    break（else 分支永不可达）→ 列表超过 3 屏的部分被静默漏收。
    实锤：run.log 01:10「最终处理 9 个机器人」缺白名单内的**游迦**。
    """

    def setUp(self):
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

    def _flow(self, pages):
        f = make_flow(FakeUI(pages))
        f._nav_robot_list = mock.Mock()   # 导航不在本用例范围内
        return f

    def test_keeps_scrolling_beyond_third_screen(self):
        # 每屏都有 1 个新名字 → 必须一直滚到最后一屏（旧实现第 3 屏就停）
        f = self._flow(_rolling_pages(12))
        names = f._collect_robot_names(max_scroll=12)
        self.assertEqual(len(names), 14)   # 12 屏窗口共 12+3-1 = 14 个
        self.assertEqual(f.ui.pos, 11)     # 确实滚到了最后一屏

    def test_stops_after_three_screens_without_new(self):
        # 到底后重复读到同一屏 → 连续 3 屏零新增才停
        same = [_robot_row("机器人A", 0), _robot_row("机器人B", 1),
                _robot_row("机器人C", 2)]
        f = self._flow([list(same) for _ in range(10)])
        names = f._collect_robot_names(max_scroll=9)
        self.assertEqual(names, ["机器人A", "机器人B", "机器人C"])
        self.assertEqual(f.ui.pos, 3)      # 第 3 屏零新增后停止

    def test_no_new_counter_resets_on_new_name(self):
        # 中间出现"零新增"屏后再有新名字 → no_new 必须归零，否则会提前误停
        pages = _rolling_pages(4)
        pages.insert(2, pages[1])          # 第 3 屏与第 2 屏内容相同（零新增）
        f = self._flow(pages)
        names = f._collect_robot_names(max_scroll=8)
        self.assertGreaterEqual(len(names), 5)


if __name__ == "__main__":
    unittest.main()
