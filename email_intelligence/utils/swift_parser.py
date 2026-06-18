import re
import frappe
from frappe.utils import getdate, nowdate

# Patterns for HTML table rows: <td>Field</td><td><strong>Value</strong></td>
_FIELD_RE = re.compile(
    r"<td[^>]*>\s*([^<]+?)\s*</td>\s*<td[^>]*>\s*(?:<[^>]+>)?\s*([^<]+?)\s*(?:</[^>]+>)?\s*</td>",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(r"([A-Z]{2,3})\s*([\d,]+\.?\d*)")
_DATE_FORMATS = ["%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"]

# Debit alert for SWIFT uses: REMITTANCE ID:[TT_NUMBER]:LODGE AND
_REMITTANCE_RE = re.compile(r"REMITTANCE ID:\[([^\]]+)\]", re.IGNORECASE)


_PIPE_KV_RE = re.compile(r"\|\s*([^|]{3,40}?)\s*\|\s*([^|]{1,60}?)(?=\s*\||\s*$)")


def _parse_html_table(body):
    """
    Parse key/value pairs from either raw HTML table or pipe-delimited text.
    EBL sends HTML; after TextExtract the body becomes pipe-delimited text.
    """
    data = {}
    if "<td" in body.lower():
        for m in _FIELD_RE.finditer(body):
            data[m.group(1).strip()] = m.group(2).strip()
    else:
        # Pipe-delimited text: ... | Value Date | 01-JUN-2026 | TT Number | 0100TTFC008984 | ...
        parts = [p.strip() for p in body.split("|") if p.strip()]
        for i in range(len(parts) - 1):
            key = parts[i]
            val = parts[i + 1]
            # Keys are short labels; values are short data strings
            if 2 <= len(key.split()) <= 5 and len(val) <= 50 and not val.startswith("Dear"):
                data[key] = val
    return data


def _parse_date(raw):
    from datetime import datetime
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return nowdate()


def _find_existing_transaction(tt_number, prefer_large=True):
    """
    Find the Bank Transaction already created from the NPR debit alert.
    The main SWIFT debit (account 200287 / LODGE AND) is the large withdrawal.
    The SWIFT charge (account 201393 / SWIFT CHA) is a small debit — skip it.
    """
    if not tt_number:
        return None
    # Get all matching by reference_number, pick the one with the largest withdrawal
    results = frappe.db.get_all(
        "Bank Transaction",
        filters={"reference_number": tt_number},
        fields=["name", "withdrawal", "deposit"],
        order_by="withdrawal desc",
    )
    if results:
        # Return the largest withdrawal (main LODGE payment, not the SWIFT CHA charge)
        return results[0].name
    # Fallback: match by description containing TT number
    results = frappe.db.get_all(
        "Bank Transaction",
        filters=[["description", "like", f"%{tt_number}%"]],
        fields=["name", "withdrawal"],
        order_by="withdrawal desc",
        limit=1,
    )
    return results[0].name if results else None


def _find_supplier(beneficiary_name):
    if not beneficiary_name:
        return None
    name = frappe.db.get_value("Supplier", {"supplier_name": beneficiary_name}, "name")
    if name:
        return name
    for word in [w for w in beneficiary_name.split() if len(w) > 3]:
        name = frappe.db.get_value(
            "Supplier",
            {"supplier_name": ["like", f"%{word}%"]},
            "name",
        )
        if name:
            return name
    return None


def parse_and_save(sender, subject, body, config):
    """
    Process SWIFT / RTGS notification email.

    SWIFT flow:
      1. Debit alert for account 200287 creates a Bank Transaction (NPR amount).
         Remarks: REMITTANCE ID:[TT_NUMBER]:LODGE AND
      2. This SWIFT email arrives with FCY amount + beneficiary.
      Strategy: find the existing Bank Transaction and enrich its description.
      If not found yet, create a placeholder — it will be enriched when the debit alert arrives.

    RTGS flow:
      Similar but Application Number / UTR is used as reference.
    """
    data = _parse_html_table(body)

    is_rtgs = "rtgs" in subject.lower() or "INR Amount" in data
    if is_rtgs:
        return _handle_rtgs(data, subject, config)
    else:
        return _handle_swift(data, subject, config)


def _handle_swift(data, subject, config):
    tt_number = data.get("TT Number", "")
    value_date_raw = data.get("Value Date", "")
    fcy_raw = data.get("FCY Amount", "")
    beneficiary = data.get("Beneficiary Name", "")

    txn_date = _parse_date(value_date_raw) if value_date_raw else nowdate()

    currency, fcy_amount = "", 0.0
    m = _AMOUNT_RE.search(fcy_raw)
    if m:
        currency, fcy_amount = m.group(1), float(m.group(2).replace(",", ""))

    supplier = _find_supplier(beneficiary)
    enriched_desc = f"SWIFT to {beneficiary} | {currency} {fcy_amount:,.2f} | Ref: {tt_number}"

    # Try to find the NPR debit Bank Transaction already created from the debit alert
    existing = _find_existing_transaction(tt_number)
    if existing:
        frappe.db.set_value(
            "Bank Transaction",
            existing,
            "description",
            enriched_desc[:200],
            update_modified=False,
        )
        frappe.logger().info(
            f"Email Intelligence: Enriched Bank Transaction {existing} with SWIFT details"
        )
        return existing

    # Debit alert hasn't arrived yet — check for duplicate placeholder
    if frappe.db.exists("Bank Transaction", {"reference_number": tt_number}):
        frappe.logger().info(
            f"Email Intelligence: SWIFT placeholder already exists for {tt_number}"
        )
        return None

    # Create a placeholder — the debit alert will later be matched by reference_number
    doc = frappe.get_doc({
        "doctype": "Bank Transaction",
        "date": txn_date,
        "withdrawal": 0.0,
        "deposit": 0.0,
        "description": enriched_desc[:200],
        "bank_account": config.swift_bank_account,
        "status": "Unreconciled",
        "reference_number": tt_number,
    })
    doc.insert(ignore_permissions=True)
    frappe.logger().info(
        f"Email Intelligence: SWIFT placeholder {doc.name} created — awaiting NPR debit alert"
    )
    return doc.name


def _handle_rtgs(data, subject, config):
    app_number = data.get("Application Number", "")
    app_date_raw = data.get("Application Date", "")
    inr_raw = data.get("INR Amount", "")
    beneficiary = data.get("Beneficiary Name", "")
    utr = data.get("UTR No.", "") or data.get("UTR No", "")

    txn_date = _parse_date(app_date_raw) if app_date_raw else nowdate()

    inr_amount = 0.0
    m = re.search(r"[\d,]+\.?\d*", inr_raw)
    if m:
        inr_amount = float(m.group(0).replace(",", ""))

    reference = utr or app_number
    supplier = _find_supplier(beneficiary)
    enriched_desc = (
        f"RTGS to {beneficiary} | INR {inr_amount:,.2f}"
        + (f" | UTR: {utr}" if utr else "")
        + (f" | App: {app_number}" if app_number else "")
    )

    # Try to find existing Bank Transaction by UTR or app number in description
    existing = _find_existing_transaction(utr) or _find_existing_transaction(app_number)
    if existing:
        frappe.db.set_value(
            "Bank Transaction",
            existing,
            "description",
            enriched_desc[:200],
            update_modified=False,
        )
        frappe.logger().info(
            f"Email Intelligence: Enriched Bank Transaction {existing} with RTGS details"
        )
        return existing

    if reference and frappe.db.exists("Bank Transaction", {"reference_number": reference}):
        return None

    doc = frappe.get_doc({
        "doctype": "Bank Transaction",
        "date": txn_date,
        "withdrawal": 0.0,
        "deposit": 0.0,
        "description": enriched_desc[:200],
        "bank_account": config.swift_bank_account,
        "status": "Unreconciled",
        "reference_number": reference,
    })
    doc.insert(ignore_permissions=True)
    frappe.logger().info(
        f"Email Intelligence: RTGS placeholder {doc.name} created"
    )
    return doc.name
