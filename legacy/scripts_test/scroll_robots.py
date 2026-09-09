import sys, time
sys.path.insert(0, 'D:/qiandao')
import yaml, logging
logging.basicConfig(level=logging.INFO)
cfg = yaml.safe_load(open('D:/qiandao/config.yaml', encoding='utf-8'))
from driver import Driver

d = Driver(cfg)
try:
    drv = d.start()
    time.sleep(2)
    # 向上滚动查看机器人列表下半部分 (swipe up: from below to above)
    w, h = 900, 1600
    d.driver.swipe(int(w*0.5), int(h*0.8), int(w*0.5), int(h*0.3), 500)
    time.sleep(2)
    import subprocess
    subprocess.run(["D:/Android/Sdk/platform-tools/adb.exe", "-s", "127.0.0.1:16384",
                    "shell", "uiautomator", "dump", "/sdcard/ui2.xml"], check=True)
    subprocess.run(["D:/Android/Sdk/platform-tools/adb.exe", "-s", "127.0.0.1:16384",
                    "shell", "cat", "/sdcard/ui2.xml"], check=True,
                   stdout=open('D:/qiandao/logs/ui_dump2.xml', 'w', encoding='utf-8'))
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    d.stop()
