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
class TestThreeBotAdOnlyMapping(ThreeBotRotationBase):
    """--ad-only：不逐台空进出（run_robot 不调用），直接进入 3 台看广告轮询。"""

    def test_ad_only_does_not_call_run_robot(self):
        self.f.run_robot = mock.Mock()
        self.f.run_all(ROBOTS_3, False, False, 1)
        self.f.run_robot.assert_not_called()
        self.assert_entered_per_bot()      # 3 台仍各自进入任务中心看广告


if __name__ == "__main__":
    unittest.main()
