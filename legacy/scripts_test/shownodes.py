import re
import sys

s = open(sys.argv[1], encoding="utf-8").read()
xs = re.findall(r"Node\('([^']*)' bounds=\((\d+),(\d+)\)-\((\d+),(\d+)\)", s)
for t, x1, y1, x2, y2 in xs:
    print(f"{t!r:28} x1={x1:4} y1={y1:4} x2={x2:4} w={int(x2)-int(x1):3}")
