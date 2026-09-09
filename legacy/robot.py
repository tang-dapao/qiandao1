"""机器人列表管理：抓取「我添加的机器人」列表，去重、跳过内测中。

说明：
- 机器人列表的获取方式受页面控件可见性限制。QQ 机器人列表可能无法直接
  通过 OCR 一次性读出全部昵称（需要滚动）。
- 本模块设计为「交互式配置 + 可扩展自动抓取」：
    * auto_collect_robots(): 尝试用 OCR/UI 自动收集昵称（滚动+去重）
    * 若自动收集不可靠，可改为在 config 或单独 json 中维护机器人昵称列表。
"""
import json
import logging
import os
import time
from typing import List

logger = logging.getLogger("robot")


class RobotManager:
    def __init__(self, config: dict, locator, driver):
        self.cfg = config
        self.locator = locator
        self.driver = driver
        self.skip_beta = config["workflow"].get("skip_beta", True)
        self.skip_dup = config["workflow"].get("skip_duplicate_name", True)
        self._state_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "robot_state.json")
        self.robots: List[str] = []
        self._load_state()

    def _load_state(self):
        if os.path.exists(self._state_file):
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    self.robots = json.load(f)
                logger.info("已从状态文件加载 %d 个机器人", len(self.robots))
            except Exception as e:  # noqa: BLE001
                logger.warning("读取机器人状态失败: %s", e)
                self.robots = []

    def save_state(self):
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(self.robots, f, ensure_ascii=False, indent=2)
        logger.info("机器人列表已保存到 %s", self._state_file)

    # ----------------------------------------------------------
    def add_robot(self, name: str):
        """手动添加一个机器人昵称（去重）。"""
        name = name.strip()
        if not name:
            return
        if name not in self.robots:
            self.robots.append(name)
            self.save_state()

    def remove_robot(self, name: str):
        if name in self.robots:
            self.robots.remove(name)
            self.save_state()

    def set_robots(self, names: List[str]):
        """用给定列表替换（去重、可过滤内测中）。"""
        seen = []
        for n in names:
            n = n.strip()
            if not n:
                continue
            if self.skip_beta and "内测中" in n:
                logger.info("跳过内测机器人: %s", n)
                continue
            if self.skip_dup and n in seen:
                logger.info("跳过重复机器人: %s", n)
                continue
            seen.append(n)
        self.robots = seen
        self.save_state()

    def filter_active(self):
        """返回需要处理的机器人（排除内测/重复，已由 set_robots 处理）。"""
        return list(self.robots)

    # ----------------------------------------------------------
    def auto_collect_robots(self, max_scroll: int = 8):
        """尝试自动抓取机器人列表（滚动 + OCR 收集昵称）。

        前提：当前已停留在「我添加的机器人」列表页。
        由于无法可靠区分昵称与其它文本，这里仅作为辅助，
        最终建议人工核对 set_robots 的结果。
        """
        collected = []
        w, h = self.driver.size()
        for _ in range(max_scroll):
            if not self.locator.ocr.available:
                break
            img = self.locator.ocr.screenshot_to_image(self.driver.driver)
            # 用 OCR 提取全部文本行（简化：仅用于提示，不自动判定昵称）
            lines = self.locator.ocr._pytesseract.image_to_string(
                img, lang=self.locator.ocr._lang)
            logger.debug("当前屏文本片段: %s", lines[:120])
            # 上滑看更多
            self.driver.swipe(w * 0.5, h * 0.7, w * 0.5, h * 0.3)
            time.sleep(1.0)
        logger.info("自动抓取完成（需人工核对昵称）")
        return collected
