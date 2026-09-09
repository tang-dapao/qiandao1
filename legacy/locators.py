"""定位器：根据 config.yaml 的 locators 配置定位并点击控件。

支持两种类型：
  - text:       使用 OCR 在屏幕截图里找文字中心点（默认，适配 QQ 无ID控件）
  - coordinate: 直接使用固定坐标
  - keyevent:   触发按键（如返回键）
"""
import logging
from typing import Optional, Tuple

from driver import Driver
from ocr_utils import OCR

logger = logging.getLogger("locators")


class Locator:
    def __init__(self, config: dict, driver: Driver, ocr: OCR):
        self.cfg = config
        self.driver = driver
        self.ocr = ocr
        self.locs = config["locators"]

    def find(self, key: str, text_override: str = None
             ) -> Optional[Tuple[int, int]]:
        """按 key 返回要点击的坐标；找不到返回 None。"""
        spec = self.locs.get(key)
        if not spec:
            logger.error("locators 中未定义: %s", key)
            return None

        typ = spec.get("type")
        if typ == "keyevent":
            # 按键类不返回坐标，由调用方处理；这里返回哨兵
            return ("KEY", spec.get("keycode", 4))

        if typ == "coordinate":
            return self.driver.resolve_point(spec["point"])

        # 默认 text / OCR
        target = text_override or spec.get("text")
        if not target:
            logger.warning("locator %s 缺少 text", key)
            return None
        return self.find_by_ocr(target)

    def find_by_ocr(self, target: str) -> Optional[Tuple[int, int]]:
        if not self.ocr.available:
            logger.error("OCR 不可用，无法定位文字: %s", target)
            return None
        img = self.ocr.screenshot_to_image(self.driver.driver)
        pt = self.ocr.find_text_center(img, target)
        if pt:
            logger.info("OCR 找到 [%s] @ %s", target, pt)
        else:
            logger.info("OCR 未找到 [%s]", target)
        return pt

    def tap(self, key: str, text_override: str = None) -> bool:
        """定位并点击，成功返回 True。"""
        result = self.find(key, text_override)
        if result is None:
            return False
        if isinstance(result, tuple) and result[0] == "KEY":
            self.driver.back()
            return True
        x, y = result
        self.driver.tap(x, y)
        return True

    def wait_and_tap(self, key: str, text_override: str = None,
                     retries: int = None) -> bool:
        """带重试地定位并点击。"""
        retries = retries or self.cfg["timing"].get("max_retry", 3)
        poll = self.cfg["timing"].get("poll_interval", 1.0)
        for attempt in range(1, retries + 1):
            if self.tap(key, text_override):
                return True
            self.driver.sleep(poll)
            logger.debug("重试 %s (%d/%d)", key, attempt, retries)
        return False

    def verify_text(self, target: str, retries: int = None) -> bool:
        """校验屏幕上是否存在某段文字（用于判定成功状态）。"""
        retries = retries or self.cfg["timing"].get("max_retry", 3)
        poll = self.cfg["timing"].get("poll_interval", 1.0)
        for _ in range(retries):
            if not self.ocr.available:
                return False
            img = self.ocr.screenshot_to_image(self.driver.driver)
            if self.ocr.find_text_center(img, target):
                return True
            self.driver.sleep(poll)
        return False
