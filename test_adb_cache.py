"""adb_ui.py nodes() 0.8s TTL 缓存的静态回归测试（mock 驱动，不依赖真机/adb）。

覆盖（对应改动 B 的验证点）：
1. TTL 内重复 nodes()/find() 命中缓存（dump 只执行 1 次）
2. 超过/达到 TTL(=0.8s) 后自动失效，重新 dump
3. dump 失败返回 [] 且【不写缓存】（避免空结果掩盖真实页面）
4. tap / swipe_up / swipe_down / back 四个动作后 _invalidate_cache() 生效
5. include_desc=True/False 使用各自独立缓存，互不污染（desc 版不会误用于非 desc）
6. 紧邻连续 find（如 _back_at_taskcenter 里 `find(A) or find(B)`）只 dump 一次
7. wait_for 轮询 interval=1.0s > TTL=0.8s ⇒ 每次轮询都重新 dump，绝不读旧缓存

运行：py -3.13 -m unittest test_adb_cache -v
"""
import unittest
from unittest import mock

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

        def _dump():
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


if __name__ == "__main__":
    unittest.main()
