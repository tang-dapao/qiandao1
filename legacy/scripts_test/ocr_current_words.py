"""对当前屏幕截图做 OCR 词级 dump 到 UTF-8 文件。"""
import sys
sys.path.insert(0, "D:/qiandao")
import yaml
from PIL import Image
from adb_ui import AdbUI
from ocr_utils import OCR

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()
ocr = OCR(cfg)

ui._run("shell", "screencap", "-p", "/sdcard/c.png")
ui._run("pull", "/sdcard/c.png", "D:/qiandao/screenshots/current_state.png")
img = Image.open("D:/qiandao/screenshots/current_state.png").convert("RGB")
scale = cfg["ocr"].get("scale", 2.0)
scaled = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
data = ocr._pytesseract.image_to_data(scaled, lang=ocr._lang,
                                      output_type=ocr._pytesseract.Output.DICT)
lines = []
for i in range(len(data["text"])):
    w = data["text"][i].strip()
    if not w:
        continue
    x = int((data["left"][i]+data["width"][i]/2)/scale)
    y = int((data["top"][i]+data["height"][i]/2)/scale)
    lines.append(f"{w}\t({x},{y})")
with open("D:/qiandao/logs/current_words.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("words:", len(lines))
