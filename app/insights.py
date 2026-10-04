import os

from google import genai

from app.database import supabase
from app.stats_service import compute_stats
from app.utils import nairobi_today


def get_genai_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is missing.")
    return genai.Client(api_key=api_key)


def build_account_summary() -> str:
    accounts_resp = (
        supabase.table("accounts")
        .select("id, account_number, account_people(people(name))")
        .execute()
    )
    bills_resp = (
        supabase.table("bills")
        .select("account_id, billing_month, billing_year, closing_balance, status")
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .execute()
    )
    latest_bill_by_account = {}
    for b in bills_resp.data:
        if b["account_id"] not in latest_bill_by_account:
            latest_bill_by_account[b["account_id"]] = b

    rows = []
    for acc in accounts_resp.data:
        names = ", ".join(ap["people"]["name"] for ap in acc["account_people"])
        bill = latest_bill_by_account.get(acc["id"])
        balance = bill["closing_balance"] if bill else None
        status = bill["status"] if bill else "no bills yet"
        rows.append((acc["account_number"], names, balance, status))

    rows.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))
    return "\n".join(
        f"{num} | {names} | balance: {bal if bal is not None else 'N/A'} | status: {status}"
        for num, names, bal, status in rows
    )


def build_stats_summary() -> str:
    s = compute_stats()
    return f"""- Billed this month ({s['current_month_name']}): KES {s['total_billed_month']:.2f}
- Billed this year: KES {s['total_billed_year']:.2f}
- Collected this month: KES {s['total_collected_month']:.2f}
- Collected this year: KES {s['total_collected_year']:.2f}
- Collection rate this month: {s['collection_rate_month']}%
- Collection rate this year: {s['collection_rate_year']}%
- Total outstanding across all accounts: KES {s['total_outstanding']:.2f}
- Bills generated but not yet sent to customers: {s['unsent_count']}
- This month's bill status breakdown: {s['status_counts']['paid']} paid, {s['status_counts']['partial']} partial, {s['status_counts']['unpaid']} unpaid
- Units consumed this month: {s['total_units_month']}
- Units consumed this year: {s['total_units_year']}
- Billed vs collected, last 6 months: {list(zip(s['chart_data']['trend_labels'], s['chart_data']['trend_billed'], s['chart_data']['trend_collected']))}
- This year vs last year, billed by month: {s['chart_data']['yoy_current']} vs {s['chart_data']['yoy_previous']}"""


def ask_ai(question: str) -> str:
    client = get_genai_client()
    account_summary = build_account_summary()
    stats_summary = build_stats_summary()

    prompt = f"""You are a financial/operations analyst for a small water utility in Kenya, \
speaking directly to the owner. You have two data sources below: aggregate business \
statistics, and a full list of individual accounts with their balances.

AGGREGATE STATISTICS:
{stats_summary}

PER-ACCOUNT DATA (sorted by balance, highest first):
{account_summary}

OWNER'S QUESTION: {question}

Your job is to give INSIGHT, not a summary. The owner can already see every number above \
on their dashboard — repeating them back is not useful. Instead:
- Connect the two data sources to each other (e.g. is the outstanding balance concentrated \
in a few accounts, or spread across many? Does a low collection rate trace back to a \
specific group of accounts rather than a general decline?)
- Point out patterns, trends, or risks that are not obvious from looking at any single \
number in isolation.
- Only mention a specific number when it's being used as evidence for a claim, never as \
the answer itself.
- If nothing meaningfully answers the question beyond what's already visible, say so \
honestly rather than padding the answer.

Write a thorough, well-reasoned answer — typically one to two short paragraphs. Use \
as much space as the question genuinely needs to properly explain your reasoning, but \
never pad the answer with numbers just to make it longer."""

    response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    return response.text


def get_or_create_daily_insight() -> str:
    today = nairobi_today().isoformat()

    existing = (
        supabase.table("daily_insights")
        .select("insight_text")
        .eq("insight_date", today)
        .execute()
    )
    if existing.data:
        return existing.data[0]["insight_text"]

    client = get_genai_client()
    account_summary = build_account_summary()
    stats_summary = build_stats_summary()

    prompt = f"""You are a financial/operations analyst for a small water utility in Kenya. \
The owner is about to open their dashboard. Give them ONE short, specific, genuinely useful \
observation about the business right now — something they'd likely find interesting or \
worth acting on, not a generic summary of numbers they can already see elsewhere.

AGGREGATE STATISTICS:
{stats_summary}

PER-ACCOUNT DATA (sorted by balance, highest first):
{account_summary}

Rules:
- Exactly one observation, 2-3 sentences.
- It must explain WHY something matters, not just restate a number.
- Prefer something non-obvious: a pattern, a concentration, a comparison across time, \
or a risk — not just "your top account owes X."
- No greeting, no preamble, no "Here's an insight:" — just the observation itself."""

    response = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    insight_text = response.text.strip()

    supabase.table("daily_insights").insert(
        {
            "insight_date": today,
            "insight_text": insight_text,
        }
    ).execute()

    return insight_text


def full_analysis() -> str:
    client = get_genai_client()
    account_summary = build_account_summary()
    stats_summary = build_stats_summary()

    prompt = f"""You are a financial/operations analyst for a small water utility in Kenya, \
speaking directly to the owner. You have two data sources below: aggregate business \
statistics, and a full list of individual accounts with their balances.

AGGREGATE STATISTICS:
{stats_summary}

PER-ACCOUNT DATA (sorted by balance, highest first):
{account_summary}

Give the owner a thorough analysis of the business right now. Produce 3-5 distinct, \
numbered observations, each covering a DIFFERENT angle — for example: cash flow health, \
risk concentration among specific accounts, trends over time, operational issues like \
unsent bills, or anything else genuinely notable in the data.

For each observation:
- Explain WHY it matters, not just what the number is.
- Connect the two data sources to each other where relevant.
- Only cite a number as evidence for a claim, never as the entire point.
- Skip anything that's just restating a number the owner can already see on this page.

If some area of the data is genuinely unremarkable, say so rather than inventing \
significance just to fill five points."""

    response = client.models.generate_content(model="gemini-3.5-flash", contents=prompt)
    return response.text
