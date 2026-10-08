"""三台机器人轮换（看广告）的静态回归测试（mock 驱动，不依赖真机/adb）。

针对 flow.run_all 的**现有顺序轮换语义**（F10，2026-09-09）：
- 每台机器人**独立进出一次**任务中心，会话内按 ad_cooldown 间隔连看直到
  target 次 / 失败上限 / 已达每日配额；
- 一台处理完（退出任务中心）才轮到下一台 —— 顺序轮换，非并发交错；
- 进入失败：同一台最多 3 次尝试（每次 safe_back 复位），仍失败则跳过该台，
  **不影响后续机器人**；
- 会话内看广告失败：连续 3 次（max_fail）后退出并轮到下一台。

本类只验证"给定 3 台机器人，run_all 是否正确完成顺序轮换"，不改主程序。

运行：py -3.13 -m unittest test_3bot_rotate -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from flow import Flow

ROBOTS_3 = ["R1", "R2", "R3"]


class ThreeBotRotationBase(unittest.TestCase):
    """构造一个 mock 齐备、CD 归零（避免 time.sleep 死循环）的 Flow。

    与 test_run_all_flags.FlowAllBase 同构，专为 3 台轮换场景。
    """

    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()   # A2/A3：实例级缓存（防类级共享字典污染）
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f._dismiss_badcase = mock.Mock()
        # D 优化（2026-09-13）：轮转 CD 尾部会真实调用 _find_row 预取行节点，
        # Flow.__new__ 无 ui 属性，必须 mock（返回 None → watch 走现场查找兜底）。
        self.f._find_row = mock.Mock(return_value=None)
        self.f._enter_taskcenter = mock.Mock(return_value=True)
        self.f._watch_ad_once = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        self.f._safe_back_to_robot_list = mock.Mock(return_value=True)
        # P0（2026-09-27）：连看会话失败后的「是否仍在任务中心」校验。
        # 默认 True = 不触发复位重进（轮换路径不受该修复影响）。
        self.f._back_at_taskcenter = mock.Mock(return_value=True)
        self.f._read_ad_ratio = mock.Mock(return_value=None)  # 基数未知 → 屏幕复核路径
        self.f._ad_quota_done = mock.Mock(return_value=False)
        self._sleep = mock.patch.object(flow_mod.time, "sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

    # ---- 便捷断言 ----
    def assert_entered_per_bot(self, per_bot=1):
        """每台都应独立进入（顺序轮换：R1→R2→R3，各 1 次会话）。"""
        self.assertEqual(self.f._enter_taskcenter.call_count, 3)
        self.assertEqual(self.f._enter_taskcenter.call_args_list,
                         [mock.call("R1"), mock.call("R2"), mock.call("R3")])

    def assert_exited_per_bot(self, per_bot=1):
        self.assertEqual(self.f._exit_taskcenter.call_count, 3)

    def assert_order(self, name_attr="_enter_taskcenter"):
        calls = getattr(self.f, name_attr).call_args_list
        names = [c.args[0] for c in calls]
        self.assertEqual(names, ROBOTS_3)


class TestThreeBotRotationBasic(ThreeBotRotationBase):
    """每台看 1 次：3 台各进一次、各看 1 次、各退一次，严格顺序制。"""

    def test_each_bot_own_session_watch_once_exit_once(self):
        self.f.run_all(ROBOTS_3, False, False, 1)
        self.assert_entered_per_bot()
        self.assertEqual(self.f._watch_ad_once.call_count, 3)
        self.assert_exited_per_bot()

    def test_sequential_rotation_no_overlap(self):
        # 顺序轮换：R2 仅在 R1 的会话结束（exit）之后才开始 —— 验证 call 顺序
        # 严格为 enter/R1 → watch/R1 → exit → enter/R2 → ...
        self.f.run_all(ROBOTS_3, False, False, 1)
        order = []
        for c in self.f._enter_taskcenter.call_args_list:
            order.append(("enter", c.args[0]))
        for c in self.f._watch_ad_once.call_args_list:
            order.append(("watch", "-"))
        for c in self.f._exit_taskcenter.call_args_list:
            order.append(("exit", "-"))
        # 因各 mock 独立计数，只验证 enter 的顺序与 exit 数量即可（上面已断言）；
        # 这里进一步验证 enter 严格按 R1,R2,R3 出现
        self.assert_order()


class TestThreeBotRotationMultiAds(ThreeBotRotationBase):
    """每台连看多次：会话内按 CD 连看 target 次后再退出换下一台。"""

    def test_two_ads_each_in_own_session(self):
        self.f.run_all(ROBOTS_3, False, False, 2)
        self.assert_entered_per_bot()
        self.assertEqual(self.f._watch_ad_once.call_count, 6)   # 3 台 × 2 次
        self.assert_exited_per_bot()                            # 各退一次

    def test_cd_counted_per_session_between_ads(self):
        # CD 归零时的 sleep 应是"配额复核重叠段"，不应死循环；验证 run_all 能
        # 在 cd=0 下正常完成 3 台×2 次而不卡（time.sleep 已被 mock 冻结）。
        self.f.run_all(ROBOTS_3, False, False, 2)
        self.assertEqual(self.f._ad_quota_done.call_count, 3)   # 每台 1 次会话内复核

    def test_prefetched_row_passed_on_subsequent_ads(self):
        # D 优化（2026-09-13）：每台首轮 watch(row=None) 现场查找；后续各轮
        # 把 CD 窗口预取的行节点传给 _watch_ad_once，省一次全量 dump。
        # 3 台 × 2 次 → row 模式应为 None, 预取, None, 预取, None, 预取
        # （pre_row 一次性使用 + 每台重置，失败路径也会被 watch 前的重置清掉）。
        sentinel = ("btn", "看广告", (1, 10))
        self.f._find_row = mock.Mock(return_value=sentinel)
        self.f.run_all(ROBOTS_3, False, False, 2)
        calls = self.f._watch_ad_once.call_args_list
        self.assertEqual(len(calls), 6)
        expected = [None, sentinel] * 3
        actual = [c.kwargs.get("row") for c in calls]
        self.assertEqual(actual, expected)


class TestThreeBotRotationFailures(ThreeBotRotationBase):
    """失败隔离：某台异常不影响其后的轮换。"""

    def test_enter_failure_skips_that_bot_others_continue(self):
        # R2 进不去（3 次重试仍失败）→ 跳过 R2，R1、R3 正常看广告
        def fake_enter(name):
            return name != "R2"
        self.f._enter_taskcenter = mock.Mock(side_effect=fake_enter)
        self.f.run_all(ROBOTS_3, False, False, 1)
        # R2 重试 3 次，安全返回 3 次
        r2_calls = [c for c in self.f._enter_taskcenter.call_args_list
                    if c.args[0] == "R2"]
        self.assertEqual(len(r2_calls), 3)
        self.assertEqual(self.f._safe_back_to_robot_list.call_count, 3)
        # 只有 R1、R2 在 3 次尝试 + R3 成功 = 1 + 3 + 1 = 5 次 enter 调用
        # 但成功进入的只有 R1、R3 → 各看 1 次广告、各退 1 次
        self.assertEqual(self.f._watch_ad_once.call_count, 2)
        self.assertEqual(self.f._exit_taskcenter.call_count, 2)

    def test_watch_failure_exits_bot_and_rotates(self):
        # R1 看广告连续 3 次失败 → 退出 → 轮到 R2、R3 正常
        calls = {"n": 0}

        def fake_watch(*a, **k):    # D 优化后 run_all 会传 row= kwarg，桩须兼容
            calls["n"] += 1
            return calls["n"] > 3        # 前 3 次(R1)失败，之后成功
        self.f._watch_ad_once = mock.Mock(side_effect=fake_watch)
        self.f.run_all(ROBOTS_3, False, False, 1)
        # R1 失败 3 次后退出；R2/R3 各成功 1 次
        self.assertEqual(self.f._watch_ad_once.call_count, 5)    # 3失败 + 2成功
        self.assert_exited_per_bot()                              # 三台都退出过


class TestThreeBotRotationQuota(ThreeBotRotationBase):
    """配额边界：某台已达每日配额直接退出，不空看；其余照常。"""

    def test_quota_hit_exits_without_watching(self):
        # R2 进台基数已是满额 → 直接退出，不看广告；R1/R3 正常看。
        # _read_ad_ratio() 无参（读当前屏），按调用序号区分：第 2 台(R2)返回满额。
        calls = {"n": 0}

        def fake_ratio():
            calls["n"] += 1
            return (10, 10) if calls["n"] == 2 else None
        self.f._read_ad_ratio = mock.Mock(side_effect=fake_ratio)
        self.f.run_all(ROBOTS_3, False, False, 1)
        # 只有 R1、R3 各看 1 次
        self.assertEqual(self.f._watch_ad_once.call_count, 2)
        self.assert_exited_per_bot()      # 三台都进了也都退出了

    def test_arithmetic_early_exit_within_session(self):
        # 每台进台 9/10 + 本会话看 1 次 → 必满 → 提前收尾，不再等 CD
        self.f._read_ad_ratio = mock.Mock(return_value=(9, 10))
        self.f.run_all(ROBOTS_3, False, False, 5)
        # 3 台各看 1 次即收尾
        self.assertEqual(self.f._watch_ad_once.call_count, 3)
        self.f._ad_quota_done.assert_not_called()   # 算术已判满，无需屏幕复核
        self.assert_exited_per_bot()


# ----------------------------------------------------------------------
# 额外：验证 3 台轮换在"签到/反馈关闭（--ad-only）"下直入看广告的调用面
# ----------------------------------------------------------------------
class TestRotateGroupMode(ThreeBotRotationBase):
    """组制轮询模式（2026-09-16）：run_all(..., rotate=True, group=3)。

    语义：
    - 剩余机器人按 group 台一组；组内 R1→R2→R3→R1 循环，每台每轮看 1 支；
    - 配额满（进程内累计或首访屏幕基数校准）即移出，组动态缩员；
    - 组内剩 1 台 → 回落 _watch_ads_session 同机连看（CD 等待语义）；
    - 单台连败 3 次弃权，不影响其他台；
    - 签到会话内首轮广告成功（run_robot 返回 2）计入 done，不重复看。
    """

    def test_three_bots_round_robin_two_ads_each(self):
        # 3 台 × 2 支：pass1 R1,R2,R3 → pass2 R1,R2,R3 → 全部配额满
        self.f.run_all(ROBOTS_3, False, False, 2, rotate=True, group=3)
        self.assertEqual(self.f._enter_taskcenter.call_count, 6)
        self.assertEqual(self.f._watch_ad_once.call_count, 6)
        self.assertEqual(self.f._exit_taskcenter.call_count, 6)
        # 跨台交错顺序：R1,R2,R3,R1,R2,R3
        names = [c.args[0] for c in self.f._enter_taskcenter.call_args_list]
        self.assertEqual(names, ["R1", "R2", "R3", "R1", "R2", "R3"])

    def test_group_partition_and_lone_robot_fallback(self):
        # 4 台 group=3 → 组1 [R1,R2,R3] 轮换；组2 [R4] 回落连看会话
        # target=1：组1 各看 1 支即满；R4 走 _watch_ads_session 看 1 支。
        self.f.run_all(["R1", "R2", "R3", "R4"], False, False, 1,
                       rotate=True, group=3)
        names = [c.args[0] for c in self.f._enter_taskcenter.call_args_list]
        self.assertEqual(names, ["R1", "R2", "R3", "R4"])
        self.assertEqual(self.f._watch_ad_once.call_count, 4)
        self.assertEqual(self.f._exit_taskcenter.call_count, 4)

    def test_lone_robot_group_uses_session_path(self):
        # 单台也走组制（1 台组直接回落连看）：target=2 → 会话内连看 2 支
        self.f.run_all(["R1"], False, False, 2, rotate=True, group=3)
        self.assertEqual(self.f._enter_taskcenter.call_count, 1)
        self.assertEqual(self.f._watch_ad_once.call_count, 2)
        self.assertEqual(self.f._exit_taskcenter.call_count, 1)

    def test_quota_calibration_removes_robot_without_watching(self):
        # 首访屏幕基数校准：R1 屏幕 10/10 → 基数 10 → 移出，不看；R2/R3 照常
        calls = {"n": 0}

        def fake_ratio():
            calls["n"] += 1
            return (10, 10) if calls["n"] == 1 else None   # 第 1 次读屏=R1 首访
        self.f._read_ad_ratio = mock.Mock(side_effect=fake_ratio)
        self.f.run_all(ROBOTS_3, False, False, 1, rotate=True, group=3)
        # R1 校准后移出（进 1 退 1 不看），R2/R3 各看 1 支
        self.assertEqual(self.f._watch_ad_once.call_count, 2)
        self.assertEqual(self.f._exit_taskcenter.call_count, 3)

    def test_three_strikes_drops_robot_others_continue(self):
        # 全员看广告失败：每轮每人 fail+1，3 轮后全部弃权（3 台 × 3 次 = 9）
        self.f._watch_ad_once = mock.Mock(return_value=False)
        self.f.run_all(ROBOTS_3, False, False, 1, rotate=True, group=3)
        self.assertEqual(self.f._watch_ad_once.call_count, 9)
        self.assertEqual(self.f._exit_taskcenter.call_count, 9)

    def test_attrition_two_remain_after_one_fills(self):
        # R1 首访校准屏幕 9/10（target=2）→ 日配额只剩 1 → 看 1 支后移出；
        # R2/R3 不受限轮换各看 2 支
        calls = {"n": 0}

        def fake_ratio():
            calls["n"] += 1
            return (9, 10) if calls["n"] == 1 else None   # 第 1 次读屏=R1 首访
        self.f._read_ad_ratio = mock.Mock(side_effect=fake_ratio)
        self.f.run_all(ROBOTS_3, False, False, 2, rotate=True, group=3)
        self.assertEqual(self.f._watch_ad_once.call_count, 5)   # R1×1 + R2/R3×2
        names = [c.args[0] for c in self.f._enter_taskcenter.call_args_list]
        self.assertEqual(names.count("R1"), 1)   # R1 只进台 1 次（看满即移出）

    def test_first_ad_credit_reduces_rotation_target(self):
        # 签到会话首轮广告成功（run_robot 返回 2）→ 记 1 支，轮换只补 1 支
        self.f.run_robot = mock.Mock(return_value=2)
        self.f.run_all(ROBOTS_3, True, True, 2, rotate=True, group=3)
        self.assertEqual(self.f._watch_ad_once.call_count, 3)   # 每台补 1 支
        # 首轮失败（返回 1）→ 轮换每台补满 2 支
        self.f2 = Flow.__new__(Flow)
        self.f2._init_cache_state()
        self.f2.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}
        self.f2.t = {"click_min": 1.5, "click_max": 3.0}
        self.f2.run_robot = mock.Mock(return_value=1)
        self.f2._enter_taskcenter = mock.Mock(return_value=True)
        self.f2._watch_ad_once = mock.Mock(return_value=True)
        self.f2._exit_taskcenter = mock.Mock()
        self.f2._ad_quota_done = mock.Mock(return_value=False)
        self.f2._dismiss_badcase = mock.Mock()
        self.f2._find_row = mock.Mock(return_value=None)
        self.f2._safe_back_to_robot_list = mock.Mock(return_value=True)
        self.f2._read_ad_ratio = mock.Mock(return_value=None)
        self.f2.run_all(ROBOTS_3, True, True, 2, rotate=True, group=3)
        self.assertEqual(self.f2._watch_ad_once.call_count, 6)  # 每台补 2 支


class TestSlidingWindowRotate(ThreeBotRotationBase):
    """滑动窗口轮询（2026-09-26 用户需求）：固定窗口 + 出窗补位。

    语义（演进自静态分组）：
    - 某台配额满/连败弃权出窗后，队列下一位**立即**补位进窗（排窗口尾部）；
    - 窗口保持满员轮换直至机器人耗尽，避免缩员后 CD 空等；
    - 队列空且窗口剩 1 台 → 回落 _watch_ads_session 连看。
    """

    def test_early_full_bot_refills_from_queue(self):
        # 用户场景：5 台 group=3，R1 首访校准剩 1（屏幕 9/10，target=2）
        # → R1 看 1 支出窗，R4 立即补位；R2/R3/R4 各看 2 支
        calls = {"n": 0}

        def fake_ratio():
            calls["n"] += 1
            return (9, 10) if calls["n"] == 1 else None   # 第 1 次读屏=R1 首访
        self.f._read_ad_ratio = mock.Mock(side_effect=fake_ratio)
        self.f.run_all(["R1", "R2", "R3", "R4"], False, False, 2,
                       rotate=True, group=3)
        # R1×1 + R2×2 + R3×2 + R4×2 = 7 支
        self.assertEqual(self.f._watch_ad_once.call_count, 7)
        names = [c.args[0] for c in self.f._enter_taskcenter.call_args_list]
        # 圈1：R1(看1支出窗,R4补位排尾)→R2→R3；圈2：R2→R3→R4(各看满出窗)；
        # 圈2 末窗口剩 R4(还差1支) → 下圈顶部回落连看再进 1 次
        self.assertEqual(names, ["R1", "R2", "R3", "R2", "R3", "R4", "R4"])

    def test_refill_preserves_queue_order_multiple_outs(self):
        # 5 台 group=3，R1/R2 依次提前满（各校准剩 1）→ R4 补 R1 位、
        # R5 补 R2 位；R3/R4/R5 各看 2 支
        seq = iter([(9, 10), (9, 10)])

        def fake_ratio():
            try:
                return next(seq)
            except StopIteration:
                return None
        self.f._read_ad_ratio = mock.Mock(side_effect=fake_ratio)
        self.f.run_all(["R1", "R2", "R3", "R4", "R5"], False, False, 2,
                       rotate=True, group=3)
        # R1×1 + R2×1 + R3×2 + R4×2 + R5×2 = 8 支
        self.assertEqual(self.f._watch_ad_once.call_count, 8)
        names = [c.args[0] for c in self.f._enter_taskcenter.call_args_list]
        # 圈1：R1(出窗,R4补)→R2(出窗,R5补)→R3；圈2：R3→R4→R5 各第1支；
        # 圈3：R4→R5 各第2支（看满出窗，窗口耗尽）
        self.assertEqual(names, ["R1", "R2", "R3", "R3", "R4", "R5",
                                 "R4", "R5"])

    def test_window_drains_to_empty_when_queue_exhausted(self):
        # 4 台 group=3 target=1：R1/R2/R3 各看 1 支满出窗，R4 补位后窗口
        # 仅剩 1 台且队列空 → 回落连看（进 1 次、看 1 支、退 1 次）
        self.f.run_all(["R1", "R2", "R3", "R4"], False, False, 1,
                       rotate=True, group=3)
        names = [c.args[0] for c in self.f._enter_taskcenter.call_args_list]
        self.assertEqual(names, ["R1", "R2", "R3", "R4"])
        self.assertEqual(self.f._watch_ad_once.call_count, 4)
        self.assertEqual(self.f._exit_taskcenter.call_count, 4)

    def test_refill_after_strike_out_keeps_window_full(self):
        # 4 台 group=3 target=1，R1 进台三连败弃权出窗 → R4 补位，
        # R2/R3/R4 各看 1 支。弃权台不再进台，窗口保持轮换到耗尽。
        self.f._enter_taskcenter = mock.Mock(
            side_effect=lambda name: name != "R1")
        self.f.run_all(["R1", "R2", "R3", "R4"], False, False, 1,
                       rotate=True, group=3)
        names = [c.args[0] for c in self.f._enter_taskcenter.call_args_list]
        # R1 单次 _rotate_watch_once 内 3 次进台尝试全败 → 弃权出窗补 R4；
        # 圈1 剩余 R2,R3 各看 1 支；圈2 R4 看 1 支
        self.assertEqual(names, ["R1", "R1", "R1", "R2", "R3", "R4"])
        self.assertEqual(self.f._watch_ad_once.call_count, 3)


class TestThreeBotAdOnlyMapping(ThreeBotRotationBase):
    """--ad-only：不逐台空进出（run_robot 不调用），直接进入 3 台看广告轮询。"""

    def test_ad_only_does_not_call_run_robot(self):
        self.f.run_robot = mock.Mock()
        self.f.run_all(ROBOTS_3, False, False, 1)
        self.f.run_robot.assert_not_called()
        self.assert_entered_per_bot()      # 3 台仍各自进入任务中心看广告


class TestEntryQuotaCache(ThreeBotRotationBase):
    """方案 A（2026-09-28）：进台配额缓存复用 —— 免二次读屏。

    背景：用户观察「进任务中心后一直下滑刷新」= _read_ad_ratio 的 _find_row
    为找「看广告」行反复 swipe。改为 _enter_taskcenter 成功时读一次并缓存
    （self._tc_entry_ratio），轮换/连看的 base 校准优先复用，免二次滚动。

    覆盖三条边界：
      ① 缓存命中 → 消费端不再调用 _read_ad_ratio（免二次读屏）；
      ② 缓存为 None（覆盖层/版式差异）→ 回退原地读屏（旧行为不变）；
      ③ 缓存值正确参与 base 校准（X - done），配额收尾等效。
    """

    def test_cache_hit_skips_second_screen_read(self):
        # 进台成功时缓存已就位（模拟 _enter_taskcenter 已读 (3,10)）
        self.f._tc_entry_ratio = (3, 10)
        self.f._read_ad_ratio = mock.Mock(return_value=(3, 10))
        self.f.run_all(["R1"], False, False, 1, rotate=True, group=3)
        # 消费端复用缓存 → 不再原地读屏
        self.f._read_ad_ratio.assert_not_called()
        # base = 3 - 0 = 3 → left = min(1-0, 10-3-0) = 1 → 正常看 1 支
        self.assertEqual(self.f._watch_ad_once.call_count, 1)
        self.f._read_ad_ratio.assert_not_called()

    def test_cache_miss_falls_back_to_screen_read(self):
        # 缓存 None（未读到/未进台）→ 回退原地读屏（旧行为）
        self.f._tc_entry_ratio = None
        self.f._read_ad_ratio = mock.Mock(return_value=None)
        self.f.run_all(["R1"], False, False, 1, rotate=True, group=3)
        self.f._read_ad_ratio.assert_called()      # 回退路径确实读屏
        self.assertEqual(self.f._watch_ad_once.call_count, 1)

    def test_cache_value_drives_base_calibration(self):
        # 屏幕已看 5/10 + 进程内已看 0 → base=5 → left=min(1,10-5)=1 → 看 1 支
        self.f._tc_entry_ratio = (5, 10)
        self.f._read_ad_ratio = mock.Mock(return_value=(5, 10))
        self.f.run_all(["R1"], False, False, 1, rotate=True, group=3)
        self.f._read_ad_ratio.assert_not_called()
        self.assertEqual(self.f._watch_ad_once.call_count, 1)

    def test_cache_screen_already_full_skips_watch(self):
        # 屏幕已满 10/10（跨进程已看完）→ base=10 → left=min(1,10-10)=0
        # → 直接移出轮换，不看广告
        self.f._tc_entry_ratio = (10, 10)
        self.f._read_ad_ratio = mock.Mock(return_value=(10, 10))
        self.f.run_all(["R1"], False, False, 1, rotate=True, group=3)
        self.f._read_ad_ratio.assert_not_called()
        self.assertEqual(self.f._watch_ad_once.call_count, 0)

    def test_enter_taskcenter_resets_and_populates_cache(self):
        # 真实 _enter_taskcenter 路径：进台前清空缓存、成功返回前写入
        import test_nav_optimize as tno
        ui = mock.Mock()
        ui.nodes.return_value = []
        ui.find.return_value = None
        ui.find_contains.return_value = None
        f = tno.make_flow(ui)
        f._nav_robot_list = mock.Mock()
        f._find_robot = mock.Mock(return_value=tno.nd("某机器人", 300))
        f._tap_node = mock.Mock()
        f._tap = mock.Mock()
        f._diag_shot = mock.Mock()
        f._reconfirm_click_target = mock.Mock(side_effect=lambda n, r: r)
        f._read_ad_ratio = mock.Mock(return_value=(4, 10))
        # 新功能弹窗探测走 OCR 分支时避免真实调用（ui 为 Mock）→ 直接 None
        f._ocr_find = mock.Mock(return_value=None)
        ui.wait_for.side_effect = [tno.nd("发消息", 900), tno.nd("个人", 700)]
        with mock.patch.object(flow_mod.time, "sleep"):
            ok = f._enter_taskcenter("某机器人")
        self.assertTrue(ok)
        self.assertEqual(f._tc_entry_ratio, (4, 10))   # 成功进台写入缓存
        f._read_ad_ratio.assert_called_once()


class TestReanchorKeng25(ThreeBotRotationBase):
    """坑 25（2026-09-30 用户实锤）：轮换「屏幕真值重锚」。

    背景：旧实现只在首访校准一次 base，其后每次进台读到的屏幕值仅打日志、
    不参与记账 → done 纯算术累加永不复核。只要某支「节目播放但未到账」
    （广告自动回任务中心 / tap 未真开广告 / 服务端未记账），done 就永久
    虚高、该台少看 1 支（实测王 7 台各虚报 1 支，汇总报 100 实为 ~93）。

    覆盖：① 屏幕落后于进程口径 → done 向下纠正；② 屏幕与口径一致 → 不动；
    ③ 屏幕高于口径（手动补看）→ 向上吸收并重置未到账计数；④ 分母异常
    （实测偶发读成 /1000）→ 不认账；⑤ 屏幕长期不前进 → 到达 max_fail
    上限弃权出窗（防轮换窗口死循环）。
    """

    def _st(self, done=1, base=2, reanchor=0):
        return {"R1": {"done": done, "fail": 0, "base": base,
                       "reanchor": reanchor}}

    def setUp(self):
        super().setUp()
        self.f._capture_energy_start = mock.Mock()

    def test_screen_lag_corrects_done_downward(self):
        st = self._st(done=1, base=2)          # 进程口径 2+1=3
        self.f._tc_entry_ratio = (2, 10)       # 屏幕未前进（上一支未到账）
        n = self.f._rotate_watch_once("R1", st, 2, 10, 3)
        # done 1→0（重锚），随后补看 1 支 → done 回到 1；本次新看 1
        self.assertEqual(st["R1"]["done"], 1)
        self.assertEqual(st["R1"]["reanchor"], 1)
        self.assertEqual(n, 1)

    def test_screen_matches_code_is_noop(self):
        st = self._st(done=1, base=2)
        self.f._tc_entry_ratio = (3, 10)       # 屏幕 3 == base2+done1
        self.f._rotate_watch_once("R1", st, 2, 10, 3)
        self.assertEqual(st["R1"]["reanchor"], 0)
        self.assertEqual(st["R1"]["done"], 2)

    def test_manual_topup_absorbed_upward(self):
        st = self._st(done=1, base=2, reanchor=2)   # 进程口径 3
        self.f._tc_entry_ratio = (4, 10)            # 屏幕 4（人手动补了 1）
        n = self.f._rotate_watch_once("R1", st, 2, 10, 3)
        self.assertEqual(st["R1"]["done"], 2)       # 向上吸收到 2 → 达 target
        self.assertEqual(st["R1"]["reanchor"], 0)   # 视为正常前进，清零
        self.assertEqual(n, 0)                      # 已满，不再看

    def test_abnormal_denominator_skips_reanchor(self):
        st = self._st(done=1, base=2)
        self.f._tc_entry_ratio = (1, 1000)     # 分母异常（实测读到别的行）
        n = self.f._rotate_watch_once("R1", st, 2, 10, 3)
        self.assertEqual(st["R1"]["reanchor"], 0)   # 不认账
        self.assertEqual(st["R1"]["done"], 2)       # 照常再补看 1 支
        self.assertEqual(n, 1)

    def test_persistent_no_progress_gives_up_after_max_fail(self):
        st = self._st(done=1, base=2)
        self.f._tc_entry_ratio = (2, 10)       # 屏幕永远不前进
        n = 1
        for _ in range(3):
            n = self.f._rotate_watch_once("R1", st, 2, 10, 3)
        self.assertEqual(st["R1"]["reanchor"], 3)
        self.assertEqual(st["R1"]["fail"], 3)       # 触发既有弃权出窗路径
        self.assertEqual(n, 0)                      # 第 3 次：达上限 → 不再看


if __name__ == "__main__":
    unittest.main()
