from app.ads import get_featured_ads, get_starter_ads
from app.database import supabase
from app.tokens import read_snapshot_token

USAGE_TREND_MONTHS = 6


def _get_account_by_number(account_number: str):
    resp = (
        supabase.table("accounts")
        .select("id, account_number, account_people(people(name))")
        .eq("account_number", account_number)
        .is_("deleted_at", "null")
        .execute()
    )
    return resp.data[0] if resp.data else None


def _get_bill_history(account_id: int):
    """Every bill for this account, newest first, each with its own
    payments attached (mirrors the pattern already used in
    /accounts/{account_number}/detail)."""
    bills_resp = (
        supabase.table("bills")
        .select("*")
        .eq("account_id", account_id)
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .execute()
    )
    bills = bills_resp.data

    bill_ids = [b["id"] for b in bills]
    payments_resp = (
        supabase.table("payments").select("*").in_("bill_id", bill_ids).execute()
        if bill_ids
        else type("", (), {"data": []})()
    )

    payments_by_bill = {}
    for p in payments_resp.data:
        payments_by_bill.setdefault(p["bill_id"], []).append(p)

    for b in bills:
        b["payments"] = payments_by_bill.get(b["id"], [])

    return bills


def _get_usage_trend(bills: list, months: int = USAGE_TREND_MONTHS):
    """Labels + units-used for the last N bills, oldest-first (charts read
    left-to-right chronologically, but `bills` itself stays newest-first
    everywhere else on the page)."""
    recent = bills[:months][::-1]
    return {
        "labels": [f"{b['billing_month']}/{b['billing_year']}" for b in recent],
        "units": [b["units_used"] for b in recent],
    }


def get_bill_view_context(token: str):
    """The one function the /bill-view route calls. Returns a dict ready
    to hand straight to the template, or None if the token is invalid or
    the account has no bills yet - the route turns None into a 404."""
    account_number = read_snapshot_token(token)
    if account_number is None:
        return None

    account = _get_account_by_number(account_number)
    if account is None:
        return None

    bills = _get_bill_history(account["id"])
    bills = _annotate_display_status(bills)
    if not bills:
        return None

    names = ", ".join(ap["people"]["name"] for ap in account["account_people"])
    latest_bill = bills[0]

    all_payments = [p for b in bills for p in b["payments"]]
    all_payments.sort(key=lambda p: p["payment_date"], reverse=True)

    return {
        "token": token,
        "account_number": account["account_number"],
        "names": names,
        "latest_bill": latest_bill,
        "bills": bills,
        "payments": all_payments,
        "usage_trend": _get_usage_trend(bills),
        "featured_ads": get_featured_ads(),
        "starter_ads": get_starter_ads(),
    }


def _annotate_display_status(bills):
    """Customer-facing status, derived from the real running balance -
    not the per-bill `status` column, which only tracks payments logged
    against that specific bill and can disagree with the true balance
    when there's credit carried forward from an earlier overpayment."""
    for b in bills:
        if b["closing_balance"] > 0:
            b["display_status"] = "unpaid"
        elif b["closing_balance"] == 0:
            b["display_status"] = "paid"
        else:
            b["display_status"] = "credit"
    return bills
