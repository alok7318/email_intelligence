import re
import frappe
from frappe.utils import getdate, nowdate

# EBL / Nabil HTML alert emails (after HTML stripping):
# 2026-06-17 16:04 Credit 121,600.00 For: 000082943426 ...
# 2026-06-17 16:04 Credit 121,600.00 658,216.94 -VEGA PHARMACEUTICALS ...  (Nabil includes balance)
TXN_ROW_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+"   # date + time
    r"(Debit|Credit)\s+"                         # type
    r"([\d,]+\.?\d*)\s+"                         # amount
    r"(?:[\d,]+\.?\d*\s+)?"                      # optional balance (Nabil)
    r"(.+?)(?:\s+Enjoy|\s+Thank you|$)",          # remarks until footer
    re.IGNORECASE,
)

# Legacy pipe-delimited format (kept for compatibility)
TXN_ROW_PATTERN_PIPE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[\s\d:]+)\s*\|\s*(Debit|Credit)\s*\|\s*([\d,]+\.?\d*)"
    r"(?:\s*\|\s*([\d,]+\.?\d*))?"
    r"\s*\|\s*(.+?)(?:\s*\||\s*$)",
    re.IGNORECASE,
)
ACCOUNT_NUMBER_PATTERN = re.compile(r"account number\s+([\w#*]+)", re.IGNORECASE)

# Standard reference (8+ uppercase alphanum)
REFERENCE_PATTERN = re.compile(r"[A-Z0-9]{8,}")

# SWIFT/RTGS remittance format: REMITTANCE ID:[0100TTFC008984]:LODGE AND
REMITTANCE_ID_RE = re.compile(r"REMITTANCE ID:\[([^\]]+)\]", re.IGNORECASE)


def _parse_amount(s):
    return float(s.replace(",", "").strip())


def _find_bank_account(body, config):
    """Match account suffix from email body against config bank senders table."""
    m = ACCOUNT_NUMBER_PATTERN.search(body)
    if not m:
        return None
    raw_account = m.group(1)
    for row in config.bank_senders or []:
        if row.account_suffix and raw_account.endswith(row.account_suffix):
            return row.bank_account
    return None


def _is_duplicate(date, amount, bank_account, reference):
    """Return True if a Bank Transaction with the same key data already exists."""
    filters = {"date": date, "bank_account": bank_account}
    if reference:
        filters["reference_number"] = reference
    else:
        # Fall back to amount-based check if no reference
        filters["withdrawal"] = amount

    return frappe.db.exists("Bank Transaction", filters)


def parse_and_save(sender, body, config):
    bank_account = _find_bank_account(body, config)

    m = TXN_ROW_PATTERN.search(body)
    if m:
        txn_date_raw = m.group(1).strip()
        txn_type     = m.group(2).strip()
        amount_raw   = m.group(3).strip()
        remarks      = m.group(4).strip()
    else:
        # Try legacy pipe-delimited format
        m = TXN_ROW_PATTERN_PIPE.search(body)
        if not m:
            frappe.log_error(
                f"Bank parser: no transaction row found\nSender: {sender}\nBody: {body[:500]}",
                "Email Intelligence",
            )
            return None
        txn_date_raw = m.group(1).strip()
        txn_type     = m.group(2).strip()
        amount_raw   = m.group(3).strip()
        remarks      = m.group(5).strip()

    try:
        txn_date = getdate(txn_date_raw.split()[0])
    except Exception:
        txn_date = nowdate()

    amount = _parse_amount(amount_raw)
    is_debit = txn_type.lower() == "debit"

    # SWIFT/RTGS alerts: REMITTANCE ID:[TT_NUMBER]:LODGE AND  or  :SWIFT CHA
    remit_match = REMITTANCE_ID_RE.search(remarks)
    if remit_match:
        tt = remit_match.group(1).strip()
        # SWIFT CHA = bank charge on 201393; add suffix to keep separate from main LODGE entry
        if "SWIFT CHA" in remarks.upper() or "RTGS CHA" in remarks.upper():
            reference = f"{tt}-CHG"
        else:
            reference = tt
    else:
        ref_match = REFERENCE_PATTERN.search(remarks)
        reference = ref_match.group(0) if ref_match else ""

    if _is_duplicate(txn_date, amount, bank_account, reference):
        frappe.logger().info(
            f"Email Intelligence: Duplicate bank transaction skipped — {reference or amount} on {txn_date}"
        )
        return None

    # If a SWIFT/RTGS placeholder already exists for this TT reference, update it
    existing = frappe.db.get_value(
        "Bank Transaction",
        {"reference_number": reference, "withdrawal": 0.0, "deposit": 0.0},
        "name",
    ) if reference else None

    if existing:
        frappe.db.set_value(
            "Bank Transaction",
            existing,
            {
                "withdrawal": amount if is_debit else 0.0,
                "deposit": 0.0 if is_debit else amount,
                "date": txn_date,
                "bank_account": bank_account,
            },
            update_modified=False,
        )
        frappe.logger().info(
            f"Email Intelligence: Updated SWIFT placeholder {existing} with NPR amount {amount}"
        )
        return existing

    doc = frappe.get_doc({
        "doctype": "Bank Transaction",
        "date": txn_date,
        "deposit": 0.0 if is_debit else amount,
        "withdrawal": amount if is_debit else 0.0,
        "description": remarks[:140],
        "bank_account": bank_account,
        "status": "Unreconciled",
        "reference_number": reference,
    })
    doc.insert(ignore_permissions=True)
    return doc.name
