"""在机器人列表点 古禹，dump 看进入什么页面。"""
import sys, time
sys.path.insert(0, "D:/qiandao")
from adb_ui import AdbUI

ui = AdbUI()
# 先滚回顶部，保证古禹可见
for _ in range(4):
    ui.swipe_down()
n = ui.find("古禹")
print("古禹:", n)
if n:
    ui.tap_node(n)
    time.sleep(2.5)
    nodes = ui.nodes()
    out = "\n".join(f"{nd.text}|{nd.x1},{nd.y1},{nd.x2},{nd.y2}" for nd in nodes)
    with open("D:/qiandao/logs/after_tap_robot.txt", "w", encoding="utf-8") as f:
        f.write(out)
    print("dumped", len(nodes))
