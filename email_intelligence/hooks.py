app_name = "email_intelligence"
app_title = "Email Intelligence"
app_publisher = "Madhav International"
app_description = "Parses incoming emails and creates Bank Transactions, CRM Leads, and Opportunities"
app_email = "sales@madhavinternational.com.np"
app_license = "MIT"

doc_events = {
    "Communication": {
        "after_insert": "email_intelligence.utils.processor.process_incoming_email"
    }
}
