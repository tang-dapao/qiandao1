import sys, time
sys.path.insert(0, 'D:/qiandao')
import yaml, logging
logging.basicConfig(level=logging.INFO)
cfg = yaml.safe_load(open('D:/qiandao/config.yaml', encoding='utf-8'))
from driver import Driver
from ocr_utils import OCR
from PIL import Image

d = Driver(cfg)
try:
    drv = d.start()
    time.sleep(2)
    ocr = OCR(cfg)
    img = ocr.screenshot_to_image(drv)
    text = ocr._pytesseract.image_to_string(img, lang=ocr._lang)
    print('=== 当前界面 OCR ===')
    c = 0
    for line in text.splitlines():
        line = line.strip()
        if line:
            print(repr(line))
            c += 1
            if c >= 25:
                break
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
