import subprocess, time, re

ADB = "D:/Android/Sdk/platform-tools/adb.exe"
DEV = "127.0.0.1:16384"
def adb_bytes(*args):
    return subprocess.run([ADB, "-s", DEV] + list(args), capture_output=True).stdout

def get_nodes():
    adb_bytes("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    out = adb_bytes("shell", "cat", "/sdcard/ui.xml")
    xml = out.decode('utf-8', errors='replace')
    nodes = re.findall(r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    return [(t, int(x1), int(y1), int(x2), int(y2)) for t, x1, y1, x2, y2 in nodes if t.strip()]

# 在竖屏(user视角)向上滑动看任务中心下方(滑到每日签到/看广告)
# swipe 从下往上: start y高 -> end y低
adb_bytes("shell", "input", "swipe", "450", "1400", "450", "400", "400")
time.sleep(2)

# dump 看能否拿到任务中心按钮坐标(UIAutomator 可能拿不到WebView)
nodes = get_nodes()
lines = ['text={} bounds=({},{})-({},{})'.format(t,x1,y1,x2,y2) for t,x1,y1,x2,y2 in nodes]
with open('D:/qiandao/logs/taskcenter_scroll.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('nodes:', len(lines))

# 截图供OCR
adb_bytes("shell", "screencap", "-p", "/sdcard/shot2.png")
adb_bytes("pull", "/sdcard/shot2.png", "D:/qiandao/screenshots/taskcenter.png")
print('shot saved')
