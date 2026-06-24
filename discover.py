# -*- coding: utf-8 -*-
"""
One-time discovery run.
Loads the Razorpay payment page, clicks Pay with 1 unit of item 0,
and logs ALL network requests+responses so we can find the exact API call.
Run via GitHub Actions: python discover.py
"""
import re, json

RAZORPAY_URL = "https://pages.razorpay.com/Pongaa"

def run():
    from playwright.sync_api import sync_playwright

    requests_log  = []
    responses_log = []

    def on_request(req):
        try:
            body = req.post_data
            requests_log.append({
                'method': req.method,
                'url':    req.url,
                'body':   body[:800] if body else None,
                'headers': {k: v for k, v in req.headers.items()
                            if k.lower() not in ('cookie',)},
            })
        except Exception:
            pass

    def on_response(resp):
        try:
            text = resp.text()
            responses_log.append({
                'status': resp.status,
                'url':    resp.url,
                'body':   text[:1200],
            })
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox','--disable-setuid-sandbox',
                  '--disable-dev-shm-usage','--disable-gpu'])
        page = browser.new_page(viewport={"width":1280,"height":900})
        page.on('request',  on_request)
        page.on('response', on_response)

        print("Loading payment page...")
        page.goto(RAZORPAY_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # ── Extract embedded config from page HTML ──────────────────────
        html = page.content()

        print("\n=== EMBEDDED CONFIG TOKENS ===")
        tokens = set(re.findall(
            r'(rzp_(?:live|test)_\w+|pl_\w+|plink_\w+|pay_page_\w+)', html))
        for t in tokens:
            print(f"  {t}")

        # Next.js __NEXT_DATA__
        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                       html, re.DOTALL)
        if nd:
            print("\n=== __NEXT_DATA__ (first 3000 chars) ===")
            print(nd.group(1)[:3000])
        else:
            # Look for any big JSON blob in script tags
            blobs = re.findall(r'<script[^>]*>(window\.\w+\s*=\s*{.*?})\s*;?\s*</script>',
                               html, re.DOTALL)
            for b in blobs[:3]:
                print(f"\n=== SCRIPT BLOB (500 chars) ===\n{b[:500]}")

        # ── Fill minimal form ───────────────────────────────────────────
        plus_btns = [b for b in page.query_selector_all("button")
                     if b.inner_text().strip() == "+"]
        if plus_btns:
            plus_btns[0].click()
            page.wait_for_timeout(200)
            print(f"\nClicked + on item 0. Total + buttons found: {len(plus_btns)}")

        page.fill("input[name='name']", "Rohit")
        tel = page.query_selector("input[type='tel']")
        if tel:
            tel.click(); tel.fill("9650000595")
        try:
            page.fill("input[name='appartment_name']", "Republic of Whitefield")
            page.fill("input[name='door_number']", "H1649")
        except Exception as e:
            print(f"Form field error: {e}")
        page.wait_for_timeout(500)
        print("Form filled.")

        # ── Note request count before clicking Pay ──────────────────────
        snap = len(requests_log)
        print(f"\nRequests so far (page load): {snap}")

        for btn in reversed(page.query_selector_all("button")):
            try:
                txt = btn.inner_text()
                if "pay" in txt.lower() or "₹" in txt:
                    btn.click()
                    print(f"Clicked Pay: {txt.strip()[:50]}")
                    break
            except Exception:
                continue

        page.wait_for_timeout(8000)
        print("\nWaited 8s after clicking Pay.")

        # ── Print ALL requests made after clicking Pay ──────────────────
        new_reqs = requests_log[snap:]
        print(f"\n{'='*60}")
        print(f"NEW REQUESTS AFTER PAY: {len(new_reqs)}")
        print('='*60)
        for i, r in enumerate(new_reqs):
            print(f"\n[REQ {i}] {r['method']} {r['url']}")
            if r['headers']:
                for k, v in list(r['headers'].items())[:8]:
                    print(f"  H: {k}: {v}")
            if r['body']:
                print(f"  BODY: {r['body']}")

        # ── Print ALL responses after clicking Pay (matching new reqs) ──
        new_urls = {r['url'] for r in new_reqs}
        new_resps = [r for r in responses_log if r['url'] in new_urls]
        print(f"\n{'='*60}")
        print(f"RESPONSES FOR THOSE REQUESTS: {len(new_resps)}")
        print('='*60)
        for i, r in enumerate(new_resps):
            print(f"\n[RESP {i}] {r['status']} {r['url']}")
            print(f"  BODY: {r['body']}")

        # ── Also log postMessages ───────────────────────────────────────
        try:
            msgs = page.evaluate("() => window.__rzp_messages__ || []")
            print(f"\n=== postMessages: {len(msgs)} ===")
            for i, m in enumerate(msgs):
                print(f"  [{i}] {str(m)[:400]}")
        except Exception:
            pass

        browser.close()
        print("\nDone.")

if __name__ == "__main__":
    # Inject postMessage listener before page load is not possible,
    # so we add it after load
    run()
