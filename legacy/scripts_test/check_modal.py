"""检查当前屏幕(签到浮层)状态，OCR 多次。"""
import sys, io, time
sys.path.insert(0, "D:/qiandao")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml
from PIL import Image
from adb_ui import AdbUI
from ocr_utils import OCR

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()
ocr = OCR(cfg)

for attempt in range(5):
    time.sleep(2.0)
    ui._run("shell", "screencap", "-p", "/sdcard/m2.png")
    ui._run("pull", "/sdcard/m2.png", "D:/qiandao/screenshots/modal2.png")
    img = Image.open("D:/qiandao/screenshots/modal2.png").convert("RGB")
    scale = cfg["ocr"]["scale"]
    sd = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
    data = ocr._pytesseract.image_to_data(sd, lang=ocr._lang,
                                          output_type=ocr._pytesseract.Output.DICT)
    out = []
    for i in range(len(data["text"])):
        w = data["text"][i].strip()
        if not w:
            continue
        x = int((data["left"][i]+data["width"][i]/2)/scale)
        y = int((data["top"][i]+data["height"][i]/2)/scale)
        out.append(f"{w}({x},{y})")
    print(f"--- try {attempt}: {len(out)} words ---")
    print(" ".join(out))
