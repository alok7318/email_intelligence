import frappe
from email_intelligence.utils.config_loader import get_config_for_company, get_company_from_communication


def process_incoming_email(doc, method=None):
    """Hook entry point — enqueue async so it never blocks the web request."""
    if doc.sent_or_received != "Received":
        return

    company = get_company_from_communication(doc)
    if not company:
        return

    # Confirm a config exists before queuing — avoids pointless jobs
    config_name = frappe.db.get_value(
        "Email Intelligence Config",
        {"company": company, "enabled": 1},
        "name",
    )
    if not config_name:
        return

    frappe.enqueue(
        "email_intelligence.utils.processor._process",
        communication_name=doc.name,
        company=company,
        queue="short",
        now=frappe.flags.in_test,  # run inline during tests
    )


def _process(communication_name, company):
    """Actual processing — runs in background worker."""
    from email_intelligence.utils.classifier import classify_email
    from email_intelligence.utils.bank_parser import parse_and_save as save_bank
    from email_intelligence.utils.swift_parser import parse_and_save as save_swift
    from email_intelligence.utils.quote_parser import parse_and_save as save_quote
    from email_intelligence.utils.config_loader import get_config_for_company

    config = get_config_for_company(company)
    if not config:
        return

    doc = frappe.get_doc("Communication", communication_name)
    sender = doc.sender or ""
    subject = doc.subject or ""
    body = doc.content or ""

    email_type = classify_email(sender, subject, body, config)
    if not email_type:
        return

    try:
        if email_type == "bank_transaction":
            name = save_bank(sender, body, config)
            if name:
                frappe.logger().info(f"Email Intelligence: Bank Transaction {name} ← {sender}")

        elif email_type == "swift_rtgs":
            name = save_swift(sender, subject, body, config)
            if name:
                frappe.logger().info(f"Email Intelligence: SWIFT Bank Transaction {name} ← {sender}")

        elif email_type == "quote_request":
            name = save_quote(sender, subject, body, config)
            if name:
                frappe.logger().info(f"Email Intelligence: Opportunity {name} ← {sender}")

    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Email Intelligence — {email_type}")
