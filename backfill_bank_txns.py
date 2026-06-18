"""
One-time backfill: read bank alert emails from IMAP and create Bank Transactions.
Run with: bench --site erp.madhavintl.localhost execute email_intelligence.backfill_bank_txns.run
"""
import imaplib, ssl, email, re
from html.parser import HTMLParser

IMAP_HOST = "mail.madhavinternational.com.np"
IMAP_USER = "sales@madhavinternational.com.np"
# Password is read from ERPNext Email Account — never hardcode credentials here

BANK_SENDERS = ["alert@ebl.com.np", "txn-alert@nabilbank.com"]


class MLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []
    def handle_data(self, d):
        self.fed.append(d)
    def get_data(self):
        return ' '.join(self.fed)


def _get_body(msg):
    body = ""
    for part in msg.walk():
        ct = part.get_content_type()
        if ct == 'text/plain':
            body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
            break
        elif ct == 'text/html' and not body:
            s = MLStripper()
            s.feed(part.get_payload(decode=True).decode('utf-8', errors='ignore'))
            body = s.get_data()
    return re.sub(r'\s+', ' ', body).strip()


def run():
    import frappe
    from email_intelligence.utils.bank_parser import parse_and_save
    from email_intelligence.utils.config_loader import get_config_for_company

    config = get_config_for_company("Madhav International Pvt Ltd")
    if not config:
        print("ERROR: No Email Intelligence Config found")
        return

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = imaplib.IMAP4_SSL(IMAP_HOST, 993, ssl_context=ctx)
    conn.login(IMAP_USER, IMAP_PASS)
    conn.select('INBOX')

    created = skipped = errors = 0

    for sender_addr in BANK_SENDERS:
        result, ids = conn.search(None, 'FROM', sender_addr)
        email_ids = ids[0].split()
        print(f"Found {len(email_ids)} emails from {sender_addr}")

        for eid in email_ids:
            try:
                res, data = conn.fetch(eid, '(RFC822)')
                msg = email.message_from_bytes(data[0][1])
                body = _get_body(msg)
                name = parse_and_save(sender_addr, body, config)
                if name:
                    created += 1
                    if created % 50 == 0:
                        frappe.db.commit()
                        print(f"  Created {created} so far...")
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                frappe.log_error(str(e), "Email Intelligence Backfill")

    frappe.db.commit()
    conn.logout()
    print(f"\nDone — Created: {created}, Skipped (duplicates): {skipped}, Errors: {errors}")
