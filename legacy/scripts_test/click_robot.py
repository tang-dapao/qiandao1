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
    time.sleep(2)
    el = drv.find_element(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("机器人")')
    el.click()
    print('clicked 机器人')
    time.sleep(3)
    # 查看机器人页
    for t in ["我添加的机器人", "机器人", "添加", "推荐"]:
        try:
            els = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("{}")'.format(t))
            print('text={} found={}'.format(t, len(els)))
            for e in els[:4]:
                print('   rect:', e.rect)
        except Exception as ex:
            print('text={} error: {}'.format(t, str(ex)[:80]))
    drv.save_screenshot('D:/qiandao/screenshots/robot_home.png')
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
