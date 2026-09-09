import sys, io, subprocess
sys.path.insert(0, 'D:/qiandao')
import pytesseract
from PIL import Image

ADB = "D:/Android/Sdk/platform-tools/adb.exe"
DEV = "127.0.0.1:16384"
pytesseract.pytesseract.tesseract_cmd = "C:/Program Files/Tesseract-OCR/tesseract.exe"

subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"],
               stdout=open("screenshots/gt2.png", "wb"))
img = Image.open("screenshots/gt2.png").convert("RGB")
print("screenshot size:", img.size)

import pytesseract as pt
data = pt.image_to_data(img, lang="chi_sim+eng",
                        output_type=pt.Output.DICT)
out = io.StringIO()
n = len(data["text"])
for i in range(n):
    t = (data["text"][i] or "").strip()
    if not t:
        continue
    x = data["left"][i] + data["width"][i] // 2
    y = data["top"][i] + data["height"][i] // 2
    out.write(f"{t!r} center=({x},{y})\n")
with io.open("logs/gt2_ocr.txt", "w", encoding="utf-8") as f:
    f.write(out.getvalue())
print("ocr words:", n)
