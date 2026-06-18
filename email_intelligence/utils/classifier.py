def classify_email(sender, subject, body, config):
    """
    Classify email type using per-company config.
    Returns: 'bank_transaction' | 'swift_rtgs' | 'quote_request' | None
    """
    sender_lower = (sender or "").lower()
    subject_lower = (subject or "").lower()
    body_lower = (body or "").lower()

    # Skip emails from own domains
    own_domains = [d.strip() for d in (config.own_domains or "").splitlines() if d.strip()]
    if any(d in sender_lower for d in own_domains):
        return None

    # Bank transaction alerts
    bank_senders = [row.sender_email.lower() for row in (config.bank_senders or [])]
    if any(s in sender_lower for s in bank_senders):
        return "bank_transaction"

    # SWIFT / RTGS outgoing payment notifications
    swift_senders = [row.sender_email.lower() for row in (config.swift_senders or [])]
    if any(s in sender_lower for s in swift_senders):
        return "swift_rtgs"

    # Quote / inquiry requests
    keywords = [k.strip().lower() for k in (config.quote_keywords or "").split(",") if k.strip()]
    if any(kw in subject_lower or kw in body_lower for kw in keywords):
        return "quote_request"

    return None
