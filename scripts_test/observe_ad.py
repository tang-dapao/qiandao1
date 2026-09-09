"""广告页证据采集脚本（只观察不自动关闭）。

用法: py -3.13 scripts_test/observe_ad.py [机器人昵称]
输出:
  screenshots/adobs_<n>_<elapsed>s.png   每个时间点截图
  logs/adobs_<n>.txt                     每个时间点的 OCR(顶部/全屏) + uiautomator 节点摘要
结束后停在广告页(最多 ~40s)，由人工决定后续。
"""
import io
import logging
import subprocess
import sys
import time
from datetime import datetime

import yaml

sys.path.insert(0, ".")
from adb_ui import AdbUI  # noqa: E402
from flow import Flow  # noqa: E402
import pytesseract  # noqa: E402
from PIL import Image  # noqa: E402

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("observe_ad")

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
pytesseract.pytesseract.tesseract_cmd = cfg["ocr"]["tesseract_cmd"]
ui = AdbUI(cfg["device"]["udid"])
flow = Flow(cfg, ui)

name = sys.argv[1] if len(sys.argv) > 1 else "李宥恩"
stamp = datetime.now().strftime("%H%M%S")
report = open(f"logs/adobs_{stamp}.txt", "w", encoding="utf-8")
SCREENS = "screenshots"


def note(msg: str):
    log.info(msg)
    report.write(msg + "\n")
    report.flush()


def ocr_words(img, region=None):
    """OCR 返回 (文字, 坐标) 列表。"""
    if region:
        img = img.crop(region)
    data = pytesseract.image_to_data(
        img, lang=cfg["ocr"]["lang"], output_type=pytesseract.Output.DICT)
    out = []
    for i in range(len(data["text"])):
        t = (data["text"][i] or "").strip()
        if t and int(data["conf"][i]) > 30:
            x = data["left"][i] + data["width"][i] // 2
            y = data["top"][i] + data["height"][i] // 2
            if region:
                x += region[0]
                y += region[1]
            out.append((t, x, y))
    return out


note(f"== 广告观察: {name} @ {stamp} ==")
if not flow._enter_taskcenter(name):
    note("RESULT: ENTER_FAIL")
    sys.exit(1)
note("已进任务中心")

btn = flow._scroll_to_row("获取随机") or flow._scroll_to_row("看广告")
if not btn:
    note("RESULT: NO_AD_BUTTON")
    sys.exit(1)
flow._tap_node(btn)
t0 = time.time()
note(f"已点击广告按钮, 开始观察")

for i in range(8):  # 8 x 5s = 40s
    time.sleep(5)
    el = time.time() - t0
    img = flow._ocr_shot()
    if img is None:
        note(f"[{el:4.0f}s] 截图失败")
        continue
    img.save(f"{SCREENS}/adobs_{stamp}_{i}_{el:.0f}s.png")
    top = ocr_words(img, (0, 0, 1080, 400))
    full = ocr_words(img)
    nodes = ui.nodes()
    node_texts = [n.text for n in nodes[:25]]
    note(f"[{el:4.0f}s] 第{i}轮 ----")
    note(f"  OCR顶部(0-400): {top}")
    note(f"  OCR全屏前20词: {full[:20]}")
    note(f"  uiautomator节点数={len(nodes)} 前15: {node_texts[:15]}")

note("== 观察结束(40s)。当前页面保留在屏幕上，请人工查看最后一张截图 ==")
report.close()
