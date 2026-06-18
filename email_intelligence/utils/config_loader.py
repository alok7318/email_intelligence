import frappe


def get_config_for_company(company):
    """Return the Email Intelligence Config document for a company, or None."""
    name = frappe.db.get_value(
        "Email Intelligence Config",
        {"company": company, "enabled": 1},
        "name",
    )
    if not name:
        return None
    return frappe.get_doc("Email Intelligence Config", name)


def get_company_from_communication(doc):
    """
    Resolve company from the Email Account that received this Communication.
    Falls back to Default Company if not found.
    """
    if doc.email_account:
        company = frappe.db.get_value("Email Account", doc.email_account, "company")
        if company:
            return company
    return frappe.defaults.get_defaults().get("company")
