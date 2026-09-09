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

# 确认当前方向: 看窗口尺寸
r = adb_bytes("shell", "wm", "size").decode(errors='replace')
print("wm size:", r.strip())
r2 = adb_bytes("shell", "dumpsys", "input", "|", "grep", "SurfaceOrientation").decode(errors='replace')
print("orientation grep:", r2.strip()[:200])
