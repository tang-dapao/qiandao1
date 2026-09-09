"""在任务中心 WebView 截图上跑 OCR，验证能找到 去完成/签到/任务中心 等。"""
import sys
sys.path.insert(0, "D:/qiandao")
import yaml
from PIL import Image
from adb_ui import AdbUI
from ocr_utils import OCR

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()

ui._run("shell", "screencap", "-p", "/sdcard/ocr.png")
ui._run("pull", "/sdcard/ocr.png", "D:/qiandao/screenshots/webview.png")

ocr = OCR(cfg)
img = Image.open("D:/qiandao/screenshots/webview.png").convert("RGB")
print("screenshot size:", img.size)

for target in ["任务中心", "每日签到", "去完成", "问题反馈", "看广告", "获取随机", "签到", "我知道了", "我明白了"]:
    pt = ocr.find_text_center(img, target)
    print(f"{target}: {pt}")

txt = ocr._pytesseract.image_to_string(img, lang=ocr._lang)
print("---- 全文预览 ----")
print(txt[:1500])
