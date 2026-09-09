"""继续: 当前在 代柯 profile。点 发消息 -> 聊天页(个人) -> 任务中心 WebView。"""
import sys, io
sys.path.insert(0, "D:/qiandao")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import yaml
from adb_ui import AdbUI

cfg = yaml.safe_load(open("D:/qiandao/config.yaml", encoding="utf-8"))
ui = AdbUI()

def native():
    return [(n.text, n.center) for n in ui.nodes() if n.text.strip()]

# 2. 找发消息
n = ui.find("发消息")
print("STEP2 发消息:", n.center if n else None, n.x1 if n else None)
if n:
    ui.tap_node(n, pause=3.0)
print("--- chat 预览 ---")
for t, c in native():
    if t in ("个人", "相册", "发送", "表情", "语音"):
        print(" ", t, c)

# 3. 点 个人
np_ = ui.find("个人")
print("STEP3 个人:", np_.center if np_ else None)
if np_:
    ui.tap_node(np_, pause=3.5)
print("--- 任务中心/当前 native 预览 ---")
for t, c in native()[:30]:
    print(" ", t, c)
