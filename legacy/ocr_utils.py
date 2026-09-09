"""OCR 工具：截图文字识别与按文字定位坐标。

策略说明：
- QQ 定制 UI 多无稳定控件 ID，因此主要用「全屏截图 + OCR 找文字中心点」来定位。
- 坐标点击为本项目的默认定位方式（config.yaml 中 locators 可配 text / coordinate）。
"""
import logging
import os
import tempfile
from typing import Optional, Tuple

from PIL import Image

logger = logging.getLogger("ocr")


class OCR:
    def __init__(self, config: dict):
        self.cfg = config["ocr"]
        self._tesseract_ready = False
        self._lang = self.cfg.get("lang", "chi_sim+eng")
        self._load_tesseract()

    def _load_tesseract(self):
        try:
            import pytesseract
            cmd = self.cfg.get("tesseract_cmd")
            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            # 触发一次版本检查确认可用
            pytesseract.get_tesseract_version()
            self._pytesseract = pytesseract
            self._tesseract_ready = True
            logger.info("Tesseract OCR 可用 (lang=%s)", self._lang)
        except Exception as e:  # noqa: BLE001
            logger.warning("Tesseract 不可用（%s），OCR 定位将失效", e)
            self._pytesseract = None

    @property
    def available(self) -> bool:
        return self._tesseract_ready

    def find_text_center(self, image: Image.Image, target: str,
                         ) -> Optional[Tuple[int, int]]:
        """在给定截图中查找目标文字，返回其中心坐标（绝对像素）。

        返回 None 表示未找到。
        """
        if not self._tesseract_ready:
            return None
        scale = self.cfg.get("scale", 2.0)
        scaled = image.resize(
            (int(image.width * scale), int(image.height * scale)),
            Image.LANCZOS,
        )
        try:
            data = self._pytesseract.image_to_data(
                scaled, lang=self._lang,
                output_type=self._pytesseract.Output.DICT
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("OCR 识别失败: %s", e)
            return None

        conf_threshold = self.cfg.get("confidence", 60)
        n = len(data["text"])
        for i in range(n):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                conf = 0
            # 简单归一化后做子串匹配（可扩展模糊匹配）
            if self._match(word, target) and conf >= conf_threshold:
                x = (data["left"][i] + data["width"][i] / 2) / scale
                y = (data["top"][i] + data["height"][i] / 2) / scale
                return int(x), int(y)
        return None

    @staticmethod
    def _match(word: str, target: str) -> bool:
        word = word.replace(" ", "").lower()
        target = target.replace(" ", "").lower()
        return target in word or word in target

    def screenshot_to_image(self, driver) -> Image.Image:
        """把 driver 当前屏幕保存为 PIL Image。"""
        tmp = os.path.join(tempfile.gettempdir(), "ocr_screen.png")
        driver.save_screenshot(tmp)
        return Image.open(tmp).convert("RGB")
