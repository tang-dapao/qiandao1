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
    time.sleep(1)
    ocr = OCR(cfg)
    tmp = 'D:/qiandao/screenshots/tmp_main.png'
    drv.save_screenshot(tmp)
    im = Image.open(tmp).convert('RGB')
    scale = 3.0
    w, h = im.size
    crop = im.crop((0, int(h * 0.85), w, h))
    sw = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    data = ocr._pytesseract.image_to_data(sw, lang=ocr._lang, output_type=ocr._pytesseract.Output.DICT)
    print('=== bottom tabs OCR ===')
    n = len(data['text'])
    for i in range(n):
        w2 = (data['text'][i] or '').strip()
        if not w2:
            continue
        x = (data['left'][i] + data['width'][i] / 2) / scale
        y = (data['top'][i] + data['height'][i] / 2) / scale + int(h * 0.85)
        print('{} @({},{}) conf={}'.format(w2, int(x), int(y), data['conf'][i]))
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
