"""
Cloudflare Pages static build script
- ocr_app/templates/index.html -> dist/index.html
- ocr_app/static/*             -> dist/static/*
- data/*.png                   -> dist/static/form_images/
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DIST = ROOT / "dist"

if DIST.exists():
    shutil.rmtree(DIST)
DIST.mkdir()

shutil.copy(ROOT / "ocr_app" / "templates" / "index.html", DIST / "index.html")
print("index.html copied")

shutil.copytree(ROOT / "ocr_app" / "static", DIST / "static")
print("static/ copied")

form_img_dest = DIST / "static" / "form_images"
form_img_dest.mkdir(exist_ok=True)
data_dir = ROOT / "data"
if data_dir.exists():
    import unicodedata
    for img in sorted(data_dir.glob("*.png")):
        name_nfc = unicodedata.normalize("NFC", img.name)
        shutil.copy(img, form_img_dest / name_nfc)
        print(f"form image: {name_nfc}")

print("Build complete -> dist/")
