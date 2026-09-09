import sys, time
sys.path.insert(0, 'D:/qiandao')
import yaml, logging
logging.basicConfig(level=logging.INFO)
cfg = yaml.safe_load(open('D:/qiandao/config.yaml', encoding='utf-8'))
from ocr_utils import OCR
from PIL import Image

ocr = OCR(cfg)
im = Image.open('D:/qiandao/screenshots/personal.png').convert('RGB')
w, h = im.size
print('image size:', w, h)

# 全屏 OCR 所有词带坐标
scale = 2.0
si = im.resize((int(w*scale), int(h*scale)), Image.LANCZOS)
data = ocr._pytesseract.image_to_data(si, lang=ocr._lang, output_type=ocr._pytesseract.Output.DICT)
lines = []
n = len(data['text'])
for i in range(n):
    t = (data['text'][i] or '').strip()
    if not t:
        continue
    x = int((data['left'][i] + data['width'][i]/2)/scale)
    y = int((data['top'][i] + data['height'][i]/2)/scale)
    lines.append('{} @({},{}) c={}'.format(t, x, y, data['conf'][i]))
with open('D:/qiandao/logs/personal_ocr.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))
print('ocr words:', len(lines))
