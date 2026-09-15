"""U2 感知层后端（阶段2，2026-09-15）：用 uiautomator2 3.x 设备端常驻 agent
替换 adb `uiautomator dump` 子进程链路，其余行为（tap/back/swipe 仍走 adb
input、OCR 截图仍走 exec-out screencap）**原样继承 AdbUI**。

背景（阶段1 探针实测，scripts_test/probe_u2.py）：
- u2 dump 中位 94ms vs adb 路径 4021ms = 42.6x 提速；每支广告 8-10 次
  dump 等待从 ~30s 压到 ~1s，是轮换切换成本（57s -> ~27s）的主要来源。
- 应用内节点 20/20 与 adb 路径完全一致；u2 额外多 4 个状态栏节点
  （时间/WLAN/信号/电量，y<20）——flow 的行/按钮过滤均带 y1>=350 等条件，
  天然排除；find() 按精确文本匹配，状态栏短文本（如 "18:34"）不会误配。
- **感知互斥（重要）**：u2 agent 常驻时 adb `uiautomator dump` 100% 被
  SIGKILL（rc=137，0 节点）；`stop_uiautomator()` 后 adb 完全恢复。
  因此 backend 必须**整体互斥切换**：
    * config `device.backend: u2` -> 本类接管全部感知；
    * config `device.backend: adb`（默认）-> 纯 AdbUI，本类不参与；
    * 本类进程退出时 atexit 自动 stop agent，不留占位（保 adb 回退通道）。
- 视频广告期 adb dump 必挂（UI 无 idle），u2 照常 34-64ms 出节点 ——
  OCR 关广告链路保持不变，u2 让 dump 型判定在广告期也活过来了。

接口契约：与 AdbUI 完全一致（flow.py 零改动）——
  nodes()/find()/find_contains()/exists()/refresh()/tap()/tap_node()/
  swipe_up()/swipe_down()/back()/wait_for()/wait_contains()/wait_gone()/
  dump_fail_streak/is_online + 属性 adb/device（flow._ocr_shot 的
  exec-out screencap 直接引用，保持不变）。
"""
import atexit
import logging
import time
from typing import Optional

from adb_ui import AdbUI

logger = logging.getLogger("adbu")

# agent 重连：stop 后立刻 connect 可能撞上设备端 UiDevice 初始化瞬态
# （阶段1 实测一次 IllegalArgumentException）， sleep 后重试一次即可恢复
_CONNECT_RETRIES = 3
_CONNECT_RETRY_SLEEP = 2.0


class U2AdbUI(AdbUI):
    """AdbUI 的 u2 感知后端：仅替换 dump 链路，接口与行为契约不变。

    感知路径：dump_hierarchy()（设备端 :9008 JSON-RPC，adb forward 本机
    回环）-> 复用 adb_ui._parse_xml -> 同一 Node 结构。tap/back/swipe/
    screencap 仍走 adb input（与旧路径逐字节一致，避免行为漂移）。
    """

    def __init__(self, device: str, adb: str = None):
        if adb is None:
            super().__init__(device)
        else:
            super().__init__(device, adb)
        self._u2 = None
        atexit.register(self._atexit_stop)

    # ----------------------------------------------------------
    # 设备端 agent 生命周期
    # ----------------------------------------------------------
    def _agent(self):
        """懒连接设备端 agent（u2.connect 会自动拉起 u2.jar server）。"""
        if self._u2 is None:
            import uiautomator2 as u2
            last = None
            for i in range(_CONNECT_RETRIES):
                try:
                    self._u2 = u2.connect(self.device)
                    logger.info("u2 agent 已连接（%s，第 %d 次尝试）",
                                self.device, i + 1)
                    # 预热 + 防冷启动首 dump 不完整：u2_test4 首台实测，
                    # agent 刚拉起后第一次 dump 层级缺底部 tab 栏 ->
                    # _looks_like_robot_list 判据失败一条 WARNING（flow 重试
                    # 自愈，无害但没必要）。connect 后立即空跑一次丢弃。
                    try:
                        self._u2.dump_hierarchy()
                    except Exception:  # noqa: BLE001
                        pass
                    return self._u2
                except Exception as e:  # noqa: BLE001
                    last = e
                    logger.warning("u2 connect 失败（%d/%d）: %s",
                                   i + 1, _CONNECT_RETRIES, e)
                    time.sleep(_CONNECT_RETRY_SLEEP)
            raise last
        return self._u2

    def stop_agent(self):
        """停掉设备端 agent，把 uiautomator instrumentation 让回 adb 路径。

        共存互斥（阶段1 实锤）：agent 活则 adb dump 100% rc=137。切回
        adb backend / 进程退出时必须调用，否则残留 agent 会杀掉后续
        任何 adb 感知。
        """
        if self._u2 is not None:
            try:
                self._u2.stop_uiautomator()
                logger.info("u2 agent 已停止（adb 感知通道已让出）")
            except Exception as e:  # noqa: BLE001
                logger.warning("u2 agent 停止失败（设备可能已断开）: %s", e)
            self._u2 = None

    def _atexit_stop(self):
        try:
            self.stop_agent()
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------
    # 感知替换：仅 dump（nodes()/find()/wait_* 全部继承，经此获得提速）
    # ----------------------------------------------------------
    def dump(self, timeout: Optional[float] = None) -> Optional[str]:
        """u2 版 dump：返回标准 uiautomator XML（与 adb 路径同格式）。

        - timeout 仅保留接口兼容：u2 走设备端常驻 agent，实测 60-110ms，
          无需掐断；视频/动画期 u2 也能正常返回（广告期照常出节点）。
        - 失败语义与基类一致：返回 None 并累计 dump_fail_streak（供
          flow._dump_stuck / OCR 兜底判定），成功即清零。
        - 基类 dump 的 1.0s 失败节流在此不适用：u2 失败本身瞬时，
          节流反而拖慢恢复；改为 0.2s 轻节流防异常风暴。
        """
        try:
            xml = self._agent().dump_hierarchy()
            if not xml:
                logger.warning("u2 dump 返回空（页面可能仍在切换）")
                self._dump_fail_streak += 1
                time.sleep(0.2)
                return None
            self._dump_fail_streak = 0
            return xml
        except Exception as e:  # noqa: BLE001
            logger.warning("u2 dump 失败: %s", e)
            self._dump_fail_streak += 1
            time.sleep(0.2)
            return None
