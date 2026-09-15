"""U2Backend 单测（阶段2 2026-09-15）：u2 感知层与 AdbUI 的行为契约。

覆盖：
- u2 dump XML（标准 uiautomator 格式，含状态栏节点）-> _parse_xml -> Node
  字段/selected 正确（解析口径与 adb 路径共用，重点验证格式兼容）
- dump 成功/空串/异常 三态下的 dump_fail_streak 语义（供 flow._dump_stuck
  与 OCR 兜底判定，必须与基类一致）
- nodes() TTL 缓存：同屏二次 nodes() 不得二次 dump（调用计数）
- find/tap 行为契约继承：find 精确文本命中、tap 仍走 adb input（_run 捕获）
- stop_agent 幂等；U2AdbUI 构造零连接（懒 agent，不碰真机）

所有用例 mock 掉 _agent()，不依赖设备/网络。
"""
import unittest
from unittest import mock

from adb_ui import Node
from adb_u2 import U2AdbUI


def _xml(*nodes_xml):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
            '<hierarchy rotation="0">'
            + "".join(nodes_xml)
            + "</hierarchy>")


def _node_tag(text, bounds, sel=False, desc=""):
    return ('<node index="0" package="com.tencent.mobileqq" '
            'class="android.widget.TextView" text="{}" content-desc="{}" '
            'checkable="false" clickable="false" selected="{}" '
            'bounds="{}"/>'.format(text, desc, "true" if sel else "false", bounds))


# 现实样张：状态栏(时间) + 底部 tab(联系人 selected) + 机器人行 + 分类行
U2_LIST_XML = _xml(
    _node_tag("18:44", "[12,11][80,40]"),                       # 状态栏（u2 才有）
    _node_tag("联系人", "[400,1875][600,1920]", sel=True),       # 底部 tab
    _node_tag("机器人", "[677,440][877,530]", sel=True),         # 中部分类行
    _node_tag("代柯", "[183,924][383,1004]"),                    # 机器人行
    _node_tag("尔尔", "[183,1040][383,1120]"),
)


class _FakeAgent:
    """可编程 fake：dump_hierarchy 按脚本出牌，并统计调用次数。"""

    def __init__(self, results):
        self.results = list(results)     # 每次 dump 弹出一个：str / Exception / None
        self.calls = 0

    def dump_hierarchy(self):
        self.calls += 1
        r = self.results.pop(0) if self.results else U2_LIST_XML
        if isinstance(r, Exception):
            raise r
        return r


def _make_u2(tc, results):
    """构造 U2AdbUI 并把 _agent 补丁挂到用例的 cleanup 上（tc=TestCase）。"""
    ui = U2AdbUI("emulator-5554")
    agent = _FakeAgent(results)
    p = mock.patch.object(U2AdbUI, "_agent", return_value=agent)
    p.start()
    tc.addCleanup(p.stop)
    ui._fake_agent = agent
    return ui


class TestU2ParseCompat(unittest.TestCase):
    """u2 dump_hierarchy 的 XML 与 adb 路径同格式 -> 解析口径共用后字段正确。"""

    def test_parse_fields_and_selected(self):
        nodes = _parse_xml_compat(U2_LIST_XML)
        by_text = {n.text: n for n in nodes}
        self.assertIn("代柯", by_text)
        n = by_text["代柯"]
        self.assertEqual((n.x1, n.y1, n.x2, n.y2), (183, 924, 383, 1004))
        self.assertTrue(by_text["联系人"].selected)
        self.assertTrue(by_text["机器人"].selected)
        self.assertFalse(n.selected)

    def test_statusbar_nodes_present_but_filterable(self):
        # 状态栏节点（u2 特有）会被解析出来 —— flow 的行过滤带 y1>=350 等条件天然排除；
        # 这里只验证解析层不吞节点（与探针实测 20+4 行为一致）。
        nodes = _parse_xml_compat(U2_LIST_XML)
        texts = [n.text for n in nodes]
        self.assertIn("18:44", texts)
        self.assertLess(next(n for n in nodes if n.text == "18:44").y1, 50)


def _parse_xml_compat(xml):
    from adb_ui import _parse_xml
    return _parse_xml(xml, True)


class TestU2DumpSemantics(unittest.TestCase):
    """dump 三态 -> dump_fail_streak 语义与基类一致（flow._dump_stuck 依赖）。"""

    def test_success_resets_streak(self):
        ui = _make_u2(self, [Exception("x"), U2_LIST_XML])
        self.assertIsNone(ui.dump())            # 失败 -> None
        self.assertEqual(ui.dump_fail_streak, 1)
        self.assertIsNotNone(ui.dump())         # 成功
        self.assertEqual(ui.dump_fail_streak, 0)

    def test_empty_xml_counts_as_fail(self):
        ui = _make_u2(self, ["", U2_LIST_XML])
        self.assertIsNone(ui.dump())
        self.assertEqual(ui.dump_fail_streak, 1)
        self.assertIsNotNone(ui.dump())
        self.assertEqual(ui.dump_fail_streak, 0)

    def test_exception_returns_none(self):
        ui = _make_u2(self, [RuntimeError("device offline")])
        self.assertIsNone(ui.dump())
        self.assertEqual(ui.dump_fail_streak, 1)


class TestU2NodesCache(unittest.TestCase):
    """nodes() TTL 缓存继承：同屏二次 nodes() 不得二次 dump。"""

    def test_second_nodes_call_uses_cache(self):
        ui = _make_u2(self, [U2_LIST_XML])
        ns1 = ui.nodes()
        ns2 = ui.nodes()
        self.assertEqual(ui._fake_agent.calls, 1)   # 仅 1 次 dump
        self.assertEqual(len(ns1), len(ns2))

    def test_refresh_forces_redump(self):
        ui = _make_u2(self, [U2_LIST_XML, U2_LIST_XML])
        ui.nodes()
        ui.refresh()
        ui.nodes()
        self.assertEqual(ui._fake_agent.calls, 2)

    def test_dump_failure_returns_empty_no_cache(self):
        # 失败 -> nodes() 返回 [] 且不写缓存（与基类契约一致）
        ui = _make_u2(self, [RuntimeError("boom"), U2_LIST_XML])
        self.assertEqual(ui.nodes(), [])
        ns = ui.nodes()
        self.assertGreater(len(ns), 0)
        self.assertEqual(ui._fake_agent.calls, 2)


class TestU2InheritedContract(unittest.TestCase):
    """find/tap 行为契约继承：flow.py 零改动可用的最低要求。"""

    def test_find_exact_text(self):
        ui = _make_u2(self, [U2_LIST_XML])
        n = ui.find("代柯")
        self.assertIsInstance(n, Node)
        self.assertEqual((n.x1, n.y1), (183, 924))
        self.assertIsNone(ui.find("不存在的机器人"))

    def test_find_ignores_statusbar_short_text(self):
        # 状态栏 "18:44" 不应干扰业务文本查找（y 区域限定 + 精确匹配）
        ui = _make_u2(self, [U2_LIST_XML])
        self.assertIsNone(ui.find("18:44", ymin=350))

    def test_tap_still_uses_adb_input(self):
        ui = _make_u2(self, [U2_LIST_XML])
        with mock.patch.object(ui, "_run", wraps=ui._run) as mrun:
            ui.tap(540, 960, pause=0.1)
        args = mrun.call_args[0]
        self.assertEqual(args[:3], ("shell", "input", "tap"))
        self.assertEqual(args[3], "540")

    def test_stop_agent_idempotent(self):
        ui = _make_u2(self, [U2_LIST_XML])
        ui.stop_agent()
        ui.stop_agent()          # 二次调用不抛错

    def test_lazy_agent_no_connect_on_init(self):
        # 构造 U2AdbUI 不得发起任何连接（_agent 懒加载）—— 未打补丁时
        # 访问 dump 才会触发 connect；仅构造 + 析构路径必须零网络。
        ui = U2AdbUI("emulator-5554")
        self.assertIsNone(ui._u2)
        ui._u2 = object()        # 塞个假对象，防 atexit 真去 stop 时报错
        ui.stop_agent()


if __name__ == "__main__":
    unittest.main()
