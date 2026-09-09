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
    # 扫描页面所有可点击元素的 text
    print('=== clickable elements with text ===')
    els = drv.find_elements(AppiumBy.ANDROID_UIAUTOMATOR,
        'new UiSelector().clickable(true)')
    print('total clickable:', len(els))
    seen = set()
    for e in els:
        try:
            t = e.text
        except Exception:
            t = ''
        c = (t, e.rect['x']//30, e.rect['y']//30)
        if t and c not in seen:
            seen.add(c)
            print('text={!r} rect={}'.format(t, e.rect))
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
