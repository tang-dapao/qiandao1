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
    # 查找联系人页上的 机器人 入口
    targets = ["机器人", "通讯录", "群聊", "我的好友", "添加"]
    for t in targets:
        try:
            els = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("{}")'.format(t))
            print('text={} found={}'.format(t, len(els)))
            for e in els[:4]:
                print('   rect:', e.rect)
        except Exception as ex:
            print('text={} error: {}'.format(t, str(ex)[:80]))
    drv.save_screenshot('D:/qiandao/screenshots/contacts2.png')
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
