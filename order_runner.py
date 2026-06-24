# -*- coding: utf-8 -*-
"""
Runs by GitHub Actions: fills Razorpay form, extracts UPI link from the
/v1/standard_checkout/checkout/order API response (no QR decoding needed).
Usage: python order_runner.py "0:1,8:2" "whatsapp:+919650000595"
"""
import sys, os, re, json, requests as http

RAZORPAY_URL = "https://pages.razorpay.com/Pongaa"
CUST_NAME    = "Rohit"
CUST_PHONE   = "9650000595"
CUST_APT     = "Republic of Whitefield"
CUST_DOOR    = "H1649"

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
            idx, qty = int(m.group(1)), int(m.group(2)) if m.group(2) else 1
            if 0 <= idx < len(PRODUCTS):
                items[idx] = items.get(idx, 0) + qty
    return items

def _twilio_client():
    from twilio.rest import Client
    return Client(os.environ['TWILIO_SID'], os.environ['TWILIO_TOKEN'])

def send_whatsapp(to, body):
    _twilio_client().messages.create(
        from_=os.environ.get('TWILIO_FROM', 'whatsapp:+14155238886'),
        to=to, body=body)
    print(f"Sent: {body[:120]}")

def run(selections, to):
    from playwright.sync_api import sync_playwright

    # We'll capture the UPI link from the checkout/order API response
    captured = {}

    def on_response(response):
        try:
            url = response.url
            # The checkout/order endpoint returns the QR with image_content = UPI link
            if 'standard_checkout/checkout/order' in url and 'x_entity_id' in url:
                try:
                    data = response.json()
                    print(f"checkout/order response: {json.dumps(data)[:400]}")
                    qr = data.get('qr_code') or {}
                    link = qr.get('image_content', '')
                    if link and 'pa=' in link:
                        captured['upi'] = link
                        print(f"UPI link captured: {link}")
                    # Also log order notes to confirm order tagging
                    notes = qr.get('notes') or data.get('notes') or {}
                    if notes:
                        print(f"Order notes: {notes}")
                except Exception as e:
                    print(f"checkout/order parse error: {e}")
        except Exception:
            pass

    print(f"Starting order: {selections}")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox','--disable-setuid-sandbox',
                  '--disable-dev-shm-usage','--disable-gpu'])
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on('response', on_response)

        page.goto(RAZORPAY_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # Set quantities via + buttons
        plus_btns = [b for b in page.query_selector_all("button")
                     if b.inner_text().strip() == "+"]
        print(f"Found {len(plus_btns)} + buttons")
        for idx, qty in selections.items():
            if idx < len(plus_btns):
                for _ in range(qty):
                    plus_btns[idx].click()
                    page.wait_for_timeout(120)
        print("Quantities set.")

        # Fill customer details
        page.fill("input[name='name']", CUST_NAME)
        tel = page.query_selector("input[type='tel']")
        if tel:
            tel.click(); tel.fill(CUST_PHONE)
        page.fill("input[name='appartment_name']", CUST_APT)
        page.fill("input[name='door_number']", CUST_DOOR)
        page.wait_for_timeout(500)
        print("Form filled.")

        # Click Pay — this triggers the checkout/order API call we're intercepting
        for btn in reversed(page.query_selector_all("button")):
            try:
                txt = btn.inner_text()
                if "pay" in txt.lower() or "₹" in txt:
                    btn.click()
                    print(f"Clicked: {txt.strip()[:50]}")
                    break
            except Exception:
                continue

        # Wait for checkout to initialize and the checkout/order API to respond
        print("Waiting for checkout/order API response (up to 12s)...")
        for _ in range(12):
            if 'upi' in captured:
                break
            page.wait_for_timeout(1000)

        upi_link = captured.get('upi')
        print(f"Final UPI link: {upi_link}")
        browser.close()

    # Build order summary
    lines = ["*Order placed!* ✅"]
    total = 0
    for idx, qty in selections.items():
        _, name, price = PRODUCTS[idx]
        line = price * qty
        total += line
        lines.append(f"  {name} x{qty} = Rs {line:.0f}")
    lines.append(f"  *Total: Rs {total:.0f}*")
    msg = "\n".join(lines)

    if upi_link and upi_link.startswith('upi://'):
        msg += f"\n\nTap to pay (opens GPay/PhonePe):\n{upi_link}"
        send_whatsapp(to, msg)
    else:
        msg += f"\n\nPay at:\n{RAZORPAY_URL}"
        send_whatsapp(to, msg)
        print("WARNING: Could not capture UPI link from checkout API.")


if __name__ == "__main__":
    raw_sel = sys.argv[1]
    to_num  = sys.argv[2]
    selections = parse_selections(raw_sel)
    if not selections:
        print("No valid selections."); sys.exit(1)
    run(selections, to_num)
