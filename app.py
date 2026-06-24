# -*- coding: utf-8 -*-
"""
Ponga Paneer WhatsApp Order Bot
Twilio WhatsApp sandbox + Playwright headless + UPI link extraction
"""
import os
import re
import io
import base64
import uuid
import threading
import logging
from flask import Flask, request, send_from_directory
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Config from environment ───────────────────────────────────────────────────
TWILIO_SID        = os.environ['TWILIO_SID']
TWILIO_TOKEN      = os.environ['TWILIO_TOKEN']
TWILIO_FROM       = os.environ.get('TWILIO_FROM', 'whatsapp:+14155238886')
RAZORPAY_URL      = "https://pages.razorpay.com/Pongaa"

# ── Order details (fixed) ─────────────────────────────────────────────────────
CUST_NAME     = "Rohit"
CUST_PHONE    = "9650000595"
CUST_APT      = "Republic of Whitefield"
CUST_DOOR     = "H1649"

# ── Product list ──────────────────────────────────────────────────────────────
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

# ── Session state (in-memory, keyed by WhatsApp number) ──────────────────────
sessions = {}   # phone -> {"state": str, "pending": dict}

# ── Helpers ───────────────────────────────────────────────────────────────────
def menu_text():
    lines = ["*PONGA PANEER — ORDER MENU*\n"]
    for i, (_, name, price) in enumerate(PRODUCTS, 1):
        lines.append(f"{i:2}. {name} — Rs {price:.0f}")
    lines.append("\nReply with item number(s):")
    lines.append("  *2*       → Paneer 500g x1")
    lines.append("  *2:2*     → Paneer 500g x2")
    lines.append("  *1,9*     → Paneer 300g + Khoya 500g")
    return "\n".join(lines)

def parse_selection(raw):
    items = {}
    tokens = re.split(r'[,\s]+', raw.strip())
    for tok in tokens:
        m = re.match(r'^(\d+)(?::(\d+))?$', tok)
        if m:
            num = int(m.group(1)) - 1
            qty = int(m.group(2)) if m.group(2) else 1
            if 0 <= num < len(PRODUCTS):
                items[num] = items.get(num, 0) + qty
    return items

def order_summary(selections):
    lines = ["*Your order:*"]
    total = 0
    for idx, qty in selections.items():
        _, name, price = PRODUCTS[idx]
        line = price * qty
        total += line
        lines.append(f"• {name} x{qty} = Rs {line:.0f}")
    lines.append(f"\n*Total: Rs {total:.0f}*")
    lines.append("\nReply *YES* to place order or *NO* to cancel")
    return "\n".join(lines)

def send_whatsapp(to, body, media_url=None):
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    kwargs = dict(from_=TWILIO_FROM, to=to, body=body)
    if media_url:
        kwargs['media_url'] = [media_url]
    client.messages.create(**kwargs)

def twiml_reply(body):
    resp = MessagingResponse()
    resp.message(body)
    return str(resp)

# ── Playwright order + UPI extraction ────────────────────────────────────────
def run_order(to, selections):
    """Fills Razorpay form headlessly, extracts UPI QR, sends payment link."""
    try:
        from playwright.sync_api import sync_playwright
        log.info(f"Starting order for {to}: {selections}")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                ]
            )
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(RAZORPAY_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            # Set quantities using + buttons
            plus_btns = [b for b in page.query_selector_all("button")
                         if b.inner_text().strip() == "+"]
            for prod_idx, qty in selections.items():
                if prod_idx < len(plus_btns):
                    for _ in range(qty):
                        plus_btns[prod_idx].click()
                        page.wait_for_timeout(120)

            # Fill contact form
            page.fill("input[name='name']", CUST_NAME)
            page.wait_for_timeout(200)
            tel = page.query_selector("input[type='tel']")
            if tel:
                tel.click()
                tel.fill(CUST_PHONE)
            page.wait_for_timeout(200)
            page.fill("input[name='appartment_name']", CUST_APT)
            page.fill("input[name='door_number']", CUST_DOOR)
            page.wait_for_timeout(500)

            # Click Pay button
            for btn in reversed(page.query_selector_all("button")):
                try:
                    txt = btn.inner_text()
                    if "pay" in txt.lower() or "₹" in txt:
                        btn.click()
                        log.info("Clicked Pay button")
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

            # Try to extract UPI link from QR canvas
            upi_link = None
            qr_image_path = None

            canvas_data = None
            try:
                canvas_data = target.evaluate("""() => {
                    const canvas = document.querySelector('canvas');
                    return canvas ? canvas.toDataURL('image/png') : null;
                }""")
            except Exception as e:
                log.warning(f"Canvas eval: {e}")

            if canvas_data and canvas_data.startswith('data:image'):
                try:
                    from PIL import Image
                    from pyzbar.pyzbar import decode as qr_decode
                    b64 = canvas_data.split(',')[1]
                    img = Image.open(io.BytesIO(base64.b64decode(b64)))
                    results = qr_decode(img)
                    if results:
                        upi_link = results[0].data.decode('utf-8')
                        log.info(f"Decoded UPI link: {upi_link}")
                except Exception as e:
                    log.warning(f"QR decode failed: {e}")

                # Save QR image to serve via Flask regardless
                if not upi_link:
                    try:
                        fname = f"qr_{uuid.uuid4().hex[:8]}.png"
                        fpath = f"/tmp/{fname}"
                        b64 = canvas_data.split(',')[1]
                        with open(fpath, 'wb') as f:
                            f.write(base64.b64decode(b64))
                        qr_image_path = fname
                    except Exception as e:
                        log.warning(f"QR save failed: {e}")

            browser.close()

        # Send result to user
        render_host = os.environ.get('RENDER_EXTERNAL_URL', '')

        if upi_link:
            send_whatsapp(to,
                f"Tap to pay:\n{upi_link}\n\nOpens GPay / PhonePe / ICICI directly.")
        elif qr_image_path and render_host:
            media = f"{render_host}/qr/{qr_image_path}"
            send_whatsapp(to,
                "Scan this QR with any UPI app to pay:",
                media_url=media)
        else:
            send_whatsapp(to,
                "Order form filled! Open the link below to pay:\n"
                f"{RAZORPAY_URL}\n"
                "(Your name, phone & address are already filled — just set qty and pay)")

    except Exception as e:
        log.error(f"Order failed: {e}", exc_info=True)
        send_whatsapp(to, f"Something went wrong: {str(e)[:120]}\nPlease try again.")
    finally:
        sessions.pop(to, None)

# ── QR image serving ─────────────────────────────────────────────────────────
@app.route('/qr/<filename>')
def serve_qr(filename):
    return send_from_directory('/tmp', filename)

# ── Health check ──────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    return 'ok'

# ── WhatsApp webhook ──────────────────────────────────────────────────────────
@app.route('/webhook', methods=['POST'])
def webhook():
    sender = request.form.get('From', '')
    body   = request.form.get('Body', '').strip()
    log.info(f"Message from {sender}: {body!r}")

    session = sessions.setdefault(sender, {'state': 'idle', 'pending': {}})

    msg_lower = body.lower()

    # Reset / menu triggers
    if msg_lower in ('hi', 'hello', 'menu', 'start', 'help', ''):
        session['state'] = 'menu'
        session['pending'] = {}
        return twiml_reply(menu_text())

    # Confirm / cancel
    if session['state'] == 'confirm':
        if msg_lower in ('yes', 'y', 'ok', 'confirm', 'haan', 'ha'):
            pending = session['pending']
            if not pending:
                return twiml_reply("No order pending. " + menu_text())
            session['state'] = 'ordering'
            threading.Thread(target=run_order, args=(sender, pending), daemon=True).start()
            return twiml_reply(
                "Order placed! Filling the form now...\n"
                "You'll get your payment link in ~30 seconds.")
        elif msg_lower in ('no', 'n', 'cancel', 'nahi', 'nope'):
            session['state'] = 'menu'
            session['pending'] = {}
            return twiml_reply("Cancelled.\n\n" + menu_text())

    # If order in progress
    if session['state'] == 'ordering':
        return twiml_reply("Your order is being processed... please wait.")

    # Try to parse as item selection
    selections = parse_selection(body)
    if selections:
        session['state'] = 'confirm'
        session['pending'] = selections
        return twiml_reply(order_summary(selections))

    # Fallback
    session['state'] = 'menu'
    return twiml_reply("I didn't understand that.\n\n" + menu_text())


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
