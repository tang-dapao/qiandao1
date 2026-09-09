"""验证: 点任务中心 每日签到 去完成(720, ~labelY)，看是否弹签到浮层。"""
import sys, time
sys.path.insert(0, "D:/qiandao")
import yaml
from PIL import Image
from adb_ui import AdbUI
from ocr_utils import OCR

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()
ocr = OCR(cfg)

def snap(path="D:/qiandao/screenshots/t.png"):
    ui._run("shell", "screencap", "-p", "/sdcard/t.png")
    ui._run("pull", "/sdcard/t.png", path)
    return Image.open(path).convert("RGB")

# 1. 找"每日签到"标签 Y -> 按钮 x720
img = snap()
pt = ocr.find_text_center(img, "每日签到")
print("每日签到:", pt)
if pt:
    label_y = pt[1]
else:
    # fallback: uiautomator
    label_y = 1053
# 按钮应在该行右侧 ~720
bx, by = 720, label_y
print("点击去完成 @", (bx, by))
ui.tap(bx, by, pause=3.0)

img2 = snap("D:/qiandao/screenshots/after_signin_click.png")
# 检测浮层: 是否出现 连续签到奖励 / 签到 按钮
for target in ["连续签到", "签到", "每日免费领", "我知道了", "奖励"]:
    p = ocr.find_text_center(img2, target)
    print(f"  {target}: {p}")
