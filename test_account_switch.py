"""多账号切换的静态回归测试（mock 驱动，不依赖真机/adb）。

覆盖 2026-09-24 新增的多账号功能（config `accounts` + flow 切号编排）：
1. flow._switch_account 链路：账户及设置 → 切换账号 → 按 UIN 定位 → 回联系人页 → 校验
   - 成功路径；账号列表顺序漂移（UIN 节点 y 变化）仍按文本命中
   - 四个失败分支：无入口 / 侧栏无「切换账号」 / 列表无该 UIN / 未回联系人页；昵称校验失败
2. flow._sidebar_nick 行匹配：昵称取「切换账号」同排左侧节点，下一行「等级：14」被排除
3. flow._ensure_account：已是该账号不切号；失败按 max_retry 重试
4. flow._collect_ready：切号后首次收集会漏号，需连续 2 次一致才采纳
5. flow.run_all_accounts：账号顺序 / 禁用跳过 / 切号失败跳过 / 白名单过滤 / 独立记账+逐账号汇总
6. main.py 层：accounts.enabled ⇒ 走 run_all_accounts；--ad-only 保持单账号旧路径；
   --only-switch 只切号

运行：py -3.13 -m unittest test_account_switch -v
"""
import sys
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import (Flow, ACCOUNT_ENTRY_TEXT, SWITCH_ACCOUNT_TEXT,
                  CONTACT_PAGE_HINT, CONTACTS_TAB)


def _n(text, x1, y1, x2, y2):
    return Node(text, x1, y1, x2, y2)


class SwitchBase(unittest.TestCase):
    """构造一个 mock 齐备、零睡眠的 Flow（不碰真机/adb）。"""

    def setUp(self):
        self.f = Flow.__new__(Flow)
        self.f._init_cache_state()
        self.f.wf = {"ad_times_per_robot": 10, "ad_cooldown": 0}
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f.acc = {"settle": 0, "max_retry": 2,
                      "ready_stable": 2, "ready_timeout": 10.0}
        self.f._page_tc = False
        self.f._at_robot_list = False
        self.f.stats = {}
        self.f.ui = mock.Mock()
        self.f.ui.nodes.return_value = []
        self.f._log_page_snapshot = mock.Mock()
        self.f._safe_back_to_robot_list = mock.Mock(return_value=True)
        self.f._exit_taskcenter = mock.Mock()
        # 2026-09-28 切号复位加强（R1）/ 落点兜底（R2）新增调用点：
        # 基类显式 mock，避免静默落到真实实现（ui.nodes() 为空 → 行为不可控）。
        self.f._on_qq_main_shell = mock.Mock(return_value=False)
        self.f._contacts_tab_active = mock.Mock(return_value=True)
        self.f._looks_like_robot_list = mock.Mock(return_value=False)
        self.f._nav_target_wait = mock.Mock(return_value=0.1)
        self.f._wait_until = mock.Mock(return_value=True)

        # 可配置的文本定位表（_find / _wait_for_text 共用语义）
        self.find_map = {}
        self.wait_map = {}
        self.f._find = mock.Mock(side_effect=lambda text, **kw: self.find_map.get(text))
        self.f._wait_for_text = mock.Mock(
            side_effect=lambda text, timeout=6.0, interval=0.4: self.wait_map.get(text))
        self.f._tap_node = mock.Mock()

        self._sleep = mock.patch.object(flow_mod.time, "sleep")
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

    def _tapped_texts(self):
        return [c.args[0].text for c in self.f._tap_node.call_args_list]

    def _setup_ok_prefix(self):
        """配置 入口 + 切换账号 + UIN 行 + 联系人页特征 四条定位。"""
        self.find_map[ACCOUNT_ENTRY_TEXT] = _n(ACCOUNT_ENTRY_TEXT, 0, 66, 146, 201)
        self.wait_map[ACCOUNT_ENTRY_TEXT] = _n(ACCOUNT_ENTRY_TEXT, 0, 66, 146, 201)
        self.wait_map[SWITCH_ACCOUNT_TEXT] = _n(SWITCH_ACCOUNT_TEXT, 468, 459, 596, 507)
        self.wait_map[CONTACT_PAGE_HINT] = _n(CONTACT_PAGE_HINT, 0, 363, 1080, 514)


# ----------------------------------------------------------------------
# 1. _switch_account
# ----------------------------------------------------------------------
class TestSwitchAccount(SwitchBase):
    def test_success_clicks_entry_switch_and_uin(self):
        self._setup_ok_prefix()
        self.wait_map["2593292352"] = _n("2593292352", 183, 653, 393, 705)
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.assertTrue(self.f._switch_account("2593292352", "唐灵"))
        self.assertEqual(self._tapped_texts(),
                         [ACCOUNT_ENTRY_TEXT, SWITCH_ACCOUNT_TEXT, "2593292352"])
        self.f._sidebar_nick.assert_called_once()

    def test_uin_located_by_text_when_list_order_drifts(self):
        """列表顺序会变（当前账号置顶）→ UIN 节点 y 从 653 漂到 1016 仍命中。"""
        self._setup_ok_prefix()
        self.wait_map["2593292352"] = _n("2593292352", 183, 1016, 393, 1068)
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.assertTrue(self.f._switch_account("2593292352", "唐灵"))
        self.assertIn("2593292352", self._tapped_texts())

    def test_no_entry_returns_false(self):
        self.find_map[ACCOUNT_ENTRY_TEXT] = None
        self.assertFalse(self.f._switch_account("1", "X"))
        self.f._log_page_snapshot.assert_called()
        self.assertEqual(self.f._tap_node.call_count, 0)

    def test_sidebar_without_switch_button_returns_false(self):
        self.find_map[ACCOUNT_ENTRY_TEXT] = _n(ACCOUNT_ENTRY_TEXT, 0, 66, 146, 201)
        self.wait_map[SWITCH_ACCOUNT_TEXT] = None
        self.assertFalse(self.f._switch_account("1", "X"))
        self.assertEqual(self._tapped_texts(), [ACCOUNT_ENTRY_TEXT])

    def test_uin_missing_in_list_returns_false(self):
        self._setup_ok_prefix()
        self.wait_map["999"] = None
        self.assertFalse(self.f._switch_account("999", "X"))
        self.assertEqual(self._tapped_texts(),
                         [ACCOUNT_ENTRY_TEXT, SWITCH_ACCOUNT_TEXT])
        self.f._log_page_snapshot.assert_called()

    def test_not_back_to_contacts_returns_false(self):
        self._setup_ok_prefix()
        self.wait_map["2593292352"] = _n("2593292352", 183, 653, 393, 705)
        self.wait_map[CONTACT_PAGE_HINT] = None
        self.assertFalse(self.f._switch_account("2593292352", "唐灵"))

    def test_nick_verify_mismatch_returns_false(self):
        self._setup_ok_prefix()
        self.wait_map["2593292352"] = _n("2593292352", 183, 653, 393, 705)
        self.f._sidebar_nick = mock.Mock(return_value="别的号")
        self.assertFalse(self.f._switch_account("2593292352", "唐灵"))

    def test_resets_route_flags_after_switch(self):
        """切号后 QQ 重置到联系人页 → 寻路快路径/任务中心状态位必须失效。"""
        self._setup_ok_prefix()
        self.f._at_robot_list = True
        self.f._page_tc = True
        self.wait_map["2593292352"] = _n("2593292352", 183, 653, 393, 705)
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.assertTrue(self.f._switch_account("2593292352", "唐灵"))
        self.assertFalse(self.f._at_robot_list)
        self.assertFalse(self.f._page_tc)

    def test_no_safe_back_when_entry_visible(self):
        """入口可见（带重试命中）→ 不得触发 safe_back 复位（省 ~15s 与多余 BACK）。"""
        self._setup_ok_prefix()
        self.wait_map["2593292352"] = _n("2593292352", 183, 653, 393, 705)
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.assertTrue(self.f._switch_account("2593292352", "唐灵"))
        self.f._safe_back_to_robot_list.assert_not_called()


# ----------------------------------------------------------------------
# 1c. 切号落点兜底（2026-09-28 真机实测新增）
#   实测：QQ 切号成功后不保证停在联系人页 —— 落点可能是个人资料页（全屏、
#   底部 tab 不可见）或消息页（主壳内非联系人页）。旧实现只等「新朋友」→
#   3/3 全败整账号跳过。本组覆盖新增的主壳兜底路径。
# ----------------------------------------------------------------------
class TestSwitchFallback(SwitchBase):
    """切号后「落点非联系人页」的兜底导航。"""

    def _setup_switch_only(self):
        """只配 UIN 行；联系人页特征不命中（模拟落点在资料页/消息页）。"""
        self._setup_ok_prefix()
        self.wait_map["2593292352"] = _n("2593292352", 183, 653, 393, 705)
        self.wait_map[CONTACT_PAGE_HINT] = None   # 首轮等不到「新朋友」

    def test_fallback_not_needed_when_hint_appears(self):
        """正常落联系人页 → 兜底方法立即返回 True 且零点击（成功路径无开销）。"""
        self._setup_ok_prefix()
        self.wait_map["2593292352"] = _n("2593292352", 183, 653, 393, 705)
        self.wait_map[CONTACT_PAGE_HINT] = _n(CONTACT_PAGE_HINT, 0, 363, 1080, 514)
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.assertTrue(self.f._switch_account("2593292352", "唐灵"))
        # 兜底方法未被调用（第 4 步判据直接命中）
        self.f._safe_back_to_robot_list.assert_not_called()

    def test_fallback_recovers_when_on_main_shell(self):
        """落消息页（主壳内）→ 点「联系人」tab 回位 → 认成功。"""
        self._setup_switch_only()
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.f._on_qq_main_shell = mock.Mock(return_value=True)
        self.f._contacts_tab_active = mock.Mock(return_value=False)
        # 底部「联系人」tab 可找到 → 兜底会点它
        self.find_map[CONTACTS_TAB] = _n(CONTACTS_TAB, 496, 1861, 583, 1904)
        # 兜底里点 tab 后，再等「新朋友」命中
        hint = _n(CONTACT_PAGE_HINT, 0, 363, 1080, 514)
        self.f._wait_for_text = mock.Mock(
            side_effect=lambda text, timeout=6.0, interval=0.4: (
                hint if text == CONTACT_PAGE_HINT
                else self.wait_map.get(text)))
        self.assertTrue(self.f._switch_account("2593292352", "唐灵"))
        self.assertIn(CONTACTS_TAB, self._tapped_texts())

    def test_fallback_uses_safe_back_for_profile_page(self):
        """落资料页（非主壳）→ 复用 safe_back 复位 → 认成功。"""
        self._setup_switch_only()
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.f._on_qq_main_shell = mock.Mock(return_value=False)
        self.f._safe_back_to_robot_list = mock.Mock(return_value=True)
        self.f._looks_like_robot_list = mock.Mock(return_value=False)
        # safe_back 之后「新朋友」命中
        calls = {"n": 0}

        def _wft(text, timeout=6.0, interval=0.4):
            if text != CONTACT_PAGE_HINT:
                return self.wait_map.get(text)
            calls["n"] += 1
            # 前 2 次落空（第 4 步 25s 超时 + 兜底开头 0.5s 预检），safe_back 后命中
            return None if calls["n"] <= 2 else _n(CONTACT_PAGE_HINT, 0, 363, 1080, 514)

        self.f._wait_for_text = mock.Mock(side_effect=_wft)
        self.assertTrue(self.f._switch_account("2593292352", "唐灵"))
        self.f._safe_back_to_robot_list.assert_called()

    def test_fallback_failure_still_returns_false(self):
        """落点完全无法回位（既非主壳、safe_back 也失败）→ 仍判失败（边界保持）。"""
        self._setup_switch_only()
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.f._on_qq_main_shell = mock.Mock(return_value=False)
        self.f._safe_back_to_robot_list = mock.Mock(return_value=False)
        self.f._looks_like_robot_list = mock.Mock(return_value=False)
        self.assertFalse(self.f._switch_account("2593292352", "唐灵"))
        self.f._log_page_snapshot.assert_called()

    def test_entry_missing_triggers_scroll_to_top(self):
        """入口未现但仍在主壳 → 先滑回列表顶部再找入口（R1 复位加强）。"""
        self._setup_ok_prefix()
        self.wait_map["2593292352"] = _n("2593292352", 183, 653, 393, 705)
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.f._on_qq_main_shell = mock.Mock(return_value=True)
        # 第 0 步入口等待落空（触发 R1），第 1 步 _find 首次也落空 → 走滑回顶部
        self.wait_map[ACCOUNT_ENTRY_TEXT] = None
        seq = {"n": 0}

        def _find(text, **kw):
            if text == ACCOUNT_ENTRY_TEXT:
                seq["n"] += 1
                if seq["n"] == 1:
                    return None
            return self.find_map.get(text)

        self.f._find = mock.Mock(side_effect=_find)
        self.assertTrue(self.f._switch_account("2593292352", "唐灵"))
        # 触发过下滑回顶部
        self.assertGreaterEqual(self.f.ui.swipe_down.call_count, 1)


# ----------------------------------------------------------------------
# 1b. _wait_for_text（强制刷帧，防两次查询命中同一坏帧）
# ----------------------------------------------------------------------
class TestWaitForText(SwitchBase):
    def test_refreshes_between_attempts(self):
        # 前两次落空、第三次命中 → 每次落空后必须 refresh（否则同帧反复失败）
        self.f._find = mock.Mock(
            side_effect=[None, None, _n("X", 0, 0, 10, 10)])
        ticks = iter([0.0, 0.0, 0.1, 0.2, 0.3])
        with mock.patch.object(flow_mod.time, "time",
                               side_effect=lambda: next(ticks, 99.0)):
            n = Flow._wait_for_text(self.f, "X", timeout=5.0, interval=0.0)
        self.assertIsNotNone(n)
        self.assertEqual(self.f._find.call_count, 3)
        self.assertEqual(self.f.ui.refresh.call_count, 2)

    def test_returns_none_on_timeout(self):
        self.f._find = mock.Mock(return_value=None)
        ticks = iter([0.0, 100.0])
        with mock.patch.object(flow_mod.time, "time",
                               side_effect=lambda: next(ticks, 999.0)):
            self.assertIsNone(Flow._wait_for_text(self.f, "X", timeout=1.0))


# ----------------------------------------------------------------------
# 2. _sidebar_nick
# ----------------------------------------------------------------------
class TestSidebarNick(SwitchBase):
    def test_nick_is_left_node_on_same_row(self):
        self.find_map[ACCOUNT_ENTRY_TEXT] = _n(ACCOUNT_ENTRY_TEXT, 0, 66, 146, 201)
        self.wait_map[SWITCH_ACCOUNT_TEXT] = _n(SWITCH_ACCOUNT_TEXT, 468, 459, 596, 507)
        self.f.ui.nodes.return_value = [
            _n("唐灵", 324, 451, 432, 514),          # 同排、x2<=468 → 命中
            _n("等级：14", 324, 632, 569, 686),       # 下一行（y+181）→ 排除
            _n(SWITCH_ACCOUNT_TEXT, 468, 459, 596, 507),
            _n("设置", 65, 1796, 141, 1844),
        ]
        self.assertEqual(self.f._sidebar_nick(), "唐灵")
        self.f.ui.back.assert_called_once()

    def test_returns_none_when_sidebar_not_open(self):
        self.find_map[ACCOUNT_ENTRY_TEXT] = _n(ACCOUNT_ENTRY_TEXT, 0, 66, 146, 201)
        self.wait_map[SWITCH_ACCOUNT_TEXT] = None
        self.assertIsNone(self.f._sidebar_nick())
        # 侧栏未打开 → 不得 BACK（防误退页面）
        self.f.ui.back.assert_not_called()


# ----------------------------------------------------------------------
# 3. _ensure_account
# ----------------------------------------------------------------------
class TestEnsureAccount(SwitchBase):
    def test_already_on_target_skips_switch(self):
        self.f._sidebar_nick = mock.Mock(return_value="唐灵")
        self.f._switch_account = mock.Mock()
        self.assertTrue(self.f._ensure_account("2593292352", "唐灵"))
        self.f._switch_account.assert_not_called()

    def test_retries_then_succeeds(self):
        self.f._sidebar_nick = mock.Mock(return_value="别的号")
        self.f._switch_account = mock.Mock(side_effect=[False, True])
        self.assertTrue(self.f._ensure_account("1", "X"))
        self.assertEqual(self.f._switch_account.call_count, 2)

    def test_exhausts_retries_returns_false(self):
        self.f._sidebar_nick = mock.Mock(return_value="别的号")
        self.f._switch_account = mock.Mock(return_value=False)
        self.assertFalse(self.f._ensure_account("1", "X"))
        self.assertEqual(self.f._switch_account.call_count, 3)  # 1 + max_retry(2)

    def test_unknown_nick_still_switches(self):
        """读不到昵称（None）时不误判"已是该账号"，照常切号。"""
        self.f._sidebar_nick = mock.Mock(return_value=None)
        self.f._switch_account = mock.Mock(return_value=True)
        self.assertTrue(self.f._ensure_account("1", "X"))
        self.f._switch_account.assert_called_once()


# ----------------------------------------------------------------------
# 4. _collect_ready（切号后首次收集漏号）
# ----------------------------------------------------------------------
class TestCollectReady(SwitchBase):
    def _patch_collect(self, seq):
        calls = {"i": 0}

        def _c(*a, **k):
            i = min(calls["i"], len(seq) - 1)
            calls["i"] += 1
            return list(seq[i])
        self.f._collect_robot_names = mock.Mock(side_effect=_c)

    def test_waits_until_two_consecutive_equal(self):
        # 实测复现：切号后首次 10 个（漏顶部），第二次 13 个，第三次起稳定
        self._patch_collect([["B"], ["A", "B", "C"], ["A", "B", "C"]])
        self.assertEqual(self.f._collect_ready(), ["A", "B", "C"])
        self.assertEqual(self.f._collect_robot_names.call_count, 3)

    def test_returns_immediately_when_already_stable(self):
        self._patch_collect([["A", "B"], ["A", "B"]])
        self.assertEqual(self.f._collect_ready(), ["A", "B"])
        self.assertEqual(self.f._collect_robot_names.call_count, 2)

    def test_stable_one_single_pass(self):
        self.f.acc["ready_stable"] = 1
        self._patch_collect([["A"]])
        self.assertEqual(self.f._collect_ready(), ["A"])
        self.assertEqual(self.f._collect_robot_names.call_count, 1)

    def test_timeout_falls_back_to_last(self):
        # 结果始终变化 → 超时后返回当次结果，不无限循环。
        # 控制 time()：第 1 次取 t0=0，第 2 次检查 0（未超时），第 3 次检查 10s ≥ 5s → 超时
        self.f.acc["ready_timeout"] = 5.0
        self._patch_collect([["A"], ["B"], ["C"]])
        ticks = iter([0.0, 0.0, 10.0, 20.0])
        with mock.patch.object(flow_mod.time, "time",
                               side_effect=lambda: next(ticks, 99.0)):
            self.assertEqual(self.f._collect_ready(), ["B"])


# ----------------------------------------------------------------------
# 5. run_all_accounts
# ----------------------------------------------------------------------
class TestRunAllAccounts(SwitchBase):
    def setUp(self):
        super().setUp()
        self.f.run_all = mock.Mock()
        self.f._log_summary = mock.Mock()

    def test_order_skip_disabled_and_whitelist(self):
        accounts = [
            {"nick": "A", "uin": "1", "enabled": True},
            {"nick": "B", "uin": "2", "enabled": False},   # 禁用 → 跳过
            {"nick": "C", "uin": "3", "enabled": True},
        ]
        self.f._ensure_account = mock.Mock(return_value=True)
        self.f._collect_ready = mock.Mock(side_effect=[["r1", "x", "r2"], ["r3"]])
        res = self.f.run_all_accounts(accounts, True, True, None,
                                      whitelist=["r1", "r2", "r3"])
        self.assertEqual([r[0] for r in res], ["A", "C"])
        # 白名单过滤（该账号没有的名字自动跳过）
        self.assertEqual(self.f.run_all.call_args_list[0].args[0], ["r1", "r2"])
        self.assertEqual(self.f.run_all.call_args_list[1].args[0], ["r3"])
        # 每账号独立记账 + 逐账号汇总
        self.assertEqual(self.f._log_summary.call_count, 2)

    def test_switch_failure_skips_account(self):
        accounts = [{"nick": "A", "uin": "1", "enabled": True}]
        self.f._ensure_account = mock.Mock(return_value=False)
        res = self.f.run_all_accounts(accounts, True, True, None)
        self.f.run_all.assert_not_called()
        self.assertEqual(res, [("A", False, [])])

    def test_empty_after_whitelist_skips_run(self):
        accounts = [{"nick": "A", "uin": "1", "enabled": True}]
        self.f._ensure_account = mock.Mock(return_value=True)
        self.f._collect_ready = mock.Mock(return_value=["x", "y"])
        res = self.f.run_all_accounts(accounts, True, True, None,
                                      whitelist=["not-there"])
        self.f.run_all.assert_not_called()
        self.assertEqual(res, [("A", True, [])])

    def test_no_uin_entry_skipped(self):
        accounts = [{"nick": "A", "uin": "", "enabled": True}]
        self.f._ensure_account = mock.Mock(return_value=True)
        self.f._collect_ready = mock.Mock(return_value=["r1"])
        res = self.f.run_all_accounts(accounts, True, True, None)
        self.f.run_all.assert_not_called()
        self.assertEqual(res, [])

    def test_run_all_exception_does_not_abort_next_account(self):
        accounts = [{"nick": "A", "uin": "1", "enabled": True},
                    {"nick": "B", "uin": "2", "enabled": True}]
        self.f._ensure_account = mock.Mock(return_value=True)
        self.f._collect_ready = mock.Mock(return_value=["r1"])
        self.f.run_all = mock.Mock(side_effect=[RuntimeError("boom"), None])
        res = self.f.run_all_accounts(accounts, True, True, None)
        self.assertEqual([(r[0], r[1]) for r in res], [("A", False), ("B", True)])
        self.assertEqual(self.f.run_all.call_count, 2)

    def test_rotate_and_group_passed_through(self):
        accounts = [{"nick": "A", "uin": "1", "enabled": True}]
        self.f._ensure_account = mock.Mock(return_value=True)
        self.f._collect_ready = mock.Mock(return_value=["r1"])
        self.f.run_all_accounts(accounts, True, True, 5, rotate=True, group=4)
        kw = self.f.run_all.call_args.kwargs
        self.assertTrue(kw["rotate"])
        self.assertEqual(kw["group"], 4)
        self.assertEqual(self.f.run_all.call_args.args[3], 5)


# ----------------------------------------------------------------------
# 6. main.py 层（真实 argparse + mock 重依赖）
# ----------------------------------------------------------------------
def _cfg(accounts_enabled=True, whitelist=None):
    return {"device": {"udid": "emulator-test"},
            "workflow": {"ad_times_per_robot": 10, "ad_cooldown": 60,
                         "robot_whitelist": whitelist or []},
            "logging": {},
            "accounts": {"enabled": accounts_enabled,
                         "list": [{"nick": "A", "uin": "1", "enabled": True},
                                  {"nick": "B", "uin": "2", "enabled": True}]}}


class TestMainAccountsMode(unittest.TestCase):
    def _run_main(self, argv_extra, cfg=None):
        cfg = cfg or _cfg()
        for target, kw in [("main.load_config", {"return_value": cfg}),
                           ("main.setup_logging", {}),
                           ("main.AdbUI", {}),
                           ("main.Flow", {})]:
            p = mock.patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)
        ui_cls = mock.patch("main.AdbUI").start()
        flow_cls = mock.patch("main.Flow").start()
        ui_cls.return_value.is_online.return_value = True
        flow_cls.return_value._collect_robot_names.return_value = ["R1"]
        argv = ["main.py"] + argv_extra
        with mock.patch.object(sys, "argv", argv):
            import main as main_mod
            main_mod.main()
        return flow_cls.return_value

    def test_accounts_enabled_routes_to_run_all_accounts(self):
        flow = self._run_main(["--rotate"])
        flow.run_all_accounts.assert_called_once()
        flow.run_all.assert_not_called()
        # 账号清单（含 enabled 标记）原样透传
        accs = flow.run_all_accounts.call_args.args[0]
        self.assertEqual([a["uin"] for a in accs], ["1", "2"])
        self.assertTrue(flow.run_all_accounts.call_args.kwargs["rotate"])

    def test_ad_only_keeps_single_account_path(self):
        flow = self._run_main(["--ad-only"])
        flow.run_all.assert_called_once()
        flow.run_all_accounts.assert_not_called()

    def test_no_ad_keeps_single_account_path(self):
        flow = self._run_main(["--no-ad"])
        flow.run_all.assert_called_once()
        flow.run_all_accounts.assert_not_called()

    def test_robots_arg_keeps_single_account_path(self):
        flow = self._run_main(["--robots", "R1"])
        flow.run_all.assert_called_once()
        flow.run_all_accounts.assert_not_called()

    def test_accounts_disabled_falls_back(self):
        flow = self._run_main(["--rotate"], cfg=_cfg(accounts_enabled=False))
        flow.run_all.assert_called_once()
        flow.run_all_accounts.assert_not_called()

    def test_force_flag_overrides_disabled_config(self):
        flow = self._run_main(["--accounts"],
                              cfg=_cfg(accounts_enabled=False))
        flow.run_all_accounts.assert_called_once()
        flow.run_all.assert_not_called()

    def test_whitelist_passed_through(self):
        flow = self._run_main(["--rotate"], cfg=_cfg(whitelist=["R1", "R2"]))
        self.assertEqual(flow.run_all_accounts.call_args.kwargs["whitelist"],
                         ["R1", "R2"])

    def test_empty_account_list_falls_back(self):
        cfg = _cfg()
        cfg["accounts"]["list"] = []
        flow = self._run_main(["--rotate"], cfg=cfg)
        flow.run_all.assert_called_once()
        flow.run_all_accounts.assert_not_called()

    def test_only_switch_runs_ensure_per_account(self):
        cfg = _cfg()
        cfg["accounts"]["list"] = [{"nick": "A", "uin": "1", "enabled": True},
                                   {"nick": "B", "uin": "2", "enabled": False},
                                   {"nick": "C", "uin": "3", "enabled": True}]
        flow = self._run_main(["--only-switch"], cfg=cfg)
        self.assertEqual(flow._ensure_account.call_count, 2)   # B 禁用跳过
        flow.run_all.assert_not_called()
        flow.run_all_accounts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
