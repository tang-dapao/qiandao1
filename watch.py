"""异常探测聚合层：把「和预期不一样」的现场变成一条可读告警。

职责边界：
- **只做判定与聚合**，不做任何页面操作 —— 挂接点全部是 flow 里已有的
  判据（弹窗锚点、进台失败、广告关闭失败…）命中后顺路调一次，**不新增
  任何 dump / 截图 / OCR**（截图只在真正要告警时才取一次）。
- **绝不影响主流程**：所有方法内部 try/except 全吞，慢了也只在后台线程。

为什么需要它：官方改版时同类事件会在一次跑批里命中上百次，直接推微信会
把人淹掉。所以：
- 「新弹窗」这类**一次性、信息量高**的事件 → 立即推（按锚点文案去重）；
- 「进台失败 / 广告关闭失败」这类**抖动大**的事件 → 连续 N 次才算数，
  成功一次就清零（阈值在 config.yaml → watch.*）。

接线（flow.py 内）：
    from watch import get_watch
    self._watch = get_watch(config)
    self._watch.bind(self)               # __init__ 末尾，供取屏/取屏文
    self._watch.enter_block(name)        # run_robot：3 次进台全失败
    self._watch.ad_result(ok, ...)       # _watch_ad_once：广告结果
    self._watch.popup(anchor)            # _dismiss_newfeat_popup：弹窗
"""
from __future__ import annotations

import io
import logging
import threading

import notify

log = logging.getLogger("watch")

_DEFAULTS = {
    "enabled": True,
    "enter_block": 2,      # 连续 N 台「进台重试 3 次仍失败」才告警
    "ad_fail": 3,          # 连续 N 支广告失败（未关闭 / 没打开）
    "task_fail": 3,        # 连续 N 次签到/反馈失败
    "popup_shot": True,    # 弹窗告警是否附截图
}


class Watch:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        raw = cfg.get("watch") if isinstance(cfg.get("watch"), dict) else {}
        self.conf = dict(_DEFAULTS)
        self.conf.update({k: raw[k] for k in raw if k in _DEFAULTS})
        self.enabled = bool(self.conf["enabled"])
        self.robot = ""
        self._flow = None
        self._c: dict = {}
        self._lock = threading.Lock()
        # 2026-11-03（用户：minigame 告警一天推 7 条太吵）：`signin_verify(once=True)`
        # 用的一次性抑制集合 —— 同一 key 当天只在**第一次**达标时推一条，之后
        # 无论再触发多少次都不再推（计数照常累加，日志里仍可见）。
        # 与 notify 的 `dedup_window`（默认 30 分钟滚动窗口）互补：那个是限频，
        # 这个是「整轮跑批只推一次」。
        self._once_done: set = set()
        # 推送通道与「异常探测开关」解耦：watch.enabled 只决定是否**探测异常**，
        # 账号进度推送（flow._push_account_summary）用同一条通道，关掉探测
        # 不应连带把进度消息也关掉（各自有独立开关 account_summary）。
        try:
            self.notifier = notify.get_notifier(cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("告警通道初始化失败（降级为仅落盘）：%s", e)
            self.notifier = None

    # ------------------------------------------------------------ 内部
    def bind(self, flow) -> None:
        """绑定 Flow 实例（只用于取屏文 / 截图，失败一律降级为空）。"""
        self._flow = flow

    def set_robot(self, name: str) -> None:
        self.robot = name or ""

    def _screen_texts(self, n: int = 10) -> str:
        try:
            return self._flow._screen_texts(n) if self._flow else ""
        except Exception:
            return ""

    def _shot(self, tag: str) -> str:
        """真正要告警时才截一张（复用 flow 的取图通道，零新增链路）。"""
        try:
            if not self._flow or not self.notifier:
                return ""
            img = self._flow._ocr_shot()
            if img is None:
                return ""
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return self.notifier.save_shot(buf.getvalue(), tag)
        except Exception as e:
            log.debug("告警截图失败（忽略）：%s", e)
            return ""

    def _emit(self, title: str, detail: str, *, kind: str, level: str = "warn",
              dedup_key: str = "", shot: str = "", extra: dict | None = None,
              dedup: bool = True) -> None:
        if not self.notifier:
            log.warning("[探测][未启用推送] %s", title)
            return
        ex = {"robot": self.robot} if self.robot else {}
        if extra:
            ex.update(extra)
        try:
            self.notifier.alert(title, detail, kind=kind, level=level,
                                dedup_key=dedup_key, extra=ex, shot_path=shot,
                                dedup=dedup)
        except Exception as e:
            log.warning("告警投递异常（忽略）：%s", e)

    def push(self, title: str, detail: str, *, kind: str = "info",
             level: str = "info", extra: dict | None = None) -> None:
        """普通通知（不做同类去重）—— 用于「每账号跑完」这类必须每次都发的统计。

        注意：只看**推送通道**是否可用，不看 watch.enabled —— 用户关掉异常探测
        时，账号进度推送仍应照常（两者语义独立）。
        """
        if not self.notifier or not getattr(self.notifier, "enabled", False):
            return
        try:
            self._emit(title, detail, kind=kind, level=level, extra=extra, dedup=False)
        except Exception as e:
            log.warning("push 异常（忽略）：%s", e)

    def _bump(self, key: str) -> int:
        with self._lock:
            self._c[key] = self._c.get(key, 0) + 1
            return self._c[key]

    def _reset(self, key: str) -> None:
        with self._lock:
            if self._c.get(key):
                self._c[key] = 0

    # ------------------------------------------------------------ 事件：弹窗
    def popup(self, anchor: str = "", unknown: bool = False) -> None:
        """检测到官方弹窗。unknown=True 表示「有『关闭』但文案不认识」。"""
        if not self.enabled:
            return
        try:
            texts = self._screen_texts(10)
            shot = self._shot("popup_unknown" if unknown else "popup")
            if unknown:
                self._emit(
                    "发现未知弹窗（文案不在锚点表）",
                    "屏幕上存在「关闭」节点，但标题文案没命中 newfeat_anchors，"
                    "旧逻辑会**静默跳过**。很可能是官方新活动 / 新玩法弹窗，"
                    "也意味着弹窗词表需要补充。\n\n屏文：%s" % (texts or "（读取失败）"),
                    kind="popup_unknown", level="error",
                    dedup_key="popup|unknown", shot=shot,
                )
            else:
                self._emit(
                    "官方新活动 / 新玩法弹窗：%s" % (anchor or "已命中锚点"),
                    "程序已自动关闭。屏文：%s" % (texts or "（读取失败）"),
                    kind="popup", level="warn",
                    dedup_key="popup|%s" % (anchor or "hit"), shot=shot,
                    extra={"anchor": anchor},
                )
        except Exception as e:
            log.warning("popup 探测异常（忽略）：%s", e)

    # ------------------------------------------------------------ 事件：进台
    def enter_ok(self) -> None:
        self._reset("enter")

    def enter_block(self, name: str, reason: str = "") -> None:
        """某台机器人「重试 3 次仍进不去任务中心」。"""
        if not self.enabled:
            return
        try:
            n = self._bump("enter")
            th = int(self.conf["enter_block"])
            log.info("进台失败计数 %d/%d（%s）", n, th, name)
            if n < th:
                return
            self._reset("enter")
            shot = self._shot("enter_block")
            self._emit(
                "连续 %d 台进不去任务中心" % th,
                "多台机器人进台失败，典型原因是官方**任务中心版式变了**"
                "（任务行判据失效）或**新弹窗没被关掉**（弹窗锚点词表需要补）。\n\n"
                "最近一台：%s%s\n屏文：%s"
                % (name, ("（" + reason + "）") if reason else "", self._screen_texts(10)),
                kind="enter_block", level="error",
                dedup_key="enter|block", shot=shot,
                extra={"count": th, "last_robot": name},
            )
        except Exception as e:
            log.warning("enter_block 异常（忽略）：%s", e)

    def task_fail(self, kind: str, name: str = "") -> None:
        """签到 / 反馈失败（连续 N 次告警）。kind = signin / feedback。"""
        if not self.enabled:
            return
        try:
            n = self._bump("task|" + kind)
            th = int(self.conf["task_fail"])
            if n < th:
                return
            self._reset("task|" + kind)
            label = {"signin": "每日签到", "feedback": "问题反馈"}.get(kind, kind)
            shot = self._shot("task_" + kind)
            self._emit(
                "连续 %d 次「%s」失败" % (th, label),
                "任务行为本身可能变了（入口词/按钮文案/流程步骤），"
                "或页面被弹层遮挡。\n\n最近一台：%s\n屏文：%s"
                % (name or self.robot, self._screen_texts(10)),
                kind="task|" + kind, level="error",
                dedup_key="task|%s" % kind, shot=shot,
            )
        except Exception as e:
            log.warning("task_fail 异常（忽略）：%s", e)

    def task_ok(self, kind: str) -> None:
        self._reset("task|" + kind)

    # ------------------------------------------------------------ 事件：广告
    def ad_result(self, ok: bool, not_opened: bool = False,
                  no_row: bool = False, name: str = "") -> None:
        """一支广告的最终结果。no_row=根本没找到「看广告/获取随机」行。"""
        if not self.enabled:
            return
        try:
            if ok:
                self._reset("ad|fail")
                self._reset("ad|notopen")
                self._reset("ad|norow")
                return
            key = "ad|norow" if no_row else ("ad|notopen" if not_opened else "ad|fail")
            n = self._bump(key)
            th = int(self.conf["ad_fail"])
            if n < th:
                return
            self._reset(key)
            if no_row:
                title = "连续 %d 次找不到「看广告」入口行" % th
                detail = ("任务中心的广告行标题/按钮文案可能又改了"
                          "（当前判据 AD_ROW_LABELS = 看广告 / 获取随机 / 看视频赚电量）。")
            elif not_opened:
                title = "连续 %d 支广告没打开" % th
                detail = ("广告行点了但广告页没出来（重取行补点也失败）。"
                          "常见原因：入口行换了控件/文案，或点击被官方拦截。")
            else:
                title = "连续 %d 支广告关不掉" % th
                detail = ("广告页关闭链路（dump 节点 + 顶条 OCR + 物理 BACK）"
                          "连续失败，广告样式很可能改了（新增浮层/关闭按钮位置变化）。")
            shot = self._shot("ad_fail")
            self._emit(title, detail + "\n\n屏文：%s" % self._screen_texts(10),
                       kind="ad|" + ("norow" if no_row else ("notopen" if not_opened else "fail")),
                       level="error", dedup_key=key, shot=shot)
        except Exception as e:
            log.warning("ad_result 异常（忽略）：%s", e)


    def signin_verify(self, kind: str, ok: bool, detail: str = "",
                      name: str = "", once: bool = False) -> None:
        """签到链路校验（2026-10-28 用户需求）。

        kind：
          - `signin_row`：签到回读 —— 点过签到后任务行仍是 n-1/n（动作做了但没生效）
          - `signin_ad` ：签到奖励广告 —— 点击被吞（没打开）或看完电量没加
          - `minigame_reward`：试玩广告没出奖励页（坑 44，2026-11-03）—— 走的是
            普通广告也适用；奖励页是到账的先行信号（26 样本零例外），连续多支
            缺失即「官方试玩创意停发奖励」的强提示，与上面两者排查方向不同。

        ok=True 重置计数；连续 N 次（复用 watch.task_fail 阈值）才告警，避免
        单次读屏抖动刷屏。与 `task_fail` 分开计数：后者是「任务动作本身失败」，
        这里是「动作成功但结果不对」，排查方向不同（一个查入口/流程，一个查
        到账/文案变更）。
        """
        if not self.enabled:
            return
        try:
            key = "verify|" + kind
            if ok:
                self._reset(key)
                return
            # once=True（2026-11-03）：本 key 已推过就不再推 —— 用于
            # 「已知的持续性投放问题」，避免一天刷 7 条同类告警。
            if once and key in self._once_done:
                return
            n = self._bump(key)
            th = int(self.conf["task_fail"])
            if n < th:
                return
            self._reset(key)
            if once:
                self._once_done.add(key)
            title = {"signin_row": "连续 %d 次签到未生效" % th,
                     "signin_ad": "连续 %d 次签到奖励广告异常" % th,
                     "minigame_reward": "连续 %d 支试玩广告无奖励页" % th}.get(
                         kind, "连续 %d 次签到校验异常" % th)
            shot = self._shot("verify_" + kind)
            self._emit(
                title,
                (detail or "无更多细节") +
                "\n\n最近一台：%s\n屏文：%s"
                % (name or self.robot, self._screen_texts(10)),
                kind="verify|" + kind, level="warn",
                dedup_key=key, shot=shot,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("signin_verify 异常（忽略）：%s", e)


# ---------------------------------------------------------------- 单例
_WATCH: "Watch | None" = None
_LOCK = threading.Lock()


def get_watch(cfg: dict | None = None) -> Watch:
    global _WATCH
    with _LOCK:
        if _WATCH is None:
            _WATCH = Watch(cfg)
        return _WATCH
