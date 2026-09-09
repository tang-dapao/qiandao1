import re, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
xml = open('D:/qiandao/logs/ui_dump.xml', encoding='utf-8').read()
nodes = re.findall(r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
for t, x1, y1, x2, y2 in nodes:
    if t.strip():
        print('text={!r} bounds=({},{})-({},{})'.format(t, x1, y1, x2, y2))
