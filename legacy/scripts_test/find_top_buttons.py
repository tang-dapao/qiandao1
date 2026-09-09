import sys, re
sys.path.insert(0, 'D:/qiandao')
from adb_ui import AdbUI

ui = AdbUI('127.0.0.1:16384')
xml = ui.dump()

pat = re.compile(
    r'<node[^>]*text="([^"]*)"[^>]*'
    r'content-desc="([^"]*)"[^>]*'
    r'clickable="(true|false)"[^>]*'
    r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
print("--- small clickable/back nodes in top area (y<220) ---")
for m in pat.finditer(xml):
    t, d, clk, x1, y1, x2, y2 = m.groups()
    x1, y1, x2, y2 = map(int, (x1, y1, x2, y2))
    if y1 < 220 and (x2 - x1) < 200 and (y2 - y1) < 140:
        print(f'clk={clk} text={t!r} desc={d!r} bounds=({x1},{y1})-({x2},{y2})')
