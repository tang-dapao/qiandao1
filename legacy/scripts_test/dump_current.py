"""从当前页: 若在聊天页则探 个人; 否则先做导航。打印当前页节点概况。"""
import sys, time
sys.path.insert(0, "D:/qiandao")
from adb_ui import AdbUI

ui = AdbUI()

def snap(tag):
    nodes = ui.nodes()
    out = "\n".join(f"{nd.text}|{nd.x1},{nd.y1},{nd.x2},{nd.y2}" for nd in nodes)
    with open(f"D:/qiandao/logs/{tag}.txt", "w", encoding="utf-8") as f:
        f.write(out)
    return nodes

nodes = snap("chat2")
print("当前页文本数量:", len(nodes))
for n in nodes[:40]:
    print(" ", n.text[:40], "@", n.center)
