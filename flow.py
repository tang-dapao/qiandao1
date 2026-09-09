"""QQ 机器人任务中心 自动签到/问题反馈/看广告 完整流程（adb + uiautomator 优先）。

坐标体系（竖屏 1080x1920，SurfaceOrientation=0，实测确认）：
- 截图/OCR/input tap/uiautomator 共用同一空间 1080x1920。
- uiautomator 在滚动后的任务中心 WebView 能可靠读出中文文本与真实坐标。
- 因此优先用 uiautomator find() 精确匹配文本 -> 取中心 -> tap；OCR 作为兜底。

任务中心行按钮固定 X≈879（各行右侧对齐，实测 1080 宽）。
"""
import io
import logging
import random
import subprocess
import time
from typing import List, Optional, Tuple

from adb_ui import AdbUI, Node

try:
    from PIL import Image
    import pytesseract
    _HAS_OCR = True
except Exception:  # noqa: BLE001
    _HAS_OCR = False

logger = logging.getLogger("flow")

# 任务中心各行「去完成/去反馈/获取随机/看广告」按钮固定 X（1080x1920，右侧对齐）
TASK_BTN_X = 879
# 签到浮层「每日免费领」（1080x1920，实测）
MODAL_SIGNIN = (301, 1800)     # 左下角【签到】按钮
MODAL_CLOSE = (996, 1143)      # 右上角 ✕
MODAL_AD_BONUS = (781, 1800)   # 看广告+
# 签到成功浮层「恭喜获得」
SUCCESS_KNOW = (540, 1189)     # 【我知道了】按钮
SUCCESS_CLOSE = (540, 1498)    # 右上角 ✕
# 左上角返回箭头三层路径（1080x1920，实测确认）
EXIT_TC_ARROW = (90, 138)         # 任务中心顶部返回箭头 -> 聊天页
EXIT_CHAT_ARROW = (59, 139)       # 聊天页左上角返回箭头（名字左侧）-> 机器人首页
EXIT_PROFILE_ARROW = (73, 133)    # profile 左上角返回箭头 -> 机器人列表
# 反馈问卷页左上角返回（优先文本「返回」定位，兜底坐标）1080x1920 实测 @(81,139)
FEEDBACK_BACK = (81, 139)
# 广告页左上角「关闭广告」按钮（1080x1920 实测 OCR @(120,152)/(202,153)，药丸中心 ~(160,152)）
AD_CLOSE = (160, 152)


class Flow:
    def __init__(self, config: dict, ui: AdbUI, ocr=None):
        self.cfg = config
        self.ui = ui
        self.ocr = ocr          # 可空，仅 OCR 兜底用
        self.t = config["timing"]
        self.wf = config["workflow"]
        if _HAS_OCR:
            _cmd = config.get("ocr", {}).get("tesseract_cmd")
            if _cmd:
                pytesseract.pytesseract.tesseract_cmd = _cmd
            self._ocr_lang = config.get("ocr", {}).get("lang", "chi_sim+eng")
        # 寻路快路径状态：_exit_taskcenter 的三层返回终点必然是机器人列表。
        # True 时 _nav_robot_list 用单次快照校验通过即跳过整个 tab 导航。
        # 初始 False（首跑/手动起点走完整导航，最安全）。
        self._at_robot_list = False

    # ----------------------------------------------------------
    # 基础：uiautomator 定位（带滚动）
    # ----------------------------------------------------------
    def _find(self, text: str, ymin: int = 0, ymax: int = 99999
              ) -> Optional[Node]:
        return self.ui.find(text, ymin=ymin, ymax=ymax)

    def _find_scroll(self, text: str, max_scroll: int = 10,
                     down_first: bool = True) -> Optional[Node]:
        """在可滚动页面里找文本：上下滚动范围内定位。返回 Node 或 None。

        寻路优化（2026-09-09）：滚动后若屏内文本集合无任何新内容（已滚到
        页面尽头），立即停止该方向，不再机械滚满 max_scroll —— 机器人列表/
        任务中心都是短列表，原实现每方向固定滚 10 次是无谓 dump+swipe。
        两方向各试一轮（外层 range(2)），兜住 overshoot 后需回滚找的情况。
        """
        for _ in range(2):
            cur = self.ui.nodes()
            n = next((x for x in cur if x.text == text), None)
            if n:
                return n
            seen = {x.text for x in cur}
            for _ in range(max_scroll):
                if down_first:
                    self.ui.swipe_up()
                else:
                    self.ui.swipe_down()
                cur = self.ui.nodes()
                n = next((x for x in cur if x.text == text), None)
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
        self.ui.tap_node(n, pause or random.uniform(self.t["click_min"],
                                                    self.t["click_max"]))

    def _tap(self, x: int, y: int, pause: Optional[float] = None):
        self.ui.tap(x, y, pause or random.uniform(self.t["click_min"],
                                                  self.t["click_max"]))

    def _tap_seq(self, pts: List[Tuple[int, int]], pause: float = 1.5):
        for x, y in pts:
            self._tap(x, y, pause)
            time.sleep(pause)

    # ----------------------------------------------------------
    # 导航
    # ----------------------------------------------------------
    def _looks_like_robot_list(self) -> bool:
        """单次快照校验当前是否「机器人列表」页（寻路快路径用）。

        判据（1080x1920 实测页面特征）：
        - 不应含任务中心/聊天页标志文本：任务中心、每日签到、发消息；
        - 应含底部主 tab「联系人」（y>=1850）。
        机器人列表页既无「发消息」（那是 profile/聊天页）也无任务中心文字。
        """
        cur = self.ui.nodes()
        texts = {x.text for x in cur}
        if "任务中心" in texts or "每日签到" in texts or "发消息" in texts:
            return False
        return any(x.text == "联系人" and x.y1 >= 1850 for x in cur)

    def _nav_robot_list(self):
        """导航到 联系人 -> 机器人 -> 我添加的机器人 列表。

        竖屏 1080x1920：底部「联系人」tab 在 y≈1880；「机器人」分类 tab 在
        联系人页中部 y≈960-1110。用区域约束消除歧义。

        寻路优化（2026-09-09，B1 快路径）：_exit_taskcenter 的三层返回箭头
        终点必然是机器人列表，故用 _at_robot_list 状态 + 单次快照校验，命中
        则整个 tab 导航直接跳过 —— 原实现每次都重新点 tab/找分组头，是
        单次「进入任务中心」70~85s 的组成部分。
        """
        logger.info("导航到机器人列表")
        # 状态快路径：刚从任务中心三层返回退出，理应停在机器人列表
        if self._at_robot_list and self._looks_like_robot_list():
            return
        self._at_robot_list = False
        # 入口状态恢复：若上一流程中断残留在了任务中心/聊天页，先退出
        # （避免假设"当前在 QQ 主界面"导致的导航失败 —— 实测踩坑）
        if self._find("任务中心") or self._find("每日签到"):
            logger.info("检测到残留的任务中心页面，先退出")
            self._exit_taskcenter()
            self._at_robot_list = True
            return
        for _ in range(3):
            if self._find("我添加的机器人") or self._find("我创建的机器人"):
                break
            # 点底部 联系人 tab（y>=1850）
            n = self._find("联系人", ymin=1850)
            if n:
                logger.debug("点底部 联系人 tab @ %s", n.center)
                self._tap_node(n)
                time.sleep(self.t.get("page_wait", 2.0))
            # 点 机器人 分类（联系人页中部 y 900~1250）
            n = self._find("机器人", ymin=900, ymax=1250)
            if n:
                logger.debug("点 机器人 分类 @ %s", n.center)
                self._tap_node(n)
                time.sleep(self.t.get("page_wait", 2.0))
        # 展开 我添加的机器人 分组（若折叠）
        img_group = self._find("我添加的机器人")
        if img_group:
            self._tap_node(img_group)
            time.sleep(self.t.get("page_wait", 2.0))
        self._at_robot_list = True

    def _find_robot(self, name: str) -> Optional[Node]:
        return self._find_scroll(name, max_scroll=10)

    def _collect_robot_names(self, max_scroll: int = 12) -> List[str]:
        """在「我添加的机器人」列表页滚动收集机器人昵称。

        昵称节点特征（1080x1920 实测）：左对齐 x1==183、宽 <=230、
        位于地区域 y1 350~1850（排除顶部 tab / 底部 nav / 账户及设置）。
        按首次出现顺序去重，跳过「内测中」。带编号的重复昵称（小麦/小麦1/
        小麦2）视为不同条目保留。
        """
        logger.info("自动抓取机器人列表")
        self._nav_robot_list()
        # 无条件反复下滑滚回列表最顶部（导航后滚动位置不固定，列表仅 2~3 屏）
        for _ in range(7):
            self.ui.swipe_down(pause=random.uniform(0.5, 0.7))
        time.sleep(0.5)
        names: List[str] = []
        seen: set = set()
        no_new = 0
        for _ in range(max_scroll):
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
                    if t not in seen:
                        seen.add(t)
                        names.append(t)
                        logger.info("  收集到机器人: %s", t)
            # 滚动看更多
            before = len(seen)
            self.ui.swipe_up(pause=random.uniform(0.8, 1.2))
            if len(seen) == before:
                no_new += 1
                if no_new >= 3:
                    break
            else:
                no_new = 0
        logger.info("共收集到 %d 个机器人: %s", len(names), names)
        return names

    def _enter_taskcenter(self, name: str) -> bool:
        """进入指定机器人的任务中心(WebView)。

        寻路优化（2026-09-09，B3）：tap 后的固定 sleep(2.5s) 改为条件等待
        （元素出现即继续），页面就绪快时省等待、就绪慢时自动多等 —— 不再
        有固定下限空等。wait_for 内部 find 失败会轮询，语义等价且更稳。
        """
        logger.info("进入机器人任务中心: %s", name)
        self._nav_robot_list()
        r = self._find_robot(name)
        if not r:
            logger.warning("列表里没找到机器人 %s", name)
            return False
        self._tap_node(r)
        # profile -> 发消息（条件等待，替代 tap 后固定 sleep 2.5s + wait_for）
        n = self.ui.wait_for("发消息", retries=8, interval=1.0)
        if not n:
            logger.error("profile 没找到 发消息")
            return False
        self._tap_node(n)
        # 聊天页 -> 个人（同样条件等待；"个人"在输入框上方 y>=600）
        n = self.ui.wait_for("个人", retries=8, interval=1.0, ymin=600)
        if not n:
            logger.error("聊天页没找到 个人")
            return False
        self._tap_node(n)
        time.sleep(self.t.get("taskcenter_wait", 3.5))
        # 已进入某机器人的任务中心：清快路径状态（下次 nav 需重新导航）
        self._at_robot_list = False
        return True

    def _exit_taskcenter(self):
        """从任务中心退出回机器人列表。

        用户要求改用「左上角返回箭头」退出（不用系统返回键）。
        实测确认的三层返回路径（1080x1920，SurfaceOrientation=0）：
          - 第1层：任务中心顶部 返回箭头 @(90,138)   -> 聊天页
          - 第2层：聊天页左上角返回箭头（名字左侧）@(59,139) -> 机器人首页(profile)
          - 第3层：profile 左上角返回箭头 @(73,133)  -> 机器人列表

        重要：必须先滑动到任务中心最上方，左上角返回箭头才会出现。
        以「开通解锁以下专属权益」/「收支详情」文字作为"已到顶"锚点。
        """
        logger.info("退出任务中心（左上角返回箭头）")
        # 1) 滑动到任务中心最上方，让返回箭头出现
        self._scroll_to_top_of_taskcenter()
        # 2) 第1层：任务中心顶部返回箭头
        self._tap(*EXIT_TC_ARROW, pause=random.uniform(1.2, 1.8))
        time.sleep(self.t.get("page_wait", 2.0))
        # 3) 第2层：聊天页返回箭头
        self._tap(*EXIT_CHAT_ARROW, pause=random.uniform(1.2, 1.8))
        time.sleep(self.t.get("page_wait", 2.0))
        # 4) 第3层：profile 返回箭头
        self._tap(*EXIT_PROFILE_ARROW, pause=random.uniform(1.2, 1.8))
        time.sleep(1.0)
        # 三层返回终点 = 机器人列表 → 置快路径状态（B1）
        self._at_robot_list = True

    def _scroll_to_top_of_taskcenter(self):
        """下滑直至任务中心顶部（返回箭头出现）。"""
        logger.info("滑动到任务中心顶部")
        for _ in range(8):
            if (self._find("开通解锁以下专属权益")
                    or self._find("收支详情")):
                logger.debug("任务中心已到顶")
                return
            self.ui.swipe_down(pause=random.uniform(0.8, 1.2))
        time.sleep(0.5)

    # ----------------------------------------------------------
    # 任务中心操作
    # ----------------------------------------------------------
    def _task_button(self, label: str) -> Optional[Node]:
        """找任务行标签节点(每日签到/问题反馈/看广告/获取随机)。"""
        return self._find_scroll(label, max_scroll=4)

    def _scroll_to_row(self, label: str) -> Optional[Node]:
        """滚动任务中心使指定行可见，返回该行右侧按钮（中心在标签中心下方约 24px，1080x1920）。"""
        n = self._find_scroll(label, max_scroll=6)
        if not n:
            return None
        cy = (n.y1 + n.y2) // 2 + 24
        return Node(label, TASK_BTN_X - 45, cy - 22,
                    TASK_BTN_X + 45, cy + 22)

    def _signin(self) -> bool:
        logger.info("== 每日签到 ==")
        # 点 每日签到 行右侧 去完成
        btn = self._scroll_to_row("每日签到")
        if not btn:
            logger.warning("未找到 每日签到 行")
            return False
        self._tap_node(btn)
        time.sleep(self.t.get("page_wait", 2.5))
        # 浮层「每日免费领」-> 点左下 签到 @(301,1800)
        self._tap(*MODAL_SIGNIN)
        time.sleep(2.0)
        # 成功浮层「我知道了」@(540,1189)
        if self._find("我知道了"):
            self._tap(*SUCCESS_KNOW)
        time.sleep(1.5)
        # 关闭「每日免费领」浮层 ✕
        self._tap(*MODAL_CLOSE)
        time.sleep(1.5)
        logger.info("签到流程完成")
        return True

    def _feedback(self) -> bool:
        logger.info("== 问题反馈 ==")
        btn = self._scroll_to_row("问题反馈")
        if not btn:
            logger.warning("未找到 问题反馈 行")
            return False
        self._tap_node(btn)
        time.sleep(self.t.get("page_wait", 2.5))
        # 反馈页左上角返回（优先文本定位「返回」，兜底坐标）1080x1920 实测 @(81,139)
        back = self._find("返回")
        if back:
            self._tap_node(back)
        else:
            self._tap(*FEEDBACK_BACK)
        time.sleep(self.t.get("page_wait", 2.0))
        return True

    def _watch_ad_once(self) -> bool:
        logger.info("看一次广告")
        btn = self._scroll_to_row("获取随机")
        if not btn:
            # 兜底：找 看广告 行
            btn = self._scroll_to_row("看广告")
        if not btn:
            logger.warning("未找到 获取随机")
            return False
        self._tap_node(btn)
        wait = random.uniform(self.wf["ad_wait_min"], self.wf["ad_wait_max"])
        logger.info("广告播放 %.1f 秒", wait)
        time.sleep(wait)
        # 关闭广告：关键！广告页 WebView 文字 uiautomator 读不到（会穿透读到
        # 背景任务中心，导致误判），必须用 OCR 检测「关闭广告」并读取其坐标，
        # 主动点击后再次用 OCR 确认该按钮消失。从实测看「关闭广告」在左上角。
        closed = self._close_ad()
        if not closed:
            logger.error("多次尝试后广告仍未关闭")
            return False
        logger.info("广告已关闭，回到任务中心")
        time.sleep(self.t.get("ad_close_wait", 2.0))
        return True

    # ----------------------------------------------------------
    # 广告关闭（OCR 定位「关闭广告」按钮）
    # ----------------------------------------------------------
    def _ocr_shot(self) -> Optional["Image.Image"]:
        """截图返回 PIL Image（1080x1920），失败返回 None。"""
        try:
            out = subprocess.run(
                [self.ui.adb, "-s", self.ui.device, "exec-out", "screencap", "-p"],
                capture_output=True)
            return Image.open(io.BytesIO(out.stdout)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            logger.warning("截图失败: %s", e)
            return None

    def _ocr_find(self, *texts: str, region: Tuple[int, int, int, int] | None = None,
                  ymax: Optional[int] = None, retries: int = 1,
                  interval: float = 1.0) -> Optional[Tuple[int, int]]:
        """OCR 全屏/指定区域找文字，返回中心坐标；找不到重试。"""
        if not _HAS_OCR:
            return None
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
            for i in range(n):
                t = (data["text"][i] or "").strip()
                if not t:
                    continue
                x = data["left"][i] + data["width"][i] // 2
                y = data["top"][i] + data["height"][i] // 2
                if region:
                    x += region[0]
                    y += region[1]
                if ymax is not None and y > ymax:
                    continue   # y 超出上限（正文误判），跳过继续找下一个词
                for k in texts:
                    if k in t:
                        return (x, y)
            time.sleep(interval)
        return None

    def _back_at_taskcenter(self) -> bool:
        """判断是否已回到任务中心（需防广告 WebView 穿透误判）。

        实测依据（2026-09-08 观察记录 logs/adobs_210451.txt）：
        - 广告覆盖时 uiautomator 可能穿透读到背景任务中心的「每日签到」，
          故必须同时用 OCR 确认全屏已无「关闭广告」按钮；
        - 视频播放期顶部区域 OCR 会假阴性（读不到「关闭广告」），故用全屏 OCR。
        """
        if not (self._find("每日签到") or self._find("任务中心")):
            return False
        # uiautomator 见到任务中心文本，再确认广告关闭按钮确实消失
        if self._ocr_find("关闭广告", "关闭", "跳过", ymax=400) is not None:
            return False   # 全屏还有关闭按钮：广告仍在（穿透误判）
        return True

    def _close_ad(self, max_tries: int = 6) -> bool:
        """主动关闭广告。

        实测三层策略（2026-09-08 实机观察确认）：
        1. uiautomator 优先：倒计时结束后广告页有「关闭广告」文本节点，
           dump 一次即可精确取 bounds（比 OCR 快 3~5 秒且更准）；
        2. OCR 兜底：广告进入视频播放后 uiautomator dump 会持续失败
           （页面刷新无法 idle），但 OCR 全屏能读到「关闭(120,152) 广告(202,153)」；
        3. 固定坐标兜底：类型 B（第三方如 4399）广告 OCR 读不到文字时，
           点左上 AD_CLOSE=(160,152)。
        广告带 ~12s 倒计时，倒计时结束前点击无效；调用前的 18~20s
        观看等待已覆盖该时长。
        """
        max_tries = self.wf.get("ad_close_retries", max_tries)
        for i in range(max_tries):
            # 1) uiautomator 定位「关闭广告」节点
            n = self._find("关闭广告")
            if not n:
                # 部分广告用「跳过」或「关闭」开头的按钮
                for cand in self.ui.nodes():
                    t = cand.text or ""
                    if "跳过" in t or t.startswith("关闭"):
                        n = cand
                        break
            if n:
                logger.info("uiautomator 定位到关闭按钮 @ %s (第%d次)",
                            n.center, i + 1)
                self._tap_node(n, pause=1.5)
                time.sleep(1.5)
                if self._back_at_taskcenter():
                    logger.info("广告已关闭（uiautomator 路径）")
                    return True
                continue
            # 2) 已回任务中心？（广告自动结束）
            if self._back_at_taskcenter():
                logger.info("广告已自动结束")
                return True
            # 3) OCR 全屏找关闭按钮（视频播放期 dump 失效时的兜底）
            pos = self._ocr_find("关闭广告", "关闭", "跳过", "取消", ymax=400)
            if pos:
                logger.info("OCR 定位到关闭按钮 @ %s (第%d次)", pos, i + 1)
                self.ui.tap(*pos, pause=1.5)
                time.sleep(1.5)
                if self._back_at_taskcenter():
                    logger.info("广告已关闭（OCR 路径）")
                    return True
                continue
            # 4) 兜底固定坐标（类型 B 广告，OCR 读不到文字）
            logger.info("未定位到关闭按钮(第%d次)，点左上角兜底 %s",
                        i + 1, AD_CLOSE)
            self._tap(*AD_CLOSE, pause=1.5)
            time.sleep(2.0)
        # 最后一搏：整体确认一次
        return self._back_at_taskcenter()

    # ----------------------------------------------------------
    # 编排
    # ----------------------------------------------------------
    def run_robot(self, name: str, do_signin: bool, do_feedback: bool) -> bool:
        logger.info("==== 处理机器人: %s ====", name)
        if not self._enter_taskcenter(name):
            return False
        if do_signin:
            if not self._signin():
                logger.warning("签到失败，重试 1 次")
                if not self._signin():
                    logger.error("签到重试后仍失败，跳过")
        if do_feedback:
            if not self._feedback():
                logger.warning("问题反馈失败，重试 1 次")
                if not self._feedback():
                    logger.error("问题反馈重试后仍失败，跳过")
        self._exit_taskcenter()
        return True

    def run_all(self, robots: List[str], do_signin: bool, do_feedback: bool,
                ad_times: Optional[int]):
        target = ad_times if ad_times is not None else self.wf.get(
            "ad_times_per_robot", 10)
        cd = self.wf.get("ad_cooldown", 60)

        for name in robots:
            try:
                self.run_robot(name, do_signin, do_feedback)
            except Exception as e:  # noqa: BLE001
                logger.error("处理 %s 出错: %s", name, e)

        if target <= 0:
            logger.info("看广告次数为 0，跳过看广告轮转")
            return

        # 看广告轮转（60s CD 多机器人消磨）
        # 注意：循环条件同时检查 counts 和 fail，防止所有未完成机器人都被
        # fail>=max_fail 踢出后，pending 永远为空导致 60s 空转死循环。
        counts = {r: 0 for r in robots}
        cd_until = {r: 0.0 for r in robots}
        fail = {r: 0 for r in robots}
        max_fail = 3
        while any(counts[r] < target and fail[r] < max_fail for r in robots):
            now = time.time()
            pending = [r for r in robots
                       if counts[r] < target and fail[r] < max_fail
                       and now >= cd_until[r]]
            cur = pending[0] if pending else None
            if cur is None:
                # pending 为空只可能是全部在 CD；无候选的情况已被 while 条件排除
                wait = min((cd_until[r] - now
                            for r in robots
                            if counts[r] < target and fail[r] < max_fail),
                           default=60)
                logger.info("全部在看广告 CD，等待 %.0f 秒", max(wait, 1))
                time.sleep(max(wait, 1))
                continue
            logger.info("==> 看广告: %s (%d/%d)", cur, counts[cur], target)
            if self._enter_taskcenter(cur):
                ok = self._watch_ad_once()
                self._exit_taskcenter()
                if ok:
                    counts[cur] += 1
                    cd_until[cur] = time.time() + cd
                    fail[cur] = 0
                else:
                    cd_until[cur] = time.time() + cd
                    fail[cur] += 1
            else:
                fail[cur] += 1
                logger.warning("进入任务中心失败 %s，累计失败 %d/%d",
                               cur, fail[cur], max_fail)
            time.sleep(1.0)
        logger.info("所有机器人看广告完成")
