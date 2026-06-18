import re
import frappe
from frappe.utils import nowdate

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _extract_sender_info(sender, body):
    display_name = ""
    m = re.match(r'"?([^<"]+)"?\s*<', sender)
    if m:
        display_name = m.group(1).strip()

    lines = [l.strip() for l in body.strip().splitlines() if l.strip()]
    sig_lines = lines[-8:] if len(lines) >= 8 else lines

    contact_name = display_name or ""
    company = ""

    for line in sig_lines:
        if re.match(r"^\d+\.", line) or line.lower().startswith("dear") or "@" in line:
            continue
        if not contact_name and len(line.split()) <= 4 and not any(c.isdigit() for c in line):
            contact_name = line
        if re.search(r"(pvt\.?\s*ltd|ltd\.?|laboratories|lab|company|corp|inc)", line, re.IGNORECASE):
            company = line

    return contact_name, company


def _extract_items(body):
    items = []
    for m in re.finditer(r"\d+\.\s+(.+?)(?:\n|$)", body):
        raw = m.group(1).strip()
        qty_m = re.search(r"(\d+)\s*(\w+)\s*$", raw)
        if qty_m:
            item_name = raw[: qty_m.start()].strip()
            qty = int(qty_m.group(1))
            uom = qty_m.group(2)
        else:
            item_name = raw
            qty = 1
            uom = "Nos"
        if item_name:
            items.append({"item_name": item_name, "qty": qty, "uom": uom})
    return items


def parse_and_save(sender, subject, body, config):
    contact_name, company = _extract_sender_info(sender, body)

    m = EMAIL_RE.search(sender)
    email_addr = m.group(0) if m else ""

    existing_lead = frappe.db.get_value("Lead", {"email_id": email_addr}, "name") if email_addr else None

    if not existing_lead:
        lead = frappe.get_doc({
            "doctype": "Lead",
            "lead_name": contact_name or company or email_addr,
            "company_name": company,
            "email_id": email_addr,
            "source": "Email",
            "status": "Open",
        })
        lead.insert(ignore_permissions=True)
        lead_name = lead.name
    else:
        lead_name = existing_lead

    items = _extract_items(body)
    opp = frappe.get_doc({
        "doctype": "Opportunity",
        "opportunity_from": "Lead",
        "party_name": lead_name,
        "opportunity_type": "Sales",
        "status": "Open",
        "transaction_date": nowdate(),
        "source": "Email",
        "notes": f"Subject: {subject}\n\n{body[:1000]}",
    })
    for it in items:
        opp.append("items", {
            "item_name": it["item_name"],
            "qty": it["qty"],
            "uom": it["uom"],
        })
    opp.insert(ignore_permissions=True)
    return opp.name
