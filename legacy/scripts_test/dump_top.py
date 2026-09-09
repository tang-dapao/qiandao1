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

lines = []
for t, x1, y1, x2, y2 in get_nodes():
    lines.append('text={} bounds=({},{})-({},{})'.format(t, x1, y1, x2, y2))
with open('D:/qiandao/logs/top_of_list.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('dumped', len(lines))
