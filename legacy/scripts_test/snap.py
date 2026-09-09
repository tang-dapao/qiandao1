import sys, io, subprocess
sys.path.insert(0, 'D:/qiandao')
import pytesseract
from PIL import Image
from adb_ui import AdbUI

ADB = "D:/Android/Sdk/platform-tools/adb.exe"
DEV = "127.0.0.1:16384"
pytesseract.pytesseract.tesseract_cmd = "C:/Program Files/Tesseract-OCR/tesseract.exe"
ui = AdbUI(DEV, ADB)


def snap(tag):
    with io.open(f'logs/snap_{tag}.txt', 'w', encoding='utf-8') as f:
        f.write(f'### {tag}\n')
        subprocess.run([ADB, "-s", DEV, "exec-out", "screencap", "-p"],
                       stdout=open(f"screenshots/{tag}.png", "wb"))
        img = Image.open(f"screenshots/{tag}.png").convert("RGB")
        f.write(f"size={img.size}\n")
        data = pytesseract.image_to_data(img, lang="chi_sim+eng",
                                         output_type=pytesseract.Output.DICT)
        words = []
        n = len(data["text"])
        for i in range(n):
            t = (data["text"][i] or "").strip()
            if not t:
                continue
            x = data["left"][i] + data["width"][i] // 2
            y = data["top"][i] + data["height"][i] // 2
            words.append(f"{t}:({x},{y})")
        for w in words:
            f.write(w + "\n")
        f.write("--- uiautomator ---\n")
        for nd in ui.nodes():
            if not (nd.x1 == 0 and nd.y1 == 0 and nd.x2 == 0 and nd.y2 == 0):
                f.write(repr(nd) + "\n")
    print(f"[{tag}] dumped")


if __name__ == "__main__":
    for t in sys.argv[1:]:
        snap(t)
