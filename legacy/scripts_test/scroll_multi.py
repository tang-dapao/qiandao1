import subprocess, time, re

ADB = "D:/Android/Sdk/platform-tools/adb.exe"
DEV = "127.0.0.1:16384"

def adb_bytes(*args):
    r = subprocess.run([ADB, "-s", DEV] + list(args), capture_output=True)
    return r.stdout

def dump(path):
    adb_bytes("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    out = adb_bytes("shell", "cat", "/sdcard/ui.xml")
    with open(path, 'wb') as f:
        f.write(out)

def show_nodes(path):
    xml = open(path, encoding='utf-8', errors='replace').read()
    nodes = re.findall(r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    return [(t, int(y1)) for t, x1, y1, x2, y2 in nodes if t.strip()]

# 多段滚动，看能否滚出下面机器人
for i in range(3):
    print("=== scroll attempt", i+1, "===")
    adb_bytes("shell", "input", "swipe", "450", "1400", "450", "300", "300")
    time.sleep(2)
    dump('D:/qiandao/logs/scroll_{}.xml'.format(i))
    nodes = show_nodes('D:/qiandao/logs/scroll_{}.xml'.format(i))
    names = [t for t, y in nodes if y > 200 and y < 860 and not t.startswith('P')]
    sysout = open('D:/qiandao/logs/scroll_nodes.txt', 'a', encoding='utf-8')
    for t, y in nodes:
        if t.strip():
            sysout.write('{} | y={}\n'.format(t, y))
    sysout.close()
print("done")
