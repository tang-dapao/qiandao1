"""Appium 驱动封装：连接、启动、通用操作。"""
import logging
import random
import time
from typing import Optional, Tuple

from appium import webdriver
from appium.options.common.base import AppiumOptions
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger("driver")


class Driver:
    """封装 Appium 会话与常用操作。"""

    def __init__(self, config: dict, caps_override: Optional[dict] = None):
        self.cfg = config
        self.device = config["device"]
        self.timing = config["timing"]
        self.driver = None
        self.caps_override = caps_override or {}

    def start(self, appium_url: str = "http://127.0.0.1:4723"):
        """建立 Appium 会话。"""
        options = AppiumOptions()
        options.set_capability("platformName", self.device["platform"])
        options.set_capability("appium:udid", self.device["udid"])
        options.set_capability("appium:platformVersion", self.device["platformVersion"])
        options.set_capability("appium:noReset", self.device.get("noReset", True))
        options.set_capability("appium:automationName", "UiAutomator2")
        options.set_capability("appium:appPackage", self.cfg["app"]["package"])
        options.set_capability("appium:appActivity", self.cfg["app"].get("activity") or "")
        options.set_capability("appium:newCommandTimeout", 120)
        for k, v in self.caps_override.items():
            options.set_capability(k, v)
        logger.info("正在连接 Appium: %s (udid=%s)", appium_url, self.device["udid"])
        self.driver = webdriver.Remote(command_executor=appium_url, options=options)
        self.driver.implicitly_wait(self.timing.get("element_timeout", 15))
        logger.info("Appium 会话已建立")
        return self.driver

    def stop(self):
        """关闭会话。"""
        if self.driver:
            try:
                self.driver.quit()
            except Exception as e:  # noqa: BLE001
                logger.warning("关闭会话出错: %s", e)
            self.driver = None

    # ---------- 基础操作 ----------
    def size(self) -> Tuple[int, int]:
        """返回屏幕宽高 (w, h)。"""
        s = self.driver.get_window_size()
        return s["width"], s["height"]

    def resolve_point(self, spec) -> Tuple[int, int]:
        """把坐标说明解析成绝对像素。

        spec 可为:
          - [x, y] 列表，数字<1 视为比例，>=1 视为绝对像素
        """
        w, h = self.size()
        x = spec[0] * w if spec[0] < 1 else spec[0]
        y = spec[1] * h if spec[1] < 1 else spec[1]
        return int(x), int(y)

    def tap(self, x: int, y: int):
        """点击指定坐标，并应用随机间隔。"""
        logger.debug("点击 (%d, %d)", x, y)
        self.driver.tap([(int(x), int(y))])
        self._human_pause()

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500):
        self.driver.swipe(int(x1), int(y1), int(x2), int(y2), duration_ms)
        self._human_pause()

    def back(self):
        """物理返回键。"""
        self.driver.press_keycode(4)
        self._human_pause()

    def screenshot(self, path: str):
        """保存当前屏幕截图。"""
        self.driver.save_screenshot(path)
        logger.debug("截图已保存: %s", path)

    def _human_pause(self):
        """点击后的随机间隔，模拟真人节奏，降低风控概率。"""
        lo = self.timing.get("click_min", 1.5)
        hi = self.timing.get("click_max", 3.0)
        time.sleep(random.uniform(lo, hi))

    def sleep(self, seconds: float):
        time.sleep(seconds)

    # ---------- 元素定位（备用：有稳定ID时可用） ----------
    def find_by_text(self, text: str, timeout: Optional[float] = None):
        """按可见文本查找元素（依赖 uiautomator 文本属性）。"""
        to = timeout or self.timing.get("element_timeout", 15)
        try:
            el = WebDriverWait(self.driver, to).until(
                EC.presence_of_element_located(
                    (AppiumBy.ANDROID_UIAUTOMATOR,
                     f'new UiSelector().text("{text}")')
                )
            )
            return el
        except Exception:  # noqa: BLE001
            return None

    def tap_text_element(self, text: str) -> bool:
        """优先尝试用文本属性点击，成功返回 True。"""
        el = self.find_by_text(text, timeout=3)
        if el:
            el.click()
            self._human_pause()
            return True
        return False
