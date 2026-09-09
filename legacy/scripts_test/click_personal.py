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

# 点击 个人 (中心约 301,713)
nodes = get_nodes()
target = None
for t, x1, y1, x2, y2 in nodes:
    if t.strip() == "个人":
        target = ((x1+x2)//2, (y1+y2)//2)
        break
print('个人 center:', target)
if target:
    adb_bytes("shell", "input", "tap", str(target[0]), str(target[1]))
    time.sleep(3)

nodes = get_nodes()
lines = ['text={} bounds=({},{})-({},{})'.format(t,x1,y1,x2,y2) for t,x1,y1,x2,y2 in nodes]
with open('D:/qiandao/logs/personal_view.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('dumped', len(lines))
