"""adb_ui.py nodes() 0.8s TTL 缓存的静态回归测试（mock 驱动，不依赖真机/adb）。

覆盖（对应改动 B 的验证点）：
1. TTL 内重复 nodes()/find() 命中缓存（dump 只执行 1 次）
2. 超过/达到 TTL(=0.8s) 后自动失效，重新 dump
3. dump 失败返回 [] 且【不写缓存】（避免空结果掩盖真实页面）
4. tap / swipe_up / swipe_down / back 四个动作后 _invalidate_cache() 生效
5. include_desc=True/False 使用各自独立缓存，互不污染（desc 版不会误用于非 desc）
6. 紧邻连续 find（如 _back_at_taskcenter 里 `find(A) or find(B)`）只 dump 一次
7. wait_for 轮询 interval=1.0s > TTL=0.8s ⇒ 每次轮询都重新 dump，绝不读旧缓存
8. 2026-09-10 看门狗：subprocess.run 全部带超时，超时转 AdbCommandError /
   dump_fail_streak（防 adb 挂起导致无人值守流程永久卡住）

运行：py -3.13 -m unittest test_adb_cache -v
"""
import subprocess
import unittest
from unittest import mock

import adb_ui as adb_ui_mod
from adb_ui import AdbUI

# 最小可被 _TEXT_RE/_DESC_RE 匹配的 XML 片段
XML_TEXT_SIGNIN = ('<node index="0" text="每日签到" class="x" '
                   'bounds="[0,100][200,140]"/>')
XML_TEXT_TASK = ('<node index="0" text="任务中心" class="x" '
                 'bounds="[0,200][200,240]"/>')
XML_MIX = ('<node index="0" text="每日签到" class="x" '
           'bounds="[0,100][200,140]"/>'
           '<node index="1" content-desc="关闭广告" class="x" '
           'bounds="[100,100][240,140]"/>')


class _FakeClock:
    """可控时钟，替代 time.time；advance() 推进时间。"""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def _texts(nodes) -> list:
    return [n.text for n in nodes]


class AdbCacheBase(unittest.TestCase):
    def setUp(self):
        self.clock = _FakeClock()
        self.sleeps = []

        # 时钟可控 + sleep 同步推进时钟（模拟真实时间流逝）
        self._t = mock.patch("adb_ui.time.time", self.clock)
        self._t.start()
        self.addCleanup(self._t.stop)

        def _fake_sleep(s):
            self.sleeps.append(s)
            self.clock.advance(s)

        self._s = mock.patch("adb_ui.time.sleep", _fake_sleep)
        self._s.start()
        self.addCleanup(self._s.stop)

        self.ui = AdbUI()
        # dump 打桩：每次调用计数，返回当前 xml（None 模拟 dump 失败）
        self.xml = None
        self.dump_calls = 0

        def _dump(timeout=None):
            self.dump_calls += 1
            return self.xml

        self.ui.dump = _dump
        # 动作方法里的 adb 调用打桩（不发真命令）
        self.ui._run = mock.Mock(return_value="")


class TestCacheHitExpiry(AdbCacheBase):
    def test_second_call_within_ttl_hits_cache(self):
        self.xml = XML_TEXT_SIGNIN
        self.assertEqual(_texts(self.ui.nodes()), ["每日签到"])
        self.assertEqual(self.dump_calls, 1)
        self.clock.advance(0.79)          # < 0.8 → 命中
        self.assertEqual(_texts(self.ui.nodes()), ["每日签到"])
        self.assertEqual(self.dump_calls, 1)

    def test_advance_beyond_ttl_expires(self):
        # 命中条件 now - ts < 0.8。0.8 在二进制浮点里不精确（0.8 可能算出
        # 0.7999… < 0.8），故用略大于 TTL 的步进来验证"过期后重新 dump"。
        self.xml = XML_TEXT_SIGNIN
        self.ui.nodes()
        self.clock.advance(0.79)         # 未到 TTL → 命中
        self.ui.nodes()
        self.assertEqual(self.dump_calls, 1)
        self.clock.advance(0.02)         # 累计 0.81 > 0.8 → 过期
        self.ui.nodes()
        self.assertEqual(self.dump_calls, 2)

    def test_expired_cache_rereads_new_screen(self):
        self.xml = XML_TEXT_SIGNIN
        self.ui.nodes()
        self.xml = XML_TEXT_TASK            # 屏幕内容已变化
        self.clock.advance(0.9)             # 超过 TTL
        self.assertEqual(_texts(self.ui.nodes()), ["任务中心"])
        self.assertEqual(self.dump_calls, 2)


class TestDumpFailureNoCache(AdbCacheBase):
    def test_dump_failure_returns_empty_and_does_not_cache(self):
        # 1) dump 失败 → [] 且不得写缓存
        self.xml = None
        self.assertEqual(self.ui.nodes(), [])
        self.assertEqual(self.dump_calls, 1)
        # 2) 紧接 0.4s 内 dump 恢复且屏幕有内容 → 必须重新 dump 并返回真实节点
        #    （若空结果被缓存，第二次会命中 [] 而不再 dump）
        self.xml = XML_TEXT_SIGNIN
        self.clock.advance(0.4)
        self.assertEqual(_texts(self.ui.nodes()), ["每日签到"])
        self.assertEqual(self.dump_calls, 2)


class TestInvalidation(AdbCacheBase):
    def _prime_cache(self):
        self.xml = XML_TEXT_SIGNIN
        self.ui.nodes()
        self.assertEqual(self.dump_calls, 1)

    def test_tap_invalidates(self):
        self._prime_cache()
        self.ui.tap(540, 1000, pause=1.2)
        self.assertEqual(self.dump_calls, 1)     # tap 本身不 dump
        self.ui.nodes()
        self.assertEqual(self.dump_calls, 2)     # 缓存已被 tap 清空 → 重新 dump

    def test_swipe_up_invalidates(self):
        self._prime_cache()
        self.ui.swipe_up(pause=1.0)
        self.ui.nodes()
        self.assertEqual(self.dump_calls, 2)

    def test_swipe_down_invalidates(self):
        self._prime_cache()
        self.ui.swipe_down(pause=1.0)
        self.ui.nodes()
        self.assertEqual(self.dump_calls, 2)

    def test_back_invalidates(self):
        self._prime_cache()
        self.ui.back(pause=1.2)
        self.ui.nodes()
        self.assertEqual(self.dump_calls, 2)


class TestIncludeDescSeparation(AdbCacheBase):
    def test_true_and_false_do_not_share_cache(self):
        self.xml = XML_MIX
        # include_desc=True：text + desc 都进结果
        self.assertEqual(_texts(self.ui.nodes(True)),
                         ["每日签到", "关闭广告"])
        self.assertEqual(self.dump_calls, 1)
        # include_desc=False：0.3s 内调用，若误用 True 版缓存会直接返回含 desc 列表；
        # 正确实现应重新 dump 且只含 text 节点
        self.clock.advance(0.3)
        self.assertEqual(_texts(self.ui.nodes(False)), ["每日签到"])
        self.assertEqual(self.dump_calls, 2)
        # False 结果再命中 False 缓存
        self.assertEqual(_texts(self.ui.nodes(False)), ["每日签到"])
        self.assertEqual(self.dump_calls, 2)
        # 回到 True：缓存 flag 是 False，不能命中 → 重新 dump 恢复含 desc
        self.clock.advance(0.3)
        self.assertEqual(_texts(self.ui.nodes(True)),
                         ["每日签到", "关闭广告"])
        self.assertEqual(self.dump_calls, 3)

    def test_include_desc_false_excludes_desc_only_nodes(self):
        self.xml = XML_MIX
        self.assertEqual(_texts(self.ui.nodes(include_desc=False)),
                         ["每日签到"])


class TestFlowPatterns(AdbCacheBase):
    def test_back_to_back_find_shares_one_dump(self):
        # 模拟 _back_at_taskcenter / _scroll_to_top 的 `find(A) or find(B)`
        self.xml = XML_TEXT_TASK
        self.assertIsNone(self.ui.find("每日签到"))      # miss（dump 1）
        self.assertIsNotNone(self.ui.find("任务中心"))    # 命中（应复用缓存）
        self.assertEqual(self.dump_calls, 1)

    def test_find_then_nodes_loop_shares_one_dump(self):
        # 模拟 _close_ad step1：_find("关闭广告") 后 for cand in ui.nodes()
        self.xml = XML_TEXT_SIGNIN
        self.ui.find("每日签到")
        self.assertEqual(_texts(self.ui.nodes()), ["每日签到"])
        self.assertEqual(self.dump_calls, 1)

    def test_wait_for_poll_refreshes_each_second(self):
        # wait_for interval=1.0 > TTL=0.8 ⇒ 每次轮询都重新 dump，绝不读旧缓存。
        # 若有人把 TTL 提到 >=1.0，本测试会失败（轮询读到上一轮缓存）。
        self.xml = XML_TEXT_TASK   # 页面里没有 发消息
        n = self.ui.wait_for("发消息", retries=3, interval=1.0)
        self.assertIsNone(n)
        self.assertEqual(self.dump_calls, 3)   # 每轮一次，共 3 次

    def test_wait_gone_poll_refreshes_each_second(self):
        self.xml = XML_TEXT_SIGNIN
        # 文本一直在 → 每轮都重新 dump 确认，最终 False
        self.assertFalse(self.ui.wait_gone("每日签到", retries=3, interval=1.0))
        self.assertEqual(self.dump_calls, 3)


# ---------------------------------------------------------------------------
# 2026-09-10 看门狗：adb 子进程超时
# ---------------------------------------------------------------------------
class TestSubprocessTimeout(unittest.TestCase):
    """adb / 模拟器卡死防护。

    背景：本工具无人值守跑 10 台 × 广告轮转可达 2 小时以上，而原先除
    is_online 外所有 subprocess.run 都没传 timeout —— adb daemon 一旦挂起，
    进程会永久阻塞、日志停在原地，第二天才发现整晚没跑完。
    """

    def test_run_timeout_raises_adb_command_error(self):
        # _run 是 tap / swipe / back / cat 的通用入口 —— 超时必须转成
        # AdbCommandError（上层已有 except 分支，可直接走既有恢复逻辑）
        ui = AdbUI()
        with mock.patch.object(adb_ui_mod.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("adb", 20)):
            with self.assertRaises(adb_ui_mod.AdbCommandError):
                ui._run("shell", "input", "tap", "10", "10")

    def test_tap_timeout_propagates(self):
        ui = AdbUI()
        with mock.patch.object(adb_ui_mod.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("adb", 20)):
            with self.assertRaises(adb_ui_mod.AdbCommandError):
                ui.tap(100, 200)

    def test_dump_timeout_increments_dump_fail_streak(self):
        # dump 超时 → 内部重试耗尽 → 计数 +1、返回 None。上层据此判断页面
        # 卡死走恢复；视频广告期的"UI 无法 idle"也靠这条路径快速失败。
        ui = AdbUI()
        ui._dump_fail_streak = 0
        with mock.patch.object(adb_ui_mod.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("adb", 4)):
            self.assertIsNone(ui.dump())
        self.assertEqual(ui.dump_fail_streak, 1)

    def test_dump_timeout_shorter_than_cmd_timeout(self):
        # dump 必须比普通命令更快失败（视频广告期 UI 不 idle，uiautomator
        # 会空等 ~12s，这里主动掐短让流程尽快转 OCR 路径）
        self.assertLess(adb_ui_mod.DUMP_TIMEOUT, adb_ui_mod.CMD_TIMEOUT)
        ui = AdbUI()
        ui._dump_fail_streak = 0
        with mock.patch.object(adb_ui_mod.subprocess, "run") as run:
            run.return_value.stdout = b"UI hierchary dumped to: /sdcard/ui.xml"
            run.return_value.returncode = 0
            ui.dump()
        self.assertEqual(run.call_args_list[0].kwargs.get("timeout"),
                         adb_ui_mod.DUMP_TIMEOUT)


# ---------------------------------------------------------------------------
# 2026-09-13 自愈：adb server 被后台回收时 is_online 自动重启找回设备
# ---------------------------------------------------------------------------
class _Proc:
    """最小 subprocess.CompletedProcess 替身。"""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestIsOnlineSelfHeal(unittest.TestCase):
    """背景：环境里 adb daemon 会被回收 → `adb devices` 为空，设备明明在线
    却被判离线。is_online 现在两级探活：常规 3 次失败后重启 adb server 再探
    2 次，全失败才判真离线（emulator-5554 走 QEMU 底层入口可自动重连）。
    """

    def setUp(self):
        self.ui = AdbUI()
        # sleep 打桩，测试瞬时完成
        self._s = mock.patch("adb_ui.time.sleep", lambda s: None)
        self._s.start()
        self.addCleanup(self._s.stop)

    def test_first_probe_ok_does_not_restart(self):
        # 成功路径零开销：首次探活成功，绝不触发 kill-server/start-server
        def fake_run(cmd, capture_output=False, timeout=None):
            cmd = list(cmd)
            assert "get-state" in cmd, f"不应执行非探活命令: {cmd}"
            return _Proc(0, b"device\n")

        with mock.patch.object(adb_ui_mod.subprocess, "run",
                               side_effect=fake_run):
            self.assertTrue(self.ui.is_online())

    def test_self_heal_restarts_adb_and_recovers(self):
        # adb server 被回收：前 3 次 get-state 全失败 → 重启后恢复在线
        state = {"restarted": False, "kills": 0}

        def fake_run(cmd, capture_output=False, timeout=None):
            cmd = list(cmd)
            if "kill-server" in cmd:
                state["kills"] += 1
                return _Proc(0)
            if "start-server" in cmd:
                state["restarted"] = True
                return _Proc(0)
            # get-state
            if state["restarted"]:
                return _Proc(0, b"device\n")
            return _Proc(1, b"error: device not found\n")

        with mock.patch.object(adb_ui_mod.subprocess, "run",
                               side_effect=fake_run):
            self.assertTrue(self.ui.is_online())
        self.assertEqual(state["kills"], 1)   # 只重启一次，不无限重试

    def test_offline_after_heal_returns_false(self):
        # 重启后依然不在线 → 判定真离线（模拟器真没开）
        def fake_run(cmd, capture_output=False, timeout=None):
            return _Proc(1, b"error\n")

        with mock.patch.object(adb_ui_mod.subprocess, "run",
                               side_effect=fake_run):
            self.assertFalse(self.ui.is_online())


if __name__ == "__main__":
    unittest.main()
