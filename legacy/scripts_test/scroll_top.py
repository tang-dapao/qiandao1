import subprocess, time

ADB = "D:/Android/Sdk/platform-tools/adb.exe"
DEV = "127.0.0.1:16384"
def adb_bytes(*args):
    return subprocess.run([ADB, "-s", DEV] + list(args), capture_output=True).stdout

# 回滚到列表顶部 (swipe down)
for i in range(3):
    adb_bytes("shell", "input", "swipe", "450", "300", "450", "1400", "300")
    time.sleep(1.5)
print("scrolled back to top")
