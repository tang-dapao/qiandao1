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

def find_center(nodes, text, ymin=0, ymax=9999):
    for t, x1, y1, x2, y2 in nodes:
        if t.strip() == text and ymin <= y1 <= ymax:
            return ((x1+x2)//2, (y1+y2)//2)
    return None

# 1. 点底部 联系人 tab (y 867 附近)
nodes = get_nodes()
c = find_center(nodes, '联系人', ymin=800)
print('bottom contacts tab:', c)
if c: adb_bytes("shell", "input", "tap", str(c[0]), str(c[1]))
time.sleep(2)

# 2. 现在顶部应该有分类菜单 (y ~112)，点 机器人
nodes = get_nodes()
r = find_center(nodes, '机器人', ymin=50, ymax=250)
print('top robot tab:', r)
if r: adb_bytes("shell", "input", "tap", str(r[0]), str(r[1]))
time.sleep(2.5)

# 3. dump 确认机器人列表
nodes = get_nodes()
lines = ['text={} bounds=({},{})-({},{})'.format(t,x1,y1,x2,y2) for t,x1,y1,x2,y2 in nodes]
with open('D:/qiandao/logs/robot_list_final.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('final dumped', len(lines))
