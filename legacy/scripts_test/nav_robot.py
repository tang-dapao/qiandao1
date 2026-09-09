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
    # 先确认当前在联系人页（点底部 联系人 tab 确保）
    contacts = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("联系人")')
    for e in contacts:
        if e.rect['y'] > 500:
            e.click()
            print('ensure on contacts tab')
            break
    time.sleep(2)
    # 点顶部 机器人 tab
    robot = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("机器人")')
    target = None
    for e in robot:
        if 100 < e.rect['y'] < 200:
            target = e
            break
    if target:
        target.click()
        print('clicked top 机器人 tab')
    time.sleep(3)
    # 现在查找 我添加的机器人
    for t in ["我添加的机器人", "机器人", "添加", "推荐机器人"]:
        try:
            els = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("{}")'.format(t))
            print('textContains={} found={}'.format(t, len(els)))
            for e in els[:4]:
                print('   rect:', e.rect)
        except Exception as ex:
            print('err:', str(ex)[:60])
    drv.save_screenshot('D:/qiandao/screenshots/robot_list.png')
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
