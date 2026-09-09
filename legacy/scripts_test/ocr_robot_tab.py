import sys, time
sys.path.insert(0, 'D:/qiandao')
import yaml, logging
logging.basicConfig(level=logging.INFO)
cfg = yaml.safe_load(open('D:/qiandao/config.yaml', encoding='utf-8'))
from driver import Driver
from appium.webdriver.common.appiumby import AppiumBy
from ocr_utils import OCR
from PIL import Image

d = Driver(cfg)
try:
    drv = d.start()
    time.sleep(2)
    ocr = OCR(cfg)
    # 点顶部机器人tab
    robot = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("机器人")')
    for e in robot:
        if 100 < e.rect['y'] < 200:
            e.click(); break
    time.sleep(3)
    tmp = 'D:/qiandao/screenshots/robot_tab.png'
    drv.save_screenshot(tmp)
    im = Image.open(tmp).convert('RGB')
    scale = 2.5
    si = im.resize((int(im.width*scale), int(im.height*scale)), Image.LANCZOS)
    data = ocr._pytesseract.image_to_data(si, lang=ocr._lang, output_type=ocr._pytesseract.Output.DICT)
    print('=== robot tab OCR words ===')
    n = len(data['text'])
    for i in range(n):
        w2 = (data['text'][i] or '').strip()
        if not w2:
            continue
        x = int((data['left'][i] + data['width'][i]/2)/scale)
        y = int((data['top'][i] + data['height'][i]/2)/scale)
        print('{} @({},{}) c={}'.format(w2, x, y, data['conf'][i]))
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
