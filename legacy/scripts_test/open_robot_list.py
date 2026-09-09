import sys, io, time
sys.path.insert(0, 'D:/qiandao')
from adb_ui import AdbUI

ui = AdbUI('127.0.0.1:16384')

def dump(tag):
    nodes = [n for n in ui.nodes()
             if not (n.x1 == 0 and n.y1 == 0 and n.x2 == 0 and n.y2 == 0)]
    with io.open(f'logs/nav_{tag}.txt', 'w', encoding='utf-8') as f:
        f.write(f'=== {tag} === total={len(nodes)}\n')
        for n in nodes:
            f.write(repr(n) + '\n')
    print(f'[{tag}] dumped {len(nodes)} nodes')

# tap 机器人 category
ui.tap(344, 575, 2.5)
dump('robot_list')
