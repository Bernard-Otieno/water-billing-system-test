from datetime import date

from app.database import supabase

test_accounts = [
    {
        "account_number": "TEST001",
        "address": "1 Test Lane",
        "account_type": "Residential",
        "price_per_unit": 150,
        "people": [
            {
                "name": "Alice Wanjiru",
                "phone_number": "254700000001",
                "contact_method": "sms",
            }
        ],
        "previous_reading": 100,
        "current_reading": 115,
        "opening_balance": 0,
        "amount_paid": None,
        "already_notified": False,
    },
    {
        "account_number": "TEST002",
        "address": "2 Test Lane",
        "account_type": "Residential",
        "price_per_unit": 150,
        "people": [
            {
                "name": "Jake Wanjiru",
                "phone_number": "254700000002",
                "contact_method": "whatsapp",
            }
        ],
        "previous_reading": 100,
        "current_reading": 300,
        "opening_balance": 50,
        "amount_paid": 8000,
        "already_notified": True,
    },
    {
        # Partial payment, not yet notified
        "account_number": "TEST003",
        "address": "3 Test Lane",
        "account_type": "Residential",
        "price_per_unit": 150,
        "people": [
            {
                "name": "Grace Achieng",
                "phone_number": "254700000003",
                "contact_method": "sms",
            }
        ],
        "previous_reading": 50,
        "current_reading": 90,
        "opening_balance": 0,
        "amount_paid": 2000,
        "already_notified": False,
    },
    {
        # Fully paid
        "account_number": "TEST004",
        "address": "4 Test Lane",
        "account_type": "Residential",
        "price_per_unit": 150,
        "people": [
            {
                "name": "Peter Mwangi",
                "phone_number": "254700000004",
                "contact_method": "whatsapp",
            }
        ],
        "previous_reading": 200,
        "current_reading": 210,
        "opening_balance": 0,
        "amount_paid": 1500,
        "already_notified": True,
    },
    {
        # Overdue, two people on one account, not notified
        "account_number": "TEST005",
        "address": "5 Test Lane",
        "account_type": "Residential",
        "price_per_unit": 150,
        "people": [
            {
                "name": "Samuel Otieno",
                "phone_number": "254700000005",
                "contact_method": "sms",
            },
            {
                "name": "Mary Otieno",
                "phone_number": "254700000006",
                "contact_method": "whatsapp",
            },
        ],
        "previous_reading": 300,
        "current_reading": 340,
        "opening_balance": 1200,
        "amount_paid": None,
        "already_notified": False,
    },
    {
        # Overdue, already notified -- good for testing the "Undo Sent" button
        "account_number": "TEST006",
        "address": "6 Test Lane",
        "account_type": "Residential",
        "price_per_unit": 150,
        "people": [
            {
                "name": "Dennis Kiprop",
                "phone_number": "254700000007",
                "contact_method": "sms",
            }
        ],
        "previous_reading": 80,
        "current_reading": 95,
        "opening_balance": 500,
        "amount_paid": None,
        "already_notified": True,
    },
    {
        # Paid, two people
        "account_number": "TEST007",
        "address": "7 Test Lane",
        "account_type": "Residential",
        "price_per_unit": 150,
        "people": [
            {
                "name": "Lucy Njeri",
                "phone_number": "254700000008",
                "contact_method": "whatsapp",
            },
            {
                "name": "John Njeri",
                "phone_number": "254700000009",
                "contact_method": "sms",
            },
        ],
        "previous_reading": 150,
        "current_reading": 165,
        "opening_balance": 0,
        "amount_paid": 2250,
        "already_notified": True,
    },
    {
        # Edge case: zero usage this month, still unpaid from before
        "account_number": "TEST008",
        "address": "8 Test Lane",
        "account_type": "Residential",
        "price_per_unit": 150,
        "people": [
            {
                "name": "Esther Wambui",
                "phone_number": "254700000010",
                "contact_method": "sms",
            }
        ],
        "previous_reading": 400,
        "current_reading": 400,
        "opening_balance": 900,
        "amount_paid": None,
        "already_notified": False,
    },
]

for spec in test_accounts:
    person_ids = [
        supabase.table("people").insert(p).execute().data[0]["id"]
        for p in spec["people"]
    ]

    account_id = (
        supabase.table("accounts")
        .insert(
            {
                "account_number": spec["account_number"],
                "address": spec["address"],
                "account_type": spec["account_type"],
                "price_per_unit": spec["price_per_unit"],
            }
        )
        .execute()
        .data[0]["id"]
    )

    for pid in person_ids:
        supabase.table("account_people").insert(
            {"account_id": account_id, "person_id": pid}
        ).execute()

    units_used = spec["current_reading"] - spec["previous_reading"]
    amount_due = round(units_used * spec["price_per_unit"], 2)
    closing_balance = round(spec["opening_balance"] + amount_due, 2)
    today = date.today()

    bill_id = (
        supabase.table("bills")
        .insert(
            {
                "account_id": account_id,
                "billing_month": today.month,
                "billing_year": today.year,
                "previous_reading": spec["previous_reading"],
                "current_reading": spec["current_reading"],
                "units_used": units_used,
                "rate_applied": spec["price_per_unit"],
                "amount_due": amount_due,
                "opening_balance": spec["opening_balance"],
                "closing_balance": closing_balance,
                "status": "unpaid",
                "notified_at": (
                    "2026-01-01T00:00:00" if spec["already_notified"] else None
                ),
            }
        )
        .execute()
        .data[0]["id"]
    )

    if spec["amount_paid"]:
        supabase.table("payments").insert(
            {
                "bill_id": bill_id,
                "amount": spec["amount_paid"],
                "payment_date": today.isoformat(),
            }
        ).execute()
        new_status = "paid" if spec["amount_paid"] >= amount_due else "partial"
        new_closing = round(closing_balance - spec["amount_paid"], 2)
        supabase.table("bills").update(
            {"status": new_status, "closing_balance": new_closing}
        ).eq("id", bill_id).execute()

print(f"Seeded {len(test_accounts)} test accounts.")
