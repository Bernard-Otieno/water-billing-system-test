import calendar
from datetime import date

from app.database import supabase
from app.utils import nairobi_today


def compute_stats() -> dict:
    today = nairobi_today()
    current_month, current_year = today.month, today.year
    previous_year = current_year - 1

    accounts_resp = (
        supabase.table("accounts")
        .select("id, account_number, account_people(people(name))")
        .execute()
    )
    bills_resp = (
        supabase.table("bills")
        .select(
            "id, account_id, billing_month, billing_year, amount_due, "
            "units_used, closing_balance, status, notified_at"
        )
        .execute()
    )
    payments_resp = supabase.table("payments").select("amount, payment_date").execute()

    bills = bills_resp.data
    payments = payments_resp.data
    accounts = accounts_resp.data

    total_billed_month = sum(
        b["amount_due"]
        for b in bills
        if b["billing_month"] == current_month and b["billing_year"] == current_year
    )
    total_billed_year = sum(
        b["amount_due"] for b in bills if b["billing_year"] == current_year
    )
    total_units_month = sum(
        b["units_used"]
        for b in bills
        if b["billing_month"] == current_month and b["billing_year"] == current_year
    )
    total_units_year = sum(
        b["units_used"] for b in bills if b["billing_year"] == current_year
    )

    total_collected_month = 0.0
    total_collected_year = 0.0
    for p in payments:
        pdate = date.fromisoformat(p["payment_date"])
        if pdate.year == current_year:
            total_collected_year += p["amount"]
            if pdate.month == current_month:
                total_collected_month += p["amount"]

    collection_rate_month = (
        round((total_collected_month / total_billed_month) * 100, 1)
        if total_billed_month
        else 0
    )
    collection_rate_year = (
        round((total_collected_year / total_billed_year) * 100, 1)
        if total_billed_year
        else 0
    )

    bills_sorted = sorted(
        bills, key=lambda b: (b["billing_year"], b["billing_month"]), reverse=True
    )
    latest_bill_by_account = {}
    for b in bills_sorted:
        if b["account_id"] not in latest_bill_by_account:
            latest_bill_by_account[b["account_id"]] = b

    total_outstanding = sum(
        b["closing_balance"] for b in latest_bill_by_account.values()
    )
    unsent_count = sum(
        1 for b in latest_bill_by_account.values() if not b["notified_at"]
    )

    month_bills = [
        b
        for b in bills
        if b["billing_month"] == current_month and b["billing_year"] == current_year
    ]
    status_counts = {"paid": 0, "partial": 0, "unpaid": 0}
    for b in month_bills:
        status_counts[b["status"]] = status_counts.get(b["status"], 0) + 1

    account_names = {}
    for acc in accounts:
        names = ", ".join(ap["people"]["name"] for ap in acc["account_people"])
        account_names[acc["id"]] = {
            "account_number": acc["account_number"],
            "names": names,
        }

    top_outstanding = []
    for acc_id, bill in latest_bill_by_account.items():
        if bill["closing_balance"] > 0 and acc_id in account_names:
            top_outstanding.append(
                {
                    "account_number": account_names[acc_id]["account_number"],
                    "names": account_names[acc_id]["names"],
                    "balance": bill["closing_balance"],
                }
            )
    top_outstanding.sort(key=lambda r: r["balance"], reverse=True)
    top_outstanding = top_outstanding[:5]

    def last_n_months(n):
        result = []
        y, m = current_year, current_month
        for _ in range(n):
            result.append((y, m))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        return list(reversed(result))

    months = last_n_months(6)
    trend_labels = [f"{calendar.month_abbr[m]} {y}" for (y, m) in months]
    trend_billed, trend_collected = [], []
    for y, m in months:
        trend_billed.append(
            round(
                sum(
                    b["amount_due"]
                    for b in bills
                    if b["billing_month"] == m and b["billing_year"] == y
                ),
                2,
            )
        )
        month_collected = 0.0
        for p in payments:
            pdate = date.fromisoformat(p["payment_date"])
            if pdate.year == y and pdate.month == m:
                month_collected += p["amount"]
        trend_collected.append(round(month_collected, 2))

    yoy_labels = [calendar.month_abbr[m] for m in range(1, 13)]
    yoy_current = [
        round(
            sum(
                b["amount_due"]
                for b in bills
                if b["billing_month"] == m and b["billing_year"] == current_year
            ),
            2,
        )
        for m in range(1, 13)
    ]
    yoy_previous = [
        round(
            sum(
                b["amount_due"]
                for b in bills
                if b["billing_month"] == m and b["billing_year"] == previous_year
            ),
            2,
        )
        for m in range(1, 13)
    ]

    return {
        "current_month_name": calendar.month_name[current_month],
        "current_year": current_year,
        "total_billed_month": total_billed_month,
        "total_billed_year": total_billed_year,
        "total_collected_month": total_collected_month,
        "total_collected_year": total_collected_year,
        "collection_rate_month": collection_rate_month,
        "collection_rate_year": collection_rate_year,
        "total_outstanding": total_outstanding,
        "unsent_count": unsent_count,
        "status_counts": status_counts,
        "total_units_month": total_units_month,
        "total_units_year": total_units_year,
        "top_outstanding": top_outstanding,
        "chart_data": {
            "trend_labels": trend_labels,
            "trend_billed": trend_billed,
            "trend_collected": trend_collected,
            "yoy_labels": yoy_labels,
            "yoy_current": yoy_current,
            "yoy_previous": yoy_previous,
        },
    }
