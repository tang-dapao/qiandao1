import subprocess, time

ADB = "D:/Android/Sdk/platform-tools/adb.exe"
DEV = "127.0.0.1:16384"

def adb_bytes(*args):
    r = subprocess.run([ADB, "-s", DEV] + list(args), capture_output=True)
    return r.stdout

# 滚动
adb_bytes("shell", "input", "swipe", "450", "1280", "450", "480", "500")
time.sleep(2)
# dump 到设备文件，再 cat 出来 (二进制安全)
adb_bytes("shell", "uiautomator", "dump", "/sdcard/ui2.xml")
out = adb_bytes("shell", "cat", "/sdcard/ui2.xml")
with open('D:/qiandao/logs/ui_dump2.xml', 'wb') as f:
    f.write(out)
print("dumped bytes:", len(out))
