# -*- coding: utf-8 -*-
"""P0-a 回归测试：广告等待窗必须吸收「关闭按钮预定位」的 OCR 耗时。

背景（2026-10-19 真机 + 日志实测）：旧实现是

    sleep(prefetch_at); _prefetch_ad_close_pos(); sleep(wait - prefetch_at)

预定位内部要做一次**顶条 OCR**（取图 0.08~0.12 + 识别 ~1.0 ≈ 1.1s），这段
耗时落在两个 sleep **之外** ⇒ 实际观看时长恒为 `wait + OCR ≈ wait + 1.1s`，
每支广告白等 1.1s（10-19 共 447 支 ≈ 8min）。修复后按「已消耗时间」补差，
总时长严格 = wait。

注：原计划的 P0-c（关闭确认改走 u2 dump 快通道）已被真机实测**否决**
（任务中心 WebView 上 u2 dump 要 1.89s，比全屏 OCR 1.68s 还慢），相关测试
随代码一并回退，详见 .workbuddy/memory/2026-10-19.md。

运行：py -3.13 -m unittest test_ad_wait_window -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import Flow


class _Clock:
    """假时钟命名空间：sleep 推进虚拟时间，time() 返回当前虚拟时刻。"""

    def __init__(self):
        self.t = 1000.0
        self.ns = mock.Mock()
        self.ns.time = self._time
        self.ns.sleep = self._sleep

    def _time(self):
        return self.t

    def _sleep(self, s):
        if s and s > 0:
            self.t += s


def make_flow(wf_extra=None):
    f = Flow.__new__(Flow)
    f._init_cache_state()
    f.ui = mock.Mock()
    f.t = {"click_min": 1.5, "click_max": 3.0, "page_wait": 2.0,
           "taskcenter_wait": 3.0, "ad_close_wait": 0.0}
    f.wf = {"ad_cooldown": 60, "ad_times_per_robot": 10,
            "ad_wait_min": 15, "ad_wait_max": 17, "ad_prefetch_at": 13.0,
            "ad_prefetch_pos": True,
            # N1（2026-10-22）：本文件只测"等待窗扣时"，关掉试玩版式探针以隔离
            # 新变量（否则 mock 的 ui.nodes() 会返回 Mock 导致不可迭代）。
            "ad_minigame": False,
            # N4（2026-10-22）：同理关掉「点开校验」「倒计时补等」——
            # 否则等待窗会被补等撑长，本文件的严格等式断言失去意义。
            # 这两项的专门用例见 TestAdOpenGuard / TestAdCountdownWait。
            "ad_open_guard": False, "ad_countdown_wait": False}
    f.wf.update(wf_extra or {})
    return f


class TestAdWaitWindow(unittest.TestCase):

    def _run(self, prefetch_cost, wait=16.0, extra=None):
        clock = _Clock()
        f = make_flow(extra)
        seen = {}

        def _pf():
            clock.t += prefetch_cost     # 模拟 OCR 真实耗时

        def _close(**kw):
            seen["elapsed"] = clock.t - start
            return True

        pf = mock.Mock(side_effect=_pf)
        f._prefetch_ad_close_pos = pf
        f._close_ad = _close
        start = clock.t
        with mock.patch.object(flow_mod, "time", clock.ns), \
                mock.patch.object(flow_mod.random, "uniform",
                                  lambda a, b: wait):
            f._ad_play_and_close()
        return seen.get("elapsed"), pf

    def test_prefetch_cost_absorbed_in_window(self):
        """旧实现实际观看 = wait + OCR(1.1) = 17.1s；修复后必须 = wait。"""
        elapsed, _ = self._run(prefetch_cost=1.1, wait=16.0)
        self.assertAlmostEqual(elapsed, 16.0, places=2)

    def test_zero_cost_still_exact(self):
        elapsed, _ = self._run(prefetch_cost=0.0, wait=15.5)
        self.assertAlmostEqual(elapsed, 15.5, places=2)

    def test_cost_larger_than_remaining(self):
        """OCR 比剩余等待还久（prefetch_at 过于靠后）时总时长 = at + cost ——
        `max(0.0, ...)` 只保证不**负睡**，时间不可能倒流。
        真实路径 cost ≈ 1.1s、at = 13.0 ⇒ 14.1s < wait_min(15)，不触发本边界。"""
        elapsed, _ = self._run(prefetch_cost=4.0, wait=16.0)
        self.assertAlmostEqual(elapsed, 17.0, places=2)

    def test_prefetch_at_13_covers_full_wait_range(self):
        """P0-b：wait ∈ [15,17] 全窗都应触发预定位（条件 wait > at + 1.0）。"""
        for wait in (15.0, 15.4, 16.0, 16.9, 17.0):
            _, pf = self._run(prefetch_cost=0.0, wait=wait)
            self.assertTrue(pf.called,
                            "wait=%.1f 时未触发预定位，覆盖面出现缺口" % wait)

    def test_prefetch_switch_off_is_plain_sleep(self):
        """ad_prefetch_pos: false → 纯 sleep(wait)，不得调用预定位。"""
        elapsed, pf = self._run(prefetch_cost=1.1, wait=16.0,
                                extra={"ad_prefetch_pos": False})
        self.assertAlmostEqual(elapsed, 16.0, places=2)
        pf.assert_not_called()


# ======================================================================
# N1（2026-10-22）：「试玩小游戏」广告版式
# 真机实测链路（古禹 11:29，dump 取证）：
#   落地页(`点击进入`) →① 授权页(`返回`) →② 奖励页(`恭喜获得奖励`,`返回`)
#   →③ 任务中心；全程只需点 2 次「返回」，且**落地页等多久都不计时**。
# ======================================================================
def nd(text, y=500, x1=100):
    return Node(text, x1, y, x1 + 200, y + 80)


class _ScreenUI:
    """按「当前屏」返回节点的假 UI；tap 由测试脚本推进到下一屏。"""

    def __init__(self, nodes):
        self.cur = list(nodes)
        self.dump_calls = 0

    def nodes(self, timeout=None):
        self.dump_calls += 1
        return list(self.cur)

    def find(self, text, ymin=0, ymax=99999, xmin=0, xmax=99999, timeout=None):
        for n in self.cur:
            if n.text == text:
                return n
        return None


LANDING = [nd("小游戏体验", 0, x1=0), nd("返回", 200, x1=60),
           nd("还需体验 15 秒", 200, x1=690), nd("点击进入", 1300, x1=174)]
AUTH = [nd("授权使用微信小程序", 120, x1=333), nd("返回", 120, x1=0),
        nd("去官网下载", 1800, x1=457)]
REWARD = [nd("返回", 200, x1=60), nd("恭喜获得奖励", 960),
          nd("已完成试玩 15 秒，提前获得奖励", 1100),
          nd("继续了解详情", 1500, x1=216)]
TC = [nd("收支详情", 120), nd("每日签到", 1218),
      nd("看视频赚电量", 1653)]


class TestMinigameAdFlow(unittest.TestCase):

    def _f(self, states):
        f = make_flow({"ad_minigame": True})
        ui = _ScreenUI(LANDING)
        f.ui = ui
        tapped = []
        queue = list(states)

        def _tap(node, pause=None):
            tapped.append(node.text)
            if queue:
                ui.cur = list(queue.pop(0))

        f._tap_node = mock.Mock(side_effect=_tap)
        f._close_ad = mock.Mock(return_value=True)
        f._prefetch_ad_close_pos = mock.Mock()
        return f, ui, tapped

    def _run(self, f):
        with mock.patch.object(flow_mod.time, "sleep", lambda s: None):
            return f._play_minigame_ad(f._minigame_cta_node())

    def test_full_chain_taps_cta_then_two_backs(self):
        """点「点击进入」→ 授权页 返回 → 奖励页 返回 → 任务中心。"""
        f, ui, tapped = self._f([AUTH, REWARD, TC])
        self.assertTrue(self._run(f))
        self.assertEqual(tapped, ["点击进入", "返回", "返回"])
        # N2（2026-10-22）：退出时已 dump 确认回任务中心 → 不该再跑 _close_ad
        f._close_ad.assert_not_called()
        self.assertEqual(f._prefetch_close, None)

    def test_never_taps_continue_details(self):
        """`继续了解详情`/`去官网下载` 绝不能点（会跳外部页）。"""
        f, ui, tapped = self._f([AUTH, REWARD, TC])
        self._run(f)
        for bad in ("继续了解详情", "去官网下载", "点击卡片下载游戏"):
            self.assertNotIn(bad, tapped)

    def test_cta_tap_swallowed_retaps_once(self):
        """「点击进入」被吞（仍停在落地页）→ 补点一次，不整支作废。"""
        f, ui, tapped = self._f([LANDING, AUTH, REWARD, TC])
        self.assertTrue(self._run(f))
        self.assertEqual(tapped.count("点击进入"), 2)
        self.assertEqual(tapped[-1], "返回")

    def test_no_back_node_stops_without_spam(self):
        """屏上没有「返回」时不得盲按 BACK / 乱点，直接交 _close_ad 收尾。"""
        auth_no_back = [nd("授权使用微信小程序", 120, x1=333),
                        nd("去官网下载", 1800, x1=457)]
        f, ui, tapped = self._f([auth_no_back])
        self.assertTrue(self._run(f))
        self.assertEqual(tapped, ["点击进入"])
        f._close_ad.assert_called_once()

    def test_backs_bounded_by_knob(self):
        """「返回」次数受 ad_minigame_backs 封顶（防无限退出）。"""
        f, ui, tapped = self._f([AUTH, REWARD, REWARD, REWARD, REWARD])
        f.wf["ad_minigame_backs"] = 2
        self._run(f)
        self.assertEqual(tapped.count("返回"), 2)

    def test_close_fail_returns_false_when_landing_unconfirmed(self):
        """退出过程**没能** dump 确认回任务中心 → 仍走 _close_ad，失败则返回 False。"""
        f, ui, tapped = self._f([AUTH, REWARD, REWARD, REWARD])
        f._close_ad = mock.Mock(return_value=False)
        self.assertFalse(self._run(f))
        f._close_ad.assert_called_once()


class TestMinigameSkipClose(unittest.TestCase):
    """N2（2026-10-22）：试玩退出已 dump 确认 → 跳过冗余 _close_ad。

    背景（10-22 觅夏 9 支真机实测）：链路末尾已打「已回到任务中心（dump 确认）」，
    代码却又完整跑一遍 _close_ad —— 没有广告可关，top-strip OCR 读不到 →
    兜底轮询，白花 4.3~13.3s（p50 4.50 / 均值 7.19）。
    """

    def _f(self, states, extra=None):
        wf = {"ad_minigame": True}
        wf.update(extra or {})
        f = make_flow(wf)
        ui = _ScreenUI(LANDING)
        f.ui = ui
        tapped = []
        queue = list(states)

        def _tap(node, pause=None):
            tapped.append(node.text)
            if queue:
                ui.cur = list(queue.pop(0))

        f._tap_node = mock.Mock(side_effect=_tap)
        f._close_ad = mock.Mock(return_value=True)
        f._prefetch_ad_close_pos = mock.Mock()
        return f, ui, tapped

    def _run(self, f):
        with mock.patch.object(flow_mod.time, "sleep", lambda s: None):
            return f._play_minigame_ad(f._minigame_cta_node())

    def test_confirmed_tc_skips_close_ad(self):
        """dump 确认回任务中心 → 一次 _close_ad 都不跑，且直接判成功。"""
        f, _, _ = self._f([AUTH, REWARD, TC])
        self.assertTrue(self._run(f))
        f._close_ad.assert_not_called()

    def test_skip_clears_suspicious_flag(self):
        """跳过关闭链 ≠ 可疑路径：必须把 _ad_close_suspicious 置 False，
        否则会触发收尾的配额复核（多一次读屏）。"""
        f, _, _ = self._f([AUTH, REWARD, TC])
        f._ad_close_suspicious = True          # 上一支遗留的脏状态
        self._run(f)
        self.assertFalse(f._ad_close_suspicious)

    def test_skip_logs_zero_timer(self):
        """日志仍打「试玩后关闭确认 0.0s」+ 显式标注跳过 —— 便于日志分析口径连续。"""
        f, _, _ = self._f([AUTH, REWARD, TC])
        with self.assertLogs("flow", level="INFO") as cm:
            self._run(f)
        joined = "\n".join(cm.output)
        self.assertIn("试玩后关闭确认 0.0s", joined)
        self.assertIn("跳过冗余关闭链", joined)

    def test_knob_off_restores_close_ad(self):
        """ad_minigame_skip_close: false → 一键回退旧链路。"""
        f, _, _ = self._f([AUTH, REWARD, TC],
                          extra={"ad_minigame_skip_close": False})
        self.assertTrue(self._run(f))
        f._close_ad.assert_called_once()

    def test_unconfirmed_landing_keeps_close_ad(self):
        """退出 3 次仍停在奖励页 → 不确认 → 必须交 _close_ad 兜底。"""
        f, _, tapped = self._f([AUTH, REWARD, REWARD, REWARD])
        self.assertTrue(self._run(f))
        f._close_ad.assert_called_once()
        self.assertNotIn("关闭广告", tapped)


class TestMinigameProbe(unittest.TestCase):
    """版式探针：命中才分流，普通广告行为与时长必须完全不变。"""

    def _run(self, screen_nodes, wf_extra=None):
        clock = _Clock()
        f = make_flow(wf_extra)
        f.wf["ad_minigame"] = True
        f.ui = _ScreenUI(screen_nodes)
        f._prefetch_ad_close_pos = mock.Mock()
        seen = {"closes": 0}

        def _close(**kw):
            seen["closes"] += 1
            seen["elapsed"] = clock.t - start
            return True

        f._close_ad = _close
        f._play_minigame_ad = mock.Mock(return_value=True)
        start = clock.t
        with mock.patch.object(flow_mod, "time", clock.ns), \
                mock.patch.object(flow_mod.random, "uniform",
                                  lambda a, b: 16.0):
            f._ad_play_and_close()
        return seen, f

    def test_cta_hit_routes_to_minigame(self):
        seen, f = self._run(LANDING)
        f._play_minigame_ad.assert_called_once()
        self.assertEqual(seen["closes"], 0)        # 不走原关闭链

    def test_normal_ad_still_exact_wait(self):
        """普通广告：探针耗时被吸收，总时长仍严格 = wait（回归 P0-a）。"""
        normal = [nd("关闭广告", 150), nd("倒计时 14s", 150),
                  nd("点击卡片下载游戏", 900)]
        seen, f = self._run(normal)
        self.assertAlmostEqual(seen["elapsed"], 16.0, places=2)
        f._play_minigame_ad.assert_not_called()

    def test_normal_ad_not_confused_by_download_cta(self):
        """「点击卡片下载游戏」「点击卡片立即安装」「点击进入详情页下载」
        都不得被判成试玩版式（双特征：还必须有页面标识词）。"""
        for txt in ("点击卡片下载游戏", "点击卡片立即安装", "点击进入详情页下载"):
            _, f = self._run([nd("关闭广告", 150), nd(txt, 900)])
            f._play_minigame_ad.assert_not_called()

    def test_cta_without_page_marker_is_ignored(self):
        """只有「点击进入」、没有页面标识词 → 不认（防普通广告误判）。"""
        _, f = self._run([nd("关闭广告", 150), nd("点击进入", 900)])
        f._play_minigame_ad.assert_not_called()

    def test_page_marker_without_cta_is_ignored(self):
        """奖励页/残留：有小游戏标识但已无「点击进入」→ 不重复触发。"""
        _, f = self._run([nd("小游戏体验", 0, x1=0),
                          nd("恭喜获得奖励", 960)])
        f._play_minigame_ad.assert_not_called()

    def test_knob_off_skips_probe_entirely(self):
        """ad_minigame: false → 一次 dump 都不多做（一键回退）。"""
        f = make_flow({"ad_minigame": False})
        ui = _ScreenUI(LANDING)
        f.ui = ui
        f._prefetch_ad_close_pos = mock.Mock()
        f._close_ad = mock.Mock(return_value=True)
        f._play_minigame_ad = mock.Mock(return_value=True)
        clock = _Clock()
        with mock.patch.object(flow_mod, "time", clock.ns), \
                mock.patch.object(flow_mod.random, "uniform",
                                  lambda a, b: 16.0):
            f._ad_play_and_close()
        self.assertEqual(ui.dump_calls, 0)
        f._play_minigame_ad.assert_not_called()


class TestAdOpenGuard(unittest.TestCase):
    """N4-a（2026-10-22）：点开广告后校验「广告页真的打开了」。

    背景：探针连续复现「_watch_ad_once 返回 True / 可疑=True / 配额不动」——
    广告行点了根本没打开广告（无 fill / 点击被吞），_close_ad 的 OCR 守卫
    看到任务中心就返回成功，于是白等满一个 16s 窗口。
    """

    TC_SCREEN = [nd("收支详情", 120), nd("每日签到", 1218),
                 nd("看视频赚电量", 1653)]
    AD_SCREEN = [nd("关闭广告", 104, x1=32), nd("倒计时 14s", 104, x1=702)]

    def _run(self, nodes, wf_extra=None, wait=16.0):
        clock = _Clock()
        f = make_flow({"ad_open_guard": True, "ad_open_guard_at": [4.0, 6.0],
                       "ad_prefetch_pos": False, "ad_countdown_wait": False})
        f.wf.update(wf_extra or {})
        f.ui = _ScreenUI(nodes)
        f._prefetch_ad_close_pos = mock.Mock()
        f._close_ad = mock.Mock(return_value=True)
        with mock.patch.object(flow_mod, "time", clock.ns), \
                mock.patch.object(flow_mod.random, "uniform",
                                  lambda a, b: wait):
            start = clock.t
            ok = f._ad_play_and_close()
            elapsed = clock.t - start
        return f, ok, elapsed

    def test_two_tc_dumps_means_not_opened(self):
        """两次都稳定读到任务中心 → 判没打开，立刻返回 False（不空等 16s）。"""
        f, ok, elapsed = self._run(self.TC_SCREEN)
        self.assertFalse(ok)
        self.assertTrue(f._ad_not_opened)
        self.assertLess(elapsed, 7.0)       # 只花了两个探针点，没等满 16s
        f._close_ad.assert_not_called()

    def test_ad_page_seen_proceeds_and_exact_wait(self):
        """看到广告页特征 → 正常往下走，且总时长仍严格 = wait。"""
        f, ok, elapsed = self._run(self.AD_SCREEN)
        self.assertTrue(ok)
        self.assertFalse(f._ad_not_opened)
        self.assertAlmostEqual(elapsed, 16.0, places=2)

    def test_unknown_screen_is_treated_as_opened(self):
        """dump 读不到东西（加载中/超时）→ 不确定 → 按已打开处理，绝不误判。"""
        f, ok, elapsed = self._run([])
        self.assertTrue(ok)
        self.assertFalse(f._ad_not_opened)
        self.assertAlmostEqual(elapsed, 16.0, places=2)

    def test_knob_off_skips_guard(self):
        f, ok, elapsed = self._run(self.TC_SCREEN,
                                   {"ad_open_guard": False})
        self.assertTrue(ok)
        self.assertFalse(f._ad_not_opened)
        self.assertAlmostEqual(elapsed, 16.0, places=2)


class TestAdOpenRetry(unittest.TestCase):
    """N4-c：判定没打开 → 重取行节点补点一次（与「发消息」被吞同源）。"""

    def _f(self, results):
        f = make_flow()
        f.wf.update({"ad_times_per_robot": 10})
        f.ui = _ScreenUI([])
        f._find_row = mock.Mock(
            return_value=(mock.Mock(), mock.Mock(), (2, 10)))
        f._tap_node = mock.Mock()
        seq = list(results)

        def _pac(**kw):
            r = seq.pop(0)
            f._ad_not_opened = r[1]
            return r[0]

        f._ad_play_and_close = _pac
        return f

    def test_retries_once_and_succeeds(self):
        f = self._f([(False, True), (True, False)])
        self.assertTrue(f._watch_ad_once())
        self.assertEqual(f._tap_node.call_count, 2)   # 补点一次

    def test_no_retry_when_flag_not_set(self):
        """只是失败、并非「没打开」→ 不补点（避免重复开广告）。"""
        f = self._f([(False, False)])
        self.assertFalse(f._watch_ad_once())
        self.assertEqual(f._tap_node.call_count, 1)

    def test_gives_up_when_row_gone(self):
        """补点时已找不到广告行（页面已变）→ 放弃，不盲点。"""
        f = self._f([(False, True)])
        f._find_row = mock.Mock(side_effect=[
            (mock.Mock(), mock.Mock(), (2, 10)), None])
        self.assertFalse(f._watch_ad_once())
        self.assertEqual(f._tap_node.call_count, 1)


class TestAdCountdownWait(unittest.TestCase):
    """N4-b：顶条还挂着「倒计时 Ns」就关 → 该支白看，故先补等。"""

    def _run(self, nodes, wf_extra=None, wait=16.0):
        clock = _Clock()
        f = make_flow({"ad_open_guard": False, "ad_countdown_wait": True,
                       "ad_prefetch_pos": False})
        f.wf.update(wf_extra or {})
        f.ui = _ScreenUI(nodes)
        f._prefetch_ad_close_pos = mock.Mock()
        f._close_ad = mock.Mock(return_value=True)
        with mock.patch.object(flow_mod, "time", clock.ns), \
                mock.patch.object(flow_mod.random, "uniform",
                                  lambda a, b: wait):
            start = clock.t
            ok = f._ad_play_and_close()
            elapsed = clock.t - start
        return f, ok, elapsed

    def test_countdown_left_parsing(self):
        f = make_flow()
        f.ui = _ScreenUI([nd("倒计时5s", 104), nd("倒计时 12s", 104),
                          nd("关闭广告", 104)])
        self.assertEqual(f._ad_countdown_left(), 12)      # 取最大
        f.ui = _ScreenUI([nd("关闭广告", 104)])
        self.assertEqual(f._ad_countdown_left(), 0)

    def test_countdown_extends_wait(self):
        _, ok, elapsed = self._run([nd("关闭广告", 104, x1=32),
                                    nd("倒计时5s", 104, x1=702)])
        self.assertTrue(ok)
        self.assertAlmostEqual(elapsed, 16.0 + 5.6, places=1)

    def test_no_countdown_keeps_exact_wait(self):
        _, ok, elapsed = self._run([nd("关闭广告", 104, x1=32)])
        self.assertTrue(ok)
        self.assertAlmostEqual(elapsed, 16.0, places=2)

    def test_countdown_capped_by_knob(self):
        _, ok, elapsed = self._run(
            [nd("倒计时 40s", 104, x1=702)],
            {"ad_countdown_max": 12})
        self.assertTrue(ok)
        self.assertAlmostEqual(elapsed, 16.0 + 12.6, places=1)

    def test_knob_off_no_extension(self):
        _, ok, elapsed = self._run([nd("倒计时5s", 104, x1=702)],
                                   {"ad_countdown_wait": False})
        self.assertTrue(ok)
        self.assertAlmostEqual(elapsed, 16.0, places=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)