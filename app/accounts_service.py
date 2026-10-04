from app.database import supabase
from app.utils import nairobi_today

_accounts_cache = None


def get_account_names():
    """Stable account_id/number/names lookup. Cached until invalidate_accounts_cache() is called."""
    global _accounts_cache
    if _accounts_cache is None:
        accounts_resp = (
            supabase.table("accounts")
            .select("id, account_number, account_people(people(name))")
            .is_("deleted_at", "null")
            .execute()
        )
        _accounts_cache = [
            {
                "id": acc["id"],
                "account_number": acc["account_number"],
                "names": [ap["people"]["name"] for ap in acc["account_people"]],
            }
            for acc in accounts_resp.data
        ]
    return _accounts_cache


def invalidate_accounts_cache():
    global _accounts_cache
    _accounts_cache = None


def get_dashboard_rows():
    """One row per account: names, balance owed, and whether the current
    bill has been taken but not yet sent to the customer."""
    accounts = get_account_names()  # reuses the cache — no extra accounts query

    bills_resp = (
        supabase.table("latest_bills")
        .select("account_id, closing_balance, billing_year, billing_month, notified_at")
        .execute()
    )
    latest_bill_by_account = {b["account_id"]: b for b in bills_resp.data}

    today = nairobi_today()
    rows = []
    for acc in accounts:
        latest = latest_bill_by_account.get(acc["id"])
        balance = latest["closing_balance"] if latest else 0
        unbilled = bool(
            latest
            and latest["billing_month"] == today.month
            and latest["billing_year"] == today.year
            and not latest["notified_at"]
        )
        rows.append(
            {
                "account_number": acc["account_number"],
                "names": ", ".join(acc["names"]),
                "balance": balance,
                "unbilled": unbilled,
            }
        )

    rows.sort(key=lambda r: r["balance"], reverse=True)
    return rows
