"""基于 adb 的健壮 UI 操作层。

使用 uiautomator dump + 正则解析，返回控件的真实 bounds（已验证在
QQ 机器人任务中心等绝大多数页面可靠，包括滚动后的 WebView）。

相比 Appium 原生定位，此方式：
- 无会话/坐标方向混乱问题（直接读物理像素 900x1600）
- 能拿到 WebView 文字的真实坐标（滚动后）
- swipe 稳定（用 adb input swipe，Appium W3C actions 在 MuMu 不稳）

坐标约定：全部使用有效坐标空间 1080x1920 竖屏（SurfaceOrientation=0）。
uiautomator / input tap / swipe / 截图 / OCR 均在此空间工作。
"""
import logging
import re
import subprocess
import time
from typing import List, Optional, Tuple

logger = logging.getLogger("adbu")

ADB = "D:/Android/Sdk/platform-tools/adb.exe"
DEFAULT_DEV = "127.0.0.1:16384"

# ---- 子进程超时（2026-09-10 添加，看门狗）----
# 背景：本工具无人值守跑 10 台 × 广告轮转可达 2 小时以上，此前所有
# subprocess.run 均未传 timeout（仅 is_online 有 10s）—— 一旦 adb daemon
# 挂起或模拟器无响应，进程会无限阻塞、日志停在原地，次日才发现没跑完。
# 取值依据：
#   - CMD_TIMEOUT  普通命令（tap/swipe/back/cat）实测毫秒级返回，20s 必属异常
#   - DUMP_TIMEOUT uiautomator dump 在「UI 无法 idle」时（如视频广告播放期）
#                  会一直空等，实测每次 ~12s 才返回——这里主动设短，快速失败
#                  后交给上层 OCR 路径（正常静态页 dump 实测 <1.5s，4s 足够）
CMD_TIMEOUT = 20.0
DUMP_TIMEOUT = 4.0
# 滑动锚点 x（#1 优化 2026-09-10）：刻意避开中列 540 —— 任务中心页中列是「签到」按钮 /
# 「问题反馈」/ 顶部 banner 的命中区，滑动起止点落中列时 WebView 自绘页会误判为点按
# 这些元素而"飘"到对应页面；左列 140 是列表左侧留白/头像区，纯纵向滑动由 ScrollView
# 接管，不会误触按钮。要微调改这一行即可。
SWIPE_X = 140

_NODE_RE = re.compile(r"<node\b[^>]*>")
_ATTR_RE = re.compile(r'\b(text|content-desc|selected|checked|bounds)="([^"]*)"')


class AdbCommandError(RuntimeError):
    """adb 命令执行失败（设备离线 / adb 路径无效 / 命令出错）。"""


class Node:
    """一个带文本的控件节点。"""
    __slots__ = ("text", "x1", "y1", "x2", "y2", "selected")

    def __init__(self, text: str, x1: int, y1: int, x2: int, y2: int,
                 selected: bool = False):
        self.text = text
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.selected = selected

    @property
    def center(self) -> Tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def __repr__(self):
        return (f"Node({self.text!r} bounds=({self.x1},{self.y1})-"
                f"({self.x2},{self.y2}) selected={self.selected})")


def _parse_xml(xml: str, include_desc: bool) -> List[Node]:
    """解析 dump XML：返回全部 text 节点（文档序）+ include_desc 时的
    content-desc 节点（仅当 desc 文字未作为 text 出现；文档序追加）。

    每个 node 同时解析 selected 属性（QQ 底部 tab / 联系人页中部 分类行
    的文本节点带 selected="true"，供 flow 精确判断激活 tab / 分类 ——
    机器人列表识别强判据依赖它）。
    """
    texts: List[Node] = []
    descs: List[Node] = []
    seen_texts = set()
    for tag in _NODE_RE.findall(xml):
        attrs = {}
        for k, v in _ATTR_RE.findall(tag):
            attrs.setdefault(k, v)          # 属性乱序，只取首次出现
        b = attrs.get("bounds")
        if not b:
            continue
        m = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 <= x1 and y2 <= y1:
            continue
        sel = attrs.get("selected") == "true"
        t = (attrs.get("text") or "").strip()
        if t:
            texts.append(Node(t, x1, y1, x2, y2, selected=sel))
            seen_texts.add(t)
        if include_desc:
            cd = (attrs.get("content-desc") or "").strip()
            if cd and cd not in seen_texts:
                descs.append(Node(cd, x1, y1, x2, y2, selected=sel))
                seen_texts.add(cd)
    return texts + descs


class AdbUI:
    def __init__(self, device: str = DEFAULT_DEV, adb: str = ADB):
        self.device = device
        self.adb = adb
        # 界面动作回调（A2 优化 2026-09-12）：tap/tap_node/swipe/back 执行后触发，
        # 供上层（Flow）失效其截图/OCR 结果缓存 —— 动作后画面必变，缓存的
        # 旧帧不可再用。设为 None 即无回调（默认，兼容既有用法/单测）。
        self.on_action = None
        # nodes() 的 TTL 缓存：dump 一次约 1~3s，短时间重复解析同一页面时
        # 直接复用缓存可显著减少 dump 次数；界面动作(tap/swipe/back)后用
        # _invalidate_cache() 清空，保证下次读到的是新页面。
        self._nodes_ttl: float = 0.8
        self._nodes_cache: Optional[List[Node]] = None
        self._nodes_include_desc: Optional[bool] = None
        self._nodes_ts: float = 0.0
        # dump 连续失败计数（页面卡动画/浏览器加载时 uiautomator 无法 idle）。
        # 单次 dump() 完全失败 +1，成功即清零 → 该值只反映"此刻是否持续 dump 失败"，
        # 供 flow 导航循环判断页面是否卡死（>=3 时应中止并走恢复，避免空转）。
        self._dump_fail_streak: int = 0

    def _run(self, *args, timeout: float = CMD_TIMEOUT) -> str:
        """执行 adb 命令，返回 stdout 字符串。失败抛 AdbCommandError。

        2026-09-10 加超时看门狗：所有 tap / swipe / back / cat 都走这里，
        未设超时时 adb daemon 挂起会让整个无人值守流程永久卡住。超时统一
        抛 AdbCommandError —— 上层（如 dump）已有 except 分支，自动走
        dump_fail_streak 恢复逻辑，无需额外改动。
        """
        cmd = [self.adb, "-s", self.device] + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AdbCommandError(
                f"adb {' '.join(args)} 超时未返回 (> {timeout}s)，"
                f"疑似 adb/设备挂起")
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", errors="replace").strip()
            raise AdbCommandError(
                f"adb {' '.join(args)} 失败 (rc={p.returncode}): {err}")
        return p.stdout.decode("utf-8", errors="replace")

    def is_online(self) -> bool:
        """探测设备是否在线（adb get-state，重试 3 次）。

        背景：每次进程启动时 adb daemon 才刚拉起，首次 get-state 可能落在
        设备状态未稳定的瞬间误报 offline（实测 emulator-5554 首次 rc=1、
        其后均 device），故重试避免启动探活误杀。
        """
        for _ in range(3):
            try:
                p = subprocess.run([self.adb, "-s", self.device, "get-state"],
                                   capture_output=True, timeout=10)
                if (p.returncode == 0
                        and p.stdout.decode(errors="replace").strip() == "device"):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.0)
        return False

    # ----------------------------------------------------------
    def dump(self, timeout: Optional[float] = None) -> Optional[str]:
        """dump 当前 UI 层次，返回 XML 字符串；失败返回 None。

        注意1：MuMu 等模拟器上 uiautomator 正常 dump 完成后，进程退出时也常
        以 rc=139(段错误) 收尾，属已知无害现象 —— 判定成功与否要看输出文本
        「dumped to」而非 returncode（故此处不走 _run 的 rc 检查）。
        注意2：uiautomator 真失败时 rc 也可能是 0（错误只打在 stdout），
        需检查输出文本，否则会 cat 到上一次的旧 XML（假页面）。
        超时即失败（#2 优化 2026-09-10）：视频/动画期 UI 无法 idle，uiautomator
        会一直空等到自己超时（实测 ~12s）。此处用 timeout 主动掐断后**立即返回
        None、不重试** —— 页面处于播放态时重试只会再白等一个整超时（原 range(2)
        重试导致每次失败耗 4s×2≈8s，是日志里 123 次 dump 超时浪费的主因）；上层
        有 OCR 兜底路径会重新 poll。仅"dump 已执行但未 idle 成功"（rc=139 段错误
        等瞬态）会在循环里重试一次。timeout 默认 DUMP_TIMEOUT，关闭广告等场景可传
        更短值（如 2.5s）进一步压低单次等待。
        """
        if timeout is None:
            timeout = DUMP_TIMEOUT
        for _ in range(2):  # 失败重试一次（仅限非超时的瞬态失败）
            try:
                cmd = [self.adb, "-s", self.device, "shell",
                       "uiautomator", "dump", "/sdcard/ui.xml"]
                try:
                    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
                except subprocess.TimeoutExpired:
                    # 视频播放期 UI 无法 idle，uiautomator 会空等到自己超时
                    # （实测 ~12s）—— 这里主动掐断后**立即失败交上层**，不重试
                    # （重试只会再白等一个整超时，是 dump 超时浪费的主因）。
                    logger.warning("uiautomator dump 超时 (> %ss)，"
                                   "页面可能仍在动画/视频中", timeout)
                    self._dump_fail_streak += 1
                    return None
                out = p.stdout.decode("utf-8", errors="replace")
                if "dumped to" not in out:
                    logger.warning("uiautomator dump 未成功 (rc=%s): %s",
                                   p.returncode, out.strip() or "无输出")
                    time.sleep(1.0)
                    continue
                self._dump_fail_streak = 0          # 成功：清零失败计数
                return self._run("shell", "cat", "/sdcard/ui.xml")
            except AdbCommandError as e:
                logger.warning("uiautomator dump 失败: %s", e)
                time.sleep(1.0)
        self._dump_fail_streak += 1                  # 完全失败：累计
        return None

    @property
    def dump_fail_streak(self) -> int:
        """连续完全失败的 dump() 调用次数（成功即清零）。供上层判断页面卡死。"""
        return self._dump_fail_streak

    def nodes(self, include_desc: bool = True,
              timeout: Optional[float] = None) -> List[Node]:
        """解析当前屏幕所有带文本/描述节点（0.8s TTL 缓存）。

        TTL 内且无界面动作时重复调用直接返回缓存；dump 失败返回 [] 且
        **不写缓存**（页面可能正处于广告播放等无法 idle 的状态，下一秒
        内容可能就不同了，缓存空结果会掩盖真实页面）。
        timeout 透传给 dump —— 关闭广告等场景可传更短值压低单次等待（#2）。
        """
        now = time.time()
        if (self._nodes_cache is not None
                and self._nodes_include_desc == include_desc
                and now - self._nodes_ts < self._nodes_ttl):
            return self._nodes_cache
        xml = self.dump(timeout)
        if not xml:
            return []
        nodes = _parse_xml(xml, include_desc)
        self._nodes_cache = nodes
        self._nodes_include_desc = include_desc
        self._nodes_ts = now
        return nodes

    def find(self, text: str,
             ymin: int = 0, ymax: int = 99999,
             xmin: int = 0, xmax: int = 99999,
             timeout: Optional[float] = None) -> Optional[Node]:
        """按文本精确查找第一个节点（可限定区域）。timeout 透传给 dump，
        用于关闭广告等场景压低单次 dump 等待（#2）。"""
        for n in self.nodes(timeout=timeout):
            if n.text == text and ymin <= n.y1 <= ymax and xmin <= n.x1 <= xmax:
                return n
        return None

    def find_contains(self, text: str,
                      ymin: int = 0, ymax: int = 99999) -> Optional[Node]:
        """按包含匹配查找（处理 OCR 无法拆分的长文本 / 组合按钮）。"""
        for n in self.nodes():
            if text in n.text and ymin <= n.y1 <= ymax:
                return n
        return None

    def exists(self, text: str, ymin: int = 0, ymax: int = 99999) -> bool:
        return self.find(text, ymin, ymax) is not None

    # ----------------------------------------------------------
    def _invalidate_cache(self):
        """清空 nodes() 缓存：界面动作后调用，下次 nodes() 重新 dump。"""
        self._nodes_cache = None
        self._nodes_include_desc = None
        self._nodes_ts = 0.0

    def refresh(self):
        """强制下次 nodes() 重新 dump（失效 TTL 缓存）。供点击关键目标前
        获取「当前真实页面」快照，防缓存/滚动动画中间帧用旧坐标点错。"""
        self._invalidate_cache()

    def _notify_action(self):
        """动作后通知上层（失效截图缓存等）。回调异常不得影响主流程。"""
        if self.on_action is not None:
            try:
                self.on_action()
            except Exception as e:  # noqa: BLE001
                logger.debug("on_action 回调异常(忽略): %s", e)

    def tap(self, x: int, y: int, pause: float = 1.2):
        self._run("shell", "input", "tap", str(int(x)), str(int(y)))
        self._invalidate_cache()
        self._notify_action()
        time.sleep(pause)

    def tap_node(self, node: Node, pause: float = 1.2):
        x, y = node.center
        self.tap(x, y, pause)

    def swipe_up(self, steps: float = 0.4, pause: float = 1.0):
        """向上滑（看下方内容）。坐标空间 1080x1920 竖屏。"""
        self._run("shell", "input", "swipe",
                  "540", "1500", "540", "700", "400")
        self._invalidate_cache()
        self._notify_action()
        time.sleep(pause)

    def swipe_down(self, pause: float = 1.0):
        """向下滑（看上方内容）。坐标空间 1080x1920 竖屏。

        锚点 x 同 swipe_up 用 SWIPE_X（左安全列），避开中列按钮命中区，防滑动误触漂移。
        """
        self._run("shell", "input", "swipe",
                  str(SWIPE_X), "700", str(SWIPE_X), "1500", "400")
        self._invalidate_cache()
        self._notify_action()
        time.sleep(pause)

    def back(self, pause: float = 1.2):
        self._run("shell", "input", "keyevent", "4")
        self._invalidate_cache()
        self._notify_action()
        time.sleep(pause)

    # ----------------------------------------------------------
    def wait_for(self, text: str, retries: int = 6, interval: float = 1.0,
                 ymin: int = 0, ymax: int = 99999) -> Optional[Node]:
        """轮询等待某文本出现，返回节点或 None。"""
        for _ in range(retries):
            n = self.find(text, ymin, ymax)
            if n:
                return n
            time.sleep(interval)
        return None

    def wait_contains(self, text: str, retries: int = 6,
                      interval: float = 1.0) -> Optional[Node]:
        for _ in range(retries):
            n = self.find_contains(text)
            if n:
                return n
            time.sleep(interval)
        return None

    def wait_gone(self, text: str, retries: int = 6, interval: float = 1.0) -> bool:
        """等待某文本消失，返回 True 表示已消失。"""
        for _ in range(retries):
            if not self.exists(text):
                return True
            time.sleep(interval)
        return False
