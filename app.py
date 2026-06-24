# -*- coding: utf-8 -*-
"""
Ponga Paneer WhatsApp Order Bot
Conversation handler on Render; order execution on GitHub Actions.
"""
import os
import re
import logging
import requests
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

TWILIO_SID    = os.environ['TWILIO_SID']
TWILIO_TOKEN  = os.environ['TWILIO_TOKEN']
TWILIO_FROM   = os.environ.get('TWILIO_FROM', 'whatsapp:+14155238886')
GITHUB_TOKEN  = os.environ['GITHUB_TOKEN']
GITHUB_REPO   = os.environ.get('GITHUB_REPO', 'RohitSharmaLinux/pongaa-bot')

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

sessions = {}

def menu_text():
    lines = ["*PONGA PANEER — ORDER MENU*\n"]
    for i, (_, name, price) in enumerate(PRODUCTS, 1):
        lines.append(f"{i:2}. {name} — Rs {price:.0f}")
    lines.append("\nReply with item number(s):")
    lines.append("  *2*     → Paneer 500g x1")
    lines.append("  *2:2*   → Paneer 500g x2")
    lines.append("  *1,9*   → Paneer 300g + Khoya 500g")
    return "\n".join(lines)

def parse_selection(raw):
    items = {}
    for tok in re.split(r'[,\s]+', raw.strip()):
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
        lines.append(f"  {name} x{qty} = Rs {line:.0f}")
    lines.append(f"\n*Total: Rs {total:.0f}*")
    lines.append("\nReply *YES* to place order or *NO* to cancel")
    return "\n".join(lines)

def selections_to_str(selections):
    return ",".join(f"{idx}:{qty}" for idx, qty in selections.items())

def trigger_github_actions(selections_str, to):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/order.yml/dispatches"
    resp = requests.post(url,
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        },
        json={"ref": "main", "inputs": {"selections": selections_str, "to": to}},
        timeout=10
    )
    log.info(f"GitHub Actions trigger: {resp.status_code}")
    return resp.status_code == 204

def twiml_reply(body):
    resp = MessagingResponse()
    resp.message(body)
    return str(resp)

@app.route('/health')
def health():
    return 'ok'

@app.route('/webhook', methods=['POST'])
def webhook():
    sender  = request.form.get('From', '')
    body    = request.form.get('Body', '').strip()
    log.info(f"From {sender}: {body!r}")

    session = sessions.setdefault(sender, {'state': 'idle', 'pending': {}})
    lower   = body.lower()

    if lower in ('hi', 'hello', 'menu', 'start', 'help', ''):
        session.update(state='menu', pending={})
        return twiml_reply(menu_text())

    if session['state'] == 'confirm':
        if lower in ('yes', 'y', 'ok', 'haan', 'ha', 'confirm'):
            pending = session['pending']
            if not pending:
                return twiml_reply("No order pending.\n\n" + menu_text())
            sel_str = selections_to_str(pending)
            session['state'] = 'ordering'
            ok = trigger_github_actions(sel_str, sender)
            if ok:
                return twiml_reply(
                    "Order queued! 🚀\n"
                    "The bot is filling the form now — you'll get your payment link in about 2 minutes.")
            else:
                session['state'] = 'idle'
                return twiml_reply("Failed to queue order. Please try again.")

        elif lower in ('no', 'n', 'cancel', 'nahi', 'nope'):
            session.update(state='menu', pending={})
            return twiml_reply("Cancelled.\n\n" + menu_text())

    if session['state'] == 'ordering':
        return twiml_reply("Your order is being processed... please wait for the payment link.")

    selections = parse_selection(body)
    if selections:
        session.update(state='confirm', pending=selections)
        return twiml_reply(order_summary(selections))

    session['state'] = 'menu'
    return twiml_reply("Didn't understand that.\n\n" + menu_text())

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
