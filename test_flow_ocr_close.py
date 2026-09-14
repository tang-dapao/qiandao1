"""flow.py 广告关闭 3 处修复的静态回归测试（mock 驱动，不依赖真机）。

覆盖：
1. `_ocr_find`：ymax 过滤 + region 偏移 + 与原逻辑等价性 + y==400 边界
   + **跨词元拼接匹配**（TestOcrMergedMatch，修 tesseract 中文分词失配）
2. `_back_at_taskcenter`：穿透误判防护（顶部关闭按钮 vs 正文关闭字样）
3. `_close_ad`：**F8（2026-09-10）** —— 所有坐标点击一律由「OCR 正向读到关闭
   按钮」放行并点**它自己的坐标**；**没有任何固定坐标盲点**（`AD_CLOSE(160,152)`
   在任务中心页就是顶部 banner 命中区，是三次误开 AI 好友 H5 的直接原因）。
   读不到就只走 OCR/uiautomator 正向定位 + 物理 BACK（封顶）。
4. `_stable_in_ad_page`：B+ 盲点前稳定检查（2026-09-10 已随方案 A 从 `_close_ad`
   主路径退役，保留测其自身语义，含 L1 dump_fail_streak 拒绝放行）

运行：py -3.13 -m unittest test_flow_ocr_close -v
"""
import unittest
from unittest import mock

import flow as flow_mod
from adb_ui import Node
from flow import Flow


def _ocr_data(words):
    """按 (text, left, top, width, height) 列表构造 image_to_data 返回的 DICT。"""
    d = {"text": [], "left": [], "top": [], "width": [], "height": []}
    for t, l, tp, w, h in words:
        d["text"].append(t)
        d["left"].append(l)
        d["top"].append(tp)
        d["width"].append(w)
        d["height"].append(h)
    return d


class Base(unittest.TestCase):
    def setUp(self):
        # 强制 OCR 可用 + 替换 pytesseract 为 fake + 去掉真实 sleep
        self._has_ocr = mock.patch.object(flow_mod, "_HAS_OCR", True)
        self._has_ocr.start()
        self.addCleanup(self._has_ocr.stop)

        self.fake_pt = mock.Mock()
        self.fake_pt.Output.DICT = "dict"
        self._pt = mock.patch.object(flow_mod, "pytesseract",
                                     self.fake_pt, create=True)
        self._pt.start()
        self.addCleanup(self._pt.stop)

        self._sleep = mock.patch.object(flow_mod.time, "sleep", mock.Mock())
        self._sleep.start()
        self.addCleanup(self._sleep.stop)

        self.f = Flow.__new__(Flow)
        # A2/A3（2026-09-12）：截图/OCR 结果缓存初始化 —— Flow.__new__ 绕过
        # __init__，类级默认的 _ocr_result_cache 是**共享字典**，会让单测之间
        # 互相污染（如 test_exact_token... 的 (150,160) 泄漏到下一个用例），
        # 必须逐测试建立实例级缓存。
        self.f._init_cache_state()
        self.f.wf = {}
        self.f.t = {"click_min": 1.5, "click_max": 3.0}
        self.f._ocr_lang = "chi_sim+eng"
        # L1：_stable_in_ad_page 真实实现访问 ui.dump_fail_streak —— 默认
        # 0（无 dump 失败），个别测试可覆盖为 >0 验证"拒绝盲点放行"。
        self.f.ui = mock.Mock()
        self.f.ui.dump_fail_streak = 0
        # E/方案1（2026-09-14）：_back_at_taskcenter 免 dump 快速通道依赖
        # _top_strip_scan；默认桩返回「顶条仍有关闭按钮」→ 跳过快速通道、
        # 走旧 dump 严格链 —— 保持既有用例的调用序列语义不变。快速通道
        # 专属用例（TestTopStripFastPath）里显式覆盖该桩。
        self.f._top_strip_scan = mock.Mock(return_value=(True, False))
        # E 优化：预定位缓存逐测试隔离（类级默认是共享值）。
        self.f._prefetch_close = None
        # 注：_stable_in_ad_page 不在 Base 默认 mock —— TestStableInAdPage
        # 需要测试真实实现（内部会调 _back_at_taskcenter）；TestCloseAd 在
        # _stub() 中显式 mock 为 True 以屏蔽内部多次检查对断言的干扰。


class TestOcrFind(Base):
    def _shot(self):
        img = mock.Mock()
        self.f._ocr_shot = mock.Mock(return_value=img)
        return img

    def test_no_region_no_ymax_returns_center(self):
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭广告", 100, 140, 44, 24)])
        self.assertEqual(self.f._ocr_find("关闭广告", "关闭"), (122, 152))

    def test_ymax_skips_below_and_matches_above(self):
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭", 100, 500, 40, 20), ("关闭广告", 100, 140, 44, 24)])
        self.assertEqual(
            self.f._ocr_find("关闭广告", "关闭", "跳过", ymax=400), (122, 152))

    def test_ymax_all_below_returns_none(self):
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭", 100, 500, 40, 20), ("跳过", 200, 600, 40, 20)])
        self.assertIsNone(self.f._ocr_find("关闭", "跳过", ymax=400))

    def test_region_offsets_coordinates(self):
        img = self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭广告", 100, 140, 44, 24)])
        pos = self.f._ocr_find("关闭广告", region=(10, 20, 300, 300))
        self.assertEqual(pos, (132, 172))
        img.crop.assert_called_once_with((10, 20, 300, 300))

    def test_ymax_exact_boundary_is_accepted(self):
        # 记录当前实现行为：ymax 用 ">"，y==ymax 时仍会被接受
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭", 100, 390, 40, 20)])  # y = 390 + 10 = 400
        self.assertEqual(self.f._ocr_find("关闭", ymax=400), (120, 400))

    def test_no_ymax_keeps_old_fullscreen_behavior(self):
        # ymax=None 等价旧行为：不裁剪，命中 y>400 的正文也算（回归等价性）
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭", 100, 500, 40, 20)])
        self.assertEqual(self.f._ocr_find("关闭"), (120, 510))

    def test_keyword_substring_order_independent_coord(self):
        # x,y 已提到外层，keyword 命中哪个都不改变坐标
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data(
            [("关闭广告", 100, 140, 44, 24)])
        self.assertEqual(self.f._ocr_find("关闭", "关闭广告"), (122, 152))


class TestOcrMergedMatch(Base):
    """【2026-09-10 核心修复】跨词元拼接匹配。

    tesseract(chi_sim) 实测会把连续中文切成单字/碎词：「任务中心」→ `任务`+`中`+
    `心`、「获取随机」→ `获取`+`随机`。旧实现只做整词匹配 → 任务中心类关键词
    全部失配 → `_taskcenter_confirmed_by_ocr()` 恒 False → `_back_at_taskcenter()`
    恒 False → 广告关不掉时无限 BACK 退出 QQ（真机实测连按 12 次 BACK 退到桌面）。
    """

    def _shot(self):
        img = mock.Mock()
        self.f._ocr_shot = mock.Mock(return_value=img)
        return img

    def test_merged_three_tokens_returns_span_center(self):
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data([
            ("任务", 100, 500, 80, 40),   # 中心 (140, 520)
            ("中",   190, 500, 40, 40),   # 中心 (210, 520)
            ("心",   240, 500, 40, 40),   # 中心 (260, 520)
        ])
        self.assertEqual(self.f._ocr_find("任务中心"), (203, 520))

    def test_merged_two_tokens_returns_span_center(self):
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data([
            ("获取", 800, 960, 80, 40),   # 中心 (840, 980)
            ("随机", 890, 960, 80, 40),   # 中心 (930, 980)
        ])
        self.assertEqual(self.f._ocr_find("获取随机"), (885, 980))

    def test_merged_match_respects_ymax(self):
        # 拼接命中但跨度中心 y 超上限 → 视为正文误判，不返回
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data([
            ("任务", 100, 700, 80, 40),
            ("中心", 190, 700, 80, 40),
        ])
        self.assertIsNone(self.f._ocr_find("任务中心", ymax=400))

    def test_exact_token_wins_over_merged(self):
        # 第 1 遍整词命中即返回，坐标语义与旧行为一致（不跑第 2 遍）
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data([
            ("任务", 100, 900, 80, 40),
            ("任务中心", 100, 140, 100, 40),   # 中心 (150, 160)
        ])
        self.assertEqual(self.f._ocr_find("任务中心"), (150, 160))

    def test_merged_match_unrelated_tokens_still_miss(self):
        # 广告页词元拼起来不含任务中心特征词 → 仍判"不在任务中心"
        self._shot()
        self.fake_pt.image_to_data.return_value = _ocr_data([
            ("关闭", 100, 140, 80, 40),
            ("广告", 190, 140, 80, 40),
            ("广东", 300, 700, 80, 40),
        ])
        self.assertIsNone(self.f._ocr_find(*Flow._TC_KEYS_OCR, retries=1))


class TestBackAtTaskcenter(Base):
    """2026-09-10 优化：`_back_at_taskcenter` 改为 OCR 先验 + dump 二次确认。

    判据顺序：
      1. OCR 读不到任务中心特征词 → 直接 False（**不触发任何 dump** —— 视频期
         dump 每次要 4s×2≈8.3s，这是本次提速的关键）
      2. OCR 命中 → 用 uiautomator 严格确认（防全屏 H5/问卷页假成功）
      3. dump 不可用（dump_fail_streak>0）→ 无法排除，信任 OCR 正向命中
    """

    @staticmethod
    def _ocr(tc=None, close=None):
        """按查询词路由 OCR 返回值：命中任务中心特征词 → tc；其余 → close。"""
        def router(*texts, **_kw):
            if any(t in Flow._TC_KEYS_OCR for t in texts):
                return tc
            return close
        return mock.Mock(side_effect=router)

    def test_penetration_plus_top_close_means_still_in_ad(self):
        # OCR 命中任务中心 + uiautomator 也读到 rows，但顶部关闭按钮仍在 → 仍在广告
        self.f._find = mock.Mock(return_value=Node("每日签到", 0, 0, 100, 100))
        self.f._ocr_find = self._ocr(tc=(540, 300), close=(120, 152))
        self.assertFalse(self.f._back_at_taskcenter())
        # 关闭按钮查询必须使用顶部条带 + ymax 过滤
        self.f._ocr_find.assert_any_call(
            "关闭广告", "关闭", "跳过", ymax=400, region=flow_mod.AD_TOP_REGION)

    def test_taskcenter_and_no_close_button_means_back(self):
        # 真在任务中心 + 顶部无关闭按钮 → 已回
        self.f._find = mock.Mock(return_value=Node("每日签到", 0, 0, 100, 100))
        self.f._ocr_find = self._ocr(tc=(540, 300), close=None)
        self.assertTrue(self.f._back_at_taskcenter())

    def test_ocr_miss_skips_dump_entirely(self):
        # 【本次提速核心】OCR 读不到任务中心特征 → 一次 dump 都不做
        self.f._find = mock.Mock(return_value=None)
        self.f._ocr_find = self._ocr(tc=None)
        self.assertFalse(self.f._back_at_taskcenter())
        self.f._find.assert_not_called()      # 省掉 8.3s 的 dump 空等

    def test_no_core_rows_means_not_back(self):
        # dump 可用但读不到「获取随机/看广告」核心行 → 没回任务中心
        self.f._find = mock.Mock(return_value=None)
        self.f._ocr_find = self._ocr(tc=(540, 300))
        self.assertFalse(self.f._back_at_taskcenter())

    def test_dump_unavailable_trusts_ocr(self):
        # dump 持续失败（视频期）但 OCR 已确认任务中心 → 信任 OCR 返回 True。
        # 否则"明明已回任务中心"会被误判成没回去，白跑一整轮关闭流程。
        self.f.ui.dump_fail_streak = 3
        self.f._find = mock.Mock(return_value=None)
        self.f._ocr_find = self._ocr(tc=(540, 300))
        self.assertTrue(self.f._back_at_taskcenter())

    def test_fallback_on_taskcenter_title(self):
        # 「获取随机」行可见 + 顶部仍有关闭按钮 → 仍在广告
        self.f._find = mock.Mock(return_value=Node("获取随机", 0, 0, 100, 100))
        self.f._ocr_find = self._ocr(tc=(540, 300), close=(120, 152))
        self.assertFalse(self.f._back_at_taskcenter())


class TestStableInAdPage(Base):
    """B+ 稳定检查：连续 N 次 _back_at_taskcenter=全部 False 才允许盲点。"""

    def test_two_false_returns_true(self):
        # 两次都返回 False（仍在广告）→ True（可以盲点）
        self.f._back_at_taskcenter = mock.Mock(return_value=False)
        self.f.wf = {}
        self.assertTrue(self.f._stable_in_ad_page())
        self.assertEqual(self.f._back_at_taskcenter.call_count, 2)

    def test_first_true_returns_false(self):
        # 第一次就 True（已回任务中心）→ 立即 False（不应盲点），只调 1 次
        self.f._back_at_taskcenter = mock.Mock(
            side_effect=[True, False, False])
        self.f.wf = {}
        self.assertFalse(self.f._stable_in_ad_page())
        self.assertEqual(self.f._back_at_taskcenter.call_count, 1)

    def test_second_true_returns_false(self):
        # 第一次 False 但第二次 True → False（不应盲点）
        self.f._back_at_taskcenter = mock.Mock(
            side_effect=[False, True, False])
        self.f.wf = {}
        self.assertFalse(self.f._stable_in_ad_page())
        self.assertEqual(self.f._back_at_taskcenter.call_count, 2)

    def test_checks_one_degrades_to_single_check(self):
        # 显式传 checks=1 → 降级为单次检查（旧行为兼容）
        self.f._back_at_taskcenter = mock.Mock(return_value=False)
        self.f.wf = {"ad_close_stable_checks": 99}  # 配置应被覆盖
        self.assertTrue(self.f._stable_in_ad_page(checks=1))
        self.assertEqual(self.f._back_at_taskcenter.call_count, 1)

    def test_wf_config_overrides_default(self):
        # wf['ad_close_stable_checks']=3 → 3 次检查
        self.f._back_at_taskcenter = mock.Mock(return_value=False)
        self.f.wf = {"ad_close_stable_checks": 3}
        self.assertTrue(self.f._stable_in_ad_page())
        self.assertEqual(self.f._back_at_taskcenter.call_count, 3)

    def test_interval_zero_skips_sleep(self):
        # interval=0 不调 sleep（已 mock 为 Mock），不影响 _back_at_taskcenter 次数
        self.f._back_at_taskcenter = mock.Mock(return_value=False)
        self.f.wf = {"ad_close_stable_checks": 2,
                     "ad_close_stable_interval": 0}
        self.assertTrue(self.f._stable_in_ad_page())
        self.assertEqual(self.f._back_at_taskcenter.call_count, 2)

    def test_dump_fail_streak_positive_blocks_blind_tap(self):
        # L1：dump 连续失败（页面动画中无法 idle / rc=139）时 _back_at_taskcenter
        # 恒 False 是"状态未知"而非"在广告页" —— 放行盲点会命中 banner。
        # streak>0 → 立即拒绝，且不执行任何稳定检查。
        self.f._back_at_taskcenter = mock.Mock(return_value=False)
        self.f.wf = {"ad_close_stable_checks": 3}
        self.f.ui.dump_fail_streak = 2
        self.assertFalse(self.f._stable_in_ad_page())
        self.assertEqual(self.f._back_at_taskcenter.call_count, 0)

    def test_dump_fail_during_checks_blocks_blind_tap(self):
        # L1：检查过程中 dump 又失败（首检通过但次检前崩溃）→ 本次判定不可信
        # → 拒绝放行，不等剩余次数。
        self.f._back_at_taskcenter = mock.Mock(return_value=False)
        self.f.wf = {"ad_close_stable_checks": 3,
                     "ad_close_stable_interval": 0.1}
        self.f.ui.dump_fail_streak = 0
        calls = {"n": 0}

        def _fail_after_first():
            calls["n"] += 1
            if calls["n"] >= 2:
                self.f.ui.dump_fail_streak = 1
            return False

        self.f._back_at_taskcenter.side_effect = _fail_after_first
        self.assertFalse(self.f._stable_in_ad_page())
        self.assertEqual(calls["n"], 2)


class TestAdTopRegion(Base):
    """F8 根因之二：关闭按钮 OCR 条带高度不能过大。

    tesseract 在大面积深色视频背景上会放弃分页、整块返回空 —— 实测 4399 视频
    广告 3 帧（肉眼「关闭广告」极清晰）在 430 高条带下全部读不出，而 320/300/260
    高条带 9/9 可读，且对 10 个任务中心帧 0 误报。
    """

    def test_region_height_excludes_video_area(self):
        # 条带高度硬上限 320：430 会把视频区一起裁进来导致整块 OCR 失败
        self.assertEqual(flow_mod.AD_TOP_REGION[0], 0)
        self.assertLessEqual(flow_mod.AD_TOP_REGION[3], 320)

    def test_region_covers_close_pill(self):
        # 药丸实测 y≈107-187，条带必须完整覆盖（留余量）
        self.assertGreaterEqual(flow_mod.AD_TOP_REGION[3], 200)


class TestCloseAd(Base):
    def _stub(self):
        f = self.f
        f._find = mock.Mock(return_value=None)
        f.ui = mock.Mock()
        f.ui.nodes.return_value = []
        f._tap_node = mock.Mock()
        f._tap = mock.Mock()
        f._back_at_taskcenter = mock.Mock(return_value=False)
        # 方案 A：直关失败后的 Badcase 自愈巡检，默认无动作（不干扰断言）
        f._dismiss_badcase = mock.Mock(return_value=True)
        f._ocr_find = mock.Mock(return_value=None)   # 默认什么都读不到
        return f

    @staticmethod
    def _ocr_router(*, tc=(), close=(), badcase=()):
        """按 OCR 查询词路由返回值（方案 A 后 _close_ad 有多类 OCR 查询）：
        - 命中 _TC_KEYS_OCR（每日签到/任务中心/…）→ 任务中心判定序列 tc
        - 命中 _BADCASE_KEYS（Badcase/反馈问卷/…）→ Badcase 巡检序列 badcase
        - 其余（关闭广告/关闭/跳过/取消）      → 关闭按钮序列 close
        序列耗尽后一律返回 None。
        """
        tc_it, close_it, bad_it = iter(tc), iter(close), iter(badcase)

        def router(*texts, **_kw):
            if any(t in Flow._TC_KEYS_OCR for t in texts):
                return next(tc_it, None)
            if any(t in Flow._BADCASE_KEYS for t in texts):
                return next(bad_it, None)
            return next(close_it, None)
        return mock.Mock(side_effect=router)

    def test_already_back_at_taskcenter_skips_all_taps(self):
        # 广告可能在 ad_wait 内已自动关闭、画面已回任务中心 —— OCR 连续读到
        # 任务中心特征词 → 直接收工，不点任何坐标。
        f = self._stub()
        f._ocr_find = self._ocr_router(tc=[(540, 900), (540, 900)])
        self.assertTrue(f._close_ad())
        f._tap.assert_not_called()
        f._tap_node.assert_not_called()
        f.ui.tap.assert_not_called()

    def test_tc_seen_reuses_watch_ad_ocr_and_only_confirms_once(self):
        # P1：_watch_ad_once 已做过一次任务中心 OCR，_close_ad 只补第 2 次
        # 确认即可收工，避免同一状态连续三次全屏 OCR。
        f = self._stub()
        f._ocr_find = self._ocr_router(tc=[(540, 900)])
        self.assertTrue(f._close_ad(tc_seen=True))
        self.assertEqual(f._ocr_find.call_count, 1)
        f._tap.assert_not_called()
        f._tap_node.assert_not_called()
        f.ui.tap.assert_not_called()

    def test_ocr_direct_close_uses_ocr_coords_not_fixed(self):
        # 【F8 核心】正向直关：OCR 读到关闭按钮 → 点**它自己的坐标**；
        # **绝不**再点固定 AD_CLOSE(160,152)（该坐标在任务中心页是顶部 banner
        # 的命中区 → 误开 AI 好友 H5）。
        f = self._stub()
        f._ocr_find = self._ocr_router(close=[(120, 152)])
        f._back_at_taskcenter = mock.Mock(return_value=True)
        self.assertTrue(f._close_ad())
        # F11：广告页关闭按钮由 OCR 正向定位（非盲点），显式 trusted=True 放行
        # 任务中心页的盲点坐标禁令。
        f._tap.assert_called_once_with(120, 152, pause=1.5, trusted=True)
        f._tap_node.assert_not_called()
        f.ui.tap.assert_not_called()
        f.ui.back.assert_not_called()
        f._dismiss_badcase.assert_not_called()   # 直关成功 → 无需自愈巡检

    def test_ocr_miss_never_taps_fixed_coordinate(self):
        # 【F8 核心回归：banner 误触根因】OCR 读不到关闭按钮时，旧实现会盲点
        # 固定 (160,152) —— 而该坐标在任务中心页正好落在顶部 banner 上，且
        # banner 是 WebView 自绘、dump 里根本不存在（任何 dump 守卫都拦不住）
        # → 误开 AI 好友 H5（11:43/11:58/12:25 三次事故）。现改为：一个坐标
        # 都不点，只做 OCR/uiautomator/BACK。
        f = self._stub()
        f.wf = {"ad_close_retries": 12, "ad_close_max_backs": 3}
        f._ocr_find = self._ocr_router()          # 什么都读不到（最坏情况）
        f._back_at_taskcenter = mock.Mock(return_value=False)
        self.assertFalse(f._close_ad())
        f._tap.assert_not_called()                # ← 关键：不再有固定坐标盲点
        f._tap_node.assert_not_called()
        f.ui.tap.assert_not_called()
        self.assertEqual(f.ui.back.call_count, 3)  # BACK 封顶仍生效
        self.assertNotIn(flow_mod.AD_CLOSE, [c.args[:2] for c in f._tap.call_args_list])

    def test_dump_failure_does_not_block_ocr_close(self):
        # 真机实锤：视频广告播放期 UI 无法 idle，dump 必然失败
        # （dump_fail_streak>0）；旧守卫 _stable_in_ad_page() 因此恒 False →
        # 关闭按钮一次都点不到（run.log 01:15 卡到需手动停止）。现由 OCR
        # 正向定位，dump 状态与"能否点击"完全解耦。
        f = self._stub()
        f.ui.dump_fail_streak = 5          # 持续 dump 失败
        f._ocr_find = self._ocr_router(close=[(120, 152)])
        f._back_at_taskcenter = mock.Mock(return_value=True)
        self.assertTrue(f._close_ad())
        # F11：广告页关闭按钮由 OCR 正向定位（非盲点），显式 trusted=True 放行
        # 任务中心页的盲点坐标禁令。
        f._tap.assert_called_once_with(120, 152, pause=1.5, trusted=True)

    def test_ocr_close_fail_triggers_selfheal_then_auto_ended(self):
        # 正向直关 tap 后仍未回任务中心 → 先巡检一次 Badcase/AI 好友（F5 自愈）
        # → 下一轮 OCR 读不到按钮但 _back_at_taskcenter 为真（广告已结束）。
        # P5：确认带 1 次重试，[False, False, True] = 首次+重试均失败（真失败）
        # → 走巡检 + 兜底，兜底第 1 轮确认成功。
        f = self._stub()
        f._ocr_find = self._ocr_router(close=[(120, 152)])
        f._back_at_taskcenter = mock.Mock(side_effect=[False, False, True])
        self.assertTrue(f._close_ad())
        # F11：广告页关闭按钮由 OCR 正向定位（非盲点），显式 trusted=True 放行
        # 任务中心页的盲点坐标禁令。
        f._tap.assert_called_once_with(120, 152, pause=1.5, trusted=True)
        f._dismiss_badcase.assert_called_once()   # 直关失败 → 巡检一次

    def test_direct_close_confirm_retry_avoids_fallback(self):
        # 【P5 核心】直关 tap 后首次确认 False（任务中心重载动画期 OCR 先验
        # 误判），沉降 gap 秒后重试确认 True → 判定直关成功，不再走 7-12s
        # 兜底（21:00 场 33/33 误判实锤，肉眼看广告当场已关）。
        f = self._stub()
        f._ocr_find = self._ocr_router(close=[(120, 152)])
        f._back_at_taskcenter = mock.Mock(side_effect=[False, True])
        self.assertTrue(f._close_ad())
        f._tap.assert_called_once_with(120, 152, pause=1.5, trusted=True)
        f._dismiss_badcase.assert_not_called()    # 重试救回 → 无需自愈巡检
        self.assertEqual(f._back_at_taskcenter.call_count, 2)  # 首次+重试各一次

    def test_confirm_retries_zero_restores_old_behavior(self):
        # ad_close_confirm_retries=0 → 与旧单次确认行为一致：首次 False 即宣告
        # 未生效、走巡检+兜底。
        f = self._stub()
        f.wf = {"ad_close_retries": 1, "ad_close_confirm_retries": 0}
        f._ocr_find = self._ocr_router(close=[(120, 152)])
        f._back_at_taskcenter = mock.Mock(side_effect=[False, True])
        self.assertTrue(f._close_ad())
        f._dismiss_badcase.assert_called_once()   # 无重试 → 直关即判失败

    def test_fallback_uiautomator_path(self):
        # OCR 读不到关闭按钮 → uiautomator 找到「关闭广告」节点
        # → tap_node 关闭成功（正向定位路径之一）
        f = self._stub()
        node = Node("关闭广告", 0, 140, 200, 164)
        f.ui.nodes.return_value = [node]
        f._ocr_find = self._ocr_router()
        f._back_at_taskcenter = mock.Mock(side_effect=[False, True])
        self.assertTrue(f._close_ad())
        f._tap_node.assert_called_once_with(node, pause=1.5)
        f._tap.assert_not_called()
        f.ui.back.assert_not_called()

    def test_find_close_node_uses_single_dump_pass(self):
        # P2：精确「关闭广告」和「跳过/关闭」候选在同一次 nodes dump 内完成，
        # 避免 _find 一次 + nodes 一次的双 dump 空等。
        f = self._stub()
        node = Node("关闭广告", 0, 140, 200, 164)
        f.ui.nodes.return_value = [node]
        self.assertIs(f._find_close_node(timeout=2.5), node)
        f.ui.nodes.assert_called_once_with(timeout=2.5)
        f._find.assert_not_called()

    def test_uiautomator_before_ocr(self):
        # 验证顺序（2026-09-10 对调）：等待结束后 uiautomator 直关先于关闭按钮
        # OCR —— 页面 idle 时 dump 1-3s 命中且坐标来自真实 bounds；OCR 只在
        # dump 有界失败（视频未播完）后才跑。步 0 的任务中心双检仍最先。
        f = self._stub()
        calls = []
        f._back_at_taskcenter = mock.Mock(
            side_effect=lambda *a, **k: calls.append("back") or False)
        f._ocr_find = mock.Mock(
            side_effect=lambda *a, **k: calls.append("ocr") or None)
        f.ui.nodes = mock.Mock(
            side_effect=lambda *a, **k: calls.append("ui") or [])
        f.wf = {"ad_close_retries": 1}
        f._close_ad()
        # 步 0：tc OCR 首检 miss → 步 1：uiautomator 直关尝试
        self.assertEqual(calls[:2], ["ocr", "ui"])
        # 关闭按钮 OCR 在 uiautomator 尝试之后才跑
        self.assertEqual(calls[2], "ocr")
        # 2026-09-14 尔尔插屏事故：首检 miss 后 2s 复读一次（过渡帧防误判，
        # 直接判"没广告"走 BACK 兜底会把流程留在插屏上 → dump 全局失效死锁）
        self.assertEqual(calls[3], "ocr")
        # 兜底循环内：任务中心校验先于 uiautomator（防 dump 残留节点误点），
        # OCR 排在 uiautomator 之后
        self.assertEqual(calls[4:7], ["back", "ui", "ocr"])

    def test_uiautomator_direct_close_first(self):
        # 【2026-09-10 对调核心场景】等待结束 → 页面 idle → uiautomator 先命中
        # 「关闭广告」节点直关；关闭按钮 OCR 一次都不用跑（17:29 实测：OCR 漏读
        # 药丸、uiautomator 一次命中 (141,150)，此路径省 ~9s OCR 空试）。
        f = self._stub()
        node = Node("关闭广告", 0, 140, 200, 164)
        f.ui.nodes.return_value = [node]
        f._ocr_find = self._ocr_router(tc=[])          # 任务中心双检均 miss
        f._back_at_taskcenter = mock.Mock(return_value=True)
        self.assertTrue(f._close_ad())
        f._tap_node.assert_called_once_with(node, pause=1.5)
        f._tap.assert_not_called()                     # 未走 OCR 坐标点击
        f._dismiss_badcase.assert_not_called()         # 直关成功 → 无自愈巡检
        self.assertEqual(f._ocr_find.call_count, 1)    # 步 0 首检 miss 后不再二检

    def test_retries_config_wins_over_default(self):
        # ad_close_retries 控制兜底轮数（BACK 未达上限时按轮数退出）
        f = self._stub()
        f._ocr_find = self._ocr_router()
        f.wf = {"ad_close_retries": 2, "ad_close_max_backs": 3}
        f._close_ad()  # 参数默认 6，但 config 2 应生效
        self.assertEqual(f.ui.back.call_count, 2)

    def test_retries_missing_key_falls_back_to_default(self):
        f = self._stub()
        f._ocr_find = self._ocr_router()
        f.wf = {}  # 缺 ad_close_retries → 默认 6
        f._close_ad()
        # 默认 6 轮，但连续 BACK 封顶 3 → 第 4 轮即放弃
        self.assertEqual(f.ui.back.call_count, 3)

    def test_max_tries_zero_direct_close_only(self):
        # ad_close_retries=0 → 不进兜底循环，只执行第 0 步正向直关
        f = self._stub()
        f.wf = {"ad_close_retries": 0}
        f._ocr_find = self._ocr_router(close=[(120, 152)])
        f._back_at_taskcenter = mock.Mock(return_value=True)
        self.assertTrue(f._close_ad())
        # F11：广告页关闭按钮由 OCR 正向定位（非盲点），显式 trusted=True 放行
        # 任务中心页的盲点坐标禁令。
        f._tap.assert_called_once_with(120, 152, pause=1.5, trusted=True)
        f._tap_node.assert_not_called()
        f.ui.tap.assert_not_called()

    # ---- F6（2026-09-10）：BACK 兜底必须封顶，防止退穿整个 App ----
    def test_back_fallback_capped_to_max_backs(self):
        # 【F6 回归】判定失灵时（每次 OCR 都读不到任务中心）会连续走物理 BACK。
        # 旧实现按 ad_close_retries=12 一路退到手机桌面（11:58 实测连按 10 次）。
        # 现连续 BACK 封顶 → 超限放弃本次关闭。
        f = self._stub()
        f.wf = {"ad_close_retries": 12, "ad_close_max_backs": 3}
        f._ocr_find = self._ocr_router()          # 什么都读不到
        f._back_at_taskcenter = mock.Mock(return_value=False)
        self.assertFalse(f._close_ad())
        self.assertEqual(f.ui.back.call_count, 3)
        f._tap.assert_not_called()                # F8：全程无坐标盲点

    def test_back_cap_defaults_to_three_when_config_missing(self):
        f = self._stub()
        f.wf = {"ad_close_retries": 9}            # 缺 ad_close_max_backs
        f._ocr_find = self._ocr_router()
        f._back_at_taskcenter = mock.Mock(return_value=False)
        self.assertFalse(f._close_ad())
        self.assertEqual(f.ui.back.call_count, 3)


class TestBadcaseGuard(Base):
    """L3 Badcase 问卷 + AI 好友 banner H5 巡检。

    2026-09-10 扩展：`_dismiss_badcase` 同时识别 Badcase 问卷与 AI 好友 H5；
    `_ai_friend_page_visible` 需排除"任务中心顶部 banner 文字含'QQ AI好友·常见
    问题答疑'"的干扰（只在该文字命中 **且** 任务中心特征词读不到时才认账）。
    """

    def _stub(self):
        f = self.f
        f._ocr_find = mock.Mock(return_value=None)
        f.ui = mock.Mock()
        f.ui.dump_fail_streak = 0
        # A4（2026-09-12）：_dismiss_badcase 检测改走 _overlay_scan（单次 OCR
        # 推理同判 Badcase/AI 好友/资料卡/任务中心各组词）。默认返回无 overlay。
        # 2026-09-13：_overlay_scan 扩为三元组 (bad, ai, profile)。
        f._overlay_scan = mock.Mock(return_value=(False, False, False))
        return f

    def test_no_overlay_returns_true_no_back(self):
        # 无 Badcase 问卷 + 无 AI 好友 H5 → 直接 True，不产生任何 BACK
        # （A4：检测合并为单次 _overlay_scan 调用，原 2 次 _ocr_find）
        f = self._stub()
        self.assertTrue(f._dismiss_badcase())
        f.ui.back.assert_not_called()
        f._overlay_scan.assert_called_once()
        f._ocr_find.assert_not_called()

    def test_badcase_cleared_after_one_back(self):
        # 首检命中 Badcase → BACK 1 次 → 复查已消失 → True
        f = self._stub()
        with mock.patch.object(f, "_overlay_scan",
                               side_effect=[(True, False, False),
                                            (False, False, False)]):
            self.assertTrue(f._dismiss_badcase())
        f.ui.back.assert_called_once()

    def test_badcase_survives_max_backs_returns_false(self):
        # 连续 3 次 BACK 后仍在问卷页 → False（交给上层失败计数兜底）
        f = self._stub()
        with mock.patch.object(f, "_overlay_scan",
                               return_value=(True, False, False)):
            self.assertFalse(f._dismiss_badcase())
        self.assertEqual(f.ui.back.call_count, 3)

    def test_ai_friend_cleared_after_one_back(self):
        # 首检命中 AI 好友 H5 → BACK → 已退出 → True
        f = self._stub()
        with mock.patch.object(f, "_overlay_scan",
                               side_effect=[(False, True, False),
                                            (False, False, False)]):
            self.assertTrue(f._dismiss_badcase())
        f.ui.back.assert_called_once()

    def test_taskcenter_top_banner_not_misread_as_ai_friend(self):
        # 【关键回归 2026-09-10】任务中心顶部 banner 文字含「QQ AI好友·常见
        # 问题答疑」—— 若只用 OCR 含这些词就判定，会把任务中心自身误判成 AI
        # 好友页而乱 BACK。必须叠加"任务中心特征词读不到"才认账。
        # 此处用真实 _ai_friend_page_visible（不 mock）验证语义：
        # _ocr_find 返回 ("常见问题答疑", 命中) + ("每日签到", 命中) → 返回 False
        f = self._stub()
        tc_hit = (540, 300)
        banner_hit = (540, 100)
        f._ocr_find = mock.Mock(side_effect=[
            banner_hit,    # _ai_friend_page_visible 第一次 OCR：AI 词命中
            tc_hit,        # 排除检查 OCR：任务中心词命中 → 排除 AI 好友页
        ])
        self.assertFalse(f._ai_friend_page_visible())

    def test_ai_friend_page_with_no_tc_text_is_recognised(self):
        # AI 好友 banner H5 全屏时：banner 词命中 + 任务中心词读不到 → True
        f = self._stub()
        banner_hit = (540, 500)
        f._ocr_find = mock.Mock(side_effect=[
            banner_hit,    # AI 词命中
            None,          # 任务中心词读不到 → 认账
        ])
        self.assertTrue(f._ai_friend_page_visible())

    def test_no_ai_friend_text_means_not_visible(self):
        # OCR 读不到 AI 词 → 直接 False（不会再做任务中心词 OCR，节省成本）
        f = self._stub()
        f._ocr_find = mock.Mock(return_value=None)
        self.assertFalse(f._ai_friend_page_visible())
        self.assertEqual(f._ocr_find.call_count, 1)

    def test_badcase_visible_wraps_ocr_find(self):
        # _badcase_visible：OCR 命中 → True / 未命中 → False
        f = self._stub()
        f._ocr_find = mock.Mock(return_value=None)
        self.assertFalse(f._badcase_visible())
        f._ocr_find = mock.Mock(return_value=(120, 400))
        self.assertTrue(f._badcase_visible())
        # 检索词必须是问卷特征词（Badcase/反馈问卷/开始填写/感谢大家一直）
        args = f._ocr_find.call_args.args
        self.assertIn("Badcase", args)
        self.assertIn("反馈问卷", args)
        self.assertEqual(f._ocr_find.call_args.kwargs.get("retries"), 1)

    # ---- F5（2026-09-10 11:58 实测）：帮助中心 H5 特征词补全 ----
    def test_ai_friend_help_center_h5_recognised(self):
        # 点中顶部 banner 后真正打开的是 AI 好友**帮助中心**：标题只有「常见
        # 问题」（无"答疑"），正文分组「基础权益 / 额度消耗规则 / 免费额度、
        # 电量与心动卡」。旧词表（常见问题答疑 / QQ AI好友）两项都命中不了这一
        # 页 —— 实测 H5 全屏持续 10 帧而 _dismiss_badcase 毫无察觉（继续 BACK
        # 直到退出 QQ）。现补入该页独有词后必须能认出来。
        f = self._stub()
        f._ocr_find = mock.Mock(side_effect=[(300, 60), None])
        # 第 1 次：AI 好友词命中；第 2 次：任务中心词读不到 → 认账
        self.assertTrue(f._ai_friend_page_visible())
        hints = f._ocr_find.call_args_list[0].args
        for k in ("额度消耗规则", "心动卡"):
            self.assertIn(k, hints)

    def test_ai_friend_hints_do_not_overlap_taskcenter_keys(self):
        # 新增特征词不得与任务中心特征词混淆（否则任务中心会被误判成 H5）
        for k in ("额度消耗规则", "心动卡", "必须付费才能", "免费额度的途径"):
            self.assertIn(k, Flow._AI_FRIEND_HINTS)
            self.assertNotIn(k, Flow._TC_KEYS_OCR)

    def test_overlay_detect_leaves_diag_shot(self):
        # F7：命中 overlay 时必须留现场截图（tag=overlay），便于区分真报/误报
        # —— 12:25 那轮日志只有一句合并文案，无法回溯当时画面。
        f = self._stub()
        f._diag_shot = mock.Mock()
        with mock.patch.object(f, "_overlay_scan",
                               return_value=(True, False, False)):
            f._dismiss_badcase()
        f._diag_shot.assert_called_once_with("overlay")

    # ---- A4（2026-09-12）：_overlay_scan 单次推理多组词的语义回归 ----
    def test_overlay_scan_badcase_hit(self):
        # 单次推理：badcase 词命中 → (True, False, False)
        f = self._stub()
        f._overlay_scan = mock.Mock(return_value=(True, False, False))
        bad, ai, profile = f._overlay_scan()
        self.assertTrue(bad)
        self.assertFalse(ai)
        self.assertFalse(profile)

    def test_overlay_scan_words_merged_from_three_key_groups(self):
        # _overlay_scan 必须同时查询三组词：BADCASE_KEYS + AI_FRIEND_HINTS
        # + TC_KEYS_OCR（AI 好友判定需要"任务中心词未命中"排除条件）
        for k in ("Badcase", "反馈问卷"):
            self.assertIn(k, Flow._BADCASE_KEYS)
        for k in ("QQ AI好友", "额度消耗规则"):
            self.assertIn(k, Flow._AI_FRIEND_HINTS)
        for k in ("每日签到", "获取随机"):
            self.assertIn(k, Flow._TC_KEYS_OCR)

    # ---- 2026-09-13 小麦事故：资料卡浮层识别与清除 ----
    def test_profile_keys_registered_and_disjoint_from_tc(self):
        # 资料卡特征词必须在册；且不得与任务中心特征词重叠（防 TC 误判成资料卡）
        for k in ("语音通话", "QQ空间"):
            self.assertIn(k, Flow._PROFILE_KEYS)
            self.assertNotIn(k, Flow._TC_KEYS_OCR)

    def test_profile_card_cleared_after_one_back(self):
        # 首检命中资料卡 → BACK 1 次 → 复查已消失 → True（小麦事故自愈路径）
        f = self._stub()
        with mock.patch.object(f, "_overlay_scan",
                               side_effect=[(False, False, True),
                                            (False, False, False)]):
            self.assertTrue(f._dismiss_badcase())
        f.ui.back.assert_called_once()

    def test_profile_card_survives_max_backs_returns_false(self):
        # 资料 BACK 3 次仍在 → False（交给上层失败计数兜底）
        f = self._stub()
        with mock.patch.object(f, "_overlay_scan",
                               return_value=(False, False, True)):
            self.assertFalse(f._dismiss_badcase())
        self.assertEqual(f.ui.back.call_count, 3)


class TestBannerGuard(Base):
    """F11（2026-09-10）：任务中心页「禁止盲点坐标点击」守卫。

    实测背景（两机 dump + 截图）：推广 banner「QQAI好友·常见问题答疑」是 WebView
    自绘、**不在 dump** 且**纵坐标随版式漂移**（游迦 y≈116-366、代柯 y≈1151-1410）
    → 没有固定 y 区间能覆盖 → 根治办法是「不盲点坐标」：banner 不在 dump，故所有
    基于真实节点的 `_tap_node` 天然安全；坐标点击在任务中心页一律拒绝，除非显式
    trusted=True（广告页关闭按钮 / 签到浮层）。

    关键约束：**只在任务中心页生效** —— 列表页机器人行（y≈410-500）与联系人页
    『机器人』分类行（y≈201-352）必须仍可点击。
    """

    def _stub(self, page_tc=True):
        f = self.f
        f.ui = mock.Mock()
        f._page_tc = page_tc
        return f

    def test_region_helper_boundaries(self):
        # 诊断用几何 helper（top-strip 记录，非守卫依据）
        r = flow_mod.BANNER_TOP_STRIP
        self.assertEqual(r, (0, 0, 1080, 440))
        self.assertTrue(flow_mod._in_region(r, 0, 0))
        self.assertTrue(flow_mod._in_region(r, 1080, 440))
        self.assertFalse(flow_mod._in_region(r, 540, 441))

    def test_guard_rejects_any_blind_tap_on_taskcenter(self):
        # 任务中心页 → 任何坐标点击都被拒绝（含历史事故坐标 160,152）
        f = self._stub(page_tc=True)
        self.assertFalse(f._tap(160, 152))
        f.ui.tap.assert_not_called()

    def test_guard_rejects_blind_tap_even_below_banner(self):
        # 版式漂移：代柯页 banner 在 y≈1151-1410 → 只禁顶部是不够的，
        # 因此任务中心页的**所有**盲点都要拒绝（含 y=1200 这种"看起来安全"的点）。
        f = self._stub(page_tc=True)
        self.assertFalse(f._tap(540, 1200))
        f.ui.tap.assert_not_called()

    def test_trusted_tap_allowed_on_taskcenter(self):
        # 唯一合法例外：广告页关闭按钮 / 签到浮层 → trusted=True
        f = self._stub(page_tc=True)
        self.assertTrue(f._tap(120, 152, pause=1.5, trusted=True))
        f.ui.tap.assert_called_once_with(120, 152, 1.5)

    def test_blind_tap_allowed_when_not_on_taskcenter(self):
        # 关键回归：机器人列表首行/联系人分类行可落在 y<440，但那不是任务中心页
        # → 必须放行（否则导航/进任务中心直接瘫痪）。
        f = self._stub(page_tc=False)
        self.assertTrue(f._tap(200, 340))        # 机器人首行中心（实测量级）
        f.ui.tap.assert_called_once()
        self.assertEqual(f.ui.tap.call_args.args[:2], (200, 340))

    def test_page_tc_defaults_false(self):
        # 新建 Flow 时 _page_tc 必须为 False（首跑从列表开始，禁令不应生效）
        cfg = {"timing": {"click_min": 1.5, "click_max": 3.0}, "workflow": {}}
        g = Flow(cfg, mock.Mock())
        self.assertFalse(g._page_tc)


class TestOverlayGate(Base):
    """F11：`_back_at_taskcenter` 的覆盖层闸门（问题反馈 / 静默假成功防线）。

    背景：Badcase 反馈问卷是 WebView H5，uiautomator dump 会**穿透**读到背后任务
    中心的「每日签到」；若只看 dump 就会在问卷盖屏时误判"已回任务中心"→ 静默假
    成功。故在即将判成功时用问卷**独有词**（任务中心绝不含）扫顶部条带复核。
    """

    def _stub(self, tc=True, rows=True, overlay=False, close=None):
        f = self.f
        f.ui = mock.Mock()
        f.ui.dump_fail_streak = 0
        f._taskcenter_confirmed_by_ocr = mock.Mock(return_value=tc)
        f._find = mock.Mock(return_value=Node("每日签到", 0, 0, 100, 100)
                            if rows else None)
        f._ocr_find = mock.Mock(return_value=close)
        f._overlay_visible_in_top = mock.Mock(return_value=overlay)
        return f

    def test_overlay_badcase_blocks_confirmation(self):
        f = self._stub(overlay=True)
        self.assertFalse(f._back_at_taskcenter())

    def test_no_overlay_allows_confirmation(self):
        f = self._stub(overlay=False)
        self.assertTrue(f._back_at_taskcenter())

    def test_overlay_checked_only_after_all_other_gates(self):
        # 覆盖层闸门只在"即将判成功"时执行（效率：不增加失败路径开销）
        f = self._stub(overlay=True)
        f._back_at_taskcenter()
        f._overlay_visible_in_top.assert_called_once()

    def test_overlay_not_checked_when_ocr_misses_taskcenter(self):
        f = self._stub(tc=False, overlay=True)
        self.assertFalse(f._back_at_taskcenter())
        f._overlay_visible_in_top.assert_not_called()

    def test_overlay_uses_unique_keys_and_top_strip(self):
        f = self.f
        f.ui = mock.Mock()
        f._ocr_find = mock.Mock(return_value=None)
        self.assertFalse(f._overlay_visible_in_top())
        args = f._ocr_find.call_args.args
        # 必须是任务中心绝不含的问卷独有词（零误报）
        self.assertIn("Badcase", args)
        self.assertIn("反馈问卷", args)
        self.assertNotIn("问题反馈", args)      # 任务中心的任务行名，会造成误报
        self.assertEqual(f._ocr_find.call_args.kwargs.get("region"),
                         flow_mod.AD_TOP_REGION)
        self.assertEqual(f._ocr_find.call_args.kwargs.get("retries"), 1)


class TestTopStripScan(Base):
    """方案1（2026-09-14）：`_top_strip_scan` 单次顶条 OCR 双检。"""

    def setUp(self):
        super().setUp()
        del self.f._top_strip_scan   # 移除 Base 桩，测真实实现

    def _scan_setup(self, words):
        img = mock.Mock()
        img.crop.return_value = img
        self.f._ocr_shot = mock.Mock(return_value=img)
        self.fake_pt.image_to_data.return_value = _ocr_data(words)

    def test_pill_hit(self):
        self._scan_setup([("关闭广告", 100, 140, 88, 24)])
        pill, overlay = self.f._top_strip_scan()
        self.assertTrue(pill)
        self.assertFalse(overlay)

    def test_overlay_hit_includes_profile_keys(self):
        # 资料卡词表纳入扫描（比 dump 链 F11 闸门更严，09-13 卡死教训）
        self._scan_setup([("QQ空间", 100, 140, 60, 24)])
        pill, overlay = self.f._top_strip_scan()
        self.assertFalse(pill)
        self.assertTrue(overlay)

    def test_badcase_overlay_hit(self):
        self._scan_setup([("反馈问卷", 200, 60, 80, 24)])
        _pill, overlay = self.f._top_strip_scan()
        self.assertTrue(overlay)

    def test_clean_top_strip(self):
        # 任务中心特征词不在 pill/overlay 词表 → 三信号可齐
        self._scan_setup([("每日", 300, 250, 40, 24), ("签到", 345, 250, 40, 24)])
        self.assertEqual(self.f._top_strip_scan(), (False, False))

    def test_shot_none_returns_clean_pair(self):
        self.f._ocr_shot = mock.Mock(return_value=None)
        self.assertEqual(self.f._top_strip_scan(), (False, False))


class TestTopStripFastPath(Base):
    """方案1（2026-09-14）：`_back_at_taskcenter` 三信号免 dump 快速通道。"""

    def _stub(self, scan=(False, False), tc=True):
        f = self.f
        f.ui = mock.Mock()
        f.ui.dump_fail_streak = 0
        f._taskcenter_confirmed_by_ocr = mock.Mock(return_value=tc)
        f._top_strip_scan = mock.Mock(return_value=scan)
        f._find = mock.Mock(return_value=Node("每日签到", 0, 0, 100, 100))
        f._ocr_find = mock.Mock(return_value=None)   # 顶部无关闭按钮
        f._overlay_visible_in_top = mock.Mock(return_value=False)
        return f

    def test_three_signals_pass_skips_dump(self):
        f = self._stub(scan=(False, False))
        self.assertTrue(f._back_at_taskcenter())
        f._find.assert_not_called()          # 一次 dump 都没付出

    def test_pill_present_falls_back_to_dump_chain(self):
        f = self._stub(scan=(True, False))
        self.assertTrue(f._back_at_taskcenter())
        f._find.assert_called()              # 信号②异常 → 落回 dump 严格链

    def test_overlay_present_falls_back_to_dump_chain(self):
        f = self._stub(scan=(False, True))
        self.assertTrue(f._back_at_taskcenter())
        f._find.assert_called()

    def test_knob_off_restores_dump_chain(self):
        f = self._stub(scan=(False, False))
        f.wf["tc_confirm_skip_dump"] = False
        self.assertTrue(f._back_at_taskcenter())
        f._find.assert_called()

    def test_ocr_miss_short_circuits_before_scan(self):
        f = self._stub(tc=False)
        self.assertFalse(f._back_at_taskcenter())
        f._top_strip_scan.assert_not_called()


class TestPrefetchClose(Base):
    """E 优化（2026-09-14）：等待窗口预定位关闭按钮 + `_close_ad` 单次消费。"""

    def _stub(self, confirm=True):
        f = self.f
        f.ui = mock.Mock()
        f.ui.dump_fail_streak = 0
        f._taskcenter_confirmed_by_ocr = mock.Mock(return_value=False)
        f._tap = mock.Mock()
        f._tap_node = mock.Mock()
        f._confirm_direct_close = mock.Mock(return_value=confirm)
        f._find_close_node = mock.Mock(return_value=None)
        f._ad_close_pos = mock.Mock(return_value=None)
        f._dismiss_badcase = mock.Mock()
        f._back_at_taskcenter = mock.Mock(return_value=False)
        f.wf["ad_close_retries"] = 1         # 兜底 1 轮，封住长尾
        return f

    def test_prefetch_consumed_single_use(self):
        f = self._stub()
        f._prefetch_close = ((141, 150), flow_mod.time.time())
        self.assertTrue(f._close_ad())
        f._tap.assert_called_once_with(141, 150, pause=1.5, trusted=True)
        f._find_close_node.assert_not_called()   # 跳过 dump 定位段
        self.assertIsNone(f._prefetch_close)     # 用完即弃

    def test_prefetch_stale_falls_back_to_dump_locate(self):
        f = self._stub()
        f._prefetch_close = ((141, 150), flow_mod.time.time() - 30.0)
        self.assertFalse(f._close_ad())
        f._tap.assert_not_called()
        f._find_close_node.assert_called()       # TTL 过期 → 走原 dump 定位链

    def test_step0_taskcenter_guard_blocks_prefetch(self):
        # F8 防线：步骤 0 已确认在任务中心 → 绝不消费预定位坐标
        #（否则 (141,150) 落到顶部 banner 命中区，复刻误开 AI 好友 H5）
        f = self._stub()
        f._taskcenter_confirmed_by_ocr = mock.Mock(return_value=True)
        f._prefetch_close = ((141, 150), flow_mod.time.time())
        self.assertTrue(f._close_ad())
        f._tap.assert_not_called()
        self.assertIsNotNone(f._prefetch_close)  # 留给 _watch_ad_once 收尾清空

    def test_prefetch_confirm_fail_selfheals(self):
        f = self._stub(confirm=False)
        f._prefetch_close = ((141, 150), flow_mod.time.time())
        self.assertFalse(f._close_ad())
        f._dismiss_badcase.assert_called()       # 与直关失败同款巡检自愈

    def test_prefetch_helper_sets_and_clears_cache(self):
        f = self.f
        f._ad_close_pos = mock.Mock(return_value=(141, 150))
        f._prefetch_ad_close_pos()
        pos, _ts = f._prefetch_close
        self.assertEqual(pos, (141, 150))
        f._ad_close_pos.return_value = None      # 插屏/版式不同读不到
        f._prefetch_ad_close_pos()
        self.assertIsNone(f._prefetch_close)

    def test_watch_ad_once_clears_prefetch_after_close(self):
        # 预定位坐标只在本观看周期内有效：_close_ad 返回后一律作废
        f = self.f
        f.ui = mock.Mock()
        f.wf.update({"ad_wait_min": 0.01, "ad_wait_max": 0.02,
                     "ad_times_per_robot": 10, "ad_prefetch_pos": False})
        f._find_row = mock.Mock(
            return_value=(mock.Mock(), mock.Mock(), (2, 10)))
        f._tap_node = mock.Mock()
        f._close_ad = mock.Mock(return_value=True)
        f._prefetch_close = ((141, 150), flow_mod.time.time())
        self.assertTrue(f._watch_ad_once())
        self.assertIsNone(f._prefetch_close)


if __name__ == "__main__":
    unittest.main()