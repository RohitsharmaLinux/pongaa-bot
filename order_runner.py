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
    # pyzbar (more reliable)
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

    # OpenCV fallback
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

def run(selections, to):
    from playwright.sync_api import sync_playwright

    # Collect UPI links intercepted from network responses
    upi_from_network = []

    def on_response(response):
        try:
            if not any(x in response.url for x in ['razorpay', 'checkout']):
                return
            text = response.text()
            found = re.findall(r'upi://pay[^\s\'"\\>]+', text)
            for f in found:
                # Skip JS template literals — real links always have pa= (payee VPA)
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

        # Listen for all postMessage events (Razorpay iframe → parent)
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

        # Fill form
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

        # Wait for Razorpay checkout to load
        # Inject postMessage listener BEFORE clicking pay (moved below)
        # We already have the listener active from before clicking Pay
        # Wait for Razorpay checkout to fully render the QR
        page.wait_for_timeout(8000)

        upi_link = None

        # Method 1: screenshot the QR canvas element directly via frame_locator
        # Playwright can screenshot cross-origin iframe elements natively
        try:
            from PIL import Image
            rz_iframe = page.frame_locator("iframe").first
            canvas = rz_iframe.locator("canvas").first
            canvas.wait_for(timeout=5000)
            qr_bytes = canvas.screenshot()
            print(f"Canvas screenshot: {len(qr_bytes)} bytes")
            # Upscale 4x for reliable QR decode
            img = Image.open(io.BytesIO(qr_bytes))
            big = img.resize((img.width * 4, img.height * 4), Image.LANCZOS)
            buf = io.BytesIO()
            big.save(buf, format='PNG')
            upi_link = decode_qr_from_bytes(buf.getvalue())
            if upi_link:
                print(f"Canvas element QR decoded: {upi_link}")
            else:
                print("Canvas screenshot taken but QR decode failed")
        except Exception as e:
            print(f"Canvas element screenshot failed: {e}")

        # Method 2: postMessage events
        if not upi_link:
            try:
                msgs = page.evaluate("() => window.__rzp_messages__ || []")
                print(f"postMessage events: {len(msgs)}")
                for i, m in enumerate(msgs):
                    print(f"  msg[{i}]: {str(m)[:300]}")
                    found = re.findall(r'upi://[^\s\'"\\>]+', str(m))
                    for f in found:
                        if 'pa=' in f and '${' not in f:
                            upi_link = f.rstrip('",}]\\')
                            print(f"postMessage UPI: {upi_link}")
                            break
                    if upi_link:
                        break
            except Exception as e:
                print(f"postMessage check failed: {e}")

        # Method 3: network interception
        if not upi_link and upi_from_network:
            upi_link = upi_from_network[0]
            print(f"Network UPI: {upi_link}")

        # Method 4: scan frame HTML for UPI link
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

        # Method 5: upload cropped QR area as image to Flask → send as WhatsApp MMS
        if not upi_link:
            try:
                from PIL import Image
                shot = page.screenshot(full_page=False)
                img = Image.open(io.BytesIO(shot))
                w, h = img.size
                # Crop to right half where Razorpay QR is shown
                qr_crop = img.crop((w//2, 0, w, h))
                buf = io.BytesIO()
                qr_crop.save(buf, format='PNG')
                shot = buf.getvalue()
                render_url = os.environ.get('RENDER_URL', 'https://pongaa-bot.onrender.com')
                resp = http.post(
                    f"{render_url}/store-qr",
                    json={"image": base64.b64encode(shot).decode()},
                    timeout=15
                )
                if resp.ok:
                    qr_image_url = resp.json().get('url')
                    print(f"QR image URL: {qr_image_url}")
                else:
                    qr_image_url = None
                    print(f"store-qr failed: {resp.status_code}")
            except Exception as e:
                qr_image_url = None
                print(f"Screenshot upload failed: {e}")
        else:
            qr_image_url = None

        browser.close()

    # Build order summary message
    lines = ["*Order placed!* ✅"]
    total = 0
    for idx, qty in selections.items():
        _, name, price = PRODUCTS[idx]
        line = price * qty
        total += line
        lines.append(f"  {name} x{qty} = Rs {line:.0f}")
    lines.append(f"  Total: Rs {total:.0f}")

    msg = "\n".join(lines)

    if upi_link and upi_link.startswith('upi://'):
        msg += f"\n\nTap to pay (opens GPay/PhonePe):\n{upi_link}"
        send_whatsapp(to, msg)
    elif qr_image_url:
        # Send order summary as text, then QR image separately
        send_whatsapp(to, msg + "\n\nScan the QR below with any UPI app 👇")
        send_whatsapp_media(to, "Scan to pay:", qr_image_url)
    else:
        msg += f"\n\nOpen to pay:\n{RAZORPAY_URL}"
        send_whatsapp(to, msg)
        print("WARNING: Could not get UPI link or QR image.")


if __name__ == "__main__":
    raw_sel = sys.argv[1]
    to_num  = sys.argv[2]
    selections = parse_selections(raw_sel)
    if not selections:
        print("No valid selections."); sys.exit(1)
    run(selections, to_num)
