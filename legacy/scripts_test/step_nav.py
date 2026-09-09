"""逐步进入 代柯 的任务中心，并在每步 dump。用于标定 flow.py。
状态: 当前在 联系人+机器人 列表 (代柯可见)。
"""
import sys, time, io
sys.path.insert(0, "D:/qiandao")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml
from PIL import Image
from adb_ui import AdbUI
from ocr_utils import OCR

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()
ocr = OCR(cfg)

def native():
    return [(n.text, n.center) for n in ui.nodes() if n.text.strip()]

def ocr_snap(tag):
    ui._run("shell", "screencap", "-p", f"/sdcard/{tag}.png")
    ui._run("pull", f"/sdcard/{tag}.png", f"D:/qiandao/screenshots/{tag}.png")
    img = Image.open(f"D:/qiandao/screenshots/{tag}.png").convert("RGB")
    scale = cfg["ocr"]["scale"]
    sd = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
    data = ocr._pytesseract.image_to_data(sd, lang=ocr._lang,
                                          output_type=ocr._pytesseract.Output.DICT)
    words = []
    for i in range(len(data["text"])):
        w = data["text"][i].strip()
        if not w:
            continue
        x = int((data["left"][i]+data["width"][i]/2)/scale)
        y = int((data["top"][i]+data["height"][i]/2)/scale)
        words.append((w, (x, y)))
    return img, words

# 1. 点 代柯 进入 profile
n = ui.find("代柯")
print("STEP1 tap 代柯:", n.center if n else None)
ui.tap_node(n, pause=2.5)
print("native:", native()[:20])
