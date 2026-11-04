# -*- coding: utf-8 -*-
"""am start 就绪等待（F1）+ 滑顶前置校验（F3）单测 —— 2026-11-02 退穿死锁事故。

事故链条（11-02 首轮 03:48~03:58，唐灵 3 台 + 凌津渡整号报废）：
    ① 裴旖 看广告连点 2 次判「点开广告后仍停在任务中心」（坑 42 假阴性）
    ② 退台链首次走完整兜底：连发 3 次 BACK 未回列表 → 三层返回未达列表
       → 滑动到任务中心顶部（8 次 swipe 白烧 15s，**此时已不在任务中心**）
       → 三层返回后未见列表 → 安全返回 6 次物理 BACK → 退穿到桌面 lawnchair
    ③ am start 拉回 QQ（前台=com.tencent.mobileqq）→ **仅 0.7s** 就判
       「未能确认在机器人列表」→ 再 6 次 BACK → 又退穿 → 循环
    ④ 全天「安全返回」被调用 10 次全失败

根因（两处，互相独立）：
  F1 `ui.nodes()` 有 0.8s TTL 缓存，而 `am start` 走 adb shell、不经过 UI 类
     → 不失效缓存 → 拉回后立刻做的判据吃的是「桌面那次 dump」→
     `_on_qq_main_shell()` 判 False → 兜底继续物理 BACK → 退穿死循环。
  F3 滑顶动作没有「是否真在任务中心」的前置校验，在广告/子页上盲滑 8 次。

修复后要守住：
  1. 拉回成功后**必须**失效节点缓存，并等主壳进树；命中即返回 True；
  2. 等不到（超时）只返回 False，**不改变调用方语义、不抛异常**；
  3. 拉回失败时**不**做就绪等待（省时间，也别掩盖失败）；
  4. 不在任务中心 → 滑顶**一次都不滑**；
  5. 在任务中心（`_page_tc` / 外壳特征 / 「每日签到」任一）→ 照旧滑；
  6. 读屏异常 → 按「在任务中心」处理（宁可多滑，方向保守）；
  7. `am_start_settle: 0` 与 `tc_scroll_guard: false` 是一键回退口。

运行：py -3.13 -m unittest test_nav_settle_guard -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from flow import Flow

QQ = "com.tencent.mobileqq"


class _NoInvalidateUI(object):
    """自定义 fake：没有 `_invalidate_cache` / `nodes` —— 模拟旧后端与单测桩。"""

    def __init__(self):
        self.swipes = 0

    def swipe_down(self, pause=1.0):
        self.swipes += 1

    def back(self, pause=1.0):
        pass

    def am_start(self, comp):
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f.wf = {"am_start_settle": 0.05, "tc_scroll_guard": True}
        self.f.ui = mock.Mock()
        self.f._page_tc = False
        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self.sleep = self._sleep.start()
        self.addCleanup(self._sleep.stop)


# ---------------------------------------------------------------- F1
class SettleAfterAmStartTest(Base):
    def test_hit_returns_true_and_invalidates_cache(self):
        """主壳进树 → True，且**必须先**失效节点缓存（否则吃旧 dump）。"""
        self.f._on_qq_main_shell = lambda: True
        self.assertTrue(self.f._settle_after_am_start())
        self.f.ui._invalidate_cache.assert_called_once()

    def test_timeout_returns_false_without_raising(self):
        """主壳一直不进树 → False，不抛异常（调用方按旧逻辑继续）。"""
        self.f._on_qq_main_shell = lambda: False
        self.assertFalse(self.f._settle_after_am_start())

    def test_predicate_exception_is_swallowed(self):
        """谓词内部读屏异常 → 由 _wait_until 吞掉，最终 False 而不是崩。"""
        def boom():
            raise RuntimeError("dump failed")
        self.f._on_qq_main_shell = boom
        self.assertFalse(self.f._settle_after_am_start())

    def test_ui_without_invalidate_cache(self):
        """旧后端/自定义 fake 没有 _invalidate_cache → 跳过清理但不报错。"""
        self.f.ui = _NoInvalidateUI()
        self.f._on_qq_main_shell = lambda: True
        self.assertTrue(self.f._settle_after_am_start())

    def test_bad_config_value_falls_back(self):
        """am_start_settle 写成非数字 → 回落默认值，不抛异常。"""
        self.f.wf["am_start_settle"] = "很快"
        self.f._on_qq_main_shell = lambda: True
        self.assertTrue(self.f._settle_after_am_start())

    def test_zero_settle_is_legacy(self):
        """am_start_settle: 0 → 不等（等价旧行为），缓存照样失效。"""
        self.f.wf["am_start_settle"] = 0
        self.f._on_qq_main_shell = lambda: False
        self.assertFalse(self.f._settle_after_am_start())
        self.f.ui._invalidate_cache.assert_called_once()


class RecoverQqWiringTest(Base):
    """`_recover_qq_foreground` 的两个成功返回点都要挂就绪等待。"""

    def _prep(self, fg_seq):
        self.f._foreground_package = mock.Mock(side_effect=fg_seq)
        self.f._qq_package = lambda: QQ
        self.f._qq_component = lambda: QQ + "/.activity.SplashActivity"
        self.f.ui.am_start = mock.Mock()
        self.f.ui.home = mock.Mock()
        self.f._settle_after_am_start = mock.Mock(return_value=True)
        self.f.wf["escape_recover_tries"] = 2

    def test_settle_called_on_first_try_success(self):
        self._prep([QQ])
        self.assertTrue(self.f._recover_qq_foreground())
        self.f._settle_after_am_start.assert_called_once()

    def test_settle_called_on_last_check_success(self):
        """第一轮没拉回（前台=lawnchair）+ HOME 后末次复核成功 → 也要等。"""
        self._prep(["app.lawnchair", QQ])
        self.assertTrue(self.f._recover_qq_foreground())
        self.f._settle_after_am_start.assert_called_once()

    def test_no_settle_when_recover_failed(self):
        """拉不回 QQ → 不做就绪等待（省时间，也别把失败掩盖成「已就绪」）。"""
        self._prep(["app.lawnchair", "app.lawnchair", "app.lawnchair"])
        self.assertFalse(self.f._recover_qq_foreground())
        self.f._settle_after_am_start.assert_not_called()


# ---------------------------------------------------------------- F3
class InTaskcenterNowTest(Base):
    def test_page_tc_flag_counts(self):
        self.f._page_tc = True
        self.f._find = mock.Mock(return_value=None)
        self.assertTrue(self.f._in_taskcenter_now())
        self.f._find.assert_not_called()

    def test_shell_key_counts(self):
        self.f._find = lambda t=None, **k: "node" if t == "收支详情" else None
        self.assertTrue(self.f._in_taskcenter_now())

    def test_signin_row_counts(self):
        self.f._find = lambda t=None, **k: "node" if t == "每日签到" else None
        self.assertTrue(self.f._in_taskcenter_now())

    def test_ad_row_label_counts(self):
        """任务中心滚下去时外壳特征可能不在屏，广告行标题还算数。"""
        self.f._find = lambda t=None, **k: "node" if t == "看视频赚电量" else None
        self.assertTrue(self.f._in_taskcenter_now())

    def test_profile_card_taskcenter_word_is_not_enough(self):
        """资料卡上也有「任务中心」入口 → 刻意不收它，避免把资料卡当任务中心。"""
        self.f._find = lambda t=None, **k: "node" if t == "任务中心" else None
        self.assertFalse(self.f._in_taskcenter_now())

    def test_not_in_taskcenter(self):
        self.f._find = lambda t=None, **k: None
        self.assertFalse(self.f._in_taskcenter_now())

    def test_find_exception_is_conservative(self):
        """读屏异常 → 按「在任务中心」处理（宁可多滑）。"""
        def boom(t=None, **k):
            raise RuntimeError("dump failed")
        self.f._find = boom
        self.assertTrue(self.f._in_taskcenter_now())


class ScrollToTopGuardTest(Base):
    def test_skip_swipes_when_not_in_taskcenter(self):
        """不在任务中心 → 一次都不滑（11-02 事故里白滑了 8 次 15s）。"""
        self.f._find = lambda t=None, **k: None
        self.f._scroll_to_top_of_taskcenter()
        self.f.ui.swipe_down.assert_not_called()

    def test_swipes_when_in_taskcenter(self):
        self.f._find = lambda t=None, **k: "node" if t == "每日签到" else None
        self.f._scroll_to_top_of_taskcenter()
        self.assertGreaterEqual(self.f.ui.swipe_down.call_count, 1)

    def test_swipes_when_page_tc(self):
        self.f._page_tc = True
        self.f._find = lambda t=None, **k: None
        self.f._scroll_to_top_of_taskcenter()
        self.assertGreaterEqual(self.f.ui.swipe_down.call_count, 1)

    def test_knob_off_keeps_legacy_behavior(self):
        """tc_scroll_guard: false → 永远滑（一键回退到旧行为）。"""
        self.f.wf["tc_scroll_guard"] = False
        self.f._find = lambda t=None, **k: None
        self.f._scroll_to_top_of_taskcenter()
        self.assertGreaterEqual(self.f.ui.swipe_down.call_count, 1)

    def test_find_exception_still_swipes(self):
        """任务中心特征读屏异常 → 判据按保守 True 放行，照旧滑顶。

        注意桩**不能**让「收支详情」抛异常：它既是任务中心外壳特征，也是滑顶
        内部自己的到顶锚点（既有代码，不在本次改动范围）。让「当前电量」
        抛就够了 —— 判据会短路成保守 True，锚点仍走到 None → 照旧滑。
        """
        def boom(t=None, **k):
            if t in ("当前电量", "去充电", "每日签到"):
                raise RuntimeError("dump failed")
            return None

        self.f._find = boom
        self.f._scroll_to_top_of_taskcenter()
        self.assertGreaterEqual(self.f.ui.swipe_down.call_count, 1)


# ------------------------------------------------- 事故复现（11-02 死锁 A/B）
class _Node(object):
    def __init__(self, text, y1=1800, y2=1860):
        self.text = text
        self.y1 = y1
        self.y2 = y2
        self.x1 = 100
        self.x2 = 300
        self.selected = False


class _DeadlockUI(object):
    """精确复现 11-02 的三个事实：
      ① `am start` **不清**节点缓存（TTL 窗口内判据吃旧 dump）；
      ② QQ 在联系人页收到 BACK = **退出 App**（回到桌面）；
      ③ dump 快照在缓存未失效时复用。
    只有同时满足 ①②③ 才会「退穿死锁」，也正是 F1 要修的东西。
    """

    def __init__(self):
        self.state = "desktop"
        self._snap = None
        self.back_calls = 0
        self.am_calls = 0

    # -- dump 快照 --
    def _build(self):
        if self.state == "desktop":
            return [_Node("QQ", 700), _Node("游戏中心", 700)]     # 桌面图标，无底部 tab
        # y1 必须 >= flow.TAB_Y(1840) 才算「底部 tab」（实机 tab 文本 y1≈1850）
        return [_Node("消息", 1860), _Node("联系人", 1860), _Node("动态", 1860)]

    def nodes(self):
        if self._snap is None:
            self._snap = self._build()
        return self._snap

    def _invalidate_cache(self):
        self._snap = None

    # -- 设备动作 --
    def foreground_package(self):
        return "app.lawnchair" if self.state == "desktop" else QQ

    def am_start(self, comp):
        self.state = "qq"
        self.am_calls += 1
        return True          # ⚠️ 故意不清缓存（这就是 11-02 的 bug）

    def home(self, pause=1.0):
        self.state = "desktop"
        self._invalidate_cache()

    def back(self, pause=1.0):
        self.back_calls += 1
        if self.state == "qq":
            self.state = "desktop"       # QQ 联系人页 BACK = 退出 App
        self._invalidate_cache()

    def swipe_down(self, pause=1.0):
        pass


class DeadlockReproTest(unittest.TestCase):
    """11-02 退穿的**最小可复现差异**（别过度声明）。

    用这个桩能稳定验证的是：改动前 `am start` 拉回后**必然先白付一次物理 BACK**
    （因为判据吃的是桌面旧 dump），而 QQ 在联系人页收一次 BACK 就是退出 App →
    于是又得再拉一次。改动后这一步完全消失（0 BACK / 1 次拉回）。
    ⚠️ 桩**不足以**复现「10 次全失败」：真实死锁还叠加了 `_nav_robot_list` 在
    错误页上乱点、`escape_recover_tries` 用尽后盲 BACK 等因素，这里不复刻 —— 
    所以本用例只钉「退穿那一步没了」，不宣称「旧代码必然报废」。
    """

    def _make(self):
        ui = _DeadlockUI()
        f = Flow.__new__(Flow)
        f.wf = {"am_start_settle": 3.0, "escape_recover": True,
                "escape_recover_tries": 2, "tc_scroll_guard": True}
        f.ui = ui
        f._page_tc = False
        f._at_robot_list = False
        f._nav_recovering = False
        f.cfg = {}
        # 导航桩：**只认 dump**（看不到 QQ 主壳就物理返回）—— 与真实实现同构
        def _nav():
            if f._on_qq_main_shell():
                f._at_robot_list = True
            else:
                f.ui.back()
        f._nav_robot_list = _nav
        f._looks_like_robot_list = lambda: f._at_robot_list
        p = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        p.start()
        self.addCleanup(p.stop)
        # 关键：真机上进兜底前**已经 dump 过一次**页面（`_looks_like_robot_list()`
        # 在循环顶部被调用），桌面快照因此进了缓存 —— 这正是 11-02 死锁的前提，
        # 不预读就根本复现不出「am start 后仍读到桌面」。
        ui.nodes()
        return f, ui

    def test_new_logic_recovers_in_one_am_start(self):
        """改动后：1 次拉回、0 次 BACK 就到列表。"""
        f, ui = self._make()
        self.assertTrue(f._safe_back_to_robot_list())
        self.assertTrue(f._at_robot_list)
        self.assertEqual(ui.am_calls, 1)      # 一次拉回，不做无谓重试
        self.assertEqual(ui.back_calls, 0)    # 一次 BACK 都不该发

    def test_old_logic_pays_an_extra_back_and_restart(self):
        """关掉就绪等待（等价改动前的代码）→ 先吃桌面旧 dump，白付 1 次 BACK
        + 1 次重新拉回（QQ 已被踢到桌面）。这就是 11-02 退穿的起点。"""
        f, ui = self._make()
        f._settle_after_am_start = lambda: False
        f._safe_back_to_robot_list()
        self.assertGreaterEqual(ui.back_calls, 1)     # 退穿那一步确实发生
        self.assertGreaterEqual(ui.am_calls, 2)       # 因为被踢出去，得再拉一次


if __name__ == "__main__":
    unittest.main(verbosity=2)