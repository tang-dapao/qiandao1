"""在当前任务中心 WebView: 用 OCR 找 每日签到 行 + 右侧按钮，验证 OCR 坐标能否正确点击。
当前在 代柯 任务中心。不真的破坏性操作——只抓取并逻辑判断，打印建议点击坐标。
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

ui._run("shell", "screencap", "-p", "/sdcard/tc.png")
ui._run("pull", "/sdcard/tc.png", "D:/qiandao/screenshots/tc_now.png")
img = Image.open("D:/qiandao/screenshots/tc_now.png").convert("RGB")
print("img size:", img.size)

# 词级
scale = cfg["ocr"]["scale"]
sd = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
data = ocr._pytesseract.image_to_data(sd, lang=ocr._lang, output_type=ocr._pytesseract.Output.DICT)
rows = []
for i in range(len(data["text"])):
    w = data["text"][i].strip()
    if not w:
        continue
    x = int((data["left"][i]+data["width"][i]/2)/scale)
    y = int((data["top"][i]+data["height"][i]/2)/scale)
    rows.append((w, x, y))
# 打印含 签到/反馈/广告/完成任务中心的词
for w, x, y in rows:
    if any(k in w for k in ["签", "反馈", "广告", "完成", "充电", "任务", "中心", "授权"]) or y < 300:
        print(f"  {w} ({x},{y})")
