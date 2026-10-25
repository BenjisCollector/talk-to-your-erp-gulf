"""Records the real demo UI (1280x800, light theme) asking three Arabic questions (bars, line, donut). Needs: pip install playwright.
Usage: python3 docs/record_demo.py [chromium-executable]  -> docs/_raw/*.webm; encode with docs/encode.sh"""
import glob, os, sys
from playwright.sync_api import sync_playwright

URL = os.environ.get("DEMO_URL", "http://127.0.0.1:8080/")
QS = ["وريني أكبر ٥ عملاء بالكويت", "اعرض المبيعات الشهرية لآخر ١٢ شهر", "اعرض أعمار الديون"]
HOLD = 2500  # ms each answer stays on screen
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_raw")
with sync_playwright() as p:
    kw = {"executable_path": sys.argv[1]} if len(sys.argv) > 1 else {}
    b = p.chromium.launch(**kw)
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, record_video_dir=out,
                        record_video_size={"width": 1280, "height": 800}, color_scheme="light")
    pg = ctx.new_page()
    pg.goto(URL)
    pg.wait_for_selector(".chip")
    pg.wait_for_timeout(300)
    for i, q in enumerate(QS, 1):
        pg.fill("#q", "")
        pg.keyboard.type(q, delay=14)
        pg.keyboard.press("Enter")
        pg.wait_for_function(f"document.querySelectorAll('.card').length >= {i}")
        pg.wait_for_timeout(HOLD)
        pg.screenshot(path=os.path.join(out, f"final{i}.png"))
    ctx.close()
    b.close()
print(glob.glob(os.path.join(out, "*.webm")))
