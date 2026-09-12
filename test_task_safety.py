"""P0/P1（任务操作安全化）回归测试（mock 驱动，不依赖真机/adb）。

覆盖：
- P1 _signin / _feedback：点击后的「浮层/问卷页出现」断言；
  失败走 _reset_after_fail（物理返回复位）而非盲点固定坐标
- P1 校准：行内 X/Y 计数（1/1 / X/10）→ 今日已完成 → 跳过不点
- P0 _safe_back_to_robot_list：物理返回逐级回列表 / 上限失败
- P0 _dump_stuck 阈值；_find_scroll / _scroll_to_top 页面卡死时中止
  （对应冒烟 bug：误入 QQBrowser 后 27 次 dump 连续失败空耗 5m53s）

运行：py -3.13 -m unittest test_task_safety -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import Flow

from test_nav_optimize import FakeUI, _bottom_tabs, _robot_list_page, nd  # 复用可翻页假 UI


def make_flow(ui, at_list=False):
    f = Flow.__new__(Flow)
    f._init_cache_state()   # A2/A3：实例级截图/OCR 缓存（防类级共享字典污染）
    f.ui = ui
    f.t = {"click_min": 1.5, "click_max": 3.0, "page_wait": 2.0,
           "taskcenter_wait": 3.5}
    f.wf = {"ad_cooldown": 60, "ad_times_per_robot": 10,
           "ad_wait_min": 0.01, "ad_wait_max": 0.02}    # 等待可忽略
    f._at_robot_list = at_list
    return f


def _row(btn_label: str, label: str = "每日签到", ratio=None):
    """构造 _find_row 返回值：(btn, label_node, ratio)。
    btn 用 nd 占位（_signin/_feedback 不读 btn 坐标）；label_node 用 nd。
    ratio 给 (X, Y) 模拟「已完成/有计数」。"""
    btn = nd("btn", 200, 400)
    lbl = nd(label, 380)
    return btn, lbl, ratio


# ----------------------------------------------------------------------
# P1: 签到断言（点行后必须出现「每日免费领」浮层）
# ----------------------------------------------------------------------
class TestSigninGuard(unittest.TestCase):
    def _f(self):
        ui = mock.Mock()
        ui.nodes.return_value = []                        # 诊断日志读屏
        f = make_flow(ui)
        f._find_row = mock.Mock(return_value=_row("去完成"))
        f._tap_node = mock.Mock()
        f._tap = mock.Mock()
        return ui, f

    def test_skips_when_ratio_says_done(self):
        ui, f = self._f()
        f._find_row.return_value = _row("连签2天", ratio=(1, 1))
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._signin()
        self.assertTrue(ok)
        self.assertEqual(f._tap_node.call_count, 0)       # 1/1 不点
        ui.wait_for.assert_not_called()                   # 不进入浮层流程

    def test_success_flow_uses_assert_and_known_coords(self):
        ui, f = self._f()
        ui.wait_for.return_value = nd("每日免费领", 900)   # 浮层出现
        ui.find.return_value = nd("我知道了", 1100)        # 成功浮层
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._signin()
        self.assertTrue(ok)
        # 浮层出现断言参数
        self.assertEqual(ui.wait_for.call_args,
                         mock.call("每日免费领", retries=5, interval=1.0))
        # 点击序列：浮层签到 -> 我知道了 -> 关闭✕（均 trusted=True：F11 放宽
        # 任务中心页的盲点坐标禁令 —— 这些点位于已确认在屏的签到浮层内）
        self.assertEqual(f._tap.call_args_list,
                         [mock.call(301, 1800, trusted=True),
                          mock.call(540, 1189, trusted=True),
                          mock.call(996, 1143, trusted=True)])

    def test_no_modal_means_already_signed_returns_false_with_reset(self):
        ui, f = self._f()
        ui.wait_for.return_value = None                    # 无浮层 = 异常
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._signin()
        self.assertFalse(ok)
        self.assertEqual(f._tap_node.call_count, 1)        # 只点了行按钮
        ui.back.assert_called_once()                       # 物理返回复位
        f._tap.assert_not_called()                         # 不盲点固定坐标


# ----------------------------------------------------------------------
# P1: 问题反馈断言（点行后必须出现问卷页「返回」）
# ----------------------------------------------------------------------
class TestFeedbackGuard(unittest.TestCase):
    def _f(self):
        ui = mock.Mock()
        ui.nodes.return_value = []
        f = make_flow(ui)
        f._find_row = mock.Mock(return_value=_row("去反馈", label="问题反馈"))
        f._tap_node = mock.Mock()
        return ui, f

    def test_skips_when_ratio_says_done(self):
        ui, f = self._f()
        f._find_row.return_value = _row("去反馈", label="问题反馈", ratio=(1, 1))
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._feedback()
        self.assertTrue(ok)
        ui.wait_for.assert_not_called()

    def test_success_back_and_return_to_taskcenter(self):
        ui, f = self._f()
        ui.wait_for.return_value = nd("返回", 120)          # 问卷页左上返回
        ui.find.side_effect = [nd("每日签到", 300)]          # 返回后见任务中心
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._feedback()
        self.assertTrue(ok)
        # 问卷返回按钮在顶部区域（ymax=300）
        self.assertEqual(ui.wait_for.call_args,
                         mock.call("返回", retries=6, interval=1.0, ymax=300))
        self.assertEqual(f._tap_node.call_count, 2)        # 点行 + 点返回

    def test_no_questionnaire_returns_false_with_reset(self):
        ui, f = self._f()
        ui.wait_for.return_value = None                    # 无问卷
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._feedback()
        self.assertFalse(ok)
        ui.back.assert_called_once()
        self.assertEqual(f._tap_node.call_count, 1)        # 未盲点返回坐标

    def test_back_not_returned_to_taskcenter_is_failure(self):
        ui, f = self._f()
        ui.wait_for.return_value = nd("返回", 120)
        ui.find.return_value = None                        # 返回后不在任务中心
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._feedback()
        self.assertFalse(ok)
        self.assertEqual(f._tap_node.call_count, 2)


# ----------------------------------------------------------------------
# P1 校准: 看广告已达目标次数跳过
# ----------------------------------------------------------------------
class TestWatchAdSkipWhenDone(unittest.TestCase):
    def _f(self, target=10):
        ui = mock.Mock()
        ui.nodes.return_value = []
        f = make_flow(ui)
        f.wf["ad_times_per_robot"] = target
        f._find_row = mock.Mock()
        f._tap_node = mock.Mock()
        return ui, f

    def test_skips_when_ratio_meets_target(self):
        ui, f = self._f()
        f._find_row.return_value = _row("获取随机", label="获取随机", ratio=(10, 10))
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._watch_ad_once()
        self.assertTrue(ok)                                # 视为"完成"
        self.assertEqual(f._tap_node.call_count, 0)       # 不点广告按钮
        # 2026-09-10：行标题+按钮词同轮查找（满额后按钮文案变「已完成」）
        f._find_row.assert_called_once_with("看广告", alt_labels=("获取随机",))

    def test_skips_with_partial_progress_below_target(self):
        ui, f = self._f(target=5)
        f._find_row.return_value = _row("获取随机", label="获取随机", ratio=(5, 10))
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._watch_ad_once()
        self.assertTrue(ok)                                # 5 >= target 5 跳过

    def test_plays_ad_when_below_target(self):
        ui, f = self._f()
        f._find_row.return_value = _row("获取随机", label="获取随机", ratio=(2, 10))
        f._close_ad = mock.Mock(return_value=True)
        # F10+：观看等待后自检已在任务中心 → 失败；本测试模拟广告页中，
        # 自检返回 False（不在任务中心），让流程继续到 _close_ad。
        f._back_at_taskcenter = mock.Mock(return_value=False)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._watch_ad_once()
        self.assertTrue(ok)
        self.assertEqual(f._tap_node.call_count, 1)        # 点了获取随机


# ----------------------------------------------------------------------
# P1 校准: _row_completion X/Y 计数读取
# ----------------------------------------------------------------------
class TestRowCompletion(unittest.TestCase):
    def test_reads_ratio_in_same_row(self):
        ui = FakeUI([[nd("每日签到", 300), nd("1/1", 300),
                      nd("连签2天", 300), nd("获取随机", 700)]])
        f = make_flow(ui)
        label = nd("每日签到", 300)
        self.assertEqual(f._row_completion(label), (1, 1))

    def test_returns_none_when_no_ratio(self):
        ui = FakeUI([[nd("每日签到", 300), nd("去完成", 300)]])
        f = make_flow(ui)
        self.assertIsNone(f._row_completion(nd("每日签到", 300)))

    def test_ignores_ratio_in_other_rows(self):
        # 1/1 在另一行（y 差 > 30）→ 不算当前 label 的计数
        ui = FakeUI([[nd("每日签到", 300), nd("获取随机", 700),
                      nd("2/10", 700)]])
        f = make_flow(ui)
        self.assertIsNone(f._row_completion(nd("每日签到", 300)))

    def test_split_ratio_nodes_joined(self):
        # 【2026-09-10 满额不直退根因】X/Y 计数被拆成多个节点
        # （代柯 10/10 实测 dump：'10' '/' '10' 三个独立节点）→
        # 第 2 遍按 x 序拼接行内文本后 search 提取。
        ui = FakeUI([[nd("看广告", 300, x1=234), nd("10", 300, x1=390),
                      nd("/", 300, x1=435), nd("10", 300, x1=450),
                      nd("已完成", 300, x1=822)]])
        f = make_flow(ui)
        self.assertEqual(f._row_completion(nd("看广告", 300)), (10, 10))

    def test_split_ratio_partial_progress(self):
        # 拆分的部分进度（8/10）同样可解析
        ui = FakeUI([[nd("看广告", 300, x1=234), nd("8", 300, x1=390),
                      nd("/", 300, x1=415), nd("10", 300, x1=440),
                      nd("获取随机", 300, x1=822)]])
        f = make_flow(ui)
        self.assertEqual(f._row_completion(nd("看广告", 300)), (8, 10))


class TestFindRowAltLabels(unittest.TestCase):
    """2026-09-10：_find_row 的 alt_labels 同轮多词查找（满额按钮文案变化）。"""

    def test_matches_alt_label_in_same_pass(self):
        # 屏上无「获取随机」（满额行），有「看广告」标题 → 同轮命中，不再二轮空滚
        ui = FakeUI([[nd("看广告", 300), nd("已完成", 300, x1=822)]])
        f = make_flow(ui)
        row = f._find_row("看广告", alt_labels=("获取随机",))
        self.assertIsNotNone(row)
        self.assertEqual(row[1].text, "看广告")

    def test_matches_primary_label(self):
        ui = FakeUI([[nd("获取随机", 300)]])
        f = make_flow(ui)
        row = f._find_row("看广告", alt_labels=("获取随机",))
        self.assertIsNotNone(row)
        self.assertEqual(row[1].text, "获取随机")

    def test_no_labels_returns_none(self):
        ui = FakeUI([[nd("每日签到", 300)]])
        f = make_flow(ui)
        self.assertIsNone(f._find_row("看广告", alt_labels=("获取随机",)))


# ----------------------------------------------------------------------
# P0+F1: _safe_back_to_robot_list（强判据停 + QQ 主壳转完整导航）
# ----------------------------------------------------------------------
class TestSafeBack(unittest.TestCase):
    def test_recovers_via_back_to_robot_list(self):
        # 在浏览器页(pos1)，back 一步回机器人列表(pos0)
        pages = [_robot_list_page(("黎小姐", 300)), [nd("常见问题", 300)]]
        ui = FakeUI(pages)
        ui.pos = 1
        f = make_flow(ui, at_list=False)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._safe_back_to_robot_list()
        self.assertTrue(ok)
        self.assertEqual(f._at_robot_list, True)
        self.assertEqual(len(ui.taps), 1)                  # 只 back 了 1 次

    def test_qq_home_message_tab_triggers_full_nav_not_false_success(self):
        # F1：停在 QQ 主界面【消息】首页(有底部联系人tab) → 旧实现会误判
        # “已回列表”直接返回(随后在错误页滚动=卡死根因)；现在必须转完整导航
        ui = FakeUI([_bottom_tabs("消息") + [nd("某会话", 400)]])
        f = make_flow(ui, at_list=False)

        def _nav_ok():
            f._at_robot_list = True
            ui.pages[0] = _robot_list_page(("黎小姐", 300))  # nav 到达真列表

        f._nav_robot_list = mock.Mock(side_effect=_nav_ok)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._safe_back_to_robot_list()
        self.assertTrue(ok)
        f._nav_robot_list.assert_called_once()

    def test_gives_up_after_max_back(self):
        ui = FakeUI([[nd("常见问题", 300)]])               # 无处可退
        f = make_flow(ui)
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._safe_back_to_robot_list(max_back=4)
        self.assertFalse(ok)
        self.assertEqual(len(ui.taps), 4)
        self.assertEqual(f._at_robot_list, False)


# ----------------------------------------------------------------------
# F4: run_robot 进入失败 → 复位重试一次；仍失败才跳过
# ----------------------------------------------------------------------
class TestRunRobotRecovery(unittest.TestCase):
    def _f(self):
        f = make_flow(mock.Mock())
        f._signin = mock.Mock()
        f._feedback = mock.Mock()
        f._exit_taskcenter = mock.Mock()
        return f

    def test_entry_failure_safe_back_then_retry_succeeds(self):
        f = self._f()
        f._enter_taskcenter = mock.Mock(side_effect=[False, True])
        f._safe_back_to_robot_list = mock.Mock(return_value=True)
        ok = f.run_robot("R", True, True)
        self.assertTrue(ok)
        self.assertEqual(f._enter_taskcenter.call_count, 2)
        f._safe_back_to_robot_list.assert_called_once()
        f._signin.assert_called_once()

    def test_entry_failure_thrice_skips_robot(self):
        # F8：进入重试 2 -> 3 次（对齐广告轮转 max_fail=3；f8 实测 2 次不够）
        f = self._f()
        f._enter_taskcenter = mock.Mock(return_value=False)
        f._safe_back_to_robot_list = mock.Mock(return_value=True)
        ok = f.run_robot("R", True, True)
        self.assertFalse(ok)
        self.assertEqual(f._enter_taskcenter.call_count, 3)
        self.assertEqual(f._safe_back_to_robot_list.call_count, 3)
        f._signin.assert_not_called()

    # A3（2026-09-10）：进入成功后先清 Badcase 问卷再签到 —— 覆盖签到+反馈
    # 两动作；进入失败路径不清理（未进任务中心无意义）
    def test_badcase_dismiss_before_signin_on_success(self):
        f = self._f()
        f._enter_taskcenter = mock.Mock(return_value=True)
        f._safe_back_to_robot_list = mock.Mock(return_value=True)
        f._dismiss_badcase = mock.Mock(return_value=True)
        order: list = []
        f._dismiss_badcase.side_effect = lambda: order.append("dismiss")
        f._signin.side_effect = lambda: order.append("signin") or True
        f._feedback.side_effect = lambda: order.append("feedback") or True
        ok = f.run_robot("R", True, True)
        self.assertTrue(ok)
        # 清理恰在签到前调用 1 次（进入成功后），顺序 dismiss → signin → feedback
        f._dismiss_badcase.assert_called_once()
        self.assertEqual(order, ["dismiss", "signin", "feedback"])

    def test_badcase_dismiss_skipped_on_entry_failure(self):
        # 进入任务中心失败（重试 3 次后跳过）→ 不清理问卷（不在任务中心）
        f = self._f()
        f._enter_taskcenter = mock.Mock(return_value=False)
        f._safe_back_to_robot_list = mock.Mock(return_value=True)
        f._dismiss_badcase = mock.Mock(return_value=True)
        ok = f.run_robot("R", True, True)
        self.assertFalse(ok)
        f._dismiss_badcase.assert_not_called()


# ----------------------------------------------------------------------
# P0: 页面卡死检测（dump 连续失败中止导航）
# ----------------------------------------------------------------------
class TestDumpStuck(unittest.TestCase):
    def test_threshold(self):
        ui = FakeUI([[nd("A", 300)]])
        f = make_flow(ui)
        self.assertFalse(f._dump_stuck())
        ui.dump_fail_streak = 2
        self.assertFalse(f._dump_stuck())
        ui.dump_fail_streak = 3
        self.assertTrue(f._dump_stuck())

    def test_find_scroll_aborts_when_stuck(self):
        ui = FakeUI([[nd("A", 300)], [nd("B", 300)], [nd("C", 300)]])
        ui.dump_fail_streak = 5                           # 已卡死
        f = make_flow(ui)
        self.assertIsNone(f._find_scroll("ZZZ", max_scroll=10))
        self.assertEqual(ui.swipes, 0)                    # 不再无谓滚动

    def test_scroll_to_top_aborts_when_stuck(self):
        ui = FakeUI([[nd("A", 300)]])
        ui.dump_fail_streak = 5
        f = make_flow(ui)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._scroll_to_top_of_taskcenter()
        self.assertEqual(ui.swipes, 0)                    # 不空转下滑


class TestScrollToTopDumpReduction(unittest.TestCase):
    """省 dump 优化（隔轮查锚点）回归：
    - 首屏已在顶 → 0 次 swipe（首轮预检保留）
    - 偶数次 swipe 到顶 → 正好在锚点出现后停（不多滑）
    - 奇数次 swipe 到顶 → 允许 1 次无害 overscroll，下一检查轮停
    - 8 次仍不到顶 → 有界退出（不崩溃），并计入检查轮次
    """

    def _flow(self, depth: int, anchor: bool = True):
        """从 pos=depth 下滑；pos=0 含顶部锚点(收支详情)时才到顶。"""
        pages = ([[nd("收支详情", 200)]] if anchor else [[nd("行0", 200)]])
        pages += [[nd(f"行{i}", 300 + 30 * i)] for i in range(1, depth + 1)]
        ui = FakeUI(pages)
        ui.pos = depth
        return make_flow(ui), ui

    def test_already_at_top_no_swipe(self):
        f, ui = self._flow(depth=0)
        f._scroll_to_top_of_taskcenter()
        self.assertEqual(ui.swipes, 0)                    # 首轮预检即命中

    def test_even_swipes_stops_exactly(self):
        f, ui = self._flow(depth=2)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._scroll_to_top_of_taskcenter()
        # pos2 -> pos1 -> pos0(锚点)；第 2 次 swipe 后下一检查轮命中
        self.assertEqual(ui.swipes, 2)
        self.assertEqual(ui.pos, 0)

    def test_odd_swipes_allows_one_overscroll_then_stops(self):
        f, ui = self._flow(depth=3)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._scroll_to_top_of_taskcenter()
        # pos3 需 3 次到顶，但第 3 次 swipe 所在轮不检查 → 多滑 1 次
        # 到顶后 overscroll（pos 钳在 0），下一检查轮(第 4 次后)命中返回
        self.assertEqual(ui.swipes, 4)
        self.assertEqual(ui.pos, 0)

    def test_never_top_bounded_at_8_swipes(self):
        f, ui = self._flow(depth=20, anchor=False)
        with mock.patch.object(flow_mod.time, "sleep"):
            f._scroll_to_top_of_taskcenter()              # 无锚点：不抛异常
        self.assertEqual(ui.swipes, 8)                    # 有界，不无限下滑
        self.assertEqual(ui.pos, 12)                      # 20 - 8 次，未误停


if __name__ == "__main__":
    unittest.main()
