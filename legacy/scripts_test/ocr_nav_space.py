"""OCR 找当前屏幕的机器人列表项(截图空间 1600x900)。确认 OCR 空间=点击空间。
当前: QQ 刚重启。导航到 机器人 列表 后 OCR。
"""
import sys, io
sys.path.insert(0, "D:/qiandao")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml
from PIL import Image
from adb_ui import AdbUI
from ocr_utils import OCR

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()
ocr = OCR(cfg)

def shot(tag):
    ui._run("shell", "screencap", "-p", f"/sdcard/{tag}.png")
    ui._run("pull", f"/sdcard/{tag}.png", f"D:/qiandao/screenshots/{tag}.png")
    return Image.open(f"D:/qiandao/screenshots/{tag}.png").convert("RGB")

def words(img):
    scale = cfg["ocr"]["scale"]
    sd = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
    data = ocr._pytesseract.image_to_data(sd, lang=ocr._lang, output_type=ocr._pytesseract.Output.DICT)
    out = []
    for i in range(len(data["text"])):
        w = data["text"][i].strip()
        if not w:
            continue
        x = int((data["left"][i]+data["width"][i]/2)/scale)
        y = int((data["top"][i]+data["height"][i]/2)/scale)
        out.append((w, x, y))
    return out

img = shot("s1")
print("size:", img.size)
for w, x, y in words(img):
    if any(k in w for k in ["联系人","机器人","消息","动态","添加","好友","新朋友"]):
        print(f"  {w} ({x},{y})")
