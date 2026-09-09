import sys, re
sys.path.insert(0, 'D:/qiandao')
from adb_ui import AdbUI

ui = AdbUI('127.0.0.1:16384')
xml = ui.dump()

pat = re.compile(
    r'<node[^>]*text="([^"]*)"[^>]*'
    r'content-desc="([^"]*)"[^>]*'
    r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
print("--- all nodes with y1 in [20,200] (title-bar zone) ---")
for m in pat.finditer(xml):
    t, d, x1, y1, x2, y2 = m.groups()
    x1, y1, x2, y2 = map(int, (x1, y1, x2, y2))
    if 20 <= y1 <= 200 and (x2 - x1) > 0 and (y2 - y1) > 0:
        print(f'text={t!r} desc={d!r} bounds=({x1},{y1})-({x2},{y2})')
