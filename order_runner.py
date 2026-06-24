# -*- coding: utf-8 -*-
"""
Runs by GitHub Actions: fills Razorpay form, extracts UPI link, notifies user.
Usage: python order_runner.py "0:1,8:2" "whatsapp:+919650000595"
"""
import sys
import os
import re
import io
import base64

RAZORPAY_URL  = "https://pages.razorpay.com/Pongaa"
CUST_NAME     = "Rohit"
CUST_PHONE    = "9650000595"
CUST_APT      = "Republic of Whitefield"
CUST_DOOR     = "H1649"

PRODUCTS = [
    (0,  "Malai Paneer 300g",   213.00),
    (1,  "Malai Paneer 500g",   325.00),
    (2,  "Malai Paneer 750g",   471.00),
    (3,  "Malai Paneer 1KG",    626.00),
    (4,  "Heeng 50g",           116.00),
    (5,  "White Butter 450g",   415.00),
    (6,  "White Butter 200g",   200.88),
    (7,  "Khoya 250g",          175.00),
    (8,  "Khoya 500g",          350.00),
    (9,  "Channa Sattu 500g",   174.00),
    (10, "Chaap Sticks 5pcs",   165.20),
    (11, "Tofu 200g",           110.00),
    (12, "Cow Ghee 490ml",      550.93),
    (13, "Jumbo Makhana 150g",  340.00),
    (14, "Kolkata Mudi 500g",   211.00),
]

def parse_selections(raw):
    items = {}
    for tok in re.split(r'[,\s]+', raw.strip()):
        m = re.match(r'^(\d+)(?::(\d+))?$', tok)
        if m:
            idx = int(m.group(1))
            qty = int(m.group(2)) if m.group(2) else 1
            if 0 <= idx < len(PRODUCTS):
                items[idx] = items.get(idx, 0) + qty
    return items

def send_whatsapp(to, body):
    from twilio.rest import Client
    client = Client(os.environ['TWILIO_SID'], os.environ['TWILIO_TOKEN'])
    client.messages.create(
        from_=os.environ.get('TWILIO_FROM', 'whatsapp:+14155238886'),
        to=to,
        body=body
    )
    print(f"Sent to {to}: {body[:80]}")

def run(selections, to):
    from playwright.sync_api import sync_playwright

    print(f"Starting order: {selections}")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(RAZORPAY_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Set quantities
        plus_btns = [b for b in page.query_selector_all("button") if b.inner_text().strip() == "+"]
        for prod_idx, qty in selections.items():
            if prod_idx < len(plus_btns):
                for _ in range(qty):
                    plus_btns[prod_idx].click()
                    page.wait_for_timeout(120)
        print("Quantities set.")

        # Fill form
        page.fill("input[name='name']", CUST_NAME)
        tel = page.query_selector("input[type='tel']")
        if tel:
            tel.click()
            tel.fill(CUST_PHONE)
        page.fill("input[name='appartment_name']", CUST_APT)
        page.fill("input[name='door_number']", CUST_DOOR)
        page.wait_for_timeout(500)
        print("Form filled.")

        # Click Pay
        for btn in reversed(page.query_selector_all("button")):
            try:
                txt = btn.inner_text()
                if "pay" in txt.lower() or "₹" in txt:
                    btn.click()
                    print(f"Clicked: {txt.strip()[:40]}")
                    break
            except Exception:
                continue

        page.wait_for_timeout(4000)

        # Find Razorpay iframe
        rz_frame = None
        for frame in page.frames:
            if "razorpay" in frame.url.lower() and frame != page.main_frame:
                rz_frame = frame
                break
        if not rz_frame and len(page.frames) > 1:
            rz_frame = page.frames[1]

        target = rz_frame or page
        upi_link = None

        # Extract QR canvas and decode
        try:
            canvas_data = target.evaluate("""() => {
                const c = document.querySelector('canvas');
                return c ? c.toDataURL('image/png') : null;
            }""")
            if canvas_data and canvas_data.startswith('data:image'):
                import cv2, numpy as np
                b64 = canvas_data.split(',')[1]
                arr = np.frombuffer(base64.b64decode(b64), np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                data, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
                if data:
                    upi_link = data
                    print(f"UPI link: {upi_link}")
        except Exception as e:
            print(f"QR decode error: {e}")

        browser.close()

    # Build order summary
    lines = ["*Order placed!* ✅"]
    total = 0
    for idx, qty in selections.items():
        _, name, price = PRODUCTS[idx]
        line = price * qty
        total += line
        lines.append(f"  {name} x{qty} = Rs {line:.0f}")
    lines.append(f"  Total: Rs {total:.0f}")

    if upi_link:
        lines.append(f"\nTap to pay (opens UPI app):\n{upi_link}")
    else:
        lines.append(f"\nOpen to pay:\n{RAZORPAY_URL}")
        lines.append("(Form is pre-filled, just tap Pay)")

    send_whatsapp(to, "\n".join(lines))

if __name__ == "__main__":
    raw_sel = sys.argv[1]   # e.g. "1:2,9:1"
    to_num  = sys.argv[2]   # e.g. "whatsapp:+919650000595"
    selections = parse_selections(raw_sel)
    if not selections:
        print("No valid selections parsed.")
        sys.exit(1)
    run(selections, to_num)
