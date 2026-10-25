"""Records the real demo UI (1280x800, light theme) typing one Arabic question. Needs: pip install playwright.
Usage: python3 docs/record_demo.py [chromium-executable]  -> docs/_raw/*.webm; encode with docs/encode.sh"""
import glob, os, sys
from playwright.sync_api import sync_playwright

URL = os.environ.get("DEMO_URL", "http://127.0.0.1:8080/")
Q = "وريني أكبر ٥ عملاء بالكويت"
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_raw")
with sync_playwright() as p:
    kw = {"executable_path": sys.argv[1]} if len(sys.argv) > 1 else {}
    b = p.chromium.launch(**kw)
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, record_video_dir=out,
                        record_video_size={"width": 1280, "height": 800}, color_scheme="light")
    pg = ctx.new_page()
    pg.goto(URL)
    pg.wait_for_selector(".chip")
    pg.wait_for_timeout(700)
    pg.click("#q")
    pg.keyboard.type(Q, delay=75)
    pg.wait_for_timeout(350)
    pg.keyboard.press("Enter")
    pg.wait_for_selector(".card")
    pg.wait_for_timeout(4200)
    pg.screenshot(path=os.path.join(out, "final.png"))
    ctx.close()
    b.close()
print(glob.glob(os.path.join(out, "*.webm")))
