"""QQ 机器人任务中心 自动签到/问题反馈/看广告 完整流程（adb + uiautomator 优先）。

坐标体系（竖屏 1080x1920，SurfaceOrientation=0，实测确认）：
- 截图/OCR/input tap/uiautomator 共用同一空间 1080x1920。
- uiautomator 在滚动后的任务中心 WebView 能可靠读出中文文本与真实坐标。
- 因此优先用 uiautomator find() 精确匹配文本 -> 取中心 -> tap；OCR 作为兜底。

任务中心行按钮固定 X≈879（各行右侧对齐，实测 1080 宽）。
"""
import io
import logging
import os
import random
import re
import subprocess
import time
from typing import Callable, List, Optional, Tuple

from adb_ui import AdbUI, Node

try:
    from PIL import Image
    import pytesseract
    _HAS_OCR = True
except Exception:  # noqa: BLE001
    _HAS_OCR = False

logger = logging.getLogger("flow")

# 任务中心各行「去完成/去反馈/获取随机/已完成」按钮固定 X（1080x1920，右侧对齐）
# （2026-09-28 文案改版：广告行标题「看广告」→「看视频赚电量」，见 AD_ROW_LABELS）
TASK_BTN_X = 879
# 广告任务行的「行标题」备选词（2026-09-28 官方文案改版实测）。
# 历史：行标题「看广告」+ 按钮「获取随机」；满额后按钮变「已完成」。
# 2026-09-28 真机 dump 实测（尔尔/席恩）：行标题已改为「看视频赚电量」
# （原「看广告」节点不复存在），按钮「获取随机」仍在 → 旧 _find_row("看广告")
# 匹配失败会 _find_scroll_any 上下各滚 6 次狂找（用户观察「进任务中心后一直
# 上下滑动」，滚动中偶触签到/反馈行）。现将标题备选词集中于此，统一传给
# _find_row：index 0 为主 label，其余为 alt_labels（见 AD_ROW_LABELS[1:]）。
# 文案再改只需改这一处。
AD_ROW_LABELS = ("看广告", "获取随机", "看视频赚电量")
# 2026-09-29 P1b：进台三处点击的 pause 旧行为均值（= timing.click_min/click_max
# 默认 1.5~3.0 的中点）。仅用于把 pause 收窄的秒数折成额外 wait_for 轮询次数，
# 保证 tap 后**总窗口**不短于旧行为（慢页最坏等待不缩短）。
ENTER_TAP_PAUSE_REF = 2.25
# 签到浮层「每日免费领」（1080x1920，实测）
MODAL_SIGNIN = (301, 1800)     # 左下角【签到】按钮
MODAL_CLOSE = (996, 1143)      # 右上角 ✕
# 签到成功浮层「恭喜获得」
SUCCESS_KNOW = (540, 1189)     # 【我知道了】按钮
# 左上角返回箭头三层路径（1080x1920，实测确认）
# ⚠️ 已弃用（2026-09-09 起）：退出流程 `_exit_taskcenter` 已全面改用系统 BACK 键
# （见该文件下层）—— 任务中心顶部 banner「QQ AI好友·常见问题答疑」升级为覆盖
# y≈0-440 的可点击入口后，这三个坐标全部落在 banner 区内，tap 必然误开 Badcase
# 反馈问卷。常量仅保留作历史坐标参考，**不要再用于任何 tap**。
EXIT_TC_ARROW = (90, 138)         # 任务中心顶部返回箭头 -> 聊天页
EXIT_CHAT_ARROW = (59, 139)       # 聊天页左上角返回箭头（名字左侧）-> 机器人首页
EXIT_PROFILE_ARROW = (73, 133)    # profile 左上角返回箭头 -> 机器人列表
# 广告页左上角「关闭广告」按钮（1080x1920 实测 OCR @(120,152)/(202,153)，药丸中心 ~(160,152)）
# ⚠️ 已弃用（2026-09-10 起）：**不要再用于任何 tap**。实测该坐标在「任务中心」页面上
# 正好落在顶部 banner「QQAI好友·常见问题答疑」的 WebView 自绘可点击区内（页顶时
# banner 占 y≈125-380，与按钮药丸 y≈107-187 重叠），而 banner 在 uiautomator dump
# 里根本不存在（任务中心页整页仅 20 个节点、顶部区无任何可点击节点）→ 任何基于 dump
# 的守卫都拦不住，盲点必然误开 AI 好友 H5（11:43 / 11:58 / 12:25 三次事故实锤）。
# 关闭按钮改由 OCR 正向定位并点击其自身坐标（见 `_close_ad` 第 0 步）。
AD_CLOSE = (160, 152)
# OCR 顶部条带（#3 优化 2026-09-09）：关闭按钮恒在 y≈150 → 只需对顶部条带跑 OCR。
# 高度修订（2026-09-10 实测）：**430 → 320**。原 430 把大片深色视频背景一起裁进来，
# tesseract 会在大面积渐变/噪声上放弃分页、整块返回空 —— 实测 4399 视频广告
# 3 帧全读不出「关闭广告」（哪怕肉眼极清晰、裁到 340x240 就能读出），而卡片类
# 广告正常 → 这是"关闭按钮时灵时不灵、只能靠固定坐标盲点"的真正原因。
# 扩样验证（9 广告帧 + 10 任务中心帧）：430 → 6/9 命中；320/300/260 → **9/9 命中**；
# 三种高度对任务中心均 0 误报。取 320 留裁字余量。
AD_TOP_REGION = (0, 0, 1080, 320)

# ---- 顶部/中部 banner（F11 2026-09-10：必做项·最高优先级）----
# 任务中心页有一张**推广 banner**「QQAI好友·常见问题答疑」（WebView 自绘、可点击）。
# 点中它会打开 AI 好友反馈 H5，并在 ~37s 后延迟弹「QQ AI好友Badcase反馈问卷」
# → 破坏后续页面判定。这就是用户 2026-09-09/10 反复反馈的「点到 banner 图 /
# 广告时跳到问题反馈」。
#
# **实测（2026-09-10 两台真机 dump + 截图，非臆测）**：
#   - banner **不在 uiautomator dump 里** —— 游迦帧 / 代柯帧均为 20 个节点，
#     顶部区只有「客服」「收支详情」等真实控件，无 banner 文案
#     （probe_f11_live.py 实测输出）。
#   - banner 的**纵坐标随版式漂移**：游迦页在 y≈116-366（顶格，电量卡之上）；
#     代柯页在 y≈1151-1410（在「开通解锁以下专属权益」卡之下、电量卡之上）。
#     ⇒ **没有任何固定 y 区间能覆盖所有版式**，靠"顶部禁点区"是不够的。
#
# 结论（根治手段，与版式无关）：**只要不盲点坐标，就永远不会命中它** ——
# banner 不在 dump 里，因此所有基于真实 dump 节点的 `_tap_node` 天然安全；
# 于是规定「**任务中心页禁止盲点坐标点击**」，见 `_tap`。
# 以下常量保留用于诊断/文档（banner 文案 + 顶部条带范围）。
BANNER_HINT_TEXTS = ("QQAI好友", "QQ AI好友", "常见问题答疑")
BANNER_TOP_STRIP = (0, 0, 1080, 440)   # 历史观测到的"顶格版式"banner 纵区


def _in_region(region: Tuple[int, int, int, int], x: int, y: int) -> bool:
    """点 (x, y) 是否落在矩形 region=(x1,y1,x2,y2) 内（含边界）。诊断用。"""
    x1, y1, x2, y2 = region
    return x1 <= x <= x2 and y1 <= y <= y2


# ---- 子进程超时（2026-09-10 看门狗，配合 adb_ui.CMD_TIMEOUT/DUMP_TIMEOUT）----
# exec-out screencap 传输 1080x1920 PNG，偶发卡顿；正常 <1s，15s 必属异常。
SHOT_TIMEOUT = 15.0
# 退出任务中心时三层 BACK 的层间等待（2026-09-10 优化）：全局 timing.page_wait
# （config 现为 2.5s）是为"整页切换/等待加载"设计的，而物理返回的过渡远快于此，
# 实测 1.2s 已足够稳定 —— 三层共省约 4s；万一判漏还有 _safe_back_to_robot_list 兜底。
EXIT_LAYER_WAIT = 1.2


# ---- 页面结构锚点（1080x1920，实机 uiautomator dump 验证 2026-09-09）----
# 底部 tab bar：文本节点 y 1861-1904、带 selected 属性；频道 已被用户停用，
# 剩余 消息/联系人/动态 3 个主 tab（x 约 151/496/871）。
TAB_Y = 1840                 # tab bar 上沿（>= 此 y 视为底部导航区）
CONTACTS_TAB = "联系人"
# 联系人页中部「分类行」：分组/好友/群聊/频道/机器人/设备 横向并排。
# 实机两轮 dump 对比发现——「机器人」分类项的 y 位置会随页面状态大幅
# 漂移（聊天返回后分类行被吸附到顶部 y≈201-352；普通联系人页中段 y≈434-
# 585），**唯独 x 范围稳定**（x1=677, x2=880）。若继续用 y 窗口，强判据会
# 在某些状态（如聊天返回后）恒 False → 导航空循环 2 分钟（实测）。改用 x 范围
# 作为「分类项」特征才是稳态锚点。
ROBOT_CAT_TEXTS = ("机器人",)
ROBOT_CAT_X1 = (670, 690)       # 机器人 文本节点 x1 范围（实测 677）
ROBOT_CAT_X2 = (860, 895)       # 机器人 文本节点 x2 范围（实测 880）
# 机器人行名字文本几何（实测 183 起、宽<=230；行高 ~68，可见带 350-1850）。
# ROW_SAFE_Y：行中心 y 必须严格小于它 —— 否则行被底部 tab bar 压住/半露出，
# tap 中心会落在 tab bar 上切走主 tab（实测：贴底行「藤非」y1896-1920，
# 中心(256,1908) 正好命中【消息】tab —— 即“看完广告下一台必卡消息下滑”根因）。
ROW_X1 = (180, 195)
ROW_WIDTH_MAX = 230
ROW_Y = (350, 1850)
ROW_SAFE_Y = 1830
# 机器人行距（实测 09-17：代柯 1226 → 尔尔 1388 → 古禹 1550，等距 162px）。
# 用于幽灵行防御：目标行与吸顶分类栏下缘的间距 < 一个行距 → 该槽位实为
# 「我添加的机器人」分组头的回收残影区（坑 15），不可直接点击。
ROW_PITCH = 162
# 分类栏「吸顶态」判定阈值：吸顶时 机器人 分类节点 y1≈201；未吸顶（页面
# 滚动到顶）时 y1≈688。取中间值 450 区分两种状态。
CAT_BAR_PINNED_Y = 450
# F9: tap 机器人行的安全 y 上限（实测 藤非 行中心 y=1771 离底部 nav 太近，
# tap 落 nav 边缘手势区被 QQ 弹回消息 tab，3/3 失败；上移 31px 到 1740 =
# TAB_Y-100 留 100px 缓冲。低于此上限的行（如游迦 1684）不受影响）。
TAP_Y_MAX = TAB_Y - 100       # 1740
# D1（坑 12）：机器人列表「禁止词」veto 的位置阈值。bot profile/聊天页的
# 「发消息」按钮固定落在底部操作栏（实测 y≈1500-1900）。MuMu/uiautomator 的
# dump 可能是**多页残影混合**（穿透 dump）：一份 nodes() 里可同时含机器人列表
# 文本与旧/后台页残留节点（如个人名片卡上的「发消息」）。旧判据"任意位置出现
# 禁止词即否决"会被单个残留「发消息」误杀 → _nav_robot_list 空转、safe_back 反复
# BACK（藤非/裴旖 卡 4-5 分钟）。故「发消息」只有在落到底部操作栏(y>=此阈值)才
# 视为确凿 profile/聊天页，才否决；中区/随机位置的视为残影容忍。
FORBIDDEN_ACTION_Y = 1400

# ---- 多账号切换（2026-09-24 真机实测）----
# QQ 自带多账号列表的切换链路：联系人页左上角「账户及设置」→ 侧栏「切换账号」
# → 账号列表弹窗 → 点目标账号行。实测免密秒切（~8.5s，4/4 成功）。
# 账号列表每项 = 「昵称 + UIN」两行 dump 节点：**UIN 唯一稳定，昵称可能重复/改动**。
# ⚠️ 列表顺序会变（当前登录账号置顶）→ 只能按 UIN 文本定位，**禁止记坐标**。
ACCOUNT_ENTRY_TEXT = "账户及设置"   # 联系人页左上角入口（实测 y 66-201）
SWITCH_ACCOUNT_TEXT = "切换账号"    # 侧栏内切号入口
# 切号完成判据：回到联系人页（「新朋友」是联系人页首屏稳定元素，y≈363-514）
CONTACT_PAGE_HINT = "新朋友"


class Flow:
    # ---- A2/A3 缓存（2026-09-12）：类级默认，防 __new__ 绕过 __init__ 时缺属性 ----
    # （单测大量用 Flow.__new__(Flow) 绕过 __init__ 构造对象，实例属性在
    # _init_cache_state() 里赋值；类级 None/空容器仅作兜底，正常实例化时
    # __init__ 会覆盖。）
    _shot_ttl: float = 0.8
    _shot_cache = None
    _shot_ts: float = 0.0
    _ocr_result_ttl: float = 0.5
    _ocr_result_cache: dict = {}
    # E 优化（2026-09-14）：广告等待窗口内预定位的关闭按钮缓存 (pos, ts)|None。
    # 仅在 _watch_ad_once 单次观看周期内有效：设置后由 _close_ad 单次消费（用完
    # 即弃），观看周期结束（_close_ad 返回后）由 _watch_ad_once 显式清空 —— 防
    # 旧坐标泄漏到其它调用路径（残留清场等）被当成新广告的关闭按钮误点。
    _prefetch_close = None
    # 多账号配置（2026-09-24）：类级默认空 dict，防 __new__ 绕过 __init__ 时缺属性。
    acc: dict = {}
    # 反馈内容已用集合（2026-09-26 用户方案：5 位随机数 + 持久化防重）。
    # 类级默认 None（懒加载标记），防 __new__ 绕过 __init__ 时缺属性。
    _fb_used = None

    def __init__(self, config: dict, ui: AdbUI, ocr=None):
        self.cfg = config
        self.ui = ui
        self.ocr = ocr          # 可空，仅 OCR 兜底用
        self.t = config["timing"]
        self.wf = config["workflow"]
        # 多账号配置（2026-09-24）：主流程逐账号切号跑。缺省 {} = 不启用。
        self.acc = config.get("accounts") or {}
        if _HAS_OCR:
            _cmd = config.get("ocr", {}).get("tesseract_cmd")
            if _cmd:
                pytesseract.pytesseract.tesseract_cmd = _cmd
            self._ocr_lang = config.get("ocr", {}).get("lang", "chi_sim+eng")
        # 寻路快路径状态：_exit_taskcenter 的三层返回终点必然是机器人列表。
        # True 时 _nav_robot_list 用单次快照校验通过即跳过整个 tab 导航。
        # 初始 False（首跑/手动起点走完整导航，最安全）。
        self._at_robot_list = False
        # F11 页面状态位：是否「当前处于某机器人的任务中心页」。
        # 仅用于禁止任务中心页的**盲点坐标点击**（见 _tap）—— 该页有 WebView 自绘的
        # 推广 banner（不在 dump 里、纵坐标随版式漂移），盲点极易命中它造成误触；
        # 而列表页/联系人页的合法坐标点击（机器人首行 y≈410-500、机器人分类行
        # y≈201-352）必须放行，故用页面状态位限定。不产生任何 adb 开销。
        self._page_tc = False
        # 方案 A（2026-09-28 用户需求）：进台即读广告配额缓存。_enter_taskcenter
        # 成功返回前读一次「看广告/获取随机」行 X/Y 暂存于此，供后续
        # _rotate_watch_once / _watch_ads_session 的 base 校准直接复用 ——
        # 免去它们进台后再次滚动查找该行（原实现读不到会 swipe 上下反复找，
        # 即用户观察到的"进任务中心后一直下滑刷新"）。
        # 语义：None=未读到/未进台（消费端回退到原地读屏，行为与旧版一致）；
        #       (X, Y)=进台瞬间屏幕进度（消费端算 base = X - done）。
        self._tc_entry_ratio = None
        # 电量统计（2026-09-29 用户需求）：账号级「本流程获得电量」。
        # _energy_start = 本账号第一台机器人首次进台后读的「当前电量」；
        # _energy_end = 广告阶段全部结束后补一次进台读的终值；差值即本地
        # 流程获得电量。读不到为 None（不阻断主流程，汇总标注失败）。
        self._energy_start: Optional[int] = None
        self._energy_end: Optional[int] = None
        # 起始读数「已尝试」标志：读失败（覆盖层/版式差异）也只试一次 ——
        # 否则每次进台重读，失败场景 90 支 × 2-4s 纯浪费。中途补读的值也
        # 不是真「起始」，单次尝试是诚实语义。
        self._energy_start_done = False
        # 运行汇总记账（2026-09-22 用户需求）：每台机器人 签到/反馈结果 +
        # 本次进程看广告支数。全部复用现有分支顺手记账，零额外读屏开销；
        # 流程结束由 _log_summary() 统一输出（日志 + logs/summary_*.txt）。
        self.stats = {}
        # 反馈防重记录文件（2026-09-26 用户方案）：每次生成反馈内容前排除历史
        # 已用、生成后追加记录（跨进程防重复 —— 官方按「账号+内容」去重且窗口
        # 未知，宁可永久不用）。相对路径与 _diag_shot 的 screenshots/ 风格一致；
        # 单测里置 None 可完全跳过文件 IO。
        self._fb_used_path = "logs/feedback_used.txt"
        # ---- A2/A3 缓存（2026-09-12）：截图 TTL 缓存 + OCR 结果短缓存 ----
        # 背景：_ocr_shot 每次都跑一次 screencap subprocess（~0.5-1s），而
        # _dismiss_badcase 一次连做两张全屏 OCR、_close_ad 确认阶段反复读屏 ——
        # 同一页面状态内的重复读屏纯属浪费。现给截图加 0.8s TTL 缓存（与
        # nodes() 的 0.8s TTL 对齐），给 OCR 结果加 0.5s TTL 缓存。
        # 失效时机（关键，防旧帧误判）：
        #   1) 任何界面动作后 —— 通过 ui.on_action 回调钩子自动失效；
        #   2) TTL 到期 —— 同 nodes() 一样按时间自然过期；
        #   3) ui.refresh()（强制重 dump）语义上意味着"画面不可信"——refresh
        #      本身不触发 on_action（不是界面动作），故 _reconfirm_click_target
        #      等调用 refresh() 的路径也依赖 TTL 自然过期兜底；关键点击前均有
        #      等待器/重确认，风险有界。
        self._init_cache_state()
        ui.on_action = self._invalidate_shot_cache

    def _init_cache_state(self):
        """初始化截图/OCR 结果缓存状态（A2/A3）。独立方法便于 __new__ 绕过
        __init__ 的单测对象按需补齐（类级默认已兜底，此处正常实例化用）。"""
        self._shot_cache = None
        self._shot_ts = 0.0
        self._ocr_result_cache = {}   # key -> (pos|None, ts)
        # 方案 A（2026-09-29 游迦 9/10 实锤）：本次 _close_ad 是否「可疑」——
        # 全程未点到任何关闭按钮、仅凭 OCR 判定已在任务中心就返回成功。
        # 该路径无法区分「广告已自动播完（官方已计入）」与「tap 从未触发/
        # 广告页从未打开（未计入）」→ _watch_ads_session 收尾据此决定是否
        # 读屏复核配额（详见 _verify_session_quota）。
        self._ad_close_suspicious = False
        # 反馈内容已用集合（2026-09-26）：None=未从记录文件懒加载；单测可预设
        # 空 set 跳过文件 IO。防重细节见 _new_feedback_text。
        self._fb_used = None

    # ----------------------------------------------------------
    # 基础：uiautomator 定位（带滚动）
    # ----------------------------------------------------------
    def _find(self, text: str, ymin: int = 0, ymax: int = 99999,
              xmin: int = 0, xmax: int = 99999,
              timeout: Optional[float] = None) -> Optional[Node]:
        """按文本精确查找第一个节点（可限定区域；F1 加 x 范围支持机器人分类）。
        timeout 透传给 dump，供关闭广告场景压低单次 dump 等待（#2）。
        """
        return self.ui.find(text, ymin=ymin, ymax=ymax, xmin=xmin, xmax=xmax,
                             timeout=timeout)

    # ----------------------------------------------------------
    # 任务中心行定位 + 完成度判定（2026-09-09 校准点）
    # ----------------------------------------------------------
    # 任务中心行内含 "X/Y" 计数：1/1=今日已签到/已反馈/已完成；X/10=已看广告 X 次。
    # 实测截图证据（2026-09-10）：每日签到 1/1 +「连签2天」；问题反馈 1/1 +「去反馈」；
    # 广告行 2/10 +「获取随机」按钮；**满额（10/10）后按钮文案变「已完成」**
    # （代柯 dump 实锤）。注意 X/Y 计数在 dump 里可能被拆成多个节点（'10' '/' '10'），
    # 解析见 _row_completion 第 2 遍。
    # ⚠️ 行标题**会**随官方改版变化（2026-09-28 实锤：「看广告」→「看视频赚电量」，
    # 原「看广告」节点不复存在）——不要用"行标题不变"做任何设计假设，行定位统一走
    # AD_ROW_LABELS 常量（本文件顶部），X/Y 计数才是稳定判据。
    # 分子/分母均限 2 位（2026-09-30 修 /1000 串扰）：本 app 任务行配额恒为
    # 1（签到/反馈 1/1）或 X/10（广告），2 位足矣；限位可避免把邻近数字
    # （电量等）拼进分母（实测偶发把 "…2/10" 读成 (2,1000)）。
    _ROW_RATIO_RE = re.compile(r"^\s*(\d{1,2})\s*/\s*(\d{1,2})\s*$")

    def _row_completion(self, label_node: Node) -> Optional[Tuple[int, int]]:
        """读 label 行内的 X/Y 计数（仅同 y 范围 ±30px 的节点，避免误命中其他行）。
        返回 (X, Y) 或 None。

        2026-09-10 修复「满额不直退」根因：X/Y 计数在 dump 里可能被拆成多个
        节点（代柯 10/10 实测为 '10' '/' '10' 三个独立节点，签到 1/1 同理）。
        旧实现只做单节点整词 fullmatch 恒失配 → ratio 恒 None → 配额校验
        （_ad_quota_done / _read_ad_ratio）整体失效。现第 2 遍把行内节点按 x
        序拼接后 search——'看广告'+'10'+'/'+'10'+'已完成' 拼为
        「看广告10/10已完成」即可提取 (10,10)。"""
        cur_y = (label_node.y1 + label_node.y2) // 2
        band = [nd_ for nd_ in self.ui.nodes()
                if abs(((nd_.y1 + nd_.y2) // 2) - cur_y) <= 30]
        # 第 1 遍：单节点整词（旧行为，MuMu 有时把计数合并为一个节点）
        for nd_ in band:
            m = self._ROW_RATIO_RE.fullmatch(nd_.text.strip())
            if m:
                return int(m.group(1)), int(m.group(2))
        # 第 2 遍：拆分节点按 x 序拼接后 search
        band.sort(key=lambda nd_: nd_.x1)
        joined = "".join(nd_.text for nd_ in band)
        m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", joined)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None

    def _find_row(self, label: str, max_scroll: int = 6,
                  alt_labels: Tuple[str, ...] = ()
                  ) -> Optional[Tuple[Node, Node, Optional[Tuple[int, int]]]]:
        """滚动找 label 行，返回 (按钮区域 Node, label 文本 Node, ratio(X,Y)|None) 或 None。
        调用方根据 ratio 判定"今日已完成"——避免点击已签/已反馈/已看满的任务。

        alt_labels（2026-09-10）：同轮滚动里同时匹配的备选词 —— 满额后按钮
        文案变化（获取随机 → 已完成，代柯 10/10 实测），用「行标题 + 按钮词」
        同轮查找一次命中，避免两轮各 28s 空滚（期间易被重弹问卷盖住）。
        2026-09-28：行标题亦会改版（「看广告」→「看视频赚电量」），广告行调用方
        统一传 AD_ROW_LABELS[0] / AD_ROW_LABELS[1:]（本文件顶部常量），
        文案再改只改一处。"""
        if alt_labels:
            n = self._find_scroll_any((label,) + tuple(alt_labels),
                                      max_scroll=max_scroll)
        else:
            n = self._find_scroll(label, max_scroll=max_scroll)
        if not n:
            return None
        cy = (n.y1 + n.y2) // 2 + 24
        btn = Node(label, TASK_BTN_X - 45, cy - 22,
                   TASK_BTN_X + 45, cy + 22)
        ratio = self._row_completion(n)
        return btn, n, ratio

    # ----------------------------------------------------------
    # 页面卡死检测与诊断（P0：防 dump 连续失败空转 / 盲点乱导航）
    # ----------------------------------------------------------
    def _screen_texts(self, limit: int = 15) -> str:
        """诊断用：当前屏可见文本摘要（dump 失败返回 <无文本/dump失败>）。"""
        try:
            cur = self.ui.nodes()
            if not cur:
                return "<无文本/dump失败>"
            return " | ".join(x.text for x in cur[:limit] if x.text)
        except Exception:  # noqa: BLE001
            return "<读屏异常>"

    def _diag_shot(self, tag: str):
        """失败现场截图（F5）：存 screenshots/fail_<tag>_<HHMMSS>.png。
        尽力而为，失败不影响主流程；供真机排障还原当时页面。"""
        try:
            img = self._ocr_shot()
            if img is None:
                return
            fn = f"screenshots/fail_{tag}_{time.strftime('%H%M%S')}.png"
            img.save(fn)
            logger.info("失败现场已存 %s", fn)
        except Exception as e:  # noqa: BLE001
            logger.warning("失败现场截图失败: %s", e)

    def _dump_stuck(self) -> bool:
        """导航上下文：页面连续 dump 完全失败(>=3 次调用)视为卡死（如误入
        浏览器/动画页无法 idle），应中止当前导航转恢复，不再空转重试。"""
        return self.ui.dump_fail_streak >= 3

    def _find_scroll(self, text: str, max_scroll: int = 10,
                     down_first: bool = True,
                     ok: Optional[Callable[[Node], bool]] = None) -> Optional[Node]:
        """在可滚动页面里找文本：上下滚动范围内定位。返回 Node 或 None。"""
        return self._find_scroll_match(lambda x: x.text == text, max_scroll,
                                       down_first, ok)

    def _find_scroll_any(self, labels, max_scroll: int = 6,
                         down_first: bool = True,
                         ok: Optional[Callable[[Node], bool]] = None) -> Optional[Node]:
        """_find_scroll 的多文本版：同轮滚动里任一 label 命中即返回（2026-09-10）。

        用途：任务中心行定位 —— 满额后按钮文案变化（获取随机 → 已完成）；
        行标题也可能改版（2026-09-28「看广告」→「看视频赚电量」，见 AD_ROW_LABELS）；
        多词同轮查找，避免先 28s 空找按钮词再重滚找标题（期间可能被 ~40s 重弹的
        Badcase 问卷盖住，18:35 实测）。"""
        return self._find_scroll_match(lambda x: x.text in labels, max_scroll,
                                       down_first, ok)

    def _find_scroll_match(self, match: Callable[[Node], bool],
                           max_scroll: int = 10, down_first: bool = True,
                           ok: Optional[Callable[[Node], bool]] = None
                           ) -> Optional[Node]:
        """在可滚动页面里按谓词找节点：上下滚动范围内定位。返回 Node 或 None。

        寻路优化（2026-09-09）：滚动后若屏内文本集合无任何新内容（已滚到
        页面尽头），立即停止该方向，不再机械滚满 max_scroll —— 机器人列表/
        任务中心都是短列表，原实现每方向固定滚 10 次是无谓 dump+swipe。
        两方向各试一轮（外层 range(2)），兜住 overshoot 后需回滚找的情况。
        P0：滚动途中页面持续 dump 失败（卡死）→ 立即中止整个查找。
        F3：ok 回调可过滤命中节点（如机器人行几何/中心 y 必须高于 tab bar）；
        文本命中但 ok 不过（典型：行贴底被 tab bar 压住）→ 视为未命中继续
        滚动，直到该行滚到可安全点击的位置。
        """
        for _ in range(2):
            cur = self.ui.nodes()
            n = next((x for x in cur
                      if match(x) and (ok is None or ok(x))), None)
            if n:
                return n
            seen = {x.text for x in cur}
            for _ in range(max_scroll):
                if self._dump_stuck():
                    logger.warning("滚动途中页面持续 dump 失败，中止查找")
                    return None
                if down_first:
                    self.ui.swipe_up()
                else:
                    self.ui.swipe_down()
                cur = self.ui.nodes()
                n = next((x for x in cur
                          if match(x) and (ok is None or ok(x))), None)
                if n:
                    return n
                nxt = {x.text for x in cur}
                if nxt <= seen:          # 无新文本：已到尽头，提前停
                    logger.debug("滚动无新内容(已到尽头)，提前停止")
                    break
                seen = nxt
            down_first = not down_first
        return None

    def _tap_node(self, n: Node, pause: Optional[float] = None):
        # C2（2026-09-12）：坐标抖动 —— 每次都点 bounds 正中心是强"机器指纹"
        # （真人点击落点天然随机）。抖动量取「短边的一半 × 安全系数」，
        # 保证落点必然仍在节点 bounds 内（F11 安全前提不变：点的是 dump
        # 里真实存在的节点，只是中心附近偏移，不可能偏到节点外）。
        w = n.x2 - n.x1
        h = n.y2 - n.y1
        margin_x = max(0, int(w * 0.25))
        margin_y = max(0, int(h * 0.25))
        jx = random.randint(-margin_x, margin_x) if margin_x else 0
        jy = random.randint(-margin_y, margin_y) if margin_y else 0
        x = n.x1 + w // 2 + jx
        y = n.y1 + h // 2 + jy
        self.ui.tap(x, y, pause or random.uniform(self.t["click_min"],
                                                  self.t["click_max"]))

    def _tap(self, x: int, y: int, pause: Optional[float] = None,
             trusted: bool = False) -> bool:
        """坐标点击统一入口 —— **任务中心页禁止盲点坐标点击（F11 根治）**。

        实测依据（2026-09-10，两台真机 dump + 截图，非臆测）：
          - 任务中心整屏由 WebView 自绘，推广 banner「QQAI好友·常见问题答疑」
            **不在 uiautomator dump 里**（游迦/代柯帧均 20 节点，顶部区只有
            「客服」「收支详情」等真实控件）；
          - banner 的**纵坐标随版式漂移**：游迦页 y≈116-366（顶格），代柯页
            y≈1151-1410（在权益卡之下）——**没有固定 y 区间能覆盖所有版式**；
          - 它是可点击入口，点中即打开 AI 好友反馈 H5，并 ~37s 后延迟弹 Badcase
            反馈问卷 → 破坏页面判定、制造"静默假成功"。即用户反馈的
            「点到 banner 图 / 广告时跳到问题反馈」。

        根治：**只要不盲点坐标就永远命中不到它** —— banner 不在 dump，故所有基于
        真实 dump 节点的 `_tap_node` 天然安全。于是规定：**任务中心页（`_page_tc`）
        拒绝一切坐标点击**，除非调用方显式 `trusted=True`。当前合法例外仅三处：
          1. `_close_ad`：广告页左上角「关闭广告」按钮（OCR 正向定位、非盲点）；
          2. `_signin`：签到浮层内的固定按钮（浮层已由 `wait_for("每日免费领")` /
             `_find("我知道了")` 确认在屏，且浮层在任务中心页之上、非 banner）；
          3. `_watch_signin_ad`：签到奖励广告 OCR 兜底（浮层在屏 + OCR 正向读到
             「看广告」才放行，同 2 同理，2026-09-16）。

        返回是否真正执行了本次点击（被守卫拦截返回 False）。
        """
        if not trusted and self._page_tc:
            logger.error(
                "拒绝 tap (%d,%d)：任务中心页**禁止盲点坐标点击**（F11）—— 该页"
                "推广 banner「QQAI好友·常见问题答疑」不在 dump 里且纵坐标随版式"
                "漂移（实测 y≈116-366 或 y≈1151-1410），任何盲点都可能命中它 → "
                "误开 AI 好友反馈 H5 并延迟弹 Badcase 问卷。任务操作请改用 "
                "_tap_node(真实 dump 节点)；确需坐标点击请显式 trusted=True", x, y)
            return False
        self.ui.tap(x, y, pause or random.uniform(self.t["click_min"],
                                                  self.t["click_max"]))
        return True

    # ----------------------------------------------------------
    # 导航（F1：页面识别判据收敛，杜绝“在消息/频道首页误当机器人列表”）
    # ----------------------------------------------------------
    def _on_qq_main_shell(self) -> bool:
        """弱判据：当前是否在 QQ 主界面壳内（底部有 联系人 tab）。

        消息/联系人/动态 三个主 tab 首页都满足 —— 只能说明“可从此处导航到
        机器人列表”，不代表已在列表。用于退出中途 / 安全返回的停止条件
        （避免在子页继续盲点返回箭头导致误触）。
        """
        return any(x.text == CONTACTS_TAB and x.y1 >= TAB_Y
                   for x in self.ui.nodes())

    def _contacts_tab_active(self) -> bool:
        """底部「联系人」tab 是否处于激活（selected=true）。"""
        return any(x.text == CONTACTS_TAB and x.selected and x.y1 >= TAB_Y
                   for x in self.ui.nodes())

    def _robot_cat_selected(self) -> bool:
        """联系人页中部「机器人」分类是否处于选中（selected=true）。

        实机验证：QQ 联系人页中部有横向分类行（分组/好友/群聊/频道/机器人/
        设备），机器人分类选中(selected=true) 时下方才是机器人列表。底部主
        tab 与分类行文本节点都带 selected 属性。

        注：分类项的 y 位置会随页面状态大幅漂移（聊天返回后可能被吸附到
        顶部 y≈201，普通联系人页 y≈434），x 范围才是稳态锚点（实测 x1=677
        x2=880）。看 selected 属性比看 y 更可靠。
        """
        return self._robot_cat_on_nodes(self.ui.nodes())

    def _robot_cat_on_nodes(self, cur) -> bool:
        """`_robot_cat_selected` 的单快照版：在调用方给定的节点列表上判定，
        不再自己触发 dump（P0 退台减 dump，2026-09-15）。"""
        x1_lo, x1_hi = ROBOT_CAT_X1
        x2_lo, x2_hi = ROBOT_CAT_X2
        return any(x.text in ROBOT_CAT_TEXTS and x.selected
                   and x1_lo <= x.x1 <= x1_hi
                   and x2_lo <= x.x2 <= x2_hi
                   for x in cur)

    def _cat_bar_node(self, cur) -> Optional[Node]:
        """单快照版：返回中部「机器人」分类节点（selected 且 x 在实测范围），
        不在则 None。供折叠态判定 / 幽灵行防御取栏体 y 边界用（坑 15）。"""
        x1_lo, x1_hi = ROBOT_CAT_X1
        x2_lo, x2_hi = ROBOT_CAT_X2
        for x in cur:
            if (x.text in ROBOT_CAT_TEXTS and x.selected
                    and x1_lo <= x.x1 <= x1_hi
                    and x2_lo <= x.x2 <= x2_hi):
                return x
        return None

    def _collapsed_groups_on_nodes(self, cur) -> bool:
        """单快照版：当前是否处于「分组折叠态」—— QQ 主壳 + 机器人分类
        selected 都在，但屏上没有任何平铺机器人行（坑 15：10:56 实测，
        此态 dump 读不到分组头节点、_looks_like_robot_list 旧判据会误判
        True → 导航快路径/安全返回全部短路、4 台连环弃权）。"""
        if not any(x.text == CONTACTS_TAB and x.y1 >= TAB_Y for x in cur):
            return False
        if self._cat_bar_node(cur) is None:
            return False
        return not any(self._is_robot_row(x) for x in cur)

    def _looks_like_robot_list(self) -> bool:
        """强判据：当前确实停留在「机器人列表视图」才返回 True。

        = 底部 联系人 tab 存在（QQ 主壳） + 中部 机器人 分类 selected + 无
        确凿的任务中心/聊天页标志文本。

        旧判据只看“底部有 联系人 tab + 无任务中心文字” → QQ 主界面【消息】
        首页同样满足，看完广告退出后被误判“已在机器人列表”，随后在消息会话
        列表里滚动/点行（点到会话、进的不是 profile）→ 卡死根源（4-tab 布局
        时误落 频道、你停用频道后 3-tab 布局轮到 消息）。

        D1（坑 12）：正的 QQ 主壳 + 机器人分类 selected 已是列表的充分证据，
        【先看正信号】再谈 veto —— 因 MuMu 的 dump 常是**多页残影混合**（穿透
        dump），一份 nodes() 里可能同时含真正的机器人列表文本与旧/后台页残留的
        「发消息」节点。若像旧判据那样"任意位置出现禁止词即否决"，一个残留的
        「发消息」就会把真·列表页打成 False → _nav_robot_list 空转、safe_back
        反复物理返回（藤非/裴旖 卡 4-5 分钟的实机症状）。因此：
          * 任务中心/每日签到：整页特征，出现即否决（不受位置影响，防把
            真·任务中心当列表）；
          * 「发消息」：仅在落到底部操作栏(y>=FORBIDDEN_ACTION_Y，真 profile
            的按钮位置)才否决；中区/随机位置的残留视为残影**容忍**并记录日志，
            便于真机诊断。
        P0 退台减 dump（2026-09-15）：本判据原先内部调
        `_on_qq_main_shell()` / `_robot_cat_selected()` 各自 `ui.nodes()`，
        加上自身的 `cur` 共 3 次 dump。而 adb_ui.nodes() 的 0.8s TTL 以
        **dump 开始时刻**计龄，MuMu 单次 dump 实测 2.4-3.8s > TTL → 三个
        调用**必然各自重新 dump**（实测一层校验 ≈ 7.2s，占第 2/3 层 9.9s
        的大头）。现改为**单快照**：一次 `ui.nodes()` 取同一时刻的节点列表，
        正信号与否决词都在同一份上判定 —— 既省 2 次 dump（≈4.8s/层），
        也消除了"壳信号来自第 1 份 dump、选中态来自第 2 份"的跨时刻混合
        状态（页面过渡期三个快照可能各不相同）。子判据方法保留，供其它
        单独使用的调用点；测试经 fake ui.nodes() 驱动，逻辑等价不受影响。
        """
        cur = self.ui.nodes()
        # 先看正信号（列表的充分证据）：QQ 主壳 + 机器人分类 selected。
        # 不在列表（如 QQ 消息/联系人首页未选机器人、或 profile/聊天页）→ False。
        if not (any(x.text == CONTACTS_TAB and x.y1 >= TAB_Y for x in cur)
                and self._robot_cat_on_nodes(cur)):
            return False
        # 正信号已在 → 仅确凿位置的任务中心/聊天页特征才否决。
        for x in cur:
            t = x.text
            if t in ("任务中心", "每日签到"):
                return False
            if t == "发消息" and x.y1 >= FORBIDDEN_ACTION_Y:
                return False
        # 未被否决 → 在机器人列表。若存在被位置判据放行的「发消息」残影
        # （穿透 dump 残留），记录以便诊断。
        stray = [x.y1 for x in cur if x.text == "发消息"]
        if stray:
            logger.warning(
                "容忍残余「发消息」节点(y=%s)，按机器人列表处理（坑12："
                "多页残影 dump 穿透）", stray)
        # 坑 15（2026-09-17）：折叠态防御。联系人>机器人 分类选中但分组被
        # 折叠时，壳信号 + 分类 selected 仍满足、也无否决词 —— 旧判据在此
        # 误判 True → 导航快路径 0.0s 跳过、安全返回 0 次 BACK，折叠页上
        # 永远找不到行（实测 4 台 × 3 次 = 12 连败弃权）。列表展开态必然
        # 有 ≥1 个行节点（x1≈183 列），缺失即折叠态 → False，交 nav 完整
        # 导航走 OCR 展开兜底。
        if not any(self._is_robot_row(x) for x in cur):
            logger.info("强判据：壳+分类选中但无平铺机器人行 → 判定分组折叠态")
            return False
        return True

    def _wait_until(self, pred: Callable[[], bool], timeout: float,
                    interval: float = 0.5, desc: str = "") -> bool:
        """事件驱动等待：轮询 pred() 直到 True 或超时（A1）。

        用于把导航里「tap 后固定 sleep(page_wait)」改成「页面真正到达目标状态
        就立刻继续」—— 页面就绪快时省时间、就绪慢时由 timeout 兜底不早于
        安全等待。返回是否在 timeout 内满足；超时则记录日志并返回 False，
        由调用方按原有语义继续（不盲点、不提前 tap 未就绪的页）。

        参数：
          pred     每次轮询调用的谓词（通常包装 _contacts_tab_active /
                   _looks_like_robot_list 等 dump 基判据）；
          timeout  最大等待（秒）。**约定 >= 原固定 page_wait**，保证最坏情况
                   等待不短于旧行为（只在页面就绪更快时提前返回）；
          interval 两次轮询间隔（秒）；
          desc     诊断用途的等待描述，超时会带它打日志。
        """
        deadline = time.time() + timeout
        while True:
            try:
                if pred():
                    return True
            except Exception as e:  # noqa: BLE001
                # 谓词内部读屏异常（dump 失败）不算满足，继续等到 timeout
                logger.warning("_wait_until(%s) 谓词异常: %s", desc or "?", e)
            if time.time() >= deadline:
                break
            time.sleep(interval)
        logger.warning("_wait_until(%s) 超时(%.1fs)，回退到固定等待语义",
                       desc or "<无描述>", timeout)
        return False

    def _nav_target_wait(self) -> float:
        """A1：导航 tap 后「事件驱动等待」的目标超时（秒）。

        读取 timing.nav_wait（默认 8.0s），并保证其**不小于旧的固定 page_wait**
        —— 这样即便页面迟迟未切换，最坏等待也不会短于旧行为（只在页面就绪
        更快时提前返回，这正是省 50s→30s 的来源）。

        说明：tap 后的最小动画起步 floor 由 `_tap_node` 的点击 pause
        （click_min~click_max，真机 1.5-3.0s）兜底，无需再单独固定睡眠；
        单元测试冻结 time.sleep 时 floor 亦无副作用。
        """
        pw = float(self.t.get("page_wait", 2.0))
        nw = float(self.t.get("nav_wait", 8.0))
        return max(nw, pw)

    def _safe_back_to_robot_list(self, max_back: int = 6) -> bool:
        """把设备安全带回「机器人列表」（P0 + F1/F4）。

        每步先强判据校验（已在机器人列表即停）；其次若已回到 QQ 主界面壳
        （底部有 联系人 tab，可能停在 消息/联系人/动态 任一主 tab）—— 旧
        实现此时误判“已回列表”直接返回，随后在错误主 tab 上滚动找机器人名
        ——改为转 _nav_robot_list 走完整导航真正进到列表。两者都不是则物理
        返回一层（BACK 不依赖 dump），上限 max_back 次避免死循环。

        ⚠️ 觅夏资料卡卡死事故（2026-09-30）：`_on_qq_main_shell` 是**弱判据**——
        机器人资料卡（WebView/H5 全屏页，uiautomator dump **读不到**）盖顶时，
        dump 穿透读到**背景机器人列表**底部「联系人」tab → 弱判据误判“已回主壳”
        → 旧实现在此**立即**转 `_nav_robot_list` 并 `return`（实测 0.17s、0 次
        BACK 就交接）。而资料卡页上 `_looks_like_robot_list` 恒 False、导航
        （在错误坐标上乱点）必然失败 → 直接返回 False → 上层连败弃权（实测
        7 台机器人连环弃权、6.5 分钟卡死）。**BACK 是唯一能清掉这种 dump 不可见
        覆盖层的动作**，却被这一步跳过了。
        修复：完整导航**最多尝试 1 次**；导航未确认列表后**不再提前收手**，
        落下去继续物理返回（不在此 return），直到强判据通过或达 max_back 上限。
        """

        logger.warning("安全返回机器人列表（异常页兜底，上限 %d 次物理返回）",
                       max_back)
        # F11：离开任务中心语义 → 关闭 banner 禁点区（列表页 y<440 的点击合法）
        self._page_tc = False

        def _dump_failing() -> bool:
            # dump 连续失败 = 读屏盲区（Mock ui 的 dump_fail_streak 不可 int 转换
            # → 视为读屏正常，保持旧语义，测试不受影响）
            try:
                return int(self.ui.dump_fail_streak) > 0
            except Exception:
                return False

        blind = 0
        nav_tried = False   # 觅夏事故防御（2026-09-30）：主壳弱判据命中后完整导航最多 1 次
        for i in range(max_back):
            if self._looks_like_robot_list():
                self._at_robot_list = True
                logger.info("安全返回成功（第 %d 步，已在机器人列表）", i + 1)
                return True
            if self._on_qq_main_shell():
                if not nav_tried:
                    nav_tried = True
                    logger.info("已回到 QQ 主界面壳（第 %d 步），转完整导航", i + 1)
                    self._at_robot_list = False
                    self._nav_robot_list()
                    if self._at_robot_list and self._looks_like_robot_list():
                        return True
                    # 导航未确认列表：弱判据可能被 WebView 资料卡 dump 穿透欺骗
                    # （见 docstring 觅夏事故）——不再 return（旧行为会在错误页上
                    # 反复导航乱点并直接返回 False 让上层弃权），落到下方继续
                    # 物理返回。BACK 才能清掉 dump 读不到的覆盖层。
                    logger.warning(
                        "主壳弱判据命中但导航后仍未确认机器人列表（疑资料卡/子页 "
                        "被 dump 穿透盖屏）→ 不再重复导航，改为继续物理返回"
                        "（第 %d 步，觅夏事故防御 2026-09-30）", i + 1)
                # else：已导航过仍未回列表 → 直接落到下方继续 BACK
            # 盲按熔断（2026-09-13 小麦事故）：dump 连续失败时两道判据全是瞎的，
            # 继续盲按 BACK 会过度返回（退穿列表顶出资料卡，卡死后续全部机器人）
            # —— 盲区最多按 3 次就停手上报，把页面留给下一台的自愈路径。
            if _dump_failing():
                blind += 1
                if blind >= 3:
                    logger.error(
                        "安全返回中止：dump 持续失败进入盲区，已盲按 %d 次 BACK "
                        "仍无法读屏——继续盲按有过度返回风险", blind)
                    return False
            else:
                blind = 0
            self.ui.back(pause=1.5)
            time.sleep(1.0)
        self._at_robot_list = False
        logger.error("安全返回失败：%d 次物理返回后仍未到 QQ 主界面/机器人列表",
                     max_back)
        return False

    def _nav_robot_list(self):
        """导航到机器人列表（联系人 -> 中部 机器人 分类选中，列表内联展示）。

        快路径（B1+F1）：_at_robot_list 且强判据通过 → 零点击直接返回。
        完整导航（F1 收紧）：只允许两个精确动作 —— 底部 联系人 tab（未激活
        才点）与 中部 机器人 分类（未选中才点）；不在 QQ 主壳（聊天/profile/
        H5 子页）时物理返回逐层退出。旧实现从“任意页”按 y900-1250 盲找
        机器人分类并固定展开分组头，布局一换（分类行实测在 y434-585）就点空
        或点偏 → 连锁误导航。

        实机结构（2026-09-09 dump）：联系人页中部 分类行 y434-585，
        「机器人」选中后下方 y924+ 直接平铺机器人行（无 我添加的机器人 分组
        头）。老版 QQ 若有折叠分组头则在此兜底展开。
        """
        logger.info("导航到机器人列表")
        # F11：导航离开任务中心语义 → 关闭 banner 禁点区（列表/联系人页 y<440 的
        # 点击是合法的：机器人分类行 y≈201-352、机器人首行 y≈410-500）。
        self._page_tc = False
        # 状态快路径：刚从任务中心三层返回退出，理应停在机器人列表
        if self._at_robot_list and self._looks_like_robot_list():
            logger.debug("快路径命中：已在机器人列表，跳过导航")
            return
        self._at_robot_list = False
        # 入口状态恢复：若上一流程中断残留在了任务中心，先退出
        if self._find("任务中心") or self._find("每日签到"):
            logger.info("检测到残留的任务中心页面，先退出")
            if not self._exit_taskcenter():
                logger.error("残留任务中心退出失败，尝试安全返回")
                self._safe_back_to_robot_list()
            self._at_robot_list = self._looks_like_robot_list()
            return
        # D-修（2026-09-13 小麦事故）：导航前先巡检并清除浮层 —— 资料卡/问卷
        # 盖顶时 dump 穿透读到背景列表文本，导航 4 次尝试全在假列表里找人名
        # （实测每台空烧 ~6 分钟、3 台配额丢失）。清完浮层导航即可自愈。
        self._dismiss_badcase()
        ocr_tried = False   # 坑 15：折叠态 OCR 展开每次导航最多尝试 1 次
        for _ in range(4):
            if self._dump_stuck():
                logger.warning("导航途中页面持续 dump 失败，转安全返回")
                self._safe_back_to_robot_list()
                return
            if self._looks_like_robot_list():
                break
            if self._on_qq_main_shell():
                if not self._contacts_tab_active():
                    tab = self._find(CONTACTS_TAB, ymin=TAB_Y)
                    if tab:
                        logger.debug("点底部 联系人 tab @ %s", tab.center)
                        self._tap_node(tab)
                        # A1：固定 sleep(page_wait) → 事件驱动等待「联系人 tab 激活」。
                        # 页面就绪快则立即继续；超时兜底（timeout>=原 page_wait，
                        # 最坏等待不短于旧行为）后仍按原有循环语义继续，不盲点。
                        self._wait_until(self._contacts_tab_active,
                                         self._nav_target_wait(),
                                         desc="联系人tab选中")
                if not self._robot_cat_selected():
                    cat = self._find("机器人",
                                     xmin=ROBOT_CAT_X1[0],
                                     xmax=ROBOT_CAT_X2[1])
                    if cat:
                        logger.info("点中部 机器人 分类 @ %s", cat.center)
                        self._tap_node(cat)
                        # A1：事件驱动等待「机器人分类展开为列表」，把等待绑定到
                        # 真实页面切换而非固定时长；超时兜底后继续原有语义。
                        self._wait_until(self._looks_like_robot_list,
                                         self._nav_target_wait(),
                                         desc="机器人分类展开为列表")
                    if not self._looks_like_robot_list():
                        # 老版 QQ：机器人分组可能折叠在分组头下，点开
                        for hdr_t in ("我添加的机器人", "我创建的机器人"):
                            hdr = self._find(hdr_t, ymax=TAB_Y)
                            if hdr:
                                logger.info("展开分组头 %s", hdr_t)
                                self._tap_node(hdr)
                                # A1：事件驱动等待分组头收敛回列表；超时则继续。
                                self._wait_until(self._looks_like_robot_list,
                                                 self._nav_target_wait(),
                                                 desc="分组头展开为列表")
                                break
                if not self._looks_like_robot_list() and not ocr_tried:
                    # 坑 15（2026-09-17）：折叠态自愈。分类已 selected 但分组
                    # 被折叠时，dump 读不到「我添加的机器人」分组头节点（实测
                    # 截图有、dump 无）→ 上面的节点兜底必空转。此态改用 OCR
                    # 全屏定位分组头并点击展开；每次导航最多尝试 1 次防连环
                    # OCR 空烧（全屏 OCR ~2-3s）。
                    cur = list(self.ui.nodes())
                    if self._collapsed_groups_on_nodes(cur):
                        ocr_tried = True
                        pos = self._ocr_find("我添加的机器人",
                                             "我创建的机器人", retries=1)
                        if pos:
                            logger.info("OCR 定位折叠分组头 @ %s → 点击展开"
                                        "（坑 15）", pos)
                            self._tap(pos[0], pos[1])
                            self._wait_until(self._looks_like_robot_list,
                                             self._nav_target_wait(),
                                             desc="OCR展开分组头")
                        else:
                            logger.warning("折叠态 OCR 未定位到分组头（坑 15）")
            else:
                logger.debug("不在 QQ 主界面壳，物理返回一层")
                self.ui.back(pause=1.5)
                time.sleep(1.0)
        self._at_robot_list = self._looks_like_robot_list()
        if not self._at_robot_list:
            logger.warning("未能确认在机器人列表；屏文=%s", self._screen_texts(12))
            self._diag_shot("nav_not_list")

    def _is_robot_row(self, n: Node) -> bool:
        """机器人行名字节点特征（F3）：左对齐 x1≈183、宽<=230、行中心 y 在
        tab bar 之上。

        贴底/半露出行（中心 y>=ROW_SAFE_Y）不算 —— 直接 tap 中心会落到
        底部 tab bar 上切走主 tab（实测：下一台机器人行被压住时
        中心恰在【消息】tab 内）。中心过高的行（顶部标题/搜索）也由
        x1/宽度特征排除。
        """
        x1_min, x1_max = ROW_X1
        if not (x1_min <= n.x1 <= x1_max):
            return False
        if (n.x2 - n.x1) > ROW_WIDTH_MAX:
            return False
        return ((n.y1 + n.y2) // 2) < ROW_SAFE_Y

    def _find_robot(self, name: str) -> Optional[Node]:
        return self._find_scroll(name, max_scroll=10, ok=self._is_robot_row)

    def _reconfirm_click_target(self, name: str, r: Node) -> Optional[Node]:
        """点击关键目标前的现场取证 + 新鲜坐标校验（F8，防间歇性点错）。

        背景：f8 全流程实测 藤非/席恩 进入任务中心失败 —— 失败截图都在 QQ
        【消息】tab 首页。缺「点击前」现场无法区分根因，且滚动动画中间帧 /
        页面被 QQ 切走时，用旧坐标点击会点错（点到消息 tab/会话行）。
        此处：清缓存强制重 dump，目标行仍在 → 用最新坐标返回（微移即防动画
        帧点错）；行已消失（页面漂移）→ 返回 None 不点，交上层复位重试。
        另打 INFO 记录点击前页面状态（at_list / looks / 行坐标），供复现取证。
        """
        logger.info("点击前现场: 目标=%s 行@(%d,%d) at_list=%s looks=%s",
                    name, r.x1, r.y1, self._at_robot_list,
                    self._looks_like_robot_list())
        self.ui.refresh()
        row = None
        for x in self.ui.nodes():
            if x.text == name and self._is_robot_row(x):
                if (x.x1, x.y1, x.x2, x.y2) != (r.x1, r.y1, r.x2, r.y2):
                    logger.info("目标行坐标刷新 (%d,%d)-(%d,%d) -> "
                                "(%d,%d)-(%d,%d)，防动画中间帧点错",
                                r.x1, r.y1, r.x2, r.y2,
                                x.x1, x.y1, x.x2, x.y2)
                row = x
                break
        if row is None:
            logger.warning("点击前重 dump 找不到 %s 行（页面可能已漂移），"
                           "放弃本次点击交复位重试；屏文=%s",
                           name, self._screen_texts(12))
            self._diag_shot("enter_vanished")
            return None
        # 坑 15（2026-09-17）幽灵行防御：列表滚动后目标行处于「吸顶分类栏
        # 下缘第一个槽位」（行.y1 - 栏.y2 < 一个行距）时，dump 可能报
        # ListView 回收残影 —— 视觉上该槽位实为「我添加的机器人」分组头
        # （10:56 实测：按残影坐标点 (256,392) 落在分组头上 → 列表被折叠
        # → 4 台 × 3 次连环弃权）。该槽位内的行一律不直接点：先下滑让目标
        # 行离开边界槽位再重找；两次纠正仍在槽内 → 放弃交上层复位。
        for _ in range(2):
            self.ui.refresh()
            bar = self._cat_bar_node(list(self.ui.nodes()))
            if (bar is None or bar.y1 >= CAT_BAR_PINNED_Y
                    or row.y1 - bar.y2 >= ROW_PITCH):
                break          # 未吸顶或行离栏缘超过一个行距 → 继续 F9 检查
            logger.warning(
                "目标 %s 行 @(%d,%d) 紧贴吸顶分类栏下缘（栏底 y=%d）——"
                "分组头回收残影区（坑 15），下滑让位后重找",
                name, row.x1, row.y1, bar.y2)
            self.ui.swipe_down(pause=1.0)
            moved = self._find_robot(name)
            if moved is None:
                logger.warning("下滑后找不到 %s 行，放弃本次点击交复位重试",
                               name)
                return None
            row = moved
        else:
            logger.warning("%s 行两次下滑纠正后仍紧贴分类栏，放弃点击交复位重试",
                           name)
            return None
        # F9（2026-09-29 修复）：行底部越界（中心 y 或 y2 > TAP_Y_MAX=1740）时
        # 直接点会盲钳到上一行（藤非 y=1791 → 1740 命中桑祁，9/10 三连败实锤）。
        # 改为上滑让位：把行滚入安全点击区后重找，最多 2 次。
        for attempt in (1, 2):
            cy = (row.y1 + row.y2) // 2
            if cy <= TAP_Y_MAX and row.y2 <= TAP_Y_MAX:
                break
            logger.warning(
                "目标 %s 行 @(%d,%d) 超出安全点击区（cy=%d y2=%d > %d）——"
                "上滑让位后重找（F9 修复，2026-09-29）",
                name, row.x1, row.y1, cy, row.y2, TAP_Y_MAX)
            self.ui.swipe_up(pause=1.0)
            moved = self._find_robot(name)
            if moved is None:
                logger.warning("上滑第 %d 次后找不到 %s 行，放弃本次点击交复位重试",
                               attempt, name)
                return None
            row = moved
        else:
            # for 正常结束（未 break）：检查最后一次 row 是否仍越界
            cy = (row.y1 + row.y2) // 2
            if cy > TAP_Y_MAX or row.y2 > TAP_Y_MAX:
                logger.error("%s 行两次上滑仍无法进入安全点击区，放弃点击", name)
                self._diag_shot("reconfirm_row_oob")
                return None
        return row

    def _screen_robot_names(self) -> List[str]:
        """当前屏所有符合昵称识别规则的文本（不去重、不落日志）。

        P1（2026-09-15）从 _collect_robot_names 抽出：「向上滚到顶部收敛」
        与「向下滚到底收集」两阶段共用同一段过滤逻辑（x1∈[180,195]、
        y1∈[350,1850]、宽<=230，排除 内测中/我添加/我创建/底部 tab）。
        """
        out: List[str] = []
        for n in self.ui.nodes():
            if (180 <= n.x1 <= 195 and 350 <= n.y1 <= 1850
                    and (n.x2 - n.x1) <= 230):
                t = n.text.strip()
                if not t:
                    continue
                if "内测中" in t:
                    continue
                if ("我添加" in t or "我创建" in t or t in ("消息", "频道",
                                                             "联系人", "动态")):
                    continue
                out.append(t)
        return out

    def _collect_robot_names(self, max_scroll: int = 12,
                             stop_when: Optional[List[str]] = None) -> List[str]:
        """在机器人列表页滚动收集机器人昵称（P1 两阶段：先到顶、再滚到底）。

        昵称节点特征（1080x1920 实测）：左对齐 x1==183、宽 <=230、
        位于地区域 y1 350~1850（排除顶部 tab / 底部 nav / 账户及设置）。
        按首次出现顺序去重，跳过「内测中」。带编号的重复昵称（小麦/小麦1/
        小麦2）视为不同条目保留。注意：起点在列表中段时 Phase 1 会先向上
        收集，names 顺序为「起点以下先收、顶部后补」，与屏幕顺序可能不同
        —— 仅影响处理顺序，不影响收集完整性。

        P1 根因（2026-09-15，两次实测实锤：09-14 11:55 / 09-15 14:25）：
        新进程启动导航后，列表页会**恢复上次的滚动位置**（上次 run 收集
        结束时停在底部附近），旧写法盲滑 7 次且没有任何"已在顶部"的确认，
        实测两次都停在列表中段（首屏从 李宥恩 开始），顶部 代柯/尔尔/古禹
        （及 小麦1/2/3）永远收不到，随后「3 屏无新增」误判到底部提前结束
        → 自动收集静默漏机器人。

        Phase 1（到顶收敛）：先无差别快滑 4 次（列表实测 2~3 屏，成本与
        旧盲滑 7 次相当），再反复下滑并对比**本屏名字序列**，连续 2 屏完全
        相同 → 确认已到顶部。列表中部每次下滑（内容上移）必然露出新行，
        唯一序列不变的位置就是顶部 —— 判据不依赖任何标记节点（实机 dump
        确认机器人列表无「我添加的机器人」分组头，无标记可用），列表顺序
        /内容变化免疫。8 次未收敛则告警并继续（降级为旧行为）。
        Phase 2（向下收集）：从顶部逐屏下滚，连续 2 屏内容不变判定到底。
        （旧判据「3 屏 seen 无新增」会被 Phase 1 已收的中段内容干扰：
        从顶部下滚重访中段时 seen 零新增连续累积 → 提前误判底部漏收。）

        stop_when（#1 优化 2026-09-09）：白名单已存在时传入目标名单，
        Phase 2 当前屏收集到名单全部名字即提前停止滚动 —— 常规白名单
        （10 个）在列表前 1~2 屏内即可集齐，省掉滚完剩余页面的一次次
        dump+swipe。传 None 保持全量收集（main --list 用它看完整列表）。
        """
        logger.info("自动抓取机器人列表")
        self._nav_robot_list()
        names: List[str] = []
        seen: set = set()

        def _absorb(screen: List[str]) -> None:
            for t in screen:
                if t not in seen:
                    seen.add(t)
                    names.append(t)
                    logger.info("  收集到机器人: %s", t)

        # ---- Phase 1：滚回列表最顶部并给出收敛证据（P1 修复 2026-09-15）----
        for _ in range(4):
            self.ui.swipe_down(pause=random.uniform(0.5, 0.7))
        time.sleep(0.5)
        self.ui.refresh()
        prev: Optional[List[str]] = None
        top_ok = False
        for _ in range(8):
            cur = self._screen_robot_names()
            _absorb(cur)
            if prev is not None and cur == prev:
                top_ok = True
                break
            prev = cur
            self.ui.swipe_down(pause=random.uniform(0.5, 0.7))
        if top_ok:
            logger.info("已收敛到列表顶部（连续 2 屏内容不变）")
        else:
            logger.warning("下滑 8 次仍未确认列表顶部，继续收集（顶部可能漏收）")

        # ---- Phase 2：从顶部向下滚动收集到底（#1 优化 2026-09-09）----
        prev = None
        for _ in range(max_scroll):
            cur = self._screen_robot_names()
            _absorb(cur)
            if stop_when and all(x in seen for x in stop_when):
                logger.info("目标名单全部收集到（%d 个），提前停止滚动", len(stop_when))
                break
            if prev is not None and cur == prev:
                logger.info("连续 2 屏内容不变，判定已滚到列表底部")
                break
            prev = cur
            # 滚动看更多
            self.ui.swipe_up(pause=random.uniform(0.8, 1.2))
        logger.info("共收集到 %d 个机器人: %s", len(names), names)
        return names

    # F12（2026-09-25）：官方给单个机器人上线新功能时的一次性弹窗（每功能
    # 仅出现一次，之后不再出现）。实测（尔尔「你蹲蹲的玩法已经上线」，dump+截图）：
    #   - 弹窗结构：标题「XX的玩法已经上线」+ 右上角「关闭」按钮（dump 节点
    #     实测 (897,774)-(1056,909)）+ 底部「去体验」大按钮（**绝不可点**，
    #     点击会跳转新功能页破坏流程）；弹窗盖在任务中心 H5 之上，任务行全遮挡。
    #   - 稳定特征 = 「关闭」节点 与 「已经上线」文案 **同屏**（双特征确认，
    #     防误关其它含「关闭」的界面；任务中心/签到浮层/广告页/心动卡促销的
    #     历史 dump 均无「已经上线」）。若官方日后改模板致检测失效，退化为
    #     现状（进台失败重试跳过），不会更糟。
    _NEWFEAT_KEY = "已经上线"
    _NEWFEAT_CLOSE = "关闭"

    def _dismiss_newfeat_popup(self) -> bool:
        """检测并关闭「新功能上线」弹窗。返回是否执行了关闭。

        双特征确认才点（_tap_node 走 dump 节点，不涉坐标盲点）；正常路径
        仅 +1 次 dump（u2 ~0.1s，有缓存期内复用），无弹窗时零额外动作。
        """
        close = self._find(self._NEWFEAT_CLOSE)
        if not close or not self.ui.find_contains(self._NEWFEAT_KEY):
            return False
        logger.info("检测到新功能上线弹窗 → 关闭（屏文=%s）",
                    self._screen_texts(8))
        self._tap_node(close)
        time.sleep(1.5)
        # 确认消失；仍在（动画/双弹窗）再点一次，最多重试 1 次
        close = self._find(self._NEWFEAT_CLOSE)
        if close and self.ui.find_contains(self._NEWFEAT_KEY):
            logger.warning("新功能弹窗关闭后仍在屏，重试一次")
            self._tap_node(close)
            time.sleep(1.5)
        return True

    def _enter_tap_pause(self) -> float:
        """进台三处点击的 pause（workflow.enter_tap_pause_min/max，默认 0.5-0.9s）。

        2026-09-29 P1b：进台 9.5s 里 6.75s（70%）是三处 `_tap_node` 后的固定
        pause 空等 —— 诊断实测（王 92 轮 / 唐六爻 91 轮进台打点）段3/段4/段5
        的分布形状完全等于 uniform(1.5, 3.0)（最小值恒卡 1.6-1.8s、88-92% 落在
        [1.4, 3.05]），而紧随其后的 `wait_for` 才是真就绪判据 → 页面早就在
        pause 窗口内加载完了。故此处只保留一个**小随机 floor**（仍非零、非固定，
        维持非机械节奏），就绪交给 wait_for 轮询。

        注意：`timing.click_min/click_max` 的设计原意是「页面动画起步 floor」
        （见 MEMORY A1 段），**不是防风控**（风控靠 ad_cooldown + C2 坐标抖动），
        且本旋钮**只作用于进台**——签到/反馈/关广告/退台节奏完全不变。
        设回 min:1.5 / max:3.0 即一键回退旧行为。
        """
        lo = float(self.wf.get("enter_tap_pause_min", 0.5))
        hi = float(self.wf.get("enter_tap_pause_max", 0.9))
        return random.uniform(min(lo, hi), max(lo, hi))

    def _enter_taskcenter(self, name: str) -> bool:
        """进入指定机器人的任务中心(WebView)。

        寻路优化（2026-09-09，B3）：tap 后的固定 sleep(2.5s) 改为条件等待
        （元素出现即继续），页面就绪快时省等待、就绪慢时自动多等 —— 不再
        有固定下限空等。wait_for 内部 find 失败会轮询，语义等价且更稳。
        """
        logger.info("进入机器人任务中心: %s", name)
        t_start = time.time()
        t0 = t_start
        # 方案 A：清上次进台的旧配额缓存，避免进台失败时残留值被误复用。
        self._tc_entry_ratio = None
        self._nav_robot_list()
        t0 = self._stage(t0, "进台.1导航列表")
        r = self._find_robot(name)
        if not r:
            logger.warning("列表里没找到机器人 %s；屏文=%s", name,
                           self._screen_texts(12))
            self._diag_shot("enter_norobot")
            return False
        # F8：点击前新鲜坐标校验（页面漂移则放弃点击，交上层复位重试）
        r = self._reconfirm_click_target(name, r)
        if r is None:
            return False
        t0 = self._stage(t0, "进台.2找行+现场")
        # F9（2026-09-29）：机器人行底部越界已由 _reconfirm_click_target
        # 上滑让位并返回安全节点，此处直接点节点中心（_tap_node 带随机抖动）。
        # P1b（2026-09-29）：pause 从 timing.click_min/max（1.5-3.0s，均值 2.25s）
        # 收到 workflow.enter_tap_pause_min/max（默认 0.5-0.9s）—— 诊断实测该
        # pause 是进台 70% 耗时的来源，就绪由下方 wait_for 轮询兜底。
        wv_fast = self.wf.get("nav_fast_wait", True)
        wv_int = 0.5 if wv_fast else 1.0
        # 总窗口守恒：pause 收窄的秒数折成额外轮询次数（旧 pause 均值
        # ENTER_TAP_PAUSE_REF + retries×interval），慢页最坏等待不缩短。
        ep = self._enter_tap_pause()
        ep_extra = max(0, int(round((ENTER_TAP_PAUSE_REF - ep) / wv_int)))
        self._tap_node(r, pause=ep)
        # profile -> 发消息（条件等待；F2：H5 profile 加载慢时 8s 偏短 → 12s）
        # P1（2026-09-29）：u2 后端单次探测毫秒级，轮询间隔 1.0→0.5s 减少
        # 页面就绪后的过冲空等（均值省 ~0.5s/段）；retries 加倍保持 12s
        # 总窗口不变。workflow.nav_fast_wait: false 回退 1.0s 旧行为。
        n = self.ui.wait_for("发消息",
                             retries=(24 if wv_fast else 12) + ep_extra,
                             interval=wv_int)
        if not n:
            logger.error("profile 没找到 发消息；屏文=%s looks=%s at_list=%s",
                         self._screen_texts(12), self._looks_like_robot_list(),
                         self._at_robot_list)
            self._diag_shot("enter_profile")
            return False
        t0 = self._stage(t0, "进台.3点行到发消息")
        ep = self._enter_tap_pause()
        ep_extra = max(0, int(round((ENTER_TAP_PAUSE_REF - ep) / wv_int)))
        self._tap_node(n, pause=ep)
        # 聊天页 -> 个人（同样条件等待；"个人"在输入框上方 y>=600）
        n = self.ui.wait_for("个人",
                             retries=(16 if wv_fast else 8) + ep_extra,
                             interval=wv_int, ymin=600)
        if not n:
            logger.error("聊天页没找到 个人；屏文=%s", self._screen_texts(12))
            self._diag_shot("enter_private")
            return False
        t0 = self._stage(t0, "进台.4发消息到个人")
        self._tap_node(n, pause=self._enter_tap_pause())
        # F12（2026-09-25）：官方给单个机器人上线新功能时会弹「XX的玩法已经上线」
        # 弹窗（每功能一次；实测 尔尔「你蹲蹲的玩法已经上线」，dump+截图）——
        # 盖在任务中心 H5 之上，不关则任务行全被遮挡 → 签到/反馈找不到行。
        # 立即检测关闭（主检测；实测弹窗随个人页即刻弹出）。
        self._dismiss_newfeat_popup()
        # F10(#5 优化)：进入任务中心的固定等待改条件等待——任务中心特征
        # （每日签到/任务中心）出现即继续，H5 加载快时不再空等满 3~3.5s。
        # 未在窗口内出现也不阻塞（不同机器人版式有差异），后续任务操作自带
        # 等待器会兜住；这里最多探测 taskcenter_wait 秒。
        found_tc = False
        for _ in range(max(1, int(self.t.get("taskcenter_wait", 3.5) * 2))):
            if self._find("每日签到") or self._find("任务中心"):
                found_tc = True
                break
            time.sleep(0.5)
        if not found_tc:
            # A（2026-09-10）：dump 窗口内未见任务中心特征时，OCR 一次区分
            # 「H5 版式加载慢（真任务中心）」与「误入错页（心动卡 H5 会员页）」。
            #   心动卡会员页是 WebView —— uiautomator dump 穿透读不到，但 OCR
            #   可读（观察器实锤：误入后画面=心动卡将于9/21到期/高级模型/记住
            #   你更懂你，f_0016）。此页无任务中心行，后续 _signin 会把"看广告
            #   入口"当签到行点 → 弹游戏广告、反馈被挡 → 故命中错页标记即判定
            #   进入失败，交上层 _safe_back_to_robot_list 复位重试（最多 3 次）。
            #   OCR 未命中错页（慢加载真任务中心）→ 维持原"版式差异"放行，
            #   由后续任务操作的等待器兜住 —— 行为不变，零影响看广告路径。
            wrong = self._ocr_find("心动卡", "高级模型", "立即续费", retries=1)
            if wrong is not None:
                # P-心动卡（2026-09-12）：真机现场截图实锤（fail_enter_wrongpage_*
                # .png）—— 该页并非陌生 H5，而是任务中心顶部插入了「心动卡促销卡」
                # （返回箭头/收支详情/banner/电量/聊天特权都在），任务行被整体推到
                # 折叠区以下，dump 只读可见区所以探测不到 每日签到。促销卡会重复
                # 出现，旧"判失败→复位重试"会 3 次全撞同一版式（09-12 20:26 实测
                # 小麦 3 次 ~5min 全废被跳过）。现改为：上滑（#1 安全左列锚点）
                # 最多 2 次，每次滑完重探任务行；探到即按正常进入继续；仍探不到
                # 才判失败（保留旧兜底，防真错页）。
                logger.info("OCR 命中心动卡促销卡（%s）→ 按任务中心促销版式处理，"
                            "上滑后重探任务行", wrong)
                for _ in range(2):
                    self.ui.swipe_up()
                    if (self._find("每日签到") or self._find("任务中心")
                            or self._find("获取随机") or self._find("看广告")
                            or self._find("看视频赚电量")):
                        found_tc = True
                        logger.info("上滑后已探测到任务行 → 按正常任务中心继续")
                        break
                if not found_tc:
                    logger.warning("上滑 2 次后仍未见任务中心特征 → 判定进入失败")
                    self._diag_shot("enter_wrongpage")
                    return False
            else:
                # F12 兜底：弹窗可能晚于个人页渲染才弹出（H5 加载时序）——
                # 关闭后重探任务行，探到即按正常进入继续；否则维持原放行。
                if self._dismiss_newfeat_popup():
                    if (self._find("每日签到") or self._find("任务中心")
                            or self._find("获取随机") or self._find("看广告")
                            or self._find("看视频赚电量")):
                        found_tc = True
                        logger.info("关闭新功能弹窗后已探测到任务行 → 按正常任务中心继续")
                if not found_tc:
                    logger.info("等待 %.1fs 未见任务中心特征（版式差异，继续流程）",
                                self.t.get("taskcenter_wait", 3.5))
        t0 = self._stage(t0, "进台.5TC加载探测")
        # 已进入某机器人的任务中心：清快路径状态（下次 nav 需重新导航）
        self._at_robot_list = False
        # F11：进入任务中心 → 启用 banner 禁点区（此后本页任何 y<440 的坐标点击
        # 都要过守卫，防误触顶部 banner）。
        self._page_tc = True
        # 方案 A（2026-09-28）：进台成功即读一次广告配额并缓存，供后续
        # base 校准复用（免二次滚动查找）。读不到（覆盖层/版式差异）则保持
        # None —— 消费端会回退到原地读屏，零行为变化。
        # 注：这里用只读的 _read_ad_ratio（内部 _find_row 只 dump 不 tap/back），
        # 不改变点击效率，也不引入额外界面动作。
        self._tc_entry_ratio = self._read_ad_ratio()
        if self._tc_entry_ratio:
            logger.info("进台配额缓存 %s（屏幕 %d/%d）", name,
                        self._tc_entry_ratio[0], self._tc_entry_ratio[1])
        # 2026-09-29 P1b：补段6打点 —— 原本「段5 → 进台合计」之间的
        # _read_ad_ratio() 是**未打点盲区**（实测恒定 1.55s，且 _find_row 走
        # max_scroll=6 会 swipe 把刚进的任务中心页面滚走）。此处**只加一条计时
        # 日志**，读取行为与判据完全不变（用户 09-29 决策：配额缓存保持现状），
        # 目的是让这 1.55s 以后在日志里可见、可对比。
        t0 = self._stage(t0, "进台.6配额缓存")
        logger.info("[计时] 进台合计 %.1fs", time.time() - t_start)
        return True

    def _exit_taskcenter(self) -> bool:
        """从任务中心退出回机器人列表。返回是否成功回到列表（P0 安全版）。

        三层全部改用系统 BACK 键（keyevent 4，2026-09-09 23:1x 实测确认）：
        - 任务中心顶部 banner「QQ AI好友·常见问题答疑」已升级成可点击入口，
          整个 banner 区（y≈0-440, x≈0-1080）任何 tap 都触发 AI 好友反馈 →
          约 37s 后弹 Badcase 问卷。
        - 原 EXIT_TC_ARROW(90,138)/EXIT_CHAT_ARROW(59,139)/EXIT_PROFILE_ARROW(73,133)
          全在 banner 区，盲点 tap 全部命中 banner → 必然误开问卷。
        - 系统 BACK 键不依赖 UI 坐标，物理返回永远避开 banner 区。
        - 实测三层返回路径（1080x1920，SurfaceOrientation=0）仍成立：任务中心
          -> 聊天页 -> profile 页 -> 机器人列表；每层返回后校验是否已回列表，
          命中即提前结束（不再继续盲点下层）。
        - 退出**不再预先滑动到顶部**（2026-09-10 优化）：早期用坐标点返回箭头，
          必须先滑到顶让箭头出现；改用系统 BACK 键后这一步毫无作用，却要花
          ~14-18s/台。现在只在「三层返回仍未回列表且还停在子页」时才补做一次。
        - 层间等待用局部常量 EXIT_LAYER_WAIT(1.2s) 而非全局 page_wait(2.5s)，
          三层共省约 4s；三层仍各自用 `_looks_like_robot_list()` **强判据**校验
          （弱判据会把 QQ 主界面【消息】首页误认成机器人列表，是历史卡死根因）。
        """
        logger.info("退出任务中心（系统 BACK 键，三层 × 最多 2 轮）")
        t_start = time.time()
        # F11：退出任务中心 → 关闭 banner 禁点区（回到列表后 y<440 的点击是合法的，
        # 如机器人首行/分类行）；本方法全程只用物理 BACK，不依赖坐标。
        self._page_tc = False
        # 前提校验：当前必须是任务中心页
        # 坑（2026-09-13 小麦事故）：重载动画期 dump 连续超时会把"还在任务中心"
        # 误判成"不在" → 安全返回盲按 BACK 过度返回 → 顶出资料卡浮层卡死后续
        # 全部机器人。dump 不可用时以 OCR 复核为准（与关闭确认路径同一策略）。
        if not (self._find("每日签到") or self._find("任务中心")):
            if self._taskcenter_confirmed_by_ocr():
                logger.info("dump 不可用，OCR 判定仍在任务中心 → 照常三层 BACK")
            else:
                logger.warning(
                    "当前不在任务中心页（无 每日签到/任务中心 特征）→ 安全返回兜底")
                return self._safe_back_to_robot_list()
        t0 = self._stage(t_start, "退台.0前提校验")
        # 1) 第 1 轮：直接三层 BACK（快路径，不做任何滚动）
        # P1（2026-09-29 实测改造，探针 probe_exit_timing.py 3 trial）：层3
        # BACK 后 0.07-0.08s 列表即可被强判据正确检出、层1/2 在 3.5s 窗口内
        # 20 次检查全不命中 —— 原层间固定 pause(1.2-1.8)+EXIT_LAYER_WAIT(1.2)
        # ≈2.7s/层 属纯盲等。改为 pause(0.8-1.2)+BACK 后立即单次强校验
        # （校验判据/层数/兜底全不变，仅去掉无谓等待），退台合计 8.9s → ~3.5s。
        # workflow.nav_fast_wait: false 一键回退旧行为。
        fast = self.wf.get("nav_fast_wait", True)
        for layer in (1, 2, 3):
            self.ui.back(pause=random.uniform(0.8, 1.2) if fast
                         else random.uniform(1.2, 1.8))
            if not fast:
                time.sleep(EXIT_LAYER_WAIT)
            hit = self._looks_like_robot_list()
            t0 = self._stage(t0, f"退台.第{layer}层")
            if hit:
                self._at_robot_list = True
                logger.info("已回到机器人列表（第%d层返回后）", layer)
                logger.info("[计时] 退台合计 %.1fs", time.time() - t_start)
                return True
        # 2) 第 2 轮兜底：仅当"还没退回 QQ 主壳"（说明确实卡在子页）时才补做
        #    坐标时代的"滑到顶再退"。已回主界面却不在列表的情况跳过这一步，
        #    避免继续 BACK 把 QQ 退到桌面。
        if not self._on_qq_main_shell():
            logger.info("三层返回未达列表且仍在子页 → 滑到顶部后补退一轮")
            self._scroll_to_top_of_taskcenter()
            if self._dump_stuck():
                logger.warning("滑动到顶途中页面卡死，转安全返回")
                return self._safe_back_to_robot_list()
            for layer in (1, 2, 3):
                self.ui.back(pause=random.uniform(0.8, 1.2) if fast
                             else random.uniform(1.2, 1.8))
                if not fast:
                    time.sleep(EXIT_LAYER_WAIT)
                if self._looks_like_robot_list():
                    self._at_robot_list = True
                    logger.info("已回到机器人列表（补退第%d层后）", layer)
                    return True
        # 3) 未达机器人列表（F1）。若已回 QQ 主界面壳 —— 旧实现把这种状态误判成
        #    “已在列表”直接返回，随后在错误主 tab 上滚动找人名（= 卡 消息/频道
        #    下滑的根因）—— 这里转完整导航真正进到机器人列表；仍在子页
        #    （聊天/profile/H5）则物理返回兜底。
        self._at_robot_list = False
        if self._on_qq_main_shell():
            logger.info("三层返回后落在 QQ 主界面（非机器人列表），转完整导航")
            self._nav_robot_list()
            if self._at_robot_list and self._looks_like_robot_list():
                return True
            logger.warning("完整导航后仍未确认机器人列表，转安全返回兜底")
        else:
            logger.warning("三层返回后未见机器人列表（可能仍在子页），转安全返回兜底")
        return self._safe_back_to_robot_list()

    def _scroll_to_top_of_taskcenter(self):
        """下滑直至任务中心顶部（返回箭头出现）。

        省 dump 优化（2026-09-09）：原实现每轮 swipe 前都 dump 查锚点。
        MuMu dump 一次约 1~3s，实测到顶通常需 4~5 轮，每轮 ~4.2s，
        其中 dump 占大头。现改为「隔轮查锚点」——首轮仍预检，其后
        每 2 次 swipe 才 dump 一次；_dump_stuck 只读计数器不触发 dump，
        卡死保护保持每轮判定。到顶通常少 2 次 dump ≈ 省 4~6s/台。
        代价：最坏情况比原来多滑 1 次（到顶后 overscroll 弹回，无害）。
        末尾记录实际 swipe/dump 次数，供真机跑分对比。
        """
        logger.info("滑动到任务中心顶部")
        n_swipes = 0
        n_checks = 0
        do_check = True            # 首轮预检，其后每 2 次 swipe 查一次
        for _ in range(8):
            if self._dump_stuck():
                logger.warning("滑动到顶途中页面持续 dump 失败，提前停止")
                return
            if do_check:
                n_checks += 1
                if (self._find("开通解锁以下专属权益")
                        or self._find("收支详情")):
                    logger.info("任务中心已到顶（%d 次 swipe / %d 次锚点检查）",
                                n_swipes, n_checks)
                    return
            self.ui.swipe_down(pause=random.uniform(0.8, 1.2))
            n_swipes += 1
            do_check = not do_check
        logger.warning("8 次 swipe 仍未确认到顶（已 %d 次锚点检查），"
                       "照常尝试返回箭头", n_checks)
        time.sleep(0.5)

    # ----------------------------------------------------------
    # 任务中心操作
    # ----------------------------------------------------------
    def _signin(self) -> bool:
        logger.info("== 每日签到 ==")
        # 汇总记账辅助位：本次调用是"真签到成功"还是"已签跳过"（True=跳过）。
        # 供 run_robot 区分汇总表里的 成功/已签，不改 bool 返回契约。
        self._signin_already = False
        row = self._find_row("每日签到")
        if not row:
            logger.warning("未找到 每日签到 行；屏内文本=%s", self._screen_texts())
            return False
        btn, _label, ratio = row
        # P1 校准：行内含 1/1 → 今日已签到（按钮已变为「连签N天」），跳过避免无效点击
        if ratio and ratio[0] >= ratio[1]:
            logger.info("今日已签到（%d/%d），跳过", ratio[0], ratio[1])
            self._signin_already = True
            return True
        self._tap_node(btn)
        # P1 断言：应弹出「每日免费领」浮层。
        # 今日已签到/页面异常 → 无浮层 → 复位并判失败（不再盲点固定坐标）。
        if self.ui.wait_for("每日免费领", retries=5, interval=1.0) is None:
            logger.warning("点签到后未出现 每日免费领 浮层（今日已签或版式不同）"
                           "；屏内文本=%s", self._screen_texts())
            self._reset_after_fail("签到")
            return False
        # 浮层「每日免费领」-> 点左下 签到 @(301,1800)
        # F11：浮层已在屏（上方 wait_for 已确认）→ trusted=True 放行盲点禁令。
        self._tap(*MODAL_SIGNIN, trusted=True)
        time.sleep(2.0)
        # 成功浮层「我知道了」@(540,1189)（节点在屏才点）
        if self._find("我知道了"):
            self._tap(*SUCCESS_KNOW, trusted=True)
        time.sleep(1.5)
        # 签到奖励广告（2026-09-16 用户需求）：签到成功后浮层底部出现
        # 「看广告 +⚡」→ 看一次 15s 广告拿奖励。失败只记日志不阻断；
        # signin_ad=false 可整体回退。已签到跳过路径不会走到这里（天然 1 次/天）。
        if self.wf.get("signin_ad", True):
            try:
                self._watch_signin_ad()
            except Exception as e:  # noqa: BLE001
                logger.warning("签到奖励广告调用异常（不阻断主流程）：%s", e)
        else:
            logger.info("signin_ad=false，跳过签到奖励广告")
        # 关闭「每日免费领」浮层 ✕（F11：浮层仍在屏才点 —— 否则该坐标 (996,1143)
        # 可能落在版式漂移后的推广 banner 上。浮层已消失则跳过，不做盲点）
        if self._find("每日免费领") or self._find("恭喜获得"):
            self._tap(*MODAL_CLOSE, trusted=True)
        else:
            logger.info("签到浮层已消失，跳过关闭 ✕ 盲点（F11 防 banner 误触）")
        time.sleep(1.5)
        logger.info("签到流程完成")
        return True

    def _feedback(self) -> bool:
        logger.info("== 问题反馈 ==")
        # 汇总记账辅助位：本次调用是"真反馈成功"还是"已反馈跳过"（True=跳过）。
        self._feedback_already = False
        row = self._find_row("问题反馈")
        if not row:
            logger.warning("未找到 问题反馈 行；屏内文本=%s", self._screen_texts())
            return False
        btn, _label, ratio = row
        # P1 校准：1/1 → 今日已反馈（按钮文案保留「去反馈」），跳过避免重复点击
        if ratio and ratio[0] >= ratio[1]:
            logger.info("今日已反馈（%d/%d），跳过", ratio[0], ratio[1])
            self._feedback_already = True
            return True
        self._tap_node(btn)
        # P1 断言：应进入反馈页（左上角出现「返回」，y<300 顶部区域）。
        # 今日已反馈/行无效 → 无反馈页 → 复位并判失败。
        back = self.ui.wait_for("返回", retries=6, interval=1.0, ymax=300)
        if back is None:
            logger.warning("点反馈后未出现反馈页(顶部无 返回)；今日已反馈或行无效"
                           "；屏内文本=%s", self._screen_texts())
            self._reset_after_fail("问题反馈")
            return False
        # 2026-09-25 官方改版：反馈页需填写内容并点「提交反馈」才算完成反馈
        # （旧版进页直接返回即可，实测新页面有必填内容框 + 提交按钮）。
        # 失败 → 物理 BACK 复位后判失败；上层重试时若实际已提交成功（如断言
        # 超时），任务中心 ratio 已 1/1 会走「已反馈跳过」，不会重复提交。
        if not self._fill_and_submit_feedback():
            logger.warning("反馈填写/提交失败")
            self._reset_after_fail("问题反馈")
            # 复位后能认出任务中心则恢复 F11 守卫状态位；认不出保持 False，
            # 由后续进台/退出路径按页面实际状态重设。
            if self._find("每日签到") or self._find("任务中心"):
                self._page_tc = True
            return False
        # 提交成功结果页左上角「返回」（页面已切换，重新定位；实测成功页返回
        # 与表单页同位 (27,107)-(135,172)），点它即回任务中心
        back2 = self.ui.wait_for("返回", retries=4, interval=1.0, ymax=300)
        if back2 is None:
            logger.warning("提交成功页未见返回；屏内文本=%s", self._screen_texts())
            self._reset_after_fail("问题反馈")
            if self._find("每日签到") or self._find("任务中心"):
                self._page_tc = True
            return False
        self._tap_node(back2)
        time.sleep(self.t.get("page_wait", 2.0))
        # P1 断言：应回到任务中心
        if not (self._find("每日签到") or self._find("任务中心")):
            logger.warning("反馈返回后未见任务中心特征（可能退到错页）；屏内文本=%s",
                           self._screen_texts())
            return False
        # F11：已回到任务中心，恢复 banner 禁点守卫
        self._page_tc = True
        logger.info("问题反馈完成")
        return True

    # ---- 2026-09-25 反馈页改版（官方新增"反馈内容"必填）----
    # 实测依据（1080x1920 dump+截图，非臆测）：
    #   - 表单页：反馈类型/所属分类默认「问题/聊天」无需操作；内容输入框为
    #     WebView 自绘**不在 dump**（placeholder「请仔细描述你的问题」，区域
    #     y≈495-1089）；字数计数器 'N/500' 在 dump（输入成功断言锚点）；
    #     底部「提交反馈」按钮 (60,1737)-(1020,1860) 在 dump。
    #   - ⚠️ 点击 y<500 会命中「反馈类型/所属分类」行跳全屏选择页，且该选择页
    #     BACK 会直接退出整个反馈页（实测 (300,350) 事故）→ 输入框点击点取
    #     y=600（placeholder 下方、框内安全区）。
    #   - 聚焦输入框后 BACK 一次 = 收键盘、不退页（实测）。
    FEEDBACK_INPUT_POS = (300, 600)   # 内容输入框聚焦点（框内安全区，见上注）
    FEEDBACK_SUBMIT_Y = 1500          # 底部提交按钮 y1 下限（区分顶部同名标题 y72-207）

    def _new_feedback_text(self) -> str:
        """生成 5 位随机反馈内容，持久化记录防重复（2026-09-26 用户方案）。

        官方按「账号+内容」去重（用户实测：同账号内只要输入过一次，往后同
        内容全部被拒），去重窗口未知 → 每次生成前排除 logs/feedback_used.txt
        里的历史已用、生成后立即追加记录（跨进程永久不用；5 位数共 9 万个，
        按每账号每天 ~10 次提交可用 6 年+）。文件 IO 异常不阻断主流程（退化为
        会话内防重）。_fb_used is None 时从记录文件懒加载；单测预设空 set 且
        _fb_used_path=None 可完全跳过 IO。
        """
        if self._fb_used is None:
            self._fb_used = set()
            if self._fb_used_path:
                try:
                    with open(self._fb_used_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                self._fb_used.add(line)
                    logger.info("反馈防重记录已加载 %d 条（%s）",
                                len(self._fb_used), self._fb_used_path)
                except FileNotFoundError:
                    pass    # 首次运行，无历史记录
                except Exception as e:  # noqa: BLE001
                    logger.warning("反馈防重记录读取失败（忽略，仅会话内防重）：%s", e)
        for _ in range(50):
            text = str(random.randint(10000, 99999))
            if text not in self._fb_used:
                break
        else:
            # 理论不可达（9 万空间）；防御性回退避免死循环
            logger.error("5 位随机数与历史记录连续冲突 50 次，回退时间戳后缀")
            text = str(int(time.time()))[-5:]
        self._fb_used.add(text)
        if self._fb_used_path:
            try:
                with open(self._fb_used_path, "a", encoding="utf-8") as f:
                    f.write(text + "\n")
            except Exception as e:  # noqa: BLE001
                logger.warning("反馈防重记录写入失败（不影响本次提交）：%s", e)
        return text

    def _fill_and_submit_feedback(self) -> bool:
        """反馈页内：填写反馈内容（5 位随机数，持久化防重）→ 点「提交反馈」
        → 断言「提交成功」结果页。

        内容方案（2026-09-26 用户指定，替代 09-25 序号方案）：5 位随机数，
        每次生成前排除历史已用并记录（logs/feedback_used.txt）。官方按
        「账号+内容」去重（实测 toast「反馈内容重复了」，toast 不进 dump），
        同账号内重复内容必被拒；去重窗口未知 → 记录永久不用。

        返回 True=提交成功（页面为结果页，顶部「返回」在屏）；
        False=任一步失败（页面状态不定，调用方负责复位）。
        config workflow.feedback_fill 为 false = 回退旧行为（不填写直接算通过）。
        """
        if not self.wf.get("feedback_fill", True):
            logger.info("feedback_fill=false，跳过填写提交（旧行为）")
            return True
        # F11 页面状态：进反馈 H5 页 = 已离开任务中心，解除坐标点击守卫
        # （输入框聚焦是反馈页唯一合法坐标点击）；回任务中心由调用方确认后恢复。
        self._page_tc = False
        # 页面守卫：反馈表单特征（计数器 或 反馈类型行）在屏才允许点输入框，
        # 防错页盲点。计数器 find_contains 兼容任意已填字数（0/500、3/500…）。
        if not (self.ui.find_contains("/500") or self._find("反馈类型")):
            logger.warning("反馈页特征缺失（无计数器/反馈类型），不点输入框；"
                           "屏内文本=%s", self._screen_texts())
            return False
        typed = False
        for attempt in (1, 2):
            # 内容在循环内生成：重试必须换新内容 —— 官方按「账号+内容」去重，
            # 同内容重试必再撞「反馈内容重复了」（2026-09-25 实测）。
            text = self._new_feedback_text()
            x, y = self.FEEDBACK_INPUT_POS
            if not self._tap(x, y):
                return False    # 守卫拦截（理论不可达：上面已置 False）
            time.sleep(1.2)     # 键盘弹出
            self.ui.input_text(text)
            time.sleep(1.0)
            # 输入成功断言：计数器 N/500（实测 0/500 → 3/500）
            if self.ui.find_contains("%d/500" % len(text)):
                typed = True
                break
            logger.warning("第 %d 次输入后未见计数器 %d/500，重试",
                           attempt, len(text))
        if not typed:
            logger.error("反馈内容输入失败（计数器始终未见 %d/500）", len(text))
            return False
        # BACK 收键盘（实测：输入框聚焦态 BACK 只收键盘不退页）
        self.ui.back(pause=1.5)
        time.sleep(1.0)
        # 提交按钮：dump 节点「提交反馈」，y1 下限区分顶部同名标题（F11：不盲点）
        submit = [n for n in self.ui.nodes()
                  if n.text == "提交反馈" and n.y1 > self.FEEDBACK_SUBMIT_Y]
        if not submit:
            logger.warning("未找到底部「提交反馈」按钮；屏内文本=%s",
                           self._screen_texts())
            return False
        self._tap_node(submit[0])
        time.sleep(self.t.get("page_wait", 2.0))
        # 提交成功断言：结果页标题/正文均含「提交成功」（子串匹配，与表单页
        # 「提交反馈」无歧义）
        if not self.ui.wait_contains("提交成功", retries=5, interval=1.0):
            logger.warning("提交后未见「提交成功」页；屏内文本=%s",
                           self._screen_texts())
            return False
        logger.info("反馈提交成功（内容=%r）", text)
        return True

    def _reset_after_fail(self, ctx: str):
        """任务操作失败后的页面复位：物理返回一次。

        作用：浮层开着 → BACK 关闭；误入问卷/H5 → BACK 退回上一页；
        已签到/已反馈（无弹层）→ 仍在任务中心，BACK 退出到聊天页由上层
        _exit_taskcenter 的安全兜底接手。避免失败状态残留 + 坐标盲点乱点。
        """
        logger.info("%s 失败复位：物理返回一次", ctx)
        self.ui.back(pause=1.5)
        time.sleep(1.0)

    def _watch_ad_once(self, row=None) -> bool:
        logger.info("看一次广告")
        # 2026-09-09 观察确认：任务中心不会自动弹问卷 —— 之前的「Badcase 反馈
        # 问卷」是 _close_ad 盲点 (160,152) 在广告已自动关闭、画面回到任务中心
        # 后误点顶部「QQ AI好友·常见问题答疑」banner 打开的。
        # 修复演进：① B+/L1 用 uiautomator dump 稳定检查过滤该竞态 → 但视频期
        #   dump 恒失败使守卫恒 False，AD_CLOSE 反而一次都点不到、广告关不掉
        #   （2026-09-10 实锤）；② 现为**方案 A**：改由 OCR 负向排除判定
        #   （_taskcenter_confirmed_by_ocr）—— 既不用 dump、又能识别"已回任务
        #   中心"，banner 误触时还有 _dismiss_badcase() 立即 BACK 自愈。
        # D 优化（2026-09-13）：轮转阶段可传入 CD 窗口内预取的行节点
        # （配额复核 dump 的缓存，~5s 龄、期间页面静止无操作），省一次全量
        # dump——实测 708/708 次点开段 ≥5s 的主因就是这里的现场 dump。
        # row=None（首轮/兜底）行为与旧版完全一致：现场 dump 查找。
        if row is None:
            row = self._find_row(AD_ROW_LABELS[0], alt_labels=AD_ROW_LABELS[1:])
            if not row:
                # 破死锁（2026-09-14 尔尔插屏事故）：find_row 失败 ≠ 页面异常
                # ——上一支广告可能还赖在屏幕上（下载型插屏视频常驻 → uiautomator
                # 永远无法 idle → 全局 dump 4s 超时 → find_row 永远失败 → 而能关
                # 广告的 _close_ad 又只在本方法后续才执行 → 死循环，实测卡死
                # 40+ 分钟烧掉后续所有机器人）。先用 OCR 认一下"关闭广告"：
                # 在屏则走关闭链路清场后重试一次；不在屏维持原失败语义。
                if self._ad_close_pos() is None:
                    logger.warning("未找到 看广告/获取随机 行")
                    return False
                logger.warning("find_row 失败但 OCR 读到关闭广告（广告页残留）"
                               "→ 走关闭链路清场后重试")
                if not self._close_ad():
                    logger.error("广告残留清场失败")
                    return False
                time.sleep(self.t.get("ad_close_wait", 2.0))
                row = self._find_row(AD_ROW_LABELS[0],
                                     alt_labels=AD_ROW_LABELS[1:])
                if not row:
                    logger.warning("清场后仍未找到 看广告/获取随机 行")
                    return False
        else:
            logger.info("使用 CD 窗口预取的行节点（D 优化，省一次 dump）")
        btn, _label, ratio = row
        # P1 校准：X/10 计数已达 target → 已看满，跳过本台本次广告
        # （run_all 会按"完成"计 1 次，目标未到继续下次轮转；目标到了自然停）
        target = self.wf.get("ad_times_per_robot", 10)
        if ratio and ratio[0] >= target:
            logger.info("看广告已达目标次数（%d/%d 目标 %d），跳过",
                        ratio[0], ratio[1], target)
            return True
        self._tap_node(btn)
        return self._ad_play_and_close()

    def _ad_play_and_close(self, sheet_ok: bool = False) -> bool:
        """广告点击后的共用段（2026-09-16 从 _watch_ad_once 原样抽出）：
        等 ad_wait_min-max 秒（E 预定位藏进等待窗）→ _close_ad → 确认。
        _watch_ad_once 与签到奖励广告 _watch_signin_ad 复用，行为零变化。
        sheet_ok：透传 _close_ad —— 签到奖励广告看完回到盖着「每日免费领」
        浮层的任务中心是合法终态（默认 False，主广告路径行为不变）。
        返回 True=广告已关闭；False=多次尝试仍未关闭。"""
        wait = random.uniform(self.wf["ad_wait_min"], self.wf["ad_wait_max"])
        logger.info("广告播放 %.1f 秒", wait)
        # E 优化（2026-09-14）：把「关闭按钮预定位」藏进等待窗口。
        # 视频播放期 uiautomator dump 必然失败（UI 不 idle），但 OCR 截屏不依赖
        # idle —— 等待进行到 ad_prefetch_at 秒时用顶条 OCR 预定位「关闭广告」并
        # 缓存坐标（~1s，纯等待时间内的开销），等待结束后 _close_ad 在步骤 0
        # 守卫通过的前提下直接用缓存坐标 tap，省掉「dump 2.5s 白等 + OCR 重新
        # 定位」的串行定位段（实测 ~3.5-4.8s/次）。不提前 tap，tap 时机与原
        # 行为完全一致；预定位读不到（插屏/加载期版式不同）则不缓存走原链。
        # 设 workflow.ad_prefetch_pos: false 可整体回退。
        prefetch_at = float(self.wf.get("ad_prefetch_at", 10.0))
        if self.wf.get("ad_prefetch_pos", True) and wait > prefetch_at + 2.0:
            time.sleep(prefetch_at)
            self._prefetch_ad_close_pos()
            time.sleep(wait - prefetch_at)
        else:
            time.sleep(wait)
        # B 优化（2026-09-13 用户确认）：移除观看等待后的任务中心 OCR 预检。
        # 09-13 全流程 97 次广告预检 0 命中（无自动关闭、无未触发），纯开销
        # ~3s/次（一轮 174 次 ≈ 9min）。广告自动结束/仍在任务中心的场景由
        # _close_ad 步骤 0 的双重 OCR 确认兜底（语义等价，且常见路径由原来的
        # "预检 + _close_ad 复检" 2 次 OCR 降为 1 次）；tap 未触发的极端 case
        # 靠下次进台读实际计数自然纠偏（实测 0 例）。
        # 关闭广告：关键！广告页 WebView 文字 uiautomator 读不到（会穿透读到
        # 背景任务中心，导致误判），必须用 OCR 检测「关闭广告」并读取其坐标，
        # 主动点击后再次用 OCR 确认该按钮消失。从实测看「关闭广告」在左上角。
        t_close0 = time.time()        # P1（2026-09-29）：关闭链耗时埋点
        closed = self._close_ad(tc_seen=False, sheet_ok=sheet_ok)
        logger.info("[计时] 广告关闭链 %.1fs", time.time() - t_close0)
        # E 优化：预定位坐标只在本观看周期内有效，无论关闭成败一律作废，
        # 防旧坐标泄漏到后续其它 _close_ad 调用路径（残留清场等）被误消费。
        self._prefetch_close = None
        if not closed:
            logger.error("多次尝试后广告仍未关闭")
            return False
        logger.info("广告已关闭，回到任务中心")
        time.sleep(self.t.get("ad_close_wait", 2.0))
        return True

    def _watch_signin_ad(self) -> bool:
        """签到浮层内的「看广告 +⚡」奖励广告（2026-09-16 用户需求）。

        前提：签到成功、「我知道了」已处理、每日免费领浮层在屏。
        找按钮：① dump 含匹配「看广告」且 y>=1600（浮层底部；y 限制避开
        dump 穿透读到的背景任务中心行）——实测按钮文本可能带 +⚡ 后缀/拆分，
        用子串包含而非整词；② OCR 兜底（底部区域），命中坐标点 trusted=True
        （浮层已确认在屏，同 _signin 现有例外同理）。
        看完复用 _ad_play_and_close()；回来若浮层仍在则关 ✕。
        任何异常只记日志不抛出 —— 奖励广告失败不阻断签到返回值与后续
        反馈/主广告。每台每天至多触发 1 次（挂在"真正签到"分支下）。
        """
        try:
            if not (self._find("每日免费领") or self._find("恭喜获得")):
                logger.info("签到浮层已不在屏，跳过签到奖励广告")
                return False
            cands = [n for n in self.ui.nodes()
                     if "看广告" in (n.text or "") and n.y1 >= 1600]
            btn = max(cands, key=lambda n: n.y1) if cands else None
            if btn is not None:
                logger.info("签到奖励广告：浮层按钮 @ (%d,%d) 文本=%r",
                            btn.x1, btn.y1, btn.text)
                self._tap_node(btn)
            else:
                pos = self._ocr_find("看广告", region=(0, 1600, 1080, 1920))
                if pos is None:
                    logger.info("签到浮层未找到 看广告 按钮"
                                "（已看过或版式不同），跳过奖励广告")
                    return False
                logger.info("签到奖励广告：dump 未命中，OCR 命中 @ %s", pos)
                self._tap(*pos, trusted=True)
            ok = self._ad_play_and_close(sheet_ok=True)
            # 看完可能回到浮层（而非任务中心）→ 浮层仍在则关 ✕（F11：在屏才点）
            if self._find("每日免费领") or self._find("恭喜获得"):
                self._tap(*MODAL_CLOSE, trusted=True)
                time.sleep(1.5)
            if ok:
                logger.info("签到奖励广告完成")
            else:
                logger.warning("签到奖励广告未正常关闭（不阻断主流程）")
            return ok
        except Exception as e:  # noqa: BLE001
            logger.warning("签到奖励广告异常（不阻断主流程）：%s", e)
            return False

    def _read_ad_ratio(self) -> Optional[Tuple[int, int]]:
        """读「获取随机」行的 X/Y 计数（与 _ad_quota_done 同口径）。

        行未读到（含页面未就绪 / 被问卷 H5 覆盖）返回 None —— 调用方自行决定
        保守策略。供 run_all 进台时读取基数：后续配额收尾用「基数 + 本会话已看」
        算术预判，不必每次看完都读屏（省审计 C2 的 dump 超时空等）。
        """
        row = self._find_row(AD_ROW_LABELS[0], alt_labels=AD_ROW_LABELS[1:])
        if not row:
            return None
        _btn, _label, ratio = row
        if not ratio:
            return None
        # 分母必须是日配额（广告行恒为 X/ad_times_per_robot，2026-09-30）：
        # 读到不符的分母（如串扰成 (2,1000)）说明命中了错误数字 → 视为读不到，
        # 走调用方的保守回退（宁可不校准，也不拿错数污染 base / 重锚）。
        quota = int(self.wf.get("ad_times_per_robot", 10))
        if ratio[1] != quota:
            logger.warning("_read_ad_ratio 分母异常（%d/%d，期望 /%d）→ 视为读不到",
                           ratio[0], ratio[1], quota)
            return None
        return ratio

    def _ad_quota_done(self) -> bool:
        """当前任务中心「看广告」行 X/10 是否已达每日配额（F10 用）。

        读取「获取随机」行内的 X/Y 计数（看广告 X/10），X>=目标即视为
        已看完 —— 会话内首检（跨进程残留/手动已看完则不再空等）与 CD 后
        复检都靠它。目标取 config workflow.ad_times_per_robot（每日配额），
        与 _watch_ad_once 的跳过守卫同口径。行未读到（含页面未就绪）返回
        False —— 保守继续，交给 watch 自身的守卫/失败计数兜底。
        """
        target = self.wf.get("ad_times_per_robot", 10)
        ratio = self._read_ad_ratio()
        return bool(ratio and ratio[0] >= target)

    def _verify_session_quota(self, name: str, done: int) -> int:
        """方案 A（2026-09-29 游迦 9/10 实锤）：连看会话收尾 break 前读屏复核。

        背景：末支广告 tap 未触发 → 广告页从未打开 → `_close_ad` 双 OCR 判
        「已在任务中心，无需关闭」（`_ad_close_suspicious=True`）→ done 无条件
        +1 虚报。09-13 B 优化删除观看后预检时假设「tap 未触发靠下次进台读屏
        自然纠偏（实测 0 例）」，但 09-28 方案 A/C 引入算术收尾后连看会话
        收尾不再读屏，「自然纠偏」通道消失 → 出现第 1 例（游迦 10/10 vs 屏
        幕 9/10）。

        策略：先沉降 ad_close_settle（默认 2.0s）再读屏，失败再等 2s 重试
        一次，共 2 次机会：
        - 读到 X：返回 X（屏幕真值）。调用方以 X 继续 while 循环 → 差几支
          补几支；X>=配额则正常收尾。done 口径本就是「屏幕等价计数」
          （start_done+已看，entry_ratio 同口径），直接覆盖语义一致。
        - 两次读不到：保守返回原 done（维持旧行为，不引入新失败模式）。

        仅在末支关闭路径可疑时被调用（正常关闭路径零额外读屏）。
        """
        for i in (1, 2):
            time.sleep(float(self.wf.get("ad_close_settle", 2.0))
                       if i == 1 else 2.0)
            ratio = self._read_ad_ratio()
            if ratio is None:
                logger.warning("%s 收尾配额复核：第 %d 次读屏失败", name, i)
                continue
            actual = ratio[0]
            if actual < done:
                logger.warning("%s 收尾配额复核：屏幕 %d/%d < 本会话计数 %d"
                               "（tap 未触发/未计入实锤）→ 按屏幕真值校准",
                               name, actual, ratio[1], done)
            else:
                logger.info("%s 收尾配额复核：屏幕 %d/%d（本会话计数 %d，一致或"
                            "有手动补看）", name, actual, ratio[1], done)
            return actual
        logger.warning("%s 收尾配额复核：两次读屏均失败 → 保守按原计数 %d 收尾",
                       name, done)
        return done

    # ----------------------------------------------------------
    # Badcase 问卷巡检（L3 2026-09-09 末次加固）
    # ----------------------------------------------------------
    # QQ AI 好友平台在广告关闭后 ~35-40s 会延迟弹出 Badcase 反馈问卷
    # （腾讯问卷 H5 加载页，实测标题含 Badcase/反馈问卷，正文含 开始填写/
    # 感谢大家一直）。该 H5 是 WebView 内容 —— uiautomator 读不到（dump
    # 穿透读到背景任务中心），只能用 OCR 识别。主循环每轮看广告前巡检
    # 一次，命中即物理 BACK 退出（不依赖任何 UI 坐标，避开 banner 区）。
    _BADCASE_KEYS = ("Badcase", "反馈问卷", "开始填写", "感谢大家一直")
    # 资料卡特征词（2026-09-13 小麦事故）：机器人个人资料卡浮层盖顶时，dump
    # 穿透读到背景列表文本，导航/安全返回判定全被欺骗（实测卡死 ~1h）。
    # 「语音通话」「QQ空间」仅出现在资料卡上，列表/任务中心/问卷均无。
    _PROFILE_KEYS = ("语音通话", "QQ空间", "她的QQ空间", "他的QQ空间")
    # AI 好友 banner H5：方案 A 实施后被观测到的"OCR 漏读任务中心特征词 →
    # 误 tap (160,152) 命中任务中心顶部 banner"事故（2026-09-10 11:43 实锤）。
    # banner H5 全屏显示「QQ AI好友·常见问题答疑」。**不能**仅凭"常见问题答疑"
    # 判定 —— 任务中心顶部 banner 自身就含这几个字，会误判。必须再叠加
    # "OCR 读不到任务中心特征词"才认账。
    # F5（2026-09-10 11:58 实测补词）：点开后真正的全屏页是 AI 好友**帮助中心**，
    # 标题只有「常见问题」（无"答疑"）、正文分组「基础权益 / 额度消耗规则 /
    # 免费额度、电量与心动卡」。旧词表两项都命中不了这一页 → 盲点误开 H5 后
    # `_dismiss_badcase()` 连续 10 帧毫无察觉（帧 #012-#021），继续 BACK 直到
    # 退出 QQ。补入该页**独有**的分组/条目词（任务中心不含这些字）。
    _AI_FRIEND_HINTS = ("常见问题答疑", "QQ AI好友", "额度消耗规则", "心动卡",
                        "必须付费才能", "免费额度的途径")

    def _badcase_visible(self) -> bool:
        """OCR 全屏是否命中 Badcase 问卷特征。全屏 OCR ~2-3s，仅主循环
        每轮开头调用一次，成本可控。"""
        return self._ocr_find(*self._BADCASE_KEYS, retries=1) is not None

    # 覆盖层独有特征词（F11，用于 `_back_at_taskcenter` 最后一道闸）：
    # 任务中心页面**绝不含**「Badcase」「反馈问卷」（顶部 banner 只有
    # 「QQ AI好友·常见问题答疑」，任务行只有「问题反馈」4 字，不含「反馈问卷」）
    # → 零误报。只扫顶部条带（~1s），成本仅在"即将判成功"的路径上付出。
    _OVERLAY_TOP_KEYS = ("Badcase", "反馈问卷")

    def _overlay_visible_in_top(self) -> bool:
        """顶部条带是否命中「Badcase 反馈问卷」独有词（F11 问题反馈防线）。

        背景：`QQ AI好友Badcase反馈问卷` 是 WebView H5，uiautomator dump 会
        **穿透**读到其背后任务中心的「每日签到」等行；若任务中心判定只看 dump，
        就会在问卷盖屏时误判"已回任务中心"→ 静默假成功（22s 白耗 + 误报完成）。
        本函数只扫 `AD_TOP_REGION`（问卷标题恰在顶部），命中即证明屏幕被问卷覆盖。
        """
        return self._ocr_find(*self._OVERLAY_TOP_KEYS, retries=1,
                              region=AD_TOP_REGION) is not None

    def _fullscreen_scan(self) -> Tuple[bool, bool, bool]:
        """单次全屏 OCR 同时判定「任务中心 / 关闭按钮 / 覆盖层」（E2 2026-09-14）。

        一次 screencap + 一次 tesseract（全屏）替代原先 `_back_at_taskcenter`
        确认链里「OCR-first 全屏 + 顶条双检」的两次截图两次识别（~2s/次）。
        覆盖层词表 = Badcase 问卷 + 个人资料卡 —— 有意比 dump 链的 F11 闸门
        （仅问卷词）更严：快速通道跳过了 dump 严格确认，用更全的词表补强
        （资料卡覆盖 dump 穿透误判、卡死 1h 的 09-13 教训）。
        匹配语义与 `_ocr_find` 一致：整词 → 跨词元拼接（`_merged_match`）。

        Returns:
            (tc, pill, overlay)：tc=读到任务中心特征词；pill=读到关闭按钮
            （广告未关）；overlay=读到覆盖层特征词（问卷/资料卡等盖屏）。
        """
        if not _HAS_OCR:
            return (False, False, False)
        img = self._ocr_shot()
        if img is None:
            return (False, False, False)
        data = pytesseract.image_to_data(
            img, lang=getattr(self, "_ocr_lang", "chi_sim+eng"),
            output_type=pytesseract.Output.DICT)
        toks: List[Tuple[str, int, int]] = []
        for i in range(len(data["text"])):
            t = (data["text"][i] or "").strip()
            if not t:
                continue
            x = data["left"][i] + data["width"][i] // 2
            y = data["top"][i] + data["height"][i] // 2
            toks.append((t, x, y))

        def _hit(keys) -> bool:
            for t, _x, _y in toks:
                for k in keys:
                    if k in t:
                        return True
            return self._merged_match(toks, keys, None) is not None

        pill_keys = ("关闭广告", "关闭", "跳过", "取消")
        overlay_keys = tuple(self._OVERLAY_TOP_KEYS) + tuple(self._PROFILE_KEYS)
        return (_hit(self._TC_KEYS_OCR), _hit(pill_keys), _hit(overlay_keys))

    def _ai_friend_page_visible(self) -> bool:
        """OCR 是否显示 AI 好友 banner H5 误开页（已点中 banner 后全屏放大）。

        不能仅用「常见问题答疑/QQ AI好友」作为判据 —— 任务中心顶部 banner 自
        身就含这几个字（背景半透明覆盖在任务中心之上）。必须叠加"OCR 读不
        到任务中心特征词"才认账。
        """
        if self._ocr_find(*self._AI_FRIEND_HINTS, retries=1) is None:
            return False
        return not self._taskcenter_confirmed_by_ocr()

    def _overlay_scan(self) -> Tuple[bool, bool, bool]:
        """单次 OCR 同时判定 Badcase 问卷 / AI 好友 H5 / 个人资料卡（A4 2026-09-12）。

        背景：_dismiss_badcase 每次调用要做 2-3 次全屏 OCR（badcase 词表一次、
        AI 好友词表一次、AI 好友命中后任务中心词表再一次），全屏推理 ~1.5s/次。
        各组词查的都是**同一帧全屏画面** —— 合并成一次 image_to_data 推理，
        在同一份词元列表上分别对各组词做整词+跨词元匹配，一次推理出全部结论。
        判定语义与原来逐个 _ocr_find 完全一致：
          - bad: 命中 _BADCASE_KEYS 任一词
          - ai:  命中 _AI_FRIEND_HINTS 任一词 且 未命中 _TC_KEYS_OCR 任一词
          - profile: 命中 _PROFILE_KEYS 任一词（2026-09-13 小麦事故新增）
        返回 (bad, ai, profile)。OCR 不可用/截图失败返回 (False, False, False)
        （与原逻辑"读不到=不认账"一致）——但补一条 WARNING：觅夏事故（2026-09-30）
        暴露出这种"失明即放行"若静默，真机排障无从判断覆盖层漏检是"没命中"还是
        "根本没读到屏"。行为不变，仅让盲区可见。
        """
        if not _HAS_OCR:
            logger.warning("_overlay_scan：OCR 不可用 → 覆盖层判定失明（返回无 overlay）")
            return False, False, False
        img = self._ocr_shot()
        if img is None:
            logger.warning("_overlay_scan：截图失败 → 覆盖层判定失明（本次按无 overlay "
                           "放行，注意资料卡/问卷可能漏检）")
            return False, False, False
        data = pytesseract.image_to_data(
            img, lang=getattr(self, "_ocr_lang", "chi_sim+eng"),
            output_type=pytesseract.Output.DICT)
        toks: List[Tuple[str, int, int]] = []
        n = len(data["text"])
        for i in range(n):
            t = (data["text"][i] or "").strip()
            if t:
                toks.append((t, data["left"][i] + data["width"][i] // 2,
                             data["top"][i] + data["height"][i] // 2))
        bad = self._merged_match(toks, self._BADCASE_KEYS, None) is not None
        ai_hit = self._merged_match(toks, self._AI_FRIEND_HINTS, None) is not None
        tc_hit = self._merged_match(toks, self._TC_KEYS_OCR, None) is not None
        ai = ai_hit and not tc_hit
        profile = self._merged_match(toks, self._PROFILE_KEYS, None) is not None
        return bad, ai, profile

    def _dismiss_badcase(self, max_back: int = 3) -> bool:
        """若 Badcase 问卷或 AI 好友 banner H5 在屏 → 物理 BACK 退出。
        返回是否已清掉（无 overlay 时直接 True，不产生任何 adb 动作）。

        A4（2026-09-12）：检测改走 _overlay_scan —— 单次 OCR 推理同时判
        Badcase/AI 好友/任务中心三组词（原先 2-3 次推理）。BACK 循环内同样
        用它复核。开销从 ~2.3s/次（全屏 OCR ×2 + sleep 1s 的 OCR 部分）
        降到 ~1.5s/次；按每轮广告 + 每台签到前各一次计，10 台全量省 ~15-20s。
        """
        bad, ai, profile = self._overlay_scan()
        if not bad and not ai and not profile:
            return True
        # F7（2026-09-10）：点名是哪种 overlay 并留现场截图 —— 此前日志只有一句
        # "检测到 Badcase 问卷 / AI 好友 H5"，真报/误报无法区分（实测 12:25 那轮
        # 反复触发却无法回溯当时画面），排查成本极高。
        logger.warning("检测到 %s，物理 BACK 退出",
                       "Badcase 问卷" if bad else
                       ("个人资料卡" if profile else "AI 好友 H5"))
        self._diag_shot("overlay")
        for _ in range(max_back):
            self.ui.back(pause=1.2)
            time.sleep(1.0)
            if not any(self._overlay_scan()):
                logger.info("Badcase/AI 好友/资料卡页已退出")
                return True
        logger.warning("连续 %d 次 BACK 后仍在 Badcase / AI 好友 / 资料卡页",
                       max_back)
        return False

    # ----------------------------------------------------------
    # 广告关闭（OCR 定位「关闭广告」按钮）
    # ----------------------------------------------------------
    def _invalidate_shot_cache(self):
        """失效截图/OCR 结果缓存（A2）。任何界面动作后由 ui.on_action 自动调用；
        画面内容随动作改变，缓存的旧帧不可再用。"""
        self._shot_cache = None
        self._shot_ts = 0.0
        self._ocr_result_cache.clear()

    def _ocr_shot(self) -> Optional["Image.Image"]:
        """截图返回 PIL Image（1080x1920），失败返回 None。

        2026-09-10 加超时：原先未设 timeout，emulator 卡住时 screencap
        会永久挂起导致整个流程停摆（无人值守场景必须避免）。
        A2（2026-09-12）：加 0.8s TTL 缓存 —— 短时间内多次 OCR 查询
        （如 _dismiss_badcase 连查 Badcase/AI 好友两张、_close_ad 确认链）
        共用同一帧，省掉重复 screencap subprocess（每次 ~0.5-1s）。
        失效：界面动作（tap/swipe/back，经 ui.on_action 钩子）、TTL 过期、
        显式 _invalidate_shot_cache()。
        """
        now = time.time()
        if (self._shot_cache is not None
                and now - self._shot_ts < self._shot_ttl):
            return self._shot_cache
        try:
            out = subprocess.run(
                [self.ui.adb, "-s", self.ui.device, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=SHOT_TIMEOUT)
            img = Image.open(io.BytesIO(out.stdout)).convert("RGB")
            self._shot_cache = img
            self._shot_ts = time.time()
            return img
        except subprocess.TimeoutExpired:
            logger.warning("截图超时 (> %.0fs)，设备可能无响应", SHOT_TIMEOUT)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("截图失败: %s", e)
            return None

    def _ocr_find(self, *texts: str, region: Tuple[int, int, int, int] | None = None,
                  ymax: Optional[int] = None, retries: int = 1,
                  interval: float = 1.0) -> Optional[Tuple[int, int]]:
        """OCR 全屏/指定区域找文字，返回中心坐标；找不到重试。

        **两遍匹配**（2026-09-10 修复「任务中心判定恒失配」）：
          1) 整词匹配（旧行为）：单个词元里包含关键词即命中，返回该词元中心。
          2) 跨词元匹配（新增）：整词全部失配时，把各词元按 OCR 阅读顺序**直接
             拼接**成一个字符串再做子串定位，返回命中跨度内各词元中心的均值。

        为什么必须有第 2 遍：tesseract(chi_sim) 实测会把连续中文切成单字/碎词 ——
        任务中心标题读到的是 `任务`+`中`+`心`，行名读到 `每`+`日`+`签到`，
        按钮读到 `获取`+`随机`。旧实现只做整词匹配 → `"任务中心" in t` 恒 False
        → `_taskcenter_confirmed_by_ocr()` 永远 False → `_back_at_taskcenter()`
        永远 False → `_close_ad` 认定"广告没关"而无限 BACK，一路退出 QQ
        （2026-09-10 11:58 实测：代柯单条广告连按 12 次 BACK 退到桌面）。

        注：广告页「关闭广告」同理被切成 `关闭`+`广告`，历史上只因关键词表里
        额外带了短词 `关闭` 才勉强命中 —— 这是"关闭能读到、任务中心读不到"的
        根因，两遍匹配后两类页面判定口径统一。

        A3（2026-09-12）：加 OCR 结果短缓存 —— 命中结果 TTL 0.5s、未命中
        TTL 0.25s（未命中更短：页面可能在变化，"没找到"的时效性要求更高）。
        同一页面状态内反复查询同一组词（如 _close_ad 快路径连续 2 次
        _taskcenter_confirmed_by_ocr）直接复用上次结果，省一次全屏 OCR
        （~1-2s）。界面动作后经 _invalidate_shot_cache 一并清空。
        """
        if not _HAS_OCR:
            return None
        cache_key = (texts, region, ymax)
        now = time.time()
        hit = self._ocr_result_cache.get(cache_key)
        if hit is not None:
            pos, ts = hit
            ttl = self._ocr_result_ttl if pos is not None else min(
                self._ocr_result_ttl, 0.25)
            if now - ts < ttl:
                return pos
        for _ in range(retries):
            img = self._ocr_shot()
            if img is None:
                time.sleep(interval)
                continue
            if region:
                img = img.crop(region)
            data = pytesseract.image_to_data(
                img, lang=getattr(self, "_ocr_lang", "chi_sim+eng"),
                output_type=pytesseract.Output.DICT)
            n = len(data["text"])
            toks: List[Tuple[str, int, int]] = []   # (词元, 中心x, 中心y)
            for i in range(n):
                t = (data["text"][i] or "").strip()
                if not t:
                    continue
                x = data["left"][i] + data["width"][i] // 2
                y = data["top"][i] + data["height"][i] // 2
                if region:
                    x += region[0]
                    y += region[1]
                # 第 1 遍：整词匹配
                if ymax is None or y <= ymax:
                    for k in texts:
                        if k in t:
                            self._ocr_result_cache[cache_key] = ((x, y), now)
                            return (x, y)
                toks.append((t, x, y))
            # 第 2 遍：跨词元拼接匹配（仅在第 1 遍全部失配时执行）
            pos = self._merged_match(toks, texts, ymax)
            if pos is not None:
                self._ocr_result_cache[cache_key] = (pos, now)
                return pos
            time.sleep(interval)
        self._ocr_result_cache[cache_key] = (None, now)   # 未命中也缓存（短 TTL）
        return None

    @staticmethod
    def _merged_match(toks: List[Tuple[str, int, int]], texts, ymax):
        """词元按阅读顺序拼接后做子串匹配；返回命中跨度中心坐标，未命中 None。

        仅用于「整词匹配已全部失配」的兜底（见 `_ocr_find` 第 2 遍）。拼接不插
        任何分隔符，因此被 tesseract 切碎的词能重新连成完整关键词；坐标取命中
        区间内所有词元中心的均值（对"关闭广告"这类被切两半的按钮，落点仍在
        按钮内）。
        """
        if not toks:
            return None
        joined = ""
        spans: List[Tuple[int, int]] = []
        for t, _x, _y in toks:
            spans.append((len(joined), len(joined) + len(t)))
            joined += t
        for k in texts:
            if not k:
                continue
            p = joined.find(k)
            if p < 0:
                continue
            end = p + len(k)
            idxs = [i for i, (s, e) in enumerate(spans) if s < end and e > p]
            if not idxs:
                continue
            x = sum(toks[i][1] for i in idxs) // len(idxs)
            y = sum(toks[i][2] for i in idxs) // len(idxs)
            if ymax is not None and y > ymax:
                continue   # 命中跨度中心超出上限（正文误判）
            return (x, y)
        return None

    def _back_at_taskcenter(self, timeout: Optional[float] = None,
                            allow_dump_fallback: bool = False) -> bool:
        """判断是否真的回到了「任务中心」页面（F10+ 严格版）。

        allow_dump_fallback（2026-09-27 新增，默认关）：OCR 快通道判「不在任务
        中心」时，额外用 dump 复核一次（`_dump_confirms_taskcenter`）。**只给
        即将执行破坏性动作的调用方开** —— `_close_ad` 的物理 BACK 兜底（误判会
        退穿到 QQ 联系人页，实测 3 次 BACK 退 3 层）与连看会话的复位重进决策
        （误判会触发全量重新进台＝页面反复刷新）。不开则行为与旧版完全一致，
        成功/正常路径零额外开销。

        timeout（2026-09-11 优化 A）：透传给 dump，供 _close_ad 关闭广告场景压低
        确认阶段的单次 dump 等待（默认 None→DUMP_TIMEOUT=4s；_close_ad 传
        ad_close_dump_timeout=2.5s）。关闭动画期 dump 必然超时，用短超时快速失败。

        必须同时满足：
        - uiautomator 看到核心任务行（获取随机/看广告 + 每日签到 或 任务中心）。
          仅靠"每日签到"字样不够 —— 问题反馈问卷等其它 H5 全屏页覆盖时，
          uiautomator 仍能穿透读到背景任务中心的"每日签到"字样（旧版漏洞：
          _close_ad 直关 tap (160,152) 在问卷页无效 → _back_at_taskcenter
          误判回任务中心 → 静默假成功，22s sleep 在问卷上白耗）。
        - OCR 顶部条带无「关闭广告/关闭/跳过」按钮（广告未关）。

        性能优化（2026-09-10，dump 重试 #2 于 2026-09-10 再优化）：**OCR 先行、
        dump 作二次确认**。视频/动画期间 uiautomator 无法 idle → dump 必然超时
        （现已改为超时即失败、不重试，单次 ~4s 而非 4s×2≈8.3s；关闭广告路径更用
        2.5s）；而任务中心特征词同样能用 OCR 读到（且不受动画影响，~1.3s）。因此
        先用 OCR 排除广告页，只有 OCR 命中时才付出 dump 的代价做严格二次确认。
        """
        # 1) 单次全屏 OCR 三合一判定（E2 2026-09-14：合并原「OCR 先验」与
        #    「顶条双检」的两次截图两次识别，~2s/次）：
        #   信号① 读到任务中心特征词（读不到 → 一定没回任务中心 → False）
        #   信号② 无关闭按钮（广告确已关闭）
        #   信号③ 无覆盖层特征词（问卷/资料卡未盖屏，词表比 F11 闸门更全）
        # 三信号齐 → 直接判 True，跳过 dump。安全性依据：
        #   - dump 超时路径现行代码本就「信任 OCR 判 True」（下方 streak>0 分支），
        #     本通道只是把「先白等 2.5s 再得出同一结论」提前；
        #   - dump 严格确认唯一能多拦住的是「OCR 误命中 H5 覆盖页」，而 OCR 截屏
        #     读的是最上层像素、本就不穿透不透明覆盖层（dump 才会穿透），信号③
        #     的覆盖层词表（问卷+资料卡）已兜住该场景。
        #   误判兜底：信号②/③任一命中 → 落回下方 dump 严格链，行为与旧版一致。
        #   设 workflow.tc_confirm_skip_dump: false 可整体回退旧两步链。
        if self.wf.get("tc_confirm_skip_dump", True):
            tc, _pill, _overlay = self._fullscreen_scan()
            if not tc:
                # 2026-09-27：OCR 快通道判「不在任务中心」→ 可选 dump 复核（默认关）。
                if allow_dump_fallback and self._dump_confirms_taskcenter(timeout):
                    return True
                return False
            if not _pill and not _overlay:
                return True
        elif not self._taskcenter_confirmed_by_ocr():
            if allow_dump_fallback and self._dump_confirms_taskcenter(timeout):
                return True
            return False
        # 2) dump 二次确认：防全屏 H5 / 问卷页 OCR 误命中导致的静默假成功
        #    （旧版漏洞：问卷页 tap 无效却被判成功，白白空等 22s）
        if not self._find("获取随机", timeout=timeout):
            # P4（2026-09-12）：第一个 dump 已在动画期超时，第二个同源 dump
            # （看广告）大概率只会再空等一次；此时沿用原兜底策略，信任 OCR 正向命中。
            if getattr(self.ui, "dump_fail_streak", 0) > 0:
                logger.info("任务中心校验：dump 不可用（连续失败 %d 次），"
                            "以 OCR 判定为准", self.ui.dump_fail_streak)
                return True
            if not (self._find("看广告", timeout=timeout)
                    or self._find("看视频赚电量", timeout=timeout)):
                if getattr(self.ui, "dump_fail_streak", 0) > 0:
                    logger.info("任务中心校验：dump 不可用（连续失败 %d 次），"
                                "以 OCR 判定为准", self.ui.dump_fail_streak)
                    return True
                return False
        if not self._find("每日签到", timeout=timeout):
            if getattr(self.ui, "dump_fail_streak", 0) > 0:
                logger.info("任务中心校验：dump 不可用（连续失败 %d 次），"
                            "以 OCR 判定为准", self.ui.dump_fail_streak)
                return True
            if not self._find("任务中心", timeout=timeout):
                if getattr(self.ui, "dump_fail_streak", 0) > 0:
                    logger.info("任务中心校验：dump 不可用（连续失败 %d 次），"
                                "以 OCR 判定为准", self.ui.dump_fail_streak)
                    return True
                return False
        # 关闭按钮消失（仍覆盖 → 广告没关）
        if self._ocr_find("关闭广告", "关闭", "跳过",
                          ymax=400, region=AD_TOP_REGION) is not None:
            return False
        # F11（问题反馈 / 静默假成功防线）：最后一道闸 —— 若顶部有 Badcase 反馈
        # 问卷覆盖，即使 dump 穿透读到背景任务中心的行，也**不算**回到任务中心。
        # 用问卷独有词、只扫顶部条带；只在"即将判成功"时执行（每个广告周期至多
        # 1 次 ~1s），对点击效率无实质影响。
        if self._overlay_visible_in_top():
            logger.warning("任务中心之上仍有覆盖层（Badcase 反馈问卷 / AI 好友 H5），"
                           "判定未回到任务中心（问题反馈静默假成功防线 F11）")
            return False
        return True

    def _dump_confirms_taskcenter(self, timeout: Optional[float] = None) -> bool:
        """dump 复核「是否在任务中心」（2026-09-27，配合 `_back_at_taskcenter`
        的 allow_dump_fallback）。

        实测依据：OCR 判据只有 4 词（每日签到/任务中心/获取随机/收支详情），
        滚动位置、WebView 重绘、视频期读屏失败都会把它打空 —— 真机实测出现过
        「页面确为任务中心（dump 有 收支详情@(955,138)、任务中心@(172,1903)）
        但 back_at_tc=False」。而 dump 在页面 idle 时稳定含任务行节点。两信号
        互补 → 叠加后可避免「误判不在任务中心 → 物理 BACK 退穿 / 全量重新进台」。

        安全（防 F11「dump 穿透」静默假成功）：dump 命中必须叠加 **OCR 未见
        覆盖层**（问卷 / 个人资料卡 / AI 好友 H5）—— OCR 截屏读最上层像素、
        不穿透，与 `_back_at_taskcenter` 信号③同一依据。

        成本：仅在 OCR 已判「不在任务中心」的路径上付出（且只在显式开
        allow_dump_fallback 的调用方），成功/正常路径零开销。
        """
        try:
            bad, ai, profile = self._overlay_scan()
        except Exception:  # noqa: BLE001
            return False
        if bad or ai or profile:
            return False
        try:
            texts = {n.text for n in self.ui.nodes(timeout=timeout) if n.text}
        except Exception:  # noqa: BLE001
            return False
        hit = texts & {"看广告", "获取随机", "看视频赚电量",
                       "每日签到", "任务中心"}
        if hit:
            logger.info("任务中心判定补强：OCR 未读到特征，但 dump 命中 %s"
                        "（且无覆盖层）→ 判为仍在任务中心", sorted(hit))
            return True
        return False

    def _stable_in_ad_page(self, checks: Optional[int] = None,
                           interval: Optional[float] = None) -> bool:
        """**状态：2026-09-10 方案 A 后已从 `_close_ad` 主路径退役**（保留备用 /
        回归参考）。原因见 `_taskcenter_confirmed_by_ocr` —— 视频广告期 dump
        恒失败时本函数必然返回 False，把它当作"能否盲点 AD_CLOSE"的守卫会导致
        广告永远关不掉（run.log 01:15 实锤）。现在改由 OCR 负向排除判定。

        B+ (2026-09-09 末次加固)：「连续 N 次 _back_at_taskcenter=全部
        False」才认为画面稳定在广告页。用于在 _close_ad 盲点前过滤 banner
        竞态（uiautomator dump 穿透 webview 残留节点导致 _back_at_taskcenter
        瞬时误判 False → 盲点命中任务中心顶部 banner 误开 AI 好友 H5，
        约 37s 后弹 Badcase 反馈问卷）。

        L1 (2026-09-09 23:5x 加固)：dump 失败（页面动画中无法 idle /
        uiautomator rc=139 崩溃）时 nodes() 返回空 → _back_at_taskcenter
        恒 False —— 代码会把"状态未知"误判成"在广告页" → 放行盲点
        (160,152)。而此刻画面可能已回任务中心，盲点落在 banner 区
        （y≈0-440 可点击入口）→ 误开 Badcase 问卷（实测延迟 ~35-40s
        弹出腾讯问卷 H5）。因此每次检查前后若 dump 连续失败（streak>0），
        一律视为状态未知 → 拒绝放行盲点。

        Args:
            checks: 稳定检查次数（默认 wf['ad_close_stable_checks']=2）。
                    传 1 可降级为单次检查（旧行为兼容）。
            interval: 每次检查间隔秒数（默认 wf['ad_close_stable_interval']=0.6）。
                      仅在 checks>1 时生效。

        Returns:
            True  -> 稳定在广告页（且各次判定均基于成功 dump），可以盲点。
            False -> 至少一次 True（已回任务中心），或 dump 失败状态未知
                     （不确定时宁可不点，不冒险命中 banner）。
        """
        n = checks if checks is not None else self.wf.get(
            "ad_close_stable_checks", 2)
        dt = interval if interval is not None else self.wf.get(
            "ad_close_stable_interval", 0.6)
        for i in range(n):
            if getattr(self.ui, "dump_fail_streak", 0) > 0:
                logger.warning("稳定检查第 %d/%d 次前 dump 连续失败 %d 次，"
                               "状态未知，拒绝盲点放行",
                               i + 1, n, self.ui.dump_fail_streak)
                return False
            if self._back_at_taskcenter():
                return False
            if getattr(self.ui, "dump_fail_streak", 0) > 0:
                logger.warning("稳定检查第 %d/%d 次内 dump 失败，本次判定"
                               "不可信，拒绝盲点放行", i + 1, n)
                return False
            if i < n - 1 and dt > 0:
                time.sleep(dt)
        return True

    # ---- 方案 A（2026-09-10）：不依赖 dump 的「是否仍在广告页」判定 ----
    # 任务中心画面必然包含下列特征词之一；广告页一个都读不到。
    # ⚠️ 关键词是**跨词元**匹配（`_ocr_find` 第 2 遍拼接），因为 tesseract 实测
    # 会把「任务中心」读成 `任务`+`中`+`心`、「获取随机」读成 `获取`+`随机`。
    _TC_KEYS_OCR = ("每日签到", "任务中心", "获取随机", "收支详情",
                    "看视频赚电量")

    def _taskcenter_confirmed_by_ocr(self) -> bool:
        """OCR 确认画面在任务中心 —— 用于决定「能否盲点 AD_CLOSE」。

        背景（2026-09-10 真机实锤）：旧判定依赖 uiautomator dump，而视频广告
        播放期 UI 无法 idle，dump 必然失败（rc=139 无输出）→ L1 加固后
        `_stable_in_ad_page()` 恒 False → 第 0 步与第 4 步通往 AD_CLOSE 的两条
        路全被拦死 → 广告关不掉（run.log 01:15 第二次甚至卡到需要手动停止）。

        方案 A：改用 OCR 做**负向排除**（OCR 不受视频/动画影响）：
          - 读不到任何任务中心特征词 → 判定仍在广告页 → 允许盲点 AD_CLOSE
          - 读到特征词 → 已在任务中心 → 禁止盲点（保住 B+/banner 防误触成果）
        """
        return self._ocr_find(*self._TC_KEYS_OCR, retries=1) is not None

    def _ad_close_pos(self) -> Optional[Tuple[int, int]]:
        """OCR 正向定位广告页「关闭广告/跳过/取消」按钮，返回其中心坐标。

        只扫顶部条带（`AD_TOP_REGION`）。实测该判据对广告页/任务中心是**干净
        的区分器**：9 个广告帧 9/9 命中、10 个任务中心帧 0 误报（任务中心顶部
        banner 文字不含这些词）。
        """
        return self._ocr_find("关闭广告", "关闭", "跳过", "取消",
                              ymax=400, region=AD_TOP_REGION)

    def _prefetch_ad_close_pos(self) -> None:
        """E 优化（2026-09-14）：广告播放中预定位「关闭广告」按钮。

        视频期 dump 必然失败，但 OCR 截屏不依赖 idle —— 顶条 OCR ~1s 即可在
        等待窗口内拿到关闭按钮坐标，存入 `self._prefetch_close = (pos, ts)`。
        命中才缓存；读不到（插屏/加载期版式不同，09-14 尔尔事故同款场景）
        不缓存、显式置 None，等待结束后 _close_ad 走原链自愈。
        仅预定位、不点击 —— tap 时机仍由 ad_wait 控制，与原行为一致。
        """
        pos = self._ad_close_pos()
        if pos is not None:
            self._prefetch_close = (pos, time.time())
            logger.info("等待窗口内预定位到关闭按钮 @ %s（E 优化）", pos)
        else:
            self._prefetch_close = None

    def _find_close_node(self, timeout: Optional[float] = None) -> Optional[Node]:
        """uiautomator 定位广告页关闭按钮节点，返回 Node（None = 未命中）。

        匹配：精确「关闭广告」→ 含「跳过」/「关闭」开头的候选（部分广告用
        「跳过」或「关闭」按钮）。坐标来自真实 dump bounds —— F11 认可的
        节点点击，比 OCR 像素定位更稳（17:29 实测 OCR 漏读药丸、dump 一次命中）。
        视频播放期 dump 有界失败 → 返回 None 交上层转 OCR（#2：超时不再重试，
        单次等待从 4s×2≈8s 降到 timeout 值，默认 2.5s）。
        """
        fallback = None
        for cand in self.ui.nodes(timeout=timeout):
            t = cand.text or ""
            if t == "关闭广告":
                return cand
            if fallback is None and ("跳过" in t or t.startswith("关闭")):
                fallback = cand
        return fallback

    def _signin_sheet_visible(self) -> bool:
        """签到奖励广告关闭后的合法终态：每日免费领浮层仍在屏。

        浮层开着 = 已从广告回到任务中心（浮层盖在 TC 之上），只是浮层未关
        —— 此时 OCR 读不到被浮层遮住的任务中心特征词，_back_at_taskcenter
        会误判"未关闭"（2026-09-16 14:18 代柯实测：E1 直关其实已生效，
        确认链不认浮层 → BACK×3 退穿到联系人列表）。u2 dump 毫秒级，代价可忽略。
        """
        try:
            return bool(self._find("每日免费领") or self._find("恭喜获得"))
        except Exception:
            return False

    def _confirm_direct_close(self, timeout: Optional[float],
                              retries: int = 0, gap: float = 1.2,
                              sheet_ok: bool = False) -> bool:
        """直关 tap 后确认已回任务中心；带沉降重试（P5，2026-09-12）。

        首次 `_back_at_taskcenter` 失败大概率是关闭动画/任务中心重载尚未结束
        （OCR 先验读不到特征词、dump 撞超时），并不代表 tap 没生效 —— 21:00 场
        33/33 误判实锤。重试时先睡 gap 秒让页面沉降，再确认一次；全部失败才
        返回 False（此时才值得走 Badcase 巡检 + 兜底）。

        sheet_ok（2026-09-16）：True 时接受「每日免费领浮层在屏」为成功 ——
        仅签到奖励广告路径使用（看完广告回到盖着浮层的任务中心是合法终态）。
        """
        if self._back_at_taskcenter(timeout=timeout) or (
                sheet_ok and self._signin_sheet_visible()):
            return True
        for _ in range(max(0, retries)):
            time.sleep(gap)
            if self._back_at_taskcenter(timeout=timeout) or (
                    sheet_ok and self._signin_sheet_visible()):
                return True
        return False

    def _log_page_snapshot(self, tag: str, timeout: float = 2.5) -> None:
        """判定失灵时的现场留痕（E3 2026-09-14 小麦事故复盘）。

        背景：16:41 小麦事故里预定位 tap 未生效、3 次 BACK 判定全部失败，
        但日志里没有任何"当时停在什么页面"的信息，事后无法归因。本方法在
        放弃关闭等关键节点把当前屏 dump 的可读节点（文本+坐标，最多 15 个）
        打进日志——只读不点，不产生任何点击副作用；dump 失败也会如实记录。
        """
        try:
            nodes = self.ui.nodes(timeout=timeout)
            parts = []
            for n in nodes:
                t = (n.text or "").strip()
                if t:
                    parts.append(f"{t!r}@{n.center}")
                if len(parts) >= 15:
                    break
            logger.warning("页面快照[%s]: dump 可读节点 %d 个: %s",
                           tag, len(parts), "；".join(parts) or "<无文本>")
        except Exception as e:  # noqa: BLE001
            logger.warning("页面快照[%s] 失败: %s", tag, e)

    def _stage(self, t0: float, tag: str) -> float:
        """进/退台分阶段耗时打点（E4 2026-09-14）：打印自上一阶段边界以来的
        耗时（INFO，前缀 [计时] 便于 grep），返回当前时刻供下一段接力。
        只读时钟、无副作用；失败路径不强行补打点（异常分支本就有独立日志）。
        用途：把进台 46s / 退台 29s 精确拆到秒，为后续进退台优化提供实测依据。
        """
        now = time.time()
        logger.info("[计时] %s %.1fs", tag, now - t0)
        return now

    def _close_ad(self, max_tries: int = 6, tc_seen: bool = False,
                  sheet_ok: bool = False) -> bool:
        """主动关闭广告。

        **F8（2026-09-10 修复「盲点误点 banner」）**：所有坐标点击一律由
        「OCR 正向读到关闭按钮」放行 —— **不再有任何固定坐标盲点**。

        事故链（11:43 / 11:58 / 12:25 三次实锤）：
          ① `AD_CLOSE(160,152)` 在广告页是「关闭广告」药丸，在任务中心页却是顶部
             banner「QQAI好友·常见问题答疑」的命中区（banner y≈125-380 与药丸
             y≈107-187 重叠）；
          ② banner 是 WebView **自绘**元素，**不在 uiautomator dump 里**（任务中心
             整页仅 20 个节点、顶部区无任何可点击节点）→ 任何 dump 守卫都拦不住；
          ③ 旧"方案 A"把放行条件写成**负向**（读不到任务中心特征词就点）——广告在
             等待期内自动播完、页面已切回任务中心而 OCR 还在读过渡帧/上一帧时，
             负向条件成立 → 盲点落到 banner → 误开 AI 好友 H5；
          ④ 根因之二：条带高度 430 过大，tesseract 在大片深色视频背景上会整块
             返回空（实测 4399 视频广告 3 帧全读不出「关闭广告」，肉眼极清晰），
             当时只能靠盲点兜底。条带收到 320 后实测广告 9/9 可读、任务中心 0 误报
             （见 `AD_TOP_REGION`）。这与 09-09 23:xx 那次 B+ 修复是同一个 bug，
             当时正是靠"OCR 正向判定"修掉的 —— 方案 A 改负向属于回退。

        现行顺序（2026-09-10 傍晚用户优化：**uiautomator 优先**）：
          0) 已回任务中心？（OCR 连续 2 次判定，快路径）→ 收工，不点任何坐标
          1) **uiautomator 定位关闭按钮**——等待结束后页面多数已 idle，dump
             1-3s 命中且坐标来自真实 bounds（F11 认可的节点点击）。17:29 实测
             OCR 漏读药丸按钮、uiautomator 一次命中 (141,150)。
          2) OCR 正向定位 → 点它自己的坐标（视频播放期 dump 有界失败后的
             可靠读屏路径）
          3) 兜底轮询：任务中心校验 → uiautomator → OCR → 物理 BACK（封顶）

        历史注：OCR 曾排第一（坑 9 时代 dump **无超时**，视频期一次空等 25-50s，
        OCR 是唯一不死等的选择）；DUMP_TIMEOUT=4s 看门狗上线后 dump 失败已有界
        （≤9s），长广告场景的代价可控，故对调。
        """
        max_tries = self.wf.get("ad_close_retries", max_tries)
        # 方案 A（2026-09-29）：每次关闭调用重置可疑标记——只有走到
        # 步骤 0「OCR 判定已在任务中心、未点任何关闭按钮」的返回才置 True。
        self._ad_close_suspicious = False
        # #2 优化（2026-09-10）：关闭广告时 uiautomator dump 用更短超时（默认 2.5s）
        # 且不重试，压低视频/动画期每次查找的等待（原 DUMP_TIMEOUT 4s×2≈8s 是日志里
        # 123 次 dump 超时浪费的主因）。dump 超时即失败、转 OCR 兜底路径。
        ad_close_dump_timeout = float(self.wf.get("ad_close_dump_timeout", 2.5))
        # B 优化（2026-09-11）：关闭按钮 tap 后的"沉降"等待（秒）。广告关闭动画/任务中心
        # 重载期 UI 无法 idle、OCR 读不到特征，立即确认必然撞 dump 超时（代柯 32s 实测）。
        # 沉降后再确认，让动画播完、OCR/首次 dump 直接命中任务中心。
        ad_close_settle = float(self.wf.get("ad_close_settle", 2.0))
        # P5（2026-09-12 晚）：直关 tap 后的确认重试。21:00 场实测 33/33 次直关
        # 被误判"未生效"（用户肉眼看广告当场已关）：关闭动画/任务中心重载期
        # settle=1.0s 后 OCR 先验仍读不到任务中心特征词 → 单次确认立即 False →
        # 白走 7-12s 兜底。现改为：首次确认失败后等 confirm_gap 秒再重试，
        # 共 1+retries 次确认，全部失败才宣告"未生效"走巡检+兜底。成功且页面
        # 沉降快时行为与耗时不变；真失败仅多花 retries×(gap+确认) 秒进兜底。
        ad_close_confirm_retries = int(self.wf.get("ad_close_confirm_retries", 1))
        ad_close_confirm_gap = float(self.wf.get("ad_close_confirm_gap", 1.2))
        # -1) E1 优化（2026-09-14 下午复盘）：预定位路径顶条快检优先。
        #     等待结束时先用顶条 OCR（~1.5-2s）查关闭按钮——命中即证明画面
        #     仍是广告页（F8 语义：OCR 正向读到关闭按钮才放行，且 tap 的是
        #     **本次现读坐标**比预定位坐标更新鲜），直接跳过步骤 0 全屏守卫
        #     （~3.4s；全场 82 次预定位命中 100% 时它纯属白跑）。快检漏读 →
        #     预定位缓存保留，落到步骤 0 全屏守卫 + 0.5 预定位消费的既有链路
        #     （行为与上一版完全一致）。快检命中但确认失败 → 清缓存走既有
        #     巡检 + 兜底链自愈。
        pref_early = self._prefetch_close
        if (pref_early is not None
                and time.time() - pref_early[1] <= float(
                    self.wf.get("ad_prefetch_ttl", 20.0))):
            pos = self._ad_close_pos()
            if pos is not None:
                logger.info("顶条快检命中关闭按钮 @ %s（E1，跳过全屏守卫）", pos)
                self._tap(*pos, pause=1.5, trusted=True)
                time.sleep(ad_close_settle)
                self._prefetch_close = None      # 快检路径已消费
                if self._confirm_direct_close(ad_close_dump_timeout,
                                              ad_close_confirm_retries,
                                              ad_close_confirm_gap,
                                              sheet_ok=sheet_ok):
                    logger.info("广告已关闭（顶条快检直关路径）")
                    return True
                logger.info("快检直关未生效，巡检一次 Badcase/AI 好友后走兜底")
                # E3：直关未生效瞬间的现场留痕（小麦 3 号素材事故归因关键）
                self._log_page_snapshot("快检直关未生效")
                # 与直关失败同款处置：万一 tap 落点已漂移误开 H5，立即 BACK
                self._dismiss_badcase()
            # 快检未命中：不消费，交给步骤 0 / 0.5 既有链路
        # 0) 快路径 + 正向直关
        #   - P1（2026-09-12）：复用 _watch_ad_once 在 ad_wait 后的任务中心 OCR。
        #     若前置 OCR 已读到任务中心特征，这里只做第 2 次确认即可收工；避免
        #     同一状态连续做 3 次全屏 OCR。仍保留双检语义，防广告素材偶然含
        #     「每日签到/任务中心」等字样导致单检误判。
        if tc_seen:
            time.sleep(0.8)
            if self._taskcenter_confirmed_by_ocr():
                logger.info("OCR 二次判定：已在任务中心（复用前置命中），无需关闭")
                # 方案 A：未点到任何关闭按钮即判成功 → 标记可疑，收尾复核配额
                self._ad_close_suspicious = True
                return True
        else:
            if self._taskcenter_confirmed_by_ocr():
                logger.info("OCR 首次判定：已在任务中心，需二次确认")
                time.sleep(0.8)
                if self._taskcenter_confirmed_by_ocr():
                    logger.info("OCR 二次判定：已在任务中心，无需关闭")
                    # 方案 A：同上（游迦 9/10 虚报即此路径，2026-09-29 实锤）
                    self._ad_close_suspicious = True
                    return True
        # 0.5) E 优化（2026-09-14）：消费等待窗口内预定位的关闭按钮坐标。
        #      前提是步骤 0 守卫刚确认「不在任务中心」—— 该守卫是 F8 防线：
        #      若广告已提前自动结束、页面已切回任务中心，这里绝不消费旧坐标
        #      （否则 (141,150) 会落到顶部 banner 命中区，复刻误开 AI 好友 H5）。
        #      守卫通过说明画面仍是广告页，预定位坐标（单次消费 + TTL 双保险）
        #      可直接 tap（trusted=True，与步骤 2「OCR 正向定位放行」同语义），
        #      省掉步骤 1 的 dump 2.5s 白等 + 步骤 2 的重新 OCR 定位。
        #      tap 后与直关同款确认链；确认失败由既有「巡检 + 兜底轮询」自愈。
        pref = self._prefetch_close
        self._prefetch_close = None          # 单次消费，用完即弃
        if (pref is not None
                and time.time() - pref[1] <= float(
                    self.wf.get("ad_prefetch_ttl", 20.0))):
            logger.info("使用等待窗口预定位的关闭按钮 @ %s（E 优化，跳过 dump 定位）",
                        pref[0])
            self._tap(*pref[0], pause=1.5, trusted=True)
            time.sleep(ad_close_settle)
            if self._confirm_direct_close(ad_close_dump_timeout,
                                          ad_close_confirm_retries,
                                          ad_close_confirm_gap,
                                          sheet_ok=sheet_ok):
                logger.info("广告已关闭（预定位直关路径）")
                return True
            logger.info("预定位直关未生效，巡检一次 Badcase/AI 好友后走兜底")
            # E3：直关未生效瞬间的现场留痕（同快检路径）
            self._log_page_snapshot("预定位直关未生效")
            # 与步骤 1/2 直关失败同款处置：万一 tap 落点已漂移误开 H5，立即 BACK
            self._dismiss_badcase()
        # 1) uiautomator 直关（2026-09-10 对调）：页面 idle 时 1-3s 命中。
        #    直关失败（tap 未生效/页面又变了）→ 巡检一次覆盖层再走 OCR/兜底。
        n = self._find_close_node(timeout=ad_close_dump_timeout)
        if n is not None:
            logger.info("uiautomator 定位到关闭按钮 @ %s（直关，第 1 步）", n.center)
            self._tap_node(n, pause=1.5)
            time.sleep(ad_close_settle)
            if self._confirm_direct_close(ad_close_dump_timeout,
                                          ad_close_confirm_retries,
                                          ad_close_confirm_gap,
                                          sheet_ok=sheet_ok):
                logger.info("广告已关闭（uiautomator 直关路径）")
                return True
            logger.info("uiautomator 直关未生效，巡检一次 Badcase/AI 好友后走兜底")
            # 万一 tap 落点已随广告结束漂移、误开了 AI 好友 H5，立即物理 BACK 退出
            self._dismiss_badcase()
        # 2) OCR 正向直关（视频播放期 dump 失效时唯一可靠读屏）
        pos = self._ad_close_pos()
        if pos is None:
            # 过渡帧防误判（2026-09-14 尔尔插屏事故）：广告页加载/切换期单次 OCR
            # 可能漏读关闭按钮，直接判"没广告"走 BACK 兜底会把流程留在广告页
            # （插屏视频常驻 → 全局 dump 失效 → 死锁 40min）。间隔 2s 复读一次
            # 再决定；两次都读不到才认账。
            time.sleep(2.0)
            pos = self._ad_close_pos()
        if pos is None:
            logger.info("OCR 未在顶部条带读到关闭按钮（%s），不盲点固定坐标，"
                        "直接走兜底轮询", AD_TOP_REGION)
        else:
            logger.info("OCR 定位到关闭按钮 @ %s，点击其坐标（正向放行）", pos)
            # F11：广告页的关闭按钮由 OCR 正向定位（非盲点）→ 显式 trusted=True
            # 放行任务中心页的盲点禁令。
            self._tap(*pos, pause=1.5, trusted=True)
            time.sleep(ad_close_settle)
            if self._confirm_direct_close(ad_close_dump_timeout,
                                          ad_close_confirm_retries,
                                          ad_close_confirm_gap,
                                          sheet_ok=sheet_ok):
                logger.info("广告已关闭（OCR 直关路径）")
                return True
            logger.info("OCR 直关未生效（%s），巡检一次 Badcase/AI 好友后走兜底", pos)
            # 万一 OCR 读到的按钮已随广告消失、tap 落到 banner 打开了 AI 好友 H5，
            # 这里立刻物理 BACK 退出（F5 词表已能识别帮助中心 H5）。
            self._dismiss_badcase()
        # F6（2026-09-10 11:58 实测加固）：BACK 兜底必须有上限。当时判定失灵导致
        # 每次循环都判"还在广告页"，12 轮里按了 10 次 BACK，从广告页一路退到手机
        # 桌面（`ad_close_retries=12`，越退越远、不可逆）。现限制连续 BACK 次数
        # （workflow.ad_close_max_backs，默认 3：广告页 1 次即回任务中心，留 2 次
        # 容错，仍在 QQ 内部），超限直接放弃本次关闭并返回 False，由 run_all 的
        # 失败计数 + _safe_back_to_robot_list 复位兜底。
        ad_close_backs = 0
        max_backs = int(self.wf.get("ad_close_max_backs", 3))
        for i in range(max_tries):
            # 1) 已回任务中心？（广告自动结束）—— 必须先于 uiautomator：
            #    防 dump 残留/穿透在任务中心页读到「关闭」类节点而误点
            #    （该坐标带与顶部 banner 重叠，见坑 13/14）。
            if self._back_at_taskcenter(timeout=ad_close_dump_timeout):
                logger.info("广告已自动结束")
                return True
            # 2) uiautomator 定位关闭按钮（页面 idle 时 1-3s 最快最准；
            #    视频期 dump 有界失败转第 3 步）
            n = self._find_close_node(timeout=ad_close_dump_timeout)
            if n:
                logger.info("uiautomator 定位到关闭按钮 @ %s (第%d次)",
                            n.center, i + 1)
                self._tap_node(n, pause=1.5)
                time.sleep(ad_close_settle)
                if self._back_at_taskcenter(timeout=ad_close_dump_timeout):
                    logger.info("广告已关闭（uiautomator 路径）")
                    return True
                continue
            # 3) OCR 顶部条带正向找关闭按钮（视频期 dump 失效时唯一可靠读屏）
            pos = self._ad_close_pos()
            if pos:
                logger.info("OCR 定位到关闭按钮 @ %s (第%d次)", pos, i + 1)
                self._tap(*pos, pause=1.5, trusted=True)   # F11：OCR 正向定位，非盲点
                time.sleep(ad_close_settle)
                if self._back_at_taskcenter(timeout=ad_close_dump_timeout):
                    logger.info("广告已关闭（OCR 路径）")
                    return True
                continue
            # 4) 兜底：**不再有任何固定坐标盲点**（F8）。
            #    旧实现的 "点左上角 (160,152) 兜底" 被删除 —— 该坐标在任务中心页
            #    就是顶部 banner 的命中区，是三次误开 H5 事故的直接原因。改用物理
            #    BACK（封顶）：广告页 1 次 BACK 即可回任务中心，退穿风险由 max_backs
            #    兜住。若 BACK 也退不出，说明页面既不是广告也不是任务中心，本就不该
            #    在这一层乱点坐标。
            # 2026-09-27（实测 D2）：这里必须叠加 dump 复核 —— 只靠 OCR 4 词会在
            # 任务中心重绘期打空（真机实测 `back_at_tc=False` 而页面确为任务中心），
            # 于是接着盲退（实测连退 3 层直接落到 QQ 联系人页）。
            if self._back_at_taskcenter(timeout=ad_close_dump_timeout,
                                        allow_dump_fallback=True):
                logger.info("未定位到关闭按钮(第%d次)，但已确认回到任务中心"
                            "（广告已结束）", i + 1)
                return True
            ad_close_backs += 1
            if ad_close_backs > max_backs:
                self._log_page_snapshot("关闭放弃-BACK封顶")
                logger.error("未定位到关闭按钮且连续 BACK 已达上限 %d 次"
                             "（判定失灵时再退会退出 QQ），放弃本次关闭",
                             max_backs)
                return False
            logger.warning("未定位到关闭按钮(第%d次)，改为物理 BACK 尝试退出当前页"
                           "（第 %d/%d 次 BACK）", i + 1, ad_close_backs, max_backs)
            self.ui.back(pause=random.uniform(1.2, 1.8))
            time.sleep(self.t.get("page_wait", 2.0))
            # 2026-09-27 退穿保护（实测）：BACK 后若已退到「机器人列表 / QQ 主壳」，
            # 说明广告其实早已结束、这次 BACK 是多余的一层 —— 立即收手上报，绝不
            # 继续盲退。实测原实现连按 3 次 BACK 会一路退到 QQ 联系人页，把
            # 「1 次关闭失败」放大成整套导航重建（用户看到的"任务中心页一直刷新"）。
            try:
                retreated = (self._looks_like_robot_list()
                             or self._on_qq_main_shell())
            except Exception:  # noqa: BLE001
                # 读屏异常（含 Mock ui / dump 全失）→ 判据不可信，维持原语义继续。
                retreated = False
            if retreated:
                logger.warning("物理 BACK 后已退到机器人列表/QQ 主壳（退穿），"
                               "停止继续 BACK，交上层复位")
                self._log_page_snapshot("关闭放弃-退穿保护")
                return False
            if self._back_at_taskcenter(timeout=ad_close_dump_timeout,
                                        allow_dump_fallback=True):
                logger.info("物理 BACK 后已回到任务中心（广告已结束）")
                return True
        # 最后一搏：整体确认一次（叠加 dump 复核，防 OCR 重绘期假阴性误判"没关掉"）
        ok = self._back_at_taskcenter(timeout=ad_close_dump_timeout,
                                      allow_dump_fallback=True)
        if not ok:
            self._log_page_snapshot("关闭放弃-最终确认失败")
        return ok

    # ----------------------------------------------------------
    # 编排
    # ----------------------------------------------------------
    def run_robot(self, name: str, do_signin: bool, do_feedback: bool,
                  first_ad: bool = False) -> bool:
        """处理单台机器人：进任务中心 → 签到 → 反馈 → （可选）看 1 次广告 → 退出。

        first_ad（2026-09-10 用户优化）：主流程第一轮在签到/反馈的**同一会话**里
        顺手看 1 次广告，剩余次数交阶段二轮转按配额补足 —— 每台省一次完整任务
        中心进出（实测 70-95s）。失败只记日志不重试（轮转阶段会补看），不影响
        run_robot 的成功返回值。

        返回值（2026-09-16 编码化，rotate 阶段记账用）：0=进台失败（falsy，
        兼容旧 False）；1=处理完成但首轮广告未看或失败；2=处理完成且首轮
        广告成功。真值语义与旧 bool 完全兼容（既有 assertTrue 不受影响）。
        """
        logger.info("==== 处理机器人: %s ====", name)
        # F4/F8：进入失败不再直接放弃 —— 失败后 _safe_back_to_robot_list 复位
        # （清错页残留，避免污染后续机器人）再重试；共 3 次（对齐广告轮转
        # max_fail=3，2026-09-09 f8 实测 2 次不够——藤非/席恩间歇点错）。
        entered = False
        for attempt in (1, 2, 3):
            if self._enter_taskcenter(name):
                entered = True
                break
            logger.warning("进入任务中心失败 %s（第 %d/3 次），复位后重试",
                           name, attempt)
            self._safe_back_to_robot_list()
        if not entered:
            logger.error("进入任务中心失败 %s（重试 3 次后仍失败），跳过", name)
            self._stat(name)["entered"] = False
            return 0
        # A3（2026-09-10）：进入任务中心后先清理可能残留/即将弹出的 Badcase
        # 问卷 —— 00:34 全量验证实锤：_feedback 操作中途 Badcase H5 会弹出
        # （腾讯问卷完整正文），若不清掉，_signin/_feedback 的 dump 穿透误判
        # 仍在任务中心，把看广告入口当任务行点、或问卷残留阻断后续机器人。
        # 一次清理覆盖签到+反馈两动作；无问卷时仅 ~2.3s OCR 开销（实测基准）。
        self._dismiss_badcase()
        # 电量统计（2026-09-29）：本账号第一台机器人进台后读起始值（幂等，
        # 仅首次生效；读在签到动作前 —— 对应「从第一次进入机器人签到开始」）。
        self._capture_energy_start()
        if do_signin:
            ok = self._signin()
            if not ok:
                logger.warning("签到失败，重试 1 次")
                ok = self._signin()
                if not ok:
                    logger.error("签到重试后仍失败，跳过")
            self._stat(name)["signin"] = (
                "失败" if not ok else
                ("已签" if getattr(self, "_signin_already", False) else "成功"))
        if do_feedback:
            ok = self._feedback()
            if not ok:
                logger.warning("问题反馈失败，重试 1 次")
                ok = self._feedback()
                if not ok:
                    logger.error("问题反馈重试后仍失败，跳过")
            self._stat(name)["feedback"] = (
                "失败" if not ok else
                ("已反馈" if getattr(self, "_feedback_already", False) else "成功"))
        # 首轮广告（2026-09-10）：签到/反馈完成后不退出，同会话先看 1 次。
        # 看前按 A3 同款巡检一次 —— 签到/反馈操作后 QQ 可能推送 Badcase 问卷
        # （坑 8：00:34 实测操作中途弹出），不清掉会让 _find_row 空转计失败。
        first_ad_ok = False
        if first_ad:
            self._dismiss_badcase()
            if self._watch_ad_once():
                first_ad_ok = True
                self._stat(name)["ad"] += 1
                logger.info("%s 首轮广告完成（剩余次数进入轮转阶段补看）", name)
            else:
                logger.warning("%s 首轮广告失败（进入轮转阶段补看）", name)
        self._exit_taskcenter()
        # 编码返回（见 docstring）：0=进台失败；1=完成无首轮/首轮失败；2=首轮成功
        return 2 if first_ad_ok else 1

    # ----------------------------------------------------------
    # 电量统计（2026-09-29 用户需求）：账号级「本流程获得电量」
    # ----------------------------------------------------------
    def _read_energy(self) -> Optional[int]:
        """读任务中心「当前电量」数值（用户截图实锤：H5 顶部卡片，数字在
        「当前电量」标签下方）。两级读取，读不到返回 None（不阻断主流程）：

        1. dump：找「当前电量」标签节点 → 标签下方 y∈[y1, y1+280] 条带内找
           纯数字节点（H5 WebView 节点可读，与 每日签到 行同源）；
        2. OCR：image_to_data 词元级扫描 —— 定位「当前电量」标签（整词，
           失配走跨词元拼接 _merged_match），标签下方条带内收集含数字词元、
           按阅读顺序拼接后提取 2-7 位数字（tesseract 会把数字切碎）。
        """
        if not self.wf.get("energy_stat", True):
            return None
        # 1) dump 路径
        try:
            label = self._find("当前电量")
            if label is not None:
                y_lo, y_hi = label.y1, label.y1 + 280
                digit_nodes = []
                for nd_ in self.ui.nodes():
                    t = (nd_.text or "").strip()
                    if not t or not (y_lo <= nd_.y1 <= y_hi):
                        continue
                    # 纯数字节点接受 1-7 位（新账号电量可能个位数）；
                    # 混排文本要求 2-7 位（防 "x8" 类噪声）
                    m = re.fullmatch(r"[0-9]{1,7}", t)
                    if m:
                        return int(t)          # 纯数字节点优先
                    m = re.search(r"([0-9]{2,7})", t)
                    if m:
                        digit_nodes.append((nd_.y1, nd_.x1, m.group(1)))
                if digit_nodes:
                    digit_nodes.sort()
                    return int(digit_nodes[0][2])
        except Exception as e:  # noqa: BLE001
            logger.warning("电量 dump 读取异常: %s", e)
        # 2) OCR 路径
        if not _HAS_OCR:
            return None
        img = self._ocr_shot()
        if img is None:
            return None
        try:
            data = pytesseract.image_to_data(
                img, lang=getattr(self, "_ocr_lang", "chi_sim+eng"),
                output_type=pytesseract.Output.DICT)
        except Exception as e:  # noqa: BLE001
            logger.warning("电量 OCR 失败: %s", e)
            return None
        toks: List[Tuple[str, int, int]] = []
        label_y = None
        for i in range(len(data["text"])):
            t = (data["text"][i] or "").strip()
            if not t:
                continue
            cx = data["left"][i] + data["width"][i] // 2
            cy = data["top"][i] + data["height"][i] // 2
            toks.append((t, cx, cy))
            if label_y is None and "当前电量" in t:
                label_y = cy
        if label_y is None:
            pos = self._merged_match(toks, ("当前电量",), None)
            label_y = pos[1] if pos else None
        if label_y is None:
            logger.info("[电量] 未找到「当前电量」标签（版式差异/覆盖层），跳过")
            return None
        digits = []
        for t, _x, y in toks:
            if label_y - 20 <= y <= label_y + 280 and re.search(r"[0-9]", t):
                digits.append((y, re.sub(r"[^0-9]", "", t)))
        digits.sort()
        joined = "".join(d for _y, d in digits)
        m = re.search(r"([0-9]{2,7})", joined)
        if m:
            return int(m.group(1))
        logger.info("[电量] 标签下方条带内未读到数字")
        return None

    def _capture_energy_start(self) -> None:
        """本账号第一台机器人首次进台后读起始电量（幂等：无论成败只尝试
        一次，后续每次进台/轮换调用零开销）。"""
        if not self.wf.get("energy_stat", True):
            return
        if getattr(self, "_energy_start_done", False):
            return
        self._energy_start_done = True
        self._energy_start = self._read_energy()
        if self._energy_start is not None:
            logger.info("[电量] 起始读数 %d", self._energy_start)
        else:
            logger.info("[电量] 起始读数失败（覆盖层/版式差异），本次差额缺失")

    def _capture_energy_end(self, robots: List[str]) -> None:
        """广告阶段全部结束后补一次进台读终值（最后一支广告看完时已退台，
        电量数字只在任务中心可见）。算出差额并记日志；起始未读到则不白跑。"""
        if not self.wf.get("energy_stat", True):
            return
        if getattr(self, "_energy_start", None) is None:
            logger.info("[电量] 起始读数缺失，跳过结束统计")
            return
        self._energy_end = getattr(self, "_energy_end", None)
        for name in robots:          # 任一台能进台即可读
            if self._enter_taskcenter(name):
                try:
                    self._dismiss_badcase()
                    self._energy_end = self._read_energy()
                finally:
                    self._exit_taskcenter()
                break
        s, e = self._energy_start, self._energy_end
        if e is not None:
            logger.info("[电量] 本流程获得：%d → %d（+%d）", s, e, e - s)
        else:
            logger.warning("[电量] 结束读数失败（起始 %s），本次差额缺失", s)

    def _stat(self, name: str) -> dict:
        """汇总记账（2026-09-22 用户需求）：取单台机器人统计条目，懒初始化。

        兼容单测 Flow.__new__(Flow) 绕过 __init__ 的构造方式（stats 缺失时
        现场补空容器）。字段：entered(进台)/signin/feedback(结果文本或 None)/
        ad(本次进程看广告支数)/note(备注，如弃权原因)。"""
        if getattr(self, "stats", None) is None:
            self.stats = {}
        return self.stats.setdefault(name, {"entered": True, "signin": None,
                                            "feedback": None, "ad": 0,
                                            "note": ""})

    def _log_summary(self, do_signin: bool = True,
                     do_feedback: bool = True,
                     title: Optional[str] = None) -> None:
        """流程结束汇总：每台机器人 签到/问题反馈 是否成功 + 本次看广告支数。

        title（2026-09-24 多账号）：非空时作为表头前缀 + 落盘文件名后缀。
        多账号模式逐账号出汇总（各账号机器人列表不同、可能有同名机器人），
        加后缀可避免汇总文件互相覆盖。

        数据全部来自运行期内存记账（self.stats），零额外读屏；输出到日志
        （run.log + 控制台）并落一份 logs/summary_YYYYMMDD_HHMMSS.txt 供事后
        翻看。写文件失败只告警不抛出（不影响流程收尾）。"""
        stats = getattr(self, "stats", None) or {}
        if not stats:
            logger.info("汇总%s：无可统计的处理记录",
                        ("·" + title) if title else "")
            return
        head = ("===== %s 运行汇总（%d 台）=====" % (title, len(stats))
                if title else "===== 本次运行汇总（%d 台）=====" % len(stats))
        lines = [head,
                 "机器人      签到      问题反馈    本次广告  备注"]
        n_signin_ok = n_feedback_ok = 0
        ad_total = 0
        for name, s in stats.items():
            if do_signin:
                v = s.get("signin")
                if v in ("成功", "已签"):
                    n_signin_ok += 1
            else:
                v = "跳过"
            if do_feedback:
                fv = s.get("feedback")
                if fv in ("成功", "已反馈"):
                    n_feedback_ok += 1
            else:
                fv = "跳过"
            ad = int(s.get("ad") or 0)
            ad_total += ad
            note = "" if s.get("entered", True) else "进台失败"
            note = note or (s.get("note") or "")
            lines.append("%-10s %-8s %-10s %-8d %s"
                         % (name, v or "—", fv or "—", ad, note))
        lines.append("合计：签到 %d/%d，问题反馈 %d/%d，广告 %d 支"
                     % (n_signin_ok, len(stats) if do_signin else 0,
                        n_feedback_ok, len(stats) if do_feedback else 0,
                        ad_total))
        # 电量统计（2026-09-29）：起始/结束读数都在时输出本流程获得电量
        es = getattr(self, "_energy_start", None)
        ee = getattr(self, "_energy_end", None)
        if getattr(self, "wf", {}).get("energy_stat", True) and es is not None:
            if ee is not None:
                lines.append("电量：%d → %d，本流程获得 +%d" % (es, ee, ee - es))
            else:
                lines.append("电量：起始 %d，结束读数失败（差额缺失）" % es)
        for ln in lines:
            logger.info("%s", ln)
        try:
            log_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "logs")
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(
                log_dir, "summary_%s%s.txt"
                % (time.strftime("%Y%m%d_%H%M%S"),
                   ("_" + re.sub(r"[^\w\u4e00-\u9fff]+", "", title))
                   if title else ""))
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            logger.info("汇总已写入 %s", path)
        except Exception as e:  # noqa: BLE001
            logger.warning("汇总文件写入失败（不影响主流程）：%s", e)

    def run_all(self, robots: List[str], do_signin: bool, do_feedback: bool,
                ad_times: Optional[int], rotate: bool = False,
                group: int = 3):
        target = ad_times if ad_times is not None else self.wf.get(
            "ad_times_per_robot", 10)
        cd = self.wf.get("ad_cooldown", 60)
        # 汇总记账（2026-09-22）：先为全部机器人建条目，覆盖 ad-only 模式
        # （不进 run_robot）、轮换阶段无待看等不逐台处理路径，保证汇总齐全。
        for name in robots:
            self._stat(name)

        # 签到/反馈阶段：仅当确实要做时进入任务中心。纯广告模式(--ad-only)下
        # run_robot 内部也会无条件先进一次任务中心再退出 —— 纯浪费（实测每台
        # 空进出 ~70-95s，4 台广告开跑前先白跑 5-6 分钟），故这里跳过。
        # 首轮广告（2026-09-10 用户优化）：target>0 时每台在签到/反馈的同一
        # 会话里先看 1 次广告，剩余次数由下方轮转阶段补足。
        # 首轮广告记账（2026-09-16 rotate 用）：run_robot 返回 2 = 签到会话内
        # 首轮广告已成功看掉 1 支。ad-only 直入时没有首轮，全部记 0。
        first_done = {name: 0 for name in robots}
        if do_signin or do_feedback:
            for name in robots:
                try:
                    r = self.run_robot(name, do_signin, do_feedback,
                                       first_ad=target > 0)
                    if r == 2:
                        first_done[name] = 1
                except Exception as e:  # noqa: BLE001
                    logger.error("处理 %s 出错: %s", name, e)
        else:
            logger.info("签到/反馈均关闭，跳过逐台进入（--ad-only 直入看广告）")

        if target <= 0:
            logger.info("看广告次数为 0，跳过看广告轮转")
            return

        if rotate:
            self._run_rotate_phase(robots, first_done, target, group)
            self._capture_energy_end(robots)   # 电量统计（2026-09-29）
            return

        # 顺序连看路径（F10 结构优化 2026-09-09，行为不变）：单次任务中心
        # 会话连看多次广告 —— 旧实现"每看 1 次广告进出一次任务中心"实测
        # 单次进出 ~68-71s。逻辑抽入 _watch_ads_session（rotate 模式 1 台
        # 回落复用同一路径），run_all 内只保留调度。
        for name in robots:
            d = self._watch_ads_session(name, target, cd, start_done=0)
            self._stat(name)["ad"] += d
        logger.info("所有机器人看广告完成")
        self._capture_energy_end(robots)       # 电量统计（2026-09-29）

    # ----------------------------------------------------------
    # 滑动窗口轮询看广告（2026-09-16 组制轮询 → 2026-09-26 演进，rotate 模式）
    # ----------------------------------------------------------
    def _quota_left(self, s: dict, target: int, quota_target: int) -> int:
        """rotate 记账：单台剩余可看支数 = min(本次目标进度, 屏幕日配额)。
        - 本次目标进度 = target - done（done 含签到首轮广告）；
        - base=首访屏幕基数校准（r[0]-done，None=未读到）：屏幕口径剩余 =
          quota_target - base - done（覆盖手动补看/多进程场景）；
        - 未校准时只用本次目标进度（与顺序模式同口径）。"""
        left = target - s["done"]
        if s["base"] is not None:
            left = min(left, quota_target - s["base"] - s["done"])
        return max(0, left)

    def _watch_ads_session(self, name: str, target: int, cd: float,
                           start_done: int = 0) -> int:
        """单机连看会话（F10 语义）：进一次任务中心（最多 3 次尝试），会话
        内按 ad_cooldown 间隔原地连看，直至 target 支 / 失败上限 / 达每日
        配额，最后退一次。

        start_done：进台前进程内已看支数。顺序模式恒 0（行为与旧内联版完全
        一致）；rotate 1 台回落时传首轮+轮换已看数 —— 算术收尾与 while 边界
        均按 done-start_done 修正（entry_ratio 是屏幕快照，已含 start_done）。
        返回本会话新看支数；进台失败返回 0。
        """
        max_fail = 3
        done, fail = start_done, 0
        # 进入任务中心（最多 3 次尝试，语义同 run_robot 签到/反馈段；
        # 旧轮转对进入失败仅 fail+1 后轮换，实为跨轮重访，等效）。
        entered = False
        for attempt in (1, 2, 3):
            if self._enter_taskcenter(name):
                entered = True
                break
            logger.warning("进入任务中心失败 %s（第 %d/3 次），复位后重试",
                           name, attempt)
            self._safe_back_to_robot_list()
            time.sleep(1.0)
        if not entered:
            logger.error("进入任务中心失败 %s（3 次仍失败），跳过其看广告",
                         name)
            return 0
        # 会话内首检：X/10 已达配额（跨进程残留/手动已看完）→ 直接退出，
        # 不再空耗（修掉旧实现会在已完成机器人上空转 ~110s/次×10 的隐患）。
        # 同一次读屏顺带拿到「进台基数」：后续配额收尾用算术预判，不必
        # 每次看完都读屏（见循环内注释）。
        # 首检前先清场（2026-09-10）：问卷 H5 盖屏时行读不到会保守判"未满"，
        # 导致满额机器人空转三连败（18:22 代柯实测 4m52s 全失败）。
        self._dismiss_badcase()
        quota_target = int(self.wf.get("ad_times_per_robot", 10))
        # 方案 A（2026-09-28）：优先复用 _enter_taskcenter 进台时的配额缓存，
        # 免二次滚动查找；缓存 None 时回退原地读屏（行为与旧版一致）。
        entry_ratio = getattr(self, "_tc_entry_ratio", None)
        if entry_ratio is None:
            entry_ratio = self._read_ad_ratio()
        else:
            logger.info("%s 复用进台配额缓存（免二次读屏）", name)
        if entry_ratio and entry_ratio[0] >= quota_target:
            logger.info("%s 看广告已达每日配额，本会话直接退出", name)
            self._exit_taskcenter()
            return done - start_done
        first_iter = True     # D 优化：首轮 dismiss 在循环顶；后续挪进 CD 窗口
        pre_row = None        # CD 窗口预取的行节点（一次性使用，用过即弃）
        while done < target and fail < max_fail:
            # L3：每轮看广告前先巡检 Badcase 问卷并清除 —— 上一轮广告
            # 关闭后 ~35-40s QQ 可能延迟弹出腾讯问卷 H5，若不清掉，
            # _watch_ad_once 的 dump 会穿透读到背景任务中心造成误判。
            # D 优化（2026-09-13）：首轮在此清场；后续各轮的 dismiss 挪进
            # CD 窗口内（见下方 CD 段），与配额复核/行预取一起吸收进等待期。
            if first_iter:
                self._dismiss_badcase()
                first_iter = False
            # 每支观看前重置可疑标记（方案 A）：_close_ad 未被调用到的
            # 路径（如已达配额跳过）不得继承上一支的旧标记
            self._ad_close_suspicious = False
            ok = self._watch_ad_once(row=pre_row)
            pre_row = None
            if not ok:
                fail += 1
                logger.warning("%s 看广告失败，累计失败 %d/%d",
                               name, fail, max_fail)
                time.sleep(1.0)
                # P0 修复（2026-09-27）：断开「一次关闭失败 → 3 连败弃权」级联。
                # 触发链：广告播完 OCR 在顶条 (0,0,1080,320) 漏读关闭按钮 →
                # _close_ad 走物理 BACK 兜底 → BACK 撞 ad_close_max_backs 上限后
                # 放弃关闭，此时页面常已被一路退到 QQ 联系人页（09-27 实测 6 次
                # `页面快照[关闭放弃-BACK封顶]: ... '联系人'...`）。原实现失败后
                # 原地重试，下一次 _watch_ad_once 只会在错误页面上必然再失败
                # （`未找到 看广告/获取随机 行`），把 1 次真实失败放大成 3 连败。
                # 修复：重试前先确认仍在任务中心，不在则复位并重新进台。
                # 成本：仅失败路径付出（成功路径零额外读屏）；末次失败不再校验。
                #
                # 2026-09-27 二次修复（用户反馈「连看时一直刷新任务中心页」）：
                # ① 判定改开 allow_dump_fallback —— OCR 4 词常被重绘/视频期读屏
                #    打空（实测真任务中心页 back_at_tc=False），叠加 dump 复核后
                #    不再把「仍在任务中心」误判成「已离开」。
                # ② 仍在任务中心时**不做任何导航**，原地沉降后重试即可 —— 那条
                #    `_safe_back_to_robot_list()+_enter_taskcenter()` 全量重进正是
                #    用户看到的「任务中心页反复刷新」；多数失败（dump 超时瞬态）
                #    根本不需要重建导航。
                if fail < max_fail:
                    probe_t = float(self.wf.get("ad_close_dump_timeout", 2.5))
                    if self._back_at_taskcenter(timeout=probe_t,
                                                allow_dump_fallback=True):
                        logger.info("%s 仍在任务中心（瞬时读屏失败）→ 原地沉降"
                                    "重试，不做导航", name)
                        time.sleep(float(self.wf.get("ad_close_settle", 2.0)))
                        continue
                    logger.info("%s 失败后已不在任务中心（页面被兜底 BACK 退出）"
                                "→ 复位并重新进台", name)
                    self._safe_back_to_robot_list()
                    if not self._enter_taskcenter(name):
                        logger.error("%s 重进任务中心失败，终止本会话", name)
                        break
                    # 重进后按会话起始语义重跑一次清场（问卷 H5 可能延迟弹出）
                    first_iter = True
                continue
            t_close = time.time()   # CD 起点：广告关闭时刻（2026-09-10 用户优化）
            done += 1
            fail = 0
            logger.info("%s 本会话已看 %d/%d 次广告", name, done, target)
            # 方案 A（2026-09-29 游迦 9/10 实锤）：末支关闭路径可疑（未点到
            # 任何关闭按钮、OCR 判已回任务中心）→ 收尾 break 前读屏复核配额，
            # 堵「tap 未触发 → 虚报计数」。正常关闭路径零额外读屏（不影响
            # 点击效率）。ad_end_verify: false 可整体回退旧行为。
            skip_arith = False
            if done >= target:
                if (self.wf.get("ad_end_verify", True)
                        and getattr(self, "_ad_close_suspicious", False)):
                    logger.warning("%s 末支关闭路径可疑（未点到关闭按钮）→ "
                                   "收尾前复核配额", name)
                    done = self._verify_session_quota(name, done)
                    if done >= target:
                        break
                    # 校准后仍差几支：屏幕真值已复核，算术收尾（基于过时的
                    # entry_ratio）本圈作废，直接落 CD 段继续补看
                    skip_arith = True
                else:
                    break
            # 算术收尾（进台基数 + 本会话已看 >= 每日配额 → 必满）：
            # 不读屏、不等 CD，直接退出。省掉旧实现"每次看完读屏复核"
            # 在刚关广告的重载动画期必然 dump 超时的 ~40s 空等（审计 C2，
            # 18:02 实测两次复核烧 43s 串行加在 CD 前）。
            # 注：done 含 start_done 而 entry_ratio 屏幕快照同样含之，
            # 会话内净看数 = done - start_done，避免重复计数提前收尾。
            if (not skip_arith and entry_ratio is not None
                    and entry_ratio[0] + done - start_done >= quota_target):
                if (self.wf.get("ad_end_verify", True)
                        and getattr(self, "_ad_close_suspicious", False)):
                    logger.warning("%s 算术收尾但末支关闭路径可疑 → 复核配额",
                                   name)
                    done = self._verify_session_quota(name, done)
                    if done >= target:
                        break
                    skip_arith = True
                else:
                    logger.info("%s 看广告已达每日配额（进台 %d/10 + 本会话 %d 次），"
                                "提前收尾", name, entry_ratio[0],
                                done - start_done)
                    break
            # 会话内 CD 从关闭时刻起算：配额复核挪进 CD 窗口末尾
            # （cd-10s 处，页面已稳定 dump 不再超时）——校验耗时与 CD
            # 重叠，不再串行累加（用户 2026-09-10 提出）。
            wake = t_close + cd
            lead = 10.0
            logger.info("%s 广告 CD %.0f 秒（从关闭起算，留在任务中心）",
                        name, cd)
            time.sleep(max(0.0, wake - lead - time.time()))
            # D 优化（2026-09-13）：dismiss/配额复核/行预取全部挪进 CD 窗口
            # —— 原顺序把 dismiss(OCR ~1.5s) 和 find_row 全量 dump(~5.5s)
            # 都排在 CD 之后纯串行（实测 708/708 次点开段 ≥5s，均值 7.2s）。
            # 顺序要求：dismiss 必须在预取之前（BACK 清 overlay 会使预取行失效）。
            self._dismiss_badcase()
            # 配额复核（手动/并发补看完成 → 收尾退出；读不到=保守继续）
            if self._ad_quota_done():
                break
            # 行预取：复用配额复核刚写入的 nodes 缓存（TTL 0.8s 内，~0 成本）；
            # 读不到（dump 超时等）= None → _watch_ad_once 现场重新查找兜底。
            pre_row = self._find_row(AD_ROW_LABELS[0],
                                     alt_labels=AD_ROW_LABELS[1:])
            time.sleep(max(0.0, wake - time.time()))
        self._exit_taskcenter()
        logger.info("%s 看广告结束（本会话 %d/%d，失败 %d）",
                    name, done, target, fail)
        return done - start_done

    def _run_rotate_phase(self, robots: List[str], first_done: dict,
                          target: int, group: int) -> None:
        """滑动窗口轮询看广告（2026-09-26 演进自 2026-09-16 组制轮询）。

        固定 group（默认 3）台窗口 + 补位队列：窗口内循环 R1→R2→R3→R1...
        每台每轮看 1 支，靠切换吸收 CD（u2 实测 ~48-49s/支 vs 同机连看
        ~89s/支）。某台配额满（进程内累计或首访屏幕基数校准）或连败
        max_fail(3) 次弃权即出窗，队列下一位立即补位进窗（排尾部），窗口
        保持满员直至机器人耗尽；队列空且窗口剩 1 台回落 _watch_ads_session
        连看。首轮广告（签到会话内）成败由 first_done 记账，不重复看。
        """
        max_fail = 3
        quota_target = int(self.wf.get("ad_times_per_robot", 10))
        cd = self.wf.get("ad_cooldown", 60)
        st = {n: {"done": int(first_done.get(n) or 0), "fail": 0,
                  "base": None, "reanchor": 0}
              for n in robots}
        pending = [n for n in robots
                   if self._quota_left(st[n], target, quota_target) > 0]
        if not pending:
            logger.info("轮换阶段：所有机器人配额已满，无待看广告")
            return
        if group <= 0:
            group = 3
        # 滑动窗口轮询（2026-09-26 用户需求）：固定 group 台窗口 + 补位队列。
        # 旧实现按 pending 静态分组，组内缩员后不补人 —— 例：3 台组
        # [剩8, 剩9, 剩9]，第一台看完出组后只剩 2 台轮换，CD 吸收效率下降
        # （每支广告的等待从 ~2×CD 退化为 ~1×CD 空等）。改为：某台出窗
        # （配额满/连败弃权）后**立即**从队列补下一位进窗（排窗口尾部，
        # 下圈轮到它，天然吸收上一台的 CD）；窗口保持满员轮换直至机器人
        # 耗尽。数学性质：窗口一旦 <group 队列必已耗尽（队列非空时出窗
        # 必补位，窗口不减），故「窗口剩 1 台」⇔ 队列空 → 回落连看。
        queue = list(pending)
        window = queue[:group]
        del queue[:group]
        logger.info("轮换阶段：%d 台待看，滑动窗口 %d 台，日配额 %d",
                    len(pending), group, quota_target)
        total = 0

        def _rotate_out(name: str, reason: str) -> None:
            """出窗 + 补位（队列下一位排窗口尾部）。"""
            window.remove(name)
            if queue:
                nxt = queue.pop(0)
                window.append(nxt)
                logger.info("%s %s出窗，%s 补位进窗（窗口 %d 台 %s，待补 %d）",
                            name, reason, nxt, len(window), window, len(queue))
            else:
                logger.info("%s %s出窗（无待补，窗口剩 %d 台）",
                            name, reason, len(window))

        while window:
            # 回落判定前置到每圈开始：窗口剩 1 台且无待补时，连看会话只需
            # 进台 1 次（轮换模式每支 1 次进台），剩余支数 >1 时省 k-1 次进台；
            # 单台初始窗口（pending 仅 1 台）同样直接走会话，不走窗口循环。
            if len(window) == 1 and not queue:
                name = window[0]
                s = st[name]
                if (self._quota_left(s, target, quota_target) > 0
                        and s["fail"] < max_fail):
                    logger.info("%s 窗口仅剩 1 台且无待补，回落同机连看"
                                "（CD 等待语义）", name)
                    d = self._watch_ads_session(name, target, cd,
                                                start_done=s["done"])
                    s["done"] += d
                    total += d
                break
            for name in list(window):
                s = st[name]
                # 圈内先处理上一圈遗留的出窗态（初始化即满的台已被 pending
                # 过滤，这里主要是连败弃权兜底），再做本轮观看。
                if (self._quota_left(s, target, quota_target) <= 0
                        or s["fail"] >= max_fail):
                    _rotate_out(name, "（判定满/弃权）")
                    continue
                total += self._rotate_watch_once(
                    name, st, target, quota_target, max_fail)
                # 看完 1 支立即判定出窗补位（用户语义：第一台看完即接入第四台）
                s = st[name]
                if (self._quota_left(s, target, quota_target) <= 0
                        or s["fail"] >= max_fail):
                    reason = ("（配额满）" if self._quota_left(
                        s, target, quota_target) <= 0 else "（连败弃权）")
                    _rotate_out(name, reason)
            if not window:
                break
        for n in robots:
            s = st[n]
            # 汇总记账（2026-09-22）：st["done"] 已含签到首轮广告，与
            # stats["ad"] 当前值同口径，直接绝对值覆盖防重复累计。
            self._stat(n)["ad"] = s["done"]
            if (s["fail"] >= max_fail
                    and self._quota_left(s, target, quota_target) > 0):
                left = self._quota_left(s, target, quota_target)
                logger.warning("%s 连败 %d 次弃权，剩余 %d 支未看",
                               n, max_fail, left)
                self._stat(n)["note"] = "连败%d次弃权，余%d支未看" % (
                    max_fail, left)
        logger.info("轮换阶段完成：本进程共看 %d 支广告", total)

    def _rotate_watch_once(self, name: str, st: dict, target: int,
                           quota_target: int, max_fail: int) -> int:
        """轮换组内单台看 1 支：进台（3 试）→ 清问卷 → 首访校准基数 →
        看 1 支 → 退台。返回本次新看支数（0/1）；失败计入 st[name]["fail"]。"""
        s = st[name]
        t_turn = time.time()          # P1（2026-09-29）：单支耗时埋点
        entered = False
        for attempt in (1, 2, 3):
            if self._enter_taskcenter(name):
                entered = True
                break
            logger.warning("轮换进台失败 %s（第 %d/3 次），复位后重试",
                           name, attempt)
            self._safe_back_to_robot_list()
            time.sleep(1.0)
        if not entered:
            logger.error("轮换：进台失败 %s（3 次），本组弃权", name)
            s["fail"] = max_fail
            return 0
        try:
            # 电量统计（2026-09-29）：--ad-only 无签到段，起始快照在首次
            # 轮换进台补读（幂等，_energy_start 已有值时零开销）。
            self._capture_energy_start()
            self._dismiss_badcase()
            # 首访基数校准：屏幕 X/10 已包含本轮进程看过的（含签到首轮广告）
            # r：本次进台读到的屏幕真值（_enter_taskcenter 每次都刷新到
            # _tc_entry_ratio，读不到为 None）——首访用它算 base，其后用它重锚。
            r = getattr(self, "_tc_entry_ratio", None)
            if s["base"] is None:
                # 方案 A（2026-09-28）：优先复用 _enter_taskcenter 进台时读到的
                # 配额缓存 —— 免去此处二次滚动查找「看广告」行（用户观察到的
                # "进台后一直下滑刷新"根因）。缓存 None（覆盖层/版式差异）则
                # 回退到原地读屏，行为与旧版完全一致。
                if r is None:
                    r = self._read_ad_ratio()
                else:
                    logger.info("%s 复用进台配额缓存（免二次读屏）", name)
                if r:
                    s["base"] = max(0, r[0] - s["done"])
                    logger.info("%s 进台基数校准：屏幕 %d/%d（进程内已看 %d）",
                                name, r[0], r[1], s["done"])
            elif r and r[1] == quota_target:
                # 坑 25（2026-09-30 用户实锤）：**每次进台都用屏幕真值重锚**。
                # 旧实现只在首访校准一次 base，其后每次进台读到的屏幕值仅打日志、
                # 不参与记账 → 轮换的 done 纯算术累加永不复核。只要某支「节目播放
                # 但未到账」（广告自动回任务中心 / tap 未真开广告 / 服务端未记账），
                # done 就永久虚高、该台少看 1 支（实测王 7 台各虚报 1 支，汇总虚报
                # 100 实为 ~93）。此处以屏幕为准修正 done：向下纠正未到账、向上
                # 吸收手动补看（人在跑流程时手动补的也会被正确认账）。
                # 分母异常（实测偶发读成 /1000，疑读到别的行）时不认账，防误读。
                code_today = s["base"] + s["done"]
                if r[0] != code_today:
                    new_done = max(0, r[0] - s["base"])
                    if new_done < s["done"]:
                        # 向下纠正 = 上一次广告「未到账」（屏幕没前进）。累计
                        # 到 max_fail 视为连败弃权 —— 否则屏幕长期不前进会让
                        # done 反复归零 → 轮换窗口死循环（无全局圈数上限）。
                        s["reanchor"] = s.get("reanchor", 0) + 1
                    else:
                        s["reanchor"] = 0     # 向上吸收（手动补看）→ 视为正常前进
                    logger.warning(
                        "%s 屏幕真值重锚（坑25）：屏幕 %d/%d vs 进程口径 %d/%d "
                        "→ done %d→%d（未到账累计 %d/%d）", name, r[0], r[1],
                        code_today, quota_target, s["done"], new_done,
                        s.get("reanchor", 0), max_fail)
                    s["done"] = new_done
                    if s.get("reanchor", 0) >= max_fail:
                        logger.error(
                            "%s 连续 %d 次广告未到账（屏幕不前进）→ 弃权出窗",
                            name, s["reanchor"])
                        s["fail"] = max_fail
                        return 0
            elif r:
                logger.warning("%s 屏幕读数分母异常（%d/%d，期望 /%d）→ 本次不重锚",
                               name, r[0], r[1], quota_target)
            if self._quota_left(s, target, quota_target) <= 0:
                logger.info("%s 已达每日配额，移出轮换", name)
                return 0
            if self._watch_ad_once():
                s["done"] += 1
                s["fail"] = 0
                base = s["base"] or 0
                logger.info("%s 轮换看广告 +1（今日 %d/%d）",
                            name, base + s["done"], quota_target)
                return 1
            s["fail"] += 1
            logger.warning("%s 轮换看广告失败，累计失败 %d/%d",
                           name, s["fail"], max_fail)
            return 0
        finally:
            self._exit_taskcenter()
            logger.info("[计时] 轮换单支合计 %.1fs（%s）",
                        time.time() - t_turn, name)

    # ----------------------------------------------------------
    # 多账号切换（2026-09-24 用户需求：跑完一个账号自动切下一个）
    # ----------------------------------------------------------
    def _wait_for_text(self, text: str, timeout: float = 6.0,
                       interval: float = 0.4) -> Optional[Node]:
        """轮询等待文本出现（以秒为单位超时，比 ui.wait_for 的 retries 语义直观）。

        每次落空后 `ui.refresh()` 强制失效 nodes() 的 0.8s TTL 缓存 —— 否则连续
        两次查询会读到**同一份坏帧**（2026-09-24 实测：进程刚起时首帧异常，
        `_sidebar_nick` 与 `_switch_account` 两次 `_find` 命中同一缓存帧都判失败
        → 误触发整套 `_safe_back_to_robot_list()` 复位）。refresh 仅清缓存、零 adb 开销。
        """
        t0 = time.time()
        while True:
            n = self._find(text)
            if n is not None:
                return n
            if time.time() - t0 >= timeout:
                return None
            time.sleep(interval)
            self.ui.refresh()

    def _sidebar_nick(self) -> Optional[str]:
        """打开左上角「账户及设置」侧栏，读当前登录账号昵称，随后 BACK 关闭侧栏。

        昵称节点判据（实测 2026-09-24）：侧栏内与「切换账号」**同一行**、
        x 更靠左的文本 —— 唐灵 (324,451)-(432,514) vs 切换账号 (468,459)-(596,507)；
        下一行「等级：14」中心 y 差 181 > 窗口 44，天然排除。
        读不到返回 None（**不**按 BACK，避免在非预期页面误退）。
        """
        entry = self._find(ACCOUNT_ENTRY_TEXT)
        if entry is None:
            return None
        self._tap_node(entry, pause=1.2)
        sw = self._wait_for_text(SWITCH_ACCOUNT_TEXT, timeout=6.0)
        if sw is None:
            return None
        nick = None
        cy = (sw.y1 + sw.y2) // 2
        best = None
        for nd in self.ui.nodes():
            t = (nd.text or "").strip()
            if not t or t == SWITCH_ACCOUNT_TEXT:
                continue
            ncy = (nd.y1 + nd.y2) // 2
            if abs(ncy - cy) <= 44 and nd.x2 <= sw.x1:
                if best is None or nd.x2 > best.x2:
                    best = nd
        nick = best.text if best is not None else None
        self.ui.back()
        time.sleep(0.8)
        return nick

    def _switch_account(self, uin: str, nick: Optional[str] = None) -> bool:
        """按 UIN 切换 QQ 登录账号（2026-09-24 实测：免密秒切，单次 ~8.5s）。

        链路：联系人页「账户及设置」→ 侧栏「切换账号」→ 账号列表点 **UIN 行**
        → 等回联系人页 → settle → 校验昵称。**全程 dump 节点定位，零盲点坐标**
        （与 F11「不盲点」铁律一致）。

        ⚠️ 账号列表顺序会变（当前登录账号置顶，实测 UIN 节点 y 从 653 → 1016），
        故必须按 UIN 文本定位，**禁止记忆任何坐标**。
        返回 True 表示已切到目标账号且昵称校验通过。
        """
        # 0. 复位：切号必须从干净主壳出发（残留任务中心/子页会让入口找不到）。
        # 入口查找带重试 + 强制刷帧 —— 单次 `_find` 撞上过渡帧就白走一次
        # safe_back 复位（2026-09-24 实测：多花 ~15s 且多发几次 BACK）。
        if getattr(self, "_page_tc", False):
            self._exit_taskcenter()
        if self._wait_for_text(ACCOUNT_ENTRY_TEXT, timeout=2.0) is None:
            self._safe_back_to_robot_list()
        # R1（2026-09-28 实测复现）：入口「账户及设置」只在**联系人页顶部**可见
        # （y≈133）。若上一流程把列表停在**中段**（吸顶分类栏覆盖顶部栏）或停在
        # 机器人**资料卡**上，则入口找不到 → 旧实现只做一次 safe_back 复位，
        # 复位层数不对时依然找不到 → 单次就白跑一个切号机会（实测第 1 次
        # 报「侧栏未出现切换账号」）。此处补「确认在列表顶部」：仍是主壳却在
        # 列表中段时，先滑回顶部再找入口；滑不回去也不阻塞（交给下一层重试）。
        if self._on_qq_main_shell() and not self._contacts_tab_active():
            tab = self._find(CONTACTS_TAB, ymin=TAB_Y)
            if tab:
                logger.info("切号复位：先点底部「联系人」tab 回位")
                self._tap_node(tab, pause=1.0)
        if self._find(ACCOUNT_ENTRY_TEXT) is None and self._on_qq_main_shell():
            # 列表被滚到中段 → 分类栏吸顶顶掉顶部入口栏。滑回顶部再找一次。
            logger.info("切号复位：入口未现，尝试滑回列表顶部")
            for _ in range(4):
                self.ui.swipe_down(pause=random.uniform(0.5, 0.7))
            self.ui.refresh()

        # 1. 打开侧栏
        entry = self._find(ACCOUNT_ENTRY_TEXT)
        if entry is None:
            logger.error("切号失败：找不到「%s」入口（当前页非 QQ 主壳？）",
                         ACCOUNT_ENTRY_TEXT)
            self._log_page_snapshot("switch_no_entry")
            return False
        self._tap_node(entry, pause=1.2)

        # 2. 点「切换账号」
        sw = self._wait_for_text(SWITCH_ACCOUNT_TEXT, timeout=6.0)
        if sw is None:
            logger.error("切号失败：侧栏未出现「%s」", SWITCH_ACCOUNT_TEXT)
            self._log_page_snapshot("switch_no_btn")
            return False
        self._tap_node(sw, pause=1.2)

        # 3. 账号列表按 UIN 文本定位（顺序会变，禁止记坐标）
        target = self._wait_for_text(uin, timeout=6.0)
        if target is None:
            logger.error("切号失败：账号列表未出现 UIN %s", uin)
            self._log_page_snapshot("switch_no_uin")
            return False
        logger.info("切号：命中账号 UIN %s @ (%d,%d)-(%d,%d)", uin,
                    target.x1, target.y1, target.x2, target.y2)
        self._tap_node(target, pause=1.0)

        # 4. 等切号完成（回到联系人页）——实测 3s 内出结果，给 25s 余量
        if self._wait_for_text(CONTACT_PAGE_HINT, timeout=25.0) is None:
            # R2（2026-09-28 实测复现）：切号本身**已完成**，但 QQ 会停在
            # 「上一个停留页」而不是联系人页 —— 实测落点两种：① 个人资料页
            # （'唐灵'@(378,482)+'切换账号'+相册/收藏/钱包/设置）；② 消息页
            # （仅底部 tab 可见）。旧实现只认「新朋友」→ 判失败，白重试到
            # 3/3 后整账号跳过（本次唐灵/觅夏受害）。此处补主壳兜底：登录已
            # 完成（在主壳内 / 资料页）时主动导航回联系人页，成功即认成功。
            # 成本：仅失败路径付出；成功路径零额外读屏。
            if self._recover_to_contacts_after_switch():
                logger.info("切号落点非联系人页（已在主壳/资料页）→ 兜底导航回位成功")
            else:
                logger.error("切号失败：点击 UIN %s 后未回到联系人页", uin)
                self._log_page_snapshot("switch_timeout")
                return False
        # 切号后 QQ 重置到联系人页默认分类：寻路快路径标志与任务中心状态位全部失效
        self._at_robot_list = False
        self._page_tc = False
        settle = float(self.acc.get("settle", 2.0) or 0)
        if settle:
            time.sleep(settle)

        # 5. 校验（昵称比对）
        cur = self._sidebar_nick()
        if nick and cur != nick:
            logger.error("切号校验失败：期望昵称 %r，实际 %r", nick, cur)
            return False
        logger.info("切号成功：%s (%s)", cur or nick, uin)
        return True

    def _recover_to_contacts_after_switch(self) -> bool:
        """切号后的落点兜底（2026-09-28 真机实测新增）。

        QQ 切号成功后并不保证停在联系人页 —— 实测两种落点：
          ① 个人资料页：全屏页（'切换账号'+相册/收藏/钱包/设置），底部 tab 栏
             **不可见**（`_on_qq_main_shell` 判 False），BACK×1~3 可退出；
          ② 消息页：QQ 主壳内（底部 tab 可见），但非联系人页。
        旧实现只等 `CONTACT_PAGE_HINT`「新朋友」→ 两种落点都判失败（本次
        唐灵/觅夏两个账号因此 3/3 全败被跳过，而账号其实已切过去）。

        本方法只在上述判据超时后调用（失败路径），做一次轻量导航复位：
          资料页 → `_safe_back_to_robot_list()`（内部 BACK 逐层退 + 到主壳转
            完整导航）；消息页 → 点「联系人」tab。
        返回 True 表示已停在联系人页（`_looks_like_robot_list` 或「新朋友」出现）。
        幂等：已在联系人页则立即返回 True，不做任何点击。
        """
        if self._wait_for_text(CONTACT_PAGE_HINT, timeout=0.5) is not None:
            return True
        # ① 主壳内但不在联系人页（消息/动态 tab）→ 点「联系人」tab
        if self._on_qq_main_shell():
            if not self._contacts_tab_active():
                tab = self._find(CONTACTS_TAB, ymin=TAB_Y)
                if tab:
                    logger.info("切号落点兜底：点底部「联系人」tab 回位")
                    self._tap_node(tab, pause=1.0)
                    self._wait_until(self._contacts_tab_active,
                                     self._nav_target_wait(), desc="联系人tab选中")
            if self._wait_for_text(CONTACT_PAGE_HINT, timeout=3.0) is not None:
                return True
        # ② 资料页等全屏子页（不在主壳）→ 复用安全返回（内部 BACK 逐层退，
        #    到主壳后转完整导航真正进列表，再验一次「新朋友」）
        logger.info("切号落点兜底：尝试安全返回联系人页（资料页/子页）")
        self._safe_back_to_robot_list()
        if self._wait_for_text(CONTACT_PAGE_HINT, timeout=3.0) is not None:
            return True
        return self._looks_like_robot_list()

    def _ensure_account(self, uin: str, nick: Optional[str] = None) -> bool:
        """确保当前登录账号为 uin：已是该账号则直接返回 True，否则切号（含重试）。

        实测切号 ~8.5s/次，故首个账号先读一次昵称，命中即省掉整套切换动作。
        """
        cur = self._sidebar_nick()
        if cur is not None and nick and cur == nick:
            logger.info("当前已是账号 %s，跳过切换", nick)
            return True
        max_retry = int(self.acc.get("max_retry", 2) or 0)
        for attempt in range(1, max_retry + 2):
            if self._switch_account(uin, nick):
                return True
            logger.warning("切号到 %s 失败（第 %d/%d 次）", nick,
                           attempt, max_retry + 1)
            time.sleep(1.0)
        return False

    def _collect_ready(self) -> List[str]:
        """切号后收集机器人列表，直到「连续 N 次结果一致」才采纳（数据加载就绪）。

        ⚠️ 实测（2026-09-24，两次独立复现）：切号后**首次收集必漏列表顶部若干行**
        —— 唐灵 7 vs 12（漏 代柯/尔尔/古禹/李宥恩/黎小姐）、觅夏 10 vs 13
        （漏 代柯/尔尔/古禹），隔一次重收即补齐并稳定。根因：QQ 切号后联系人
        数据在后台重建，重建期 `_collect_robot_names` 的 Phase1「连续 2 屏内容
        不变 ⇒ 已到顶」被误判 → 顶部行永远收不到。故必须等结果稳定后才采纳。
        """
        stable = max(1, int(self.acc.get("ready_stable", 2) or 1))
        _rt = self.acc.get("ready_timeout", 120.0)
        timeout = 120.0 if _rt is None else float(_rt)   # 不用 `or`：0 是合法值
        if stable <= 1:
            return self._collect_robot_names()
        t0 = time.time()
        prev: Optional[List[str]] = None
        while True:
            names = self._collect_robot_names()
            if prev is not None and names == prev:
                logger.info("账号数据已就绪（连续 2 次结果一致，%d 个）", len(names))
                return names
            if time.time() - t0 >= timeout:
                logger.warning("账号数据就绪判定超时（>%.0fs），采用末次结果 %d 个",
                               timeout, len(names))
                return names
            logger.info("账号数据仍在加载（本次 %d 个），等待后重收", len(names))
            prev = names
            time.sleep(1.0)

    def run_all_accounts(self, accounts: List[dict], do_signin: bool,
                         do_feedback: bool, ad_times: Optional[int],
                         rotate: bool = False, group: int = 3,
                         whitelist: Optional[List[str]] = None) -> List[tuple]:
        """多账号主流程编排（2026-09-24）：逐账号 切号 → 收集 → run_all。

        accounts：config `accounts.list`，列表顺序即执行顺序，每项
          {"nick": 昵称, "uin": QQ 号, "enabled": bool}。
        whitelist：全局机器人白名单（沿用 workflow.robot_whitelist）——
          实测各账号机器人列表不同（唐灵 12 / 王 11 / 觅夏 13），名单里该账号
          没有的名字自动跳过，无名可跑则跳过该账号。
        rotate/group：原样透传给 run_all（滑动窗口轮询模式）。
        返回 [(nick, 该账号是否跑通, 该账号处理的机器人列表), ...]。
        """
        results: List[tuple] = []
        total = len(accounts)
        for idx, acc in enumerate(accounts, 1):
            nick = acc.get("nick") or ""
            uin = str(acc.get("uin") or "")
            if not acc.get("enabled", True):
                logger.info("[账号 %d/%d] %s 已禁用，跳过", idx, total, nick)
                continue
            if not uin:
                logger.error("[账号 %d/%d] %s 缺少 uin，跳过", idx, total, nick)
                continue
            logger.info("========== [账号 %d/%d] %s (%s) ==========",
                        idx, total, nick, uin)

            # 1) 切号（首个账号若已是该号则自动跳过切换）
            if not self._ensure_account(uin, nick):
                logger.error("[账号 %s] 切号失败，跳过该账号", nick)
                results.append((nick, False, []))
                continue

            # 2) 收集该账号机器人（等数据加载就绪，防切号后首次收集漏号）
            robots = self._collect_ready()

            # 3) 白名单过滤（全局一份，该账号没有的名字自动跳过）
            if whitelist:
                skipped = [r for r in robots if r not in whitelist]
                if skipped:
                    logger.info("[账号 %s] 跳过白名单外机器人 %d 个: %s",
                                nick, len(skipped), skipped)
                robots = [r for r in robots if r in whitelist]
            if not robots:
                logger.warning("[账号 %s] 白名单过滤后无可处理机器人，跳过", nick)
                results.append((nick, True, []))
                continue
            logger.info("[账号 %s] 处理 %d 个机器人: %s", nick, len(robots), robots)

            # 4) 复用既有主流程；每账号独立记账（防跨账号同名机器人串账），
            #    跑完立即出该账号汇总再清空。
            self.stats = {}
            self._energy_start = None      # 电量统计按账号独立（2026-09-29）
            self._energy_end = None
            self._energy_start_done = False
            try:
                self.run_all(robots, do_signin, do_feedback, ad_times,
                             rotate=rotate, group=group)
                results.append((nick, True, robots))
            except Exception as e:  # noqa: BLE001
                logger.error("[账号 %s] 主流程异常: %s", nick, e)
                results.append((nick, False, robots))
            finally:
                self._log_summary(do_signin, do_feedback,
                                  title="账号 %s (%s)" % (nick, uin))
                self.stats = {}

        logger.info("多账号主流程结束：%s",
                    ", ".join("%s=%s" % (n, "OK" if ok else "FAIL")
                              for n, ok, _ in results))
        return results
