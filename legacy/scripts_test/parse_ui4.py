import re
xml = open('D:/qiandao/logs/ui_dump2.xml', encoding='utf-8').read()
nodes = re.findall(r'text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
out = []
for t, x1, y1, x2, y2 in nodes:
    if t.strip():
        out.append('{} | ({},{})-({},{})'.format(t, x1, y1, x2, y2))
open('D:/qiandao/logs/ui2_parsed.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('written', len(out))
