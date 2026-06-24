# -*- coding: utf-8 -*-
"""
Runs by GitHub Actions: fills Razorpay form, extracts UPI link, notifies user.
Usage: python order_runner.py "0:1,8:2" "whatsapp:+919650000595"
"""
import sys, os, re, io, base64, json, requests as http

RAZORPAY_URL = "https://pages.razorpay.com/Pongaa"
CUST_NAME    = "Rohit"
CUST_PHONE   = "9650000595"
CUST_APT     = "Republic of Whitefield"
CUST_DOOR    = "H1649"
CUST_UPI     = "mailbox.rohitsharma@okicici"

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

def send_whatsapp_media(to, body, media_url):
    _twilio_client().messages.create(
        from_=os.environ.get('TWILIO_FROM', 'whatsapp:+14155238886'),
        to=to, body=body, media_url=[media_url])
    print(f"Sent media: {media_url}")

def decode_qr_from_bytes(img_bytes):
    """Try pyzbar first, fall back to OpenCV."""
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode as qr_decode
        img = Image.open(io.BytesIO(img_bytes))
        results = qr_decode(img)
        if results:
            link = results[0].data.decode('utf-8')
            print(f"pyzbar decoded: {link}")
            return link
    except Exception as e:
        print(f"pyzbar failed: {e}")

    try:
        import cv2, numpy as np
        arr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        if data:
            print(f"OpenCV decoded: {data}")
            return data
    except Exception as e:
        print(f"OpenCV failed: {e}")
    return None

def try_enter_upi_in_checkout(page):
    """
    Try to enter CUST_UPI in the Razorpay checkout iframe.
    Returns True if the UPI ID was submitted (collect request sent).
    """
    print("Trying to enter UPI ID in Razorpay checkout...")
    for frame in page.frames:
        if not frame.url or 'razorpay' not in frame.url:
            continue
        try:
            # Try to find UPI input field
            upi_input = None
            for sel in [
                'input[placeholder*="UPI"]',
                'input[placeholder*="upi"]',
                'input[placeholder*="VPA"]',
                'input[placeholder*="vpa"]',
                'input[name="vpa"]',
                'input[type="text"]',
            ]:
                try:
                    el = frame.locator(sel).first
                    if el.is_visible(timeout=1500):
                        upi_input = el
                        print(f"UPI input found with selector: {sel}")
                        break
                except Exception:
                    pass

            if upi_input:
                upi_input.clear()
                upi_input.fill(CUST_UPI)
                page.wait_for_timeout(600)
                # Click verify/pay button
                for btn_sel in [
                    'button:has-text("Verify")',
                    'button:has-text("Pay")',
                    'button[type="submit"]',
                    'button:has-text("Submit")',
                ]:
                    try:
                        btn = frame.locator(btn_sel).first
                        if btn.is_visible(timeout=1500):
                            btn.click()
                            print(f"Clicked UPI submit: {btn_sel}")
                            return True
                    except Exception:
                        pass
        except Exception as e:
            print(f"Frame UPI entry error ({frame.url[:50]}): {e}")
    return False

def scan_canvas_qr(page):
    """Scan all frames for a canvas element and decode QR from it."""
    from PIL import Image
    for frame in page.frames:
        try:
            canvas = frame.query_selector("canvas")
            if not canvas:
                continue
            qr_bytes = canvas.screenshot()
            print(f"Canvas found in frame {frame.url[:60]}: {len(qr_bytes)} bytes")
            img = Image.open(io.BytesIO(qr_bytes))
            # Upscale 4x for reliable QR decode
            big = img.resize((img.width * 4, img.height * 4), Image.LANCZOS)
            buf = io.BytesIO()
            big.save(buf, format='PNG')
            decoded = decode_qr_from_bytes(buf.getvalue())
            if decoded and 'pa=' in decoded:
                return decoded
            elif decoded:
                print(f"Decoded but no pa=: {decoded}")
        except Exception as fe:
            print(f"Frame canvas error: {fe}")
    return None

def run(selections, to):
    from playwright.sync_api import sync_playwright

    upi_from_network = []

    def on_response(response):
        try:
            if not any(x in response.url for x in ['razorpay', 'checkout']):
                return
            text = response.text()
            found = re.findall(r'upi://pay[^\s\'"\\>]+', text)
            for f in found:
                if 'pa=' in f and '${' not in f:
                    print(f"Network UPI found: {f}")
                    upi_from_network.append(f)
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

        # Capture postMessage events from Razorpay iframe
        page.evaluate("""() => {
            window.__rzp_messages__ = [];
            window.addEventListener('message', function(e) {
                try { window.__rzp_messages__.push(JSON.stringify(e.data)); }
                catch(err) { window.__rzp_messages__.push(String(e.data)); }
            }, true);
        }""")

        # Set quantities
        plus_btns = [b for b in page.query_selector_all("button")
                     if b.inner_text().strip() == "+"]
        for idx, qty in selections.items():
            if idx < len(plus_btns):
                for _ in range(qty):
                    plus_btns[idx].click()
                    page.wait_for_timeout(120)
        print("Quantities set.")

        # Fill form fields
        page.fill("input[name='name']", CUST_NAME)
        tel = page.query_selector("input[type='tel']")
        if tel:
            tel.click(); tel.fill(CUST_PHONE)
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
                    print(f"Clicked pay: {txt.strip()[:40]}")
                    break
            except Exception:
                continue

        # Wait for Razorpay checkout to fully load and render
        print("Waiting 5s for checkout to open...")
        page.wait_for_timeout(5000)

        # Log postMessage state so far
        try:
            msgs = page.evaluate("() => window.__rzp_messages__ || []")
            print(f"postMessage after 5s: {len(msgs)} events")
            for i, m in enumerate(msgs):
                print(f"  msg[{i}]: {str(m)[:300]}")
        except Exception as e:
            print(f"postMessage check failed: {e}")

        # Log all frame URLs so we can see checkout structure
        print(f"Frames loaded: {len(page.frames)}")
        for f in page.frames:
            print(f"  frame: {f.url[:80]}")

        # Try to enter UPI ID directly in checkout (most reliable — triggers collect request)
        upi_submitted = try_enter_upi_in_checkout(page)

        if upi_submitted:
            # Razorpay will send a UPI collect request to CUST_UPI
            print("UPI collect request sent to", CUST_UPI)
            page.wait_for_timeout(3000)
        else:
            # Wait more for QR canvas to render
            print("UPI entry skipped — waiting 7 more seconds for QR canvas...")
            page.wait_for_timeout(7000)

        upi_link = None

        # Scan canvas in all frames
        try:
            upi_link = scan_canvas_qr(page)
            if upi_link:
                print(f"Canvas QR decoded: {upi_link}")
        except Exception as e:
            print(f"Canvas scan error: {e}")

        # Network interception fallback
        if not upi_link and upi_from_network:
            upi_link = upi_from_network[0]
            print(f"Network UPI: {upi_link}")

        # Frame HTML scan fallback
        if not upi_link:
            for frame in page.frames:
                try:
                    src = frame.evaluate("() => document.documentElement.innerHTML")
                    found = re.findall(r'upi://pay\?[^\s\'"\\<>]+', src)
                    for f in found:
                        if 'pa=' in f and '${' not in f:
                            upi_link = f.rstrip('",}]\\')
                            print(f"Frame HTML UPI: {upi_link}")
                            break
                except Exception:
                    pass
                if upi_link:
                    break

        # Screenshot fallback — upload to Flask and send as MMS
        qr_image_url = None
        if not upi_link:
            try:
                from PIL import Image
                shot = page.screenshot(full_page=False)
                img = Image.open(io.BytesIO(shot))
                w, h = img.size
                qr_crop = img.crop((w // 2, 0, w, h))
                buf = io.BytesIO()
                qr_crop.save(buf, format='PNG')
                render_url = os.environ.get('RENDER_URL', 'https://pongaa-bot.onrender.com')
                resp = http.post(
                    f"{render_url}/store-qr",
                    json={"image": base64.b64encode(buf.getvalue()).decode()},
                    timeout=15
                )
                if resp.ok:
                    qr_image_url = resp.json().get('url')
                    print(f"QR image URL: {qr_image_url}")
                else:
                    print(f"store-qr failed: {resp.status_code}")
            except Exception as e:
                print(f"Screenshot upload failed: {e}")

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

    if upi_submitted and not upi_link:
        # Collect request was sent to the user's UPI app
        msg += (f"\n\nPayment request of Rs {total:.0f} sent to your GPay/PhonePe.\n"
                f"Open your UPI app and *approve the pending request* to pay. ✅")
        send_whatsapp(to, msg)
    elif upi_link and upi_link.startswith('upi://'):
        msg += f"\n\nTap to pay (opens GPay/PhonePe):\n{upi_link}"
        send_whatsapp(to, msg)
    elif qr_image_url:
        send_whatsapp(to, msg + "\n\nScan the QR below with any UPI app 👇")
        send_whatsapp_media(to, "Scan to pay:", qr_image_url)
    else:
        msg += f"\n\nOpen to pay:\n{RAZORPAY_URL}"
        send_whatsapp(to, msg)
        print("WARNING: Could not get UPI link or QR.")


if __name__ == "__main__":
    raw_sel = sys.argv[1]
    to_num  = sys.argv[2]
    selections = parse_selections(raw_sel)
    if not selections:
        print("No valid selections."); sys.exit(1)
    run(selections, to_num)
