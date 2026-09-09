import io
from adb_ui import AdbUI

ui = AdbUI('127.0.0.1:16384')
xml = ui.dump()
with io.open('logs/exit_dump.xml', 'w', encoding='utf-8') as f:
    f.write(xml)

print("--- all clickable-ish left/top nodes ---")
for n in ui.nodes(include_desc=True):
    if n.x2 - n.x1 < 400 and n.x1 < 300:
        print(repr(n))
