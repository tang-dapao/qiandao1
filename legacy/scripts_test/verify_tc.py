"""用 flow 进入 代柯 任务中心，点 每日签到 去完成，OCR 检查浮层与按钮坐标。"""
import sys, io, time
sys.path.insert(0, "D:/qiandao")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml
from PIL import Image
from adb_ui import AdbUI
from ocr_utils import OCR
from flow import Flow

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()
ocr = OCR(cfg)
flow = Flow(cfg, ui, ocr)

def ocr_dump(tag):
    img = flow._screenshot(tag)
    scale = cfg["ocr"]["scale"]
    sd = img.resize((int(img.width*scale), int(img.height*scale)), Image.LANCZOS)
    data = ocr._pytesseract.image_to_data(sd, lang=ocr._lang, output_type=ocr._pytesseract.Output.DICT)
    lines = []
    for i in range(len(data["text"])):
        w = data["text"][i].strip()
        if not w: continue
        x = int((data["left"][i]+data["width"][i]/2)/scale)
        y = int((data["top"][i]+data["height"][i]/2)/scale)
        lines.append(f"{w} ({x},{y})")
    with open(f"D:/qiandao/logs/{tag}.txt","w",encoding="utf-8") as f:
        f.write("\n".join(lines))
    return img, lines

ok = flow._enter_robot("代柯")
print("进入任务中心:", ok)
time.sleep(2)
img, ls = ocr_dump("tc_enter")
print("--- 任务中心巡检 ---")
for l_ in ls:
    if any(k in l_ for k in ["签","反馈","告","完成","充电","任务","免费","奖励"]):
        print("  ", l_)
