import sys, time
sys.path.insert(0, 'D:/qiandao')
import yaml, logging
logging.basicConfig(level=logging.INFO)
cfg = yaml.safe_load(open('D:/qiandao/config.yaml', encoding='utf-8'))
from driver import Driver
from appium.webdriver.common.appiumby import AppiumBy

d = Driver(cfg)
try:
    drv = d.start()
    time.sleep(1)
    # 点击底部 联系人 tab (y=867那一组)
    els = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("联系人")')
    # 选 y 坐标较大的那个（底部导航）
    target = None
    for e in els:
        if e.rect['y'] > 500:
            target = e
            break
    if target:
        target.click()
        print('clicked 联系人 tab at', target.rect)
    time.sleep(3)
    drv.save_screenshot('D:/qiandao/screenshots/contacts.png')
    print('activity:', drv.current_activity)
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
