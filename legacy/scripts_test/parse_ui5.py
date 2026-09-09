import re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
xml = open('D:/qiandao/logs/ui_dump2.xml', encoding='utf-8', errors='replace').read()
# 滚动后 vs 滚动前对比
xml1 = open('D:/qiandao/logs/ui_dump.xml', encoding='utf-8', errors='replace').read()
nodes = re.findall(r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
lines = []
for t, x1, y1, x2, y2 in nodes:
    if t.strip():
        lines.append('{} | y={}-{}'.format(t, y1, y2))
print('\n'.join(lines))
