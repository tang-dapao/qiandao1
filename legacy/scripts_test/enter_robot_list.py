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

# 通过 text=机器人 找到分类栏里的"机器人"并点击 (选 y 在 400-700 区间的那个)
nodes = get_nodes()
target = None
for t, x1, y1, x2, y2 in nodes:
    if t.strip() == "机器人" and 300 < y1 < 700:
        target = ((x1+x2)//2, (y1+y2)//2)
        break
print("robot tab center:", target)
if target:
    adb_bytes("shell", "input", "tap", str(target[0]), str(target[1]))
    time.sleep(2.5)

# dump 确认进入机器人列表
nodes2 = get_nodes()
lines = ['text={} bounds=({},{})-({},{})'.format(t,x1,y1,x2,y2) for t,x1,y1,x2,y2 in nodes2]
with open('D:/qiandao/logs/after_robot_tab.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('dumped', len(lines))
