import os

from dotenv import load_dotenv
from google import genai

from app.database import supabase

load_dotenv()
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

# --- Step 1: pull the same shape of data your dashboard route already builds ---
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

# --- Step 2: build a compact text summary, sorted by balance descending ---
rows = []
for acc in accounts_resp.data:
    names = ", ".join(ap["people"]["name"] for ap in acc["account_people"])
    bill = latest_bill_by_account.get(acc["id"])
    balance = bill["closing_balance"] if bill else None
    status = bill["status"] if bill else "no bills yet"
    rows.append((acc["account_number"], names, balance, status))

# Sort by balance descending; accounts with no bills (None) go last
rows.sort(key=lambda r: (r[2] is None, -(r[2] or 0)))

lines = [
    f"{num} | {names} | balance: {bal if bal is not None else 'N/A'} | status: {status}"
    for num, names, bal, status in rows
]
data_summary = "\n".join(lines)

# --- Step 3: ask a hardcoded test question against real data ---
question = "Which 5 accounts owe the most money?"

prompt = f"""You are helping a water utility owner understand their billing data.
Here is the current account data (format: account_number | names | balance | status):

{data_summary}

Question: {question}

Answer clearly and concisely, referencing specific account numbers and amounts."""

response = client.models.generate_content(
    model="gemini-3.5-flash-lite", contents=prompt
)

print(response.text)
