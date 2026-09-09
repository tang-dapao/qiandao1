"""OCR 词级诊断：输出每个识别词的中心坐标到 UTF-8 文件。"""
import sys
sys.path.insert(0, "D:/qiandao")
import yaml
from PIL import Image
from ocr_utils import OCR

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ocr = OCR(cfg)
img = Image.open("D:/qiandao/screenshots/webview.png").convert("RGB")

scale = cfg["ocr"].get("scale", 2.0)
scaled = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
data = ocr._pytesseract.image_to_data(scaled, lang=ocr._lang,
                                      output_type=ocr._pytesseract.Output.DICT)
lines = []
n = len(data["text"])
for i in range(n):
    w = data["text"][i].strip()
    if not w:
        continue
    conf = data["conf"][i]
    x = int((data["left"][i] + data["width"][i]/2)/scale)
    y = int((data["top"][i] + data["height"][i]/2)/scale)
    lines.append(f"{w}\tconf={conf}\tcenter=({x},{y})")
with open("D:/qiandao/logs/ocr_words.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("words:", len(lines))
