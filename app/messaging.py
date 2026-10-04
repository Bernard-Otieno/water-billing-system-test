# app/messaging.py
# app/messaging.py
def build_bill_message(names, latest_bill, snapshot_link):
    return (
        f"Dear {names},\n\n"
        f"Your water bill for {latest_bill['billing_month']}/{latest_bill['billing_year']} is ready.\n\n"
        f"View your full bill summary here:\n{snapshot_link}\n\n"
        f"Kindly settle promptly. Thank you."
    )
