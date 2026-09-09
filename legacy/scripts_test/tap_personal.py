"""点 个人 (聊天页), 观察进入的页面。"""
import sys, time
sys.path.insert(0, "D:/qiandao")
from adb_ui import AdbUI

ui = AdbUI()
# 个人 位于聊天页上方 (y~1413)
n = ui.find("个人")
print("个人:", n)
if n:
    ui.tap_node(n, pause=3.0)
    nodes = ui.nodes()
    out = "\n".join(f"{nd.text}|{nd.x1},{nd.y1},{nd.x2},{nd.y2}" for nd in nodes)
    with open("D:/qiandao/logs/personal_page.txt", "w", encoding="utf-8") as f:
        f.write(out)
    print("dumped", len(nodes))
