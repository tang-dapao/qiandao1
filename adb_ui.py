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

_TEXT_RE = re.compile(
    r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
_DESC_RE = re.compile(
    r'content-desc="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')


class AdbCommandError(RuntimeError):
    """adb 命令执行失败（设备离线 / adb 路径无效 / 命令出错）。"""


class Node:
    """一个带文本的控件节点。"""
    __slots__ = ("text", "x1", "y1", "x2", "y2")

    def __init__(self, text: str, x1: int, y1: int, x2: int, y2: int):
        self.text = text
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2

    @property
    def center(self) -> Tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def __repr__(self):
        return f"Node({self.text!r} bounds=({self.x1},{self.y1})-({self.x2},{self.y2}))"


class AdbUI:
    def __init__(self, device: str = DEFAULT_DEV, adb: str = ADB):
        self.device = device
        self.adb = adb
        # nodes() 的 TTL 缓存：dump 一次约 1~3s，短时间重复解析同一页面时
        # 直接复用缓存可显著减少 dump 次数；界面动作(tap/swipe/back)后用
        # _invalidate_cache() 清空，保证下次读到的是新页面。
        self._nodes_ttl: float = 0.8
        self._nodes_cache: Optional[List[Node]] = None
        self._nodes_include_desc: Optional[bool] = None
        self._nodes_ts: float = 0.0

    def _run(self, *args) -> str:
        """执行 adb 命令，返回 stdout 字符串。失败抛 AdbCommandError。"""
        cmd = [self.adb, "-s", self.device] + list(args)
        p = subprocess.run(cmd, capture_output=True)
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
    def dump(self) -> Optional[str]:
        """dump 当前 UI 层次，返回 XML 字符串；失败返回 None。

        注意1：MuMu 等模拟器上 uiautomator 正常 dump 完成后，进程退出时也常
        以 rc=139(段错误) 收尾，属已知无害现象 —— 判定成功与否要看输出文本
        「dumped to」而非 returncode（故此处不走 _run 的 rc 检查）。
        注意2：uiautomator 真失败时 rc 也可能是 0（错误只打在 stdout），
        需检查输出文本，否则会 cat 到上一次的旧 XML（假页面）。
        """
        for _ in range(2):  # 失败重试一次
            try:
                cmd = [self.adb, "-s", self.device, "shell",
                       "uiautomator", "dump", "/sdcard/ui.xml"]
                p = subprocess.run(cmd, capture_output=True)
                out = p.stdout.decode("utf-8", errors="replace")
                if "dumped to" not in out:
                    logger.warning("uiautomator dump 未成功 (rc=%s): %s",
                                   p.returncode, out.strip() or "无输出")
                    time.sleep(1.0)
                    continue
                return self._run("shell", "cat", "/sdcard/ui.xml")
            except AdbCommandError as e:
                logger.warning("uiautomator dump 失败: %s", e)
                time.sleep(1.0)
        return None

    def nodes(self, include_desc: bool = True) -> List[Node]:
        """解析当前屏幕所有带文本/描述节点（0.8s TTL 缓存）。

        TTL 内且无界面动作时重复调用直接返回缓存；dump 失败返回 [] 且
        **不写缓存**（页面可能正处于广告播放等无法 idle 的状态，下一秒
        内容可能就不同了，缓存空结果会掩盖真实页面）。
        """
        now = time.time()
        if (self._nodes_cache is not None
                and self._nodes_include_desc == include_desc
                and now - self._nodes_ts < self._nodes_ttl):
            return self._nodes_cache
        xml = self.dump()
        if not xml:
            return []
        nodes = []
        for t, x1, y1, x2, y2 in _TEXT_RE.findall(xml):
            if t.strip() and (x2 > x1 or y2 > y1):
                nodes.append(Node(t, int(x1), int(y1), int(x2), int(y2)))
        if include_desc:
            for t, x1, y1, x2, y2 in _DESC_RE.findall(xml):
                if (t.strip() and (x2 > x1 or y2 > y1)
                        and not any(n.text == t for n in nodes)):
                    nodes.append(Node(t, int(x1), int(y1), int(x2), int(y2)))
        self._nodes_cache = nodes
        self._nodes_include_desc = include_desc
        self._nodes_ts = now
        return nodes

    def find(self, text: str,
             ymin: int = 0, ymax: int = 99999,
             xmin: int = 0, xmax: int = 99999) -> Optional[Node]:
        """按文本精确查找第一个节点（可限定区域）。"""
        for n in self.nodes():
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

    def tap(self, x: int, y: int, pause: float = 1.2):
        self._run("shell", "input", "tap", str(int(x)), str(int(y)))
        self._invalidate_cache()
        time.sleep(pause)

    def tap_node(self, node: Node, pause: float = 1.2):
        x, y = node.center
        self.tap(x, y, pause)

    def swipe_up(self, steps: float = 0.4, pause: float = 1.0):
        """向上滑（看下方内容）。坐标空间 1080x1920 竖屏。"""
        self._run("shell", "input", "swipe",
                  "540", "1500", "540", "700", "400")
        self._invalidate_cache()
        time.sleep(pause)

    def swipe_down(self, pause: float = 1.0):
        """向下滑（看上方内容）。坐标空间 1080x1920 竖屏。"""
        self._run("shell", "input", "swipe",
                  "540", "700", "540", "1500", "400")
        self._invalidate_cache()
        time.sleep(pause)

    def back(self, pause: float = 1.2):
        self._run("shell", "input", "keyevent", "4")
        self._invalidate_cache()
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
