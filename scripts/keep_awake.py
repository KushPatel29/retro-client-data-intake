"""Keep Streamlit Community Cloud apps awake.

Community Cloud puts an app to sleep after about 12 hours without a real visit.
A plain HTTP ping does not count, so this opens each app in a headless browser,
clicks "Yes, get this app back up!" if the app is asleep, and waits until the
app itself has rendered.

Usage: python scripts/keep_awake.py https://retro-intake-kp.streamlit.app [more urls]
"""
import sys
import time

from playwright.sync_api import sync_playwright

WAKE_TEXT = "get this app back up"
READY_SELECTOR = '[data-testid="stSidebar"], [data-testid="stAppViewContainer"]'


def find_ready(page):
    for frame in page.frames:
        try:
            if frame.query_selector(READY_SELECTOR):
                return True
        except Exception:
            pass
    return False


def app_error(page):
    """Return the first error message the app is showing, if any."""
    for frame in page.frames:
        try:
            el = frame.query_selector('[data-testid="stException"], [data-testid="stAlertContentError"]')
            if el:
                return el.inner_text()[:500]
        except Exception:
            pass
    return None


def click_wake(page):
    for frame in page.frames:
        try:
            btn = frame.get_by_role("button", name=lambda n: n and WAKE_TEXT in n.lower())
            if btn.count():
                btn.first.click()
                return True
        except Exception:
            try:
                el = frame.get_by_text(WAKE_TEXT, exact=False)
                if el.count():
                    el.first.click()
                    return True
            except Exception:
                pass
    return False


def visit(browser, url, timeout_s=300):
    page = browser.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    woke = False
    start = time.time()
    while time.time() - start < timeout_s:
        if find_ready(page):
            page.wait_for_timeout(8_000)  # let the page finish rendering before the proof screenshot
            err = app_error(page)
            page.screenshot(path="keep_awake_last.png", full_page=True)
            if err:
                print(f"FAIL  {url}  app shows an error: {err}")
                page.close()
                return False
            print(f"OK    {url}  ({'woken up' if woke else 'already awake'}, {time.time() - start:.0f}s)")
            page.close()
            return True
        if not woke and click_wake(page):
            woke = True
            print(f"SLEEP {url}  clicked wake button")
        page.wait_for_timeout(5_000)
    print(f"FAIL  {url}  app did not render within {timeout_s}s")
    page.screenshot(path="keep_awake_failure.png", full_page=True)
    page.close()
    return False


def main(urls):
    ok = True
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for url in urls:
            try:
                ok = visit(browser, url) and ok
            except Exception as exc:  # keep going for the other apps
                print(f"ERROR {url}  {exc}")
                ok = False
        browser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["https://retro-intake-kp.streamlit.app"]))
