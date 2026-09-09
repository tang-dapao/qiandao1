"""实时验证: 导航进 代柯 任务中心，点 每日签到 去完成(1071,~labelY)，OCR 检查浮层。
目标: 确认 签到/✕/我知道了 按钮的真实坐标(1600x900 截图空间)。
"""
import sys, io, time
sys.path.insert(0, "D:/qiandao")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml
from adb_ui import AdbUI
from flow import Flow

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()
flow = Flow(cfg, ui, None)

# 直接用 flow 的截图/OCR（依赖 ocr 实例）
from ocr_utils import OCR
flow.ocr = OCR(cfg)

def dump_img(img, tag):
    scale = cfg["ocr"]["scale"]
    sd = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
    data = flow.ocr._pytesseract.image_to_data(sd, lang=flow.ocr._lang, output_type=flow.ocr._pytesseract.Output.DICT)
    lines = []
    for i in range(len(data["text"])):
        w = data["text"][i].strip()
        if not w:
            continue
        x = int((data["left"][i]+data["width"][i]/2)/scale)
        y = int((data["top"][i]+data["height"][i]/2)/scale)
        lines.append(f"{w} ({x},{y})")
    open(f"D:/qiandao/logs/{tag}.txt","w",encoding="utf-8").write("\n".join(lines))
    return lines

from PIL import Image

# 1 进入 代柯 任务中心
print("进入代柯...")
ok = flow._enter_robot("代柯")
print("进入结果:", ok)
time.sleep(2)
img = flow._screenshot("tc0")
ls = dump_img(img, "tc0")
print("--- 任务中心关键 ---")
for l_ in ls:
    if any(k in l_ for k in ["签到","反馈","广告","完成","充电","任务"]):
        print("  ", l_)
