import subprocess, time, re

ADB = "D:/Android/Sdk/platform-tools/adb.exe"
DEV = "127.0.0.1:16384"

def adb_bytes(*args):
    r = subprocess.run([ADB, "-s", DEV] + list(args), capture_output=True)
    return r.stdout

def get_nodes():
    adb_bytes("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    out = adb_bytes("shell", "cat", "/sdcard/ui.xml")
    xml = out.decode('utf-8', errors='replace')
    nodes = re.findall(r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    return [(t, int(x1), int(y1), int(x2), int(y2)) for t, x1, y1, x2, y2 in nodes if t.strip()]

seen = []
for i in range(6):
    for t, x1, y1, x2, y2 in get_nodes():
        if 180 < y1 < 860 and y2-y1 < 200:  # 列表区，排除顶部菜单和底部导航
            seen.append((t, x1, y1))
    adb_bytes("shell", "input", "swipe", "450", "1400", "450", "300", "300")
    time.sleep(1.8)

lines = []
for t, x, y in seen:
    lines.append('text={} center=({},{})'.format(t, x+(x+0), y))
with open('D:/qiandao/logs/robots_seen.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('total nodes seen:', len(seen))
