"""滚动机器人列表，收集全部机器人昵称（含 X 组的小麦/席恩/游迦等）。"""
import sys, time
sys.path.insert(0, "D:/qiandao")
from adb_ui import AdbUI

ui = AdbUI()
seen = []
for i in range(9):
    nodes = ui.nodes()
    for n in nodes:
        # 机器人列表项: text 在 x 90-400 区域，且高度小(单行昵称)
        if 85 <= n.x1 < 400 and 0 < n.y2 - n.y1 <= 45 and n.text.strip():
            tag = f"{n.text}|{n.x1},{n.y1},{n.x2},{n.y2}"
            if not any(s.split("|")[0].endswith(n.text) for s in seen if "|" in s):
                # 去重: 已存在同 text 则跳过
                if not any(s.split("|")[0] == n.text for s in seen):
                    seen.append(tag)
    ui.swipe_up()

out = "=== 机器人列表收集结果 ===\n"
for s in seen:
    out += s + "\n"
with open("D:/qiandao/logs/robot_full.txt", "w", encoding="utf-8") as f:
    f.write(out)
print("written", len(seen))
