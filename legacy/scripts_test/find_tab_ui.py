import sys, time
sys.path.insert(0, 'D:/qiandao')
import yaml, logging
logging.basicConfig(level=logging.INFO)
cfg = yaml.safe_load(open('D:/qiandao/config.yaml', encoding='utf-8'))
from driver import Driver

d = Driver(cfg)
try:
    drv = d.start()
    time.sleep(1)
    # 尝试用 uiautomator text 属性查找底部tab
    from appium.webdriver.common.appiumby import AppiumBy
    targets = ["联系人", "消息", "动态", "频道"]
    for t in targets:
        try:
            els = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("{}")'.format(t))
            print('text={} found={}'.format(t, len(els)))
            for e in els[:3]:
                print('   rect:', e.rect)
        except Exception as ex:
            print('text={} error: {}'.format(t, str(ex)[:100]))
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
