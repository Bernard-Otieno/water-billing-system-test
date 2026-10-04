import io
import os
import urllib.parse
from datetime import datetime

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.accounts_service import (
    get_account_names,
    get_dashboard_rows,
    invalidate_accounts_cache,
)
from app.auth import verify_password
from app.bill_view import get_bill_view_context
from app.database import supabase
from app.insights import ask_ai, full_analysis, get_or_create_daily_insight
from app.messaging import build_bill_message
from app.mpesa_payments_service import (
    AccountNotFound,
    InvalidToken,
    MpesaRequestFailed,
    NoBillFound,
    NothingOwing,
    TransactionNotFound,
    get_transaction_status,
    initiate_bill_payment,
    process_callback,
)
from app.schemas import ReadingSubmission
from app.snapshot import generate_snapshot_png
from app.stats_service import compute_stats
from app.tokens import (
    TokenExpired,
    TokenInvalid,
    make_snapshot_token,
    read_snapshot_token,
)
from app.utils import nairobi_today, static_url

load_dotenv()

app = FastAPI()
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.add_middleware(SessionMiddleware, secret_key=os.environ["SESSION_SECRET_KEY"])
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["static_url"] = static_url


class CachedStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", CachedStaticFiles(directory="app/static"), name="static")


def get_current_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_owner(request: Request):
    user = get_current_user(request)
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ----------------404--------------------#


@app.exception_handler(StarletteHTTPException)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code not in (403, 404):
        # Anything else — hand off to FastAPI's own default handling (this is what
        # preserves things like the 303 + Location header login-redirect trick).
        return await http_exception_handler(request, exc)

    user = request.session.get("user")
    if not user:
        home_url, home_label = "/login", "Back to Login"
    elif user["role"] == "owner":
        home_url, home_label = "/dashboard", "Back to Dashboard"
    else:
        home_url, home_label = "/meter-form", "Back to Meter Reading"

    if exc.status_code == 403:
        message = (
            "You don't have permission to view this. "
            "If you think that's wrong, ask the admin to check your account."
        )
        return templates.TemplateResponse(
            request=request,
            name="403.html",
            context={
                "message": message,
                "home_url": home_url,
                "home_label": home_label,
            },
            status_code=403,
        )

    # Starlette's own "no route matched" 404s carry the generic detail "Not Found".
    # Our own `raise HTTPException(404, detail="Account not found")` calls carry
    # something more specific — show that instead when we have it.
    if exc.detail and exc.detail != "Not Found":
        message = (
            f"{exc.detail}. It may have been removed, or the link may be out of date."
        )
    else:
        message = "That page doesn't exist. It may have been moved, or the link might be mistyped."

    return templates.TemplateResponse(
        request=request,
        name="404.html",
        context={"message": message, "home_url": home_url, "home_label": home_label},
        status_code=404,
    )


# ---------- AUTH ROUTES (new) ----------


@app.get("/")
def index():
    return RedirectResponse(url="/login")


@app.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={})


@app.post("/login")
def login_submit(
    request: Request, username: str = Form(...), password: str = Form(...)
):
    result = supabase.table("users").select("*").eq("username", username).execute()
    if not result.data:
        return templates.TemplateResponse(
            request=request, name="login.html", context={"error": "Invalid credentials"}
        )

    user = result.data[0]

    if not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request=request, name="login.html", context={"error": "Invalid credentials"}
        )
    request.session["user"] = {"username": user["username"], "role": user["role"]}

    if user["role"] == "owner":
        return RedirectResponse(url="/dashboard", status_code=303)
    else:
        return RedirectResponse(url="/meter-form", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")


# ---------- EXISTING ROUTES (unchanged, from before) ----------


@app.get("/ads/{ad_id}/click")
def ad_click(ad_id: int):
    ad_resp = supabase.table("ads").select("*").eq("id", ad_id).execute()
    if not ad_resp.data:
        raise HTTPException(status_code=404, detail="Ad not found")
    ad = ad_resp.data[0]

    # Count the tap, then send them on to the business - increment first so a
    # customer closing the tab mid-redirect still counts as a genuine tap.
    supabase.table("ads").update({"click_count": ad["click_count"] + 1}).eq(
        "id", ad_id
    ).execute()

    return RedirectResponse(url=f"tel:+{ad['phone_number']}", status_code=302)


@app.get("/bill-view/{token}")
def bill_view(request: Request, token: str):
    try:
        context = get_bill_view_context(token)
    except TokenExpired:
        raise HTTPException(
            status_code=404, detail="This link has expired. Text us for a new one."
        ) from None
    except TokenInvalid:
        raise HTTPException(status_code=404, detail="This link isn't valid.") from None

    if context is None:
        raise HTTPException(status_code=404, detail="Account not found")

    return templates.TemplateResponse(
        request=request, name="bill_view.html", context=context
    )


@app.get("/accounts/{account_number}/summary")
def get_account_summary(account_number: str, user: dict = Depends(get_current_user)):

    today = nairobi_today()

    account_resp = (
        supabase.table("accounts")
        .select("id, account_number, price_per_unit, account_people(people(name))")
        .eq("account_number", account_number)
        .is_("deleted_at", "null")
        .execute()
    )
    if not account_resp.data:
        raise HTTPException(status_code=404, detail="Account not found")

    account = account_resp.data[0]
    names = [ap["people"]["name"] for ap in account["account_people"]]

    last_bill_resp = (
        supabase.table("bills")
        .select("current_reading, closing_balance")
        .eq("account_id", account["id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
    )
    last_bill = last_bill_resp.data[0] if last_bill_resp.data else None

    this_month_resp = (
        supabase.table("bills")
        .select("current_reading, previous_reading")
        .eq("account_id", account["id"])
        .eq("billing_month", today.month)
        .eq("billing_year", today.year)
        .execute()
    )
    this_month_bill = this_month_resp.data[0] if this_month_resp.data else None

    return {
        "account_number": account["account_number"],
        "names": names,
        "current_outstanding": last_bill["closing_balance"] if last_bill else 0,
        "already_read_this_month": this_month_bill is not None,
        "this_month_reading": (
            this_month_bill["current_reading"] if this_month_bill else None
        ),
        "reference_reading": (
            this_month_bill["previous_reading"]
            if this_month_bill
            else (last_bill["current_reading"] if last_bill else 0)
        ),
    }


@app.post("/bills/submit")
def submit_reading(
    submission: ReadingSubmission, user: dict = Depends(get_current_user)
):
    today = nairobi_today()
    billing_month = today.month
    billing_year = today.year

    account_resp = (
        supabase.table("accounts")
        .select("id, price_per_unit")
        .eq("account_number", submission.account_number)
        .is_("deleted_at", "null")
        .execute()
    )
    if not account_resp.data:
        raise HTTPException(status_code=404, detail="Account not found")
    account = account_resp.data[0]

    last_bill_resp = (
        supabase.table("bills")
        .select("*")
        .eq("account_id", account["id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
    )
    last_bill = last_bill_resp.data[0] if last_bill_resp.data else None
    previous_reading = last_bill["current_reading"] if last_bill else 0
    opening_balance = last_bill["closing_balance"] if last_bill else 0

    # Fold in any corrections made since the last bill was generated
    since = last_bill["created_at"] if last_bill else "1970-01-01"
    adjustments_resp = (
        supabase.table("adjustments")
        .select("amount")
        .eq("account_id", account["id"])
        .gt("created_at", since)
        .execute()
    )
    opening_balance += sum(a["amount"] for a in adjustments_resp.data)

    units_used = submission.current_reading - previous_reading

    if units_used < 0:
        raise HTTPException(
            status_code=400,
            detail=f"Current reading ({submission.current_reading}) is less than "
            f"previous reading ({previous_reading}). This would result in "
            f"negative units and is not allowed.",
        )

    amount_due = round(units_used * account["price_per_unit"], 2)
    closing_balance = round(opening_balance + amount_due, 2)

    existing_resp = (
        supabase.table("bills")
        .select("*")
        .eq("account_id", account["id"])
        .eq("billing_month", billing_month)
        .eq("billing_year", billing_year)
        .execute()
    )
    existing = existing_resp.data[0] if existing_resp.data else None

    if existing and not submission.confirm_overwrite:
        return {
            "status": "warning",
            "message": "A reading already exists for this account this month.",
            "existing_reading": existing["current_reading"],
            "existing_amount_due": existing["amount_due"],
            "new_reading": submission.current_reading,
            "new_amount_due": amount_due,
        }

    bill_data = {
        "account_id": account["id"],
        "billing_month": billing_month,
        "billing_year": billing_year,
        "previous_reading": previous_reading,
        "current_reading": submission.current_reading,
        "units_used": round(units_used, 2),
        "rate_applied": account["price_per_unit"],
        "amount_due": amount_due,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "status": "unpaid",
    }

    if existing:
        supabase.table("bills").update(bill_data).eq("id", existing["id"]).execute()
        return {"status": "updated", "bill": bill_data}
    else:
        supabase.table("bills").insert(bill_data).execute()
        return {"status": "created", "bill": bill_data}


@app.get("/meter-form")
def meter_form_page(request: Request, user: dict = Depends(get_current_user)):

    today = nairobi_today()

    accounts_resp = (
        supabase.table("accounts").select("id").is_("deleted_at", "null").execute()
    )
    total_accounts = len(accounts_resp.data)

    bills_resp = (
        supabase.table("bills")
        .select("account_id")
        .eq("billing_month", today.month)
        .eq("billing_year", today.year)
        .execute()
    )
    read_count = len({b["account_id"] for b in bills_resp.data})

    return templates.TemplateResponse(
        request=request,
        name="meter_form.html",
        context={
            "read_count": read_count,
            "total_accounts": total_accounts,
        },
    )


@app.get("/accounts/search")
def search_accounts(q: str, user: dict = Depends(get_current_user)):
    if len(q.strip()) < 2:
        return []

    # Step 1: find people whose name partially matches what was typed
    people_resp = (
        supabase.table("people").select("id, name").ilike("name", f"%{q}%").execute()
    )
    if not people_resp.data:
        return []
    person_ids = [p["id"] for p in people_resp.data]

    # Step 2: find which accounts those people are linked to
    links_resp = (
        supabase.table("account_people")
        .select("account_id")
        .in_("person_id", person_ids)
        .execute()
    )
    account_ids = list({link["account_id"] for link in links_resp.data})
    if not account_ids:
        return []

    # Step 3: get full account + all linked names (so "Nancy Mwangi" shows her account partner too)
    accounts_resp = (
        supabase.table("accounts")
        .select("account_number, account_people(people(name))")
        .in_("id", account_ids)
        .is_("deleted_at", "null")
        .execute()
    )

    results = []
    for acc in accounts_resp.data:
        names = [ap["people"]["name"] for ap in acc["account_people"]]
        results.append({"account_number": acc["account_number"], "names": names})

    return results[:10]  # cap it so the list never gets overwhelming


@app.get("/accounts/list")
def list_accounts(user: dict = Depends(get_current_user)):

    today = nairobi_today()
    this_month_bills_resp = (
        supabase.table("bills")
        .select("account_id")
        .eq("billing_month", today.month)
        .eq("billing_year", today.year)
        .execute()
    )
    read_account_ids = {b["account_id"] for b in this_month_bills_resp.data}

    results = [
        {
            "account_number": acc["account_number"],
            "names": acc["names"],
            "already_read": acc["id"] in read_account_ids,
        }
        for acc in get_account_names()
    ]
    results.sort(key=lambda r: r["names"][0].lower() if r["names"] else "")
    return results


# ----------------------------- DASHBOARD -----------------------------#


@app.get("/dashboard")
def dashboard(request: Request, user: dict = Depends(require_owner)):
    rows = get_dashboard_rows()
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={"rows": rows, "wide": True}
    )


@app.get("/accounts/{account_number}/detail")
def account_detail(
    request: Request, account_number: str, user: dict = Depends(require_owner)
):
    account_resp = (
        supabase.table("accounts")
        .select("id, account_number, price_per_unit, account_people(people(name))")
        .eq("account_number", account_number)
        .execute()
    )
    if not account_resp.data:
        raise HTTPException(status_code=404, detail="Account not found")
    account = account_resp.data[0]
    names = ", ".join(ap["people"]["name"] for ap in account["account_people"])

    bills_resp = (
        supabase.table("bills")
        .select("*")
        .eq("account_id", account["id"])
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
        b["is_latest"] = b["id"] == bills[0]["id"]

    adjustments_resp = (
        supabase.table("adjustments")
        .select("*")
        .eq("account_id", account["id"])
        .order("created_at", desc=True)
        .execute()
    )

    snapshot_token = make_snapshot_token(account["account_number"])

    return templates.TemplateResponse(
        request=request,
        name="account_detail.html",
        context={
            "account_number": account["account_number"],
            "names": names,
            "bills": bills,
            "adjustments": adjustments_resp.data,
            "snapshot_token": snapshot_token,  # new
            "wide": True,
        },
    )


def generate_next_account_number() -> str:
    """
    Looks at every existing account_number matching 'ACC-####', finds the
    highest number used, and returns the next one in the same format.
    Using max()+1 (not count+1) means it still works correctly even if
    an account was ever deleted or numbers have gaps.
    """
    existing = (
        supabase.table("accounts")
        .select("account_number")
        .like("account_number", "ACC-%")
        .execute()
    )
    highest = 0
    for row in existing.data:
        suffix = row["account_number"].replace("ACC-", "")
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"ACC-{highest + 1:04d}"


@app.get("/accounts/add")
def add_account_form(request: Request, user: dict = Depends(require_owner)):
    next_number = generate_next_account_number()
    return templates.TemplateResponse(
        request=request, name="account_add.html", context={"next_number": next_number}
    )


@app.post("/accounts/add")
async def add_account(request: Request, user: dict = Depends(require_owner)):
    form = await request.form()

    address = form.get("address", "").strip()
    account_type = form.get("account_type", "").strip()
    price_per_unit_raw = form.get("price_per_unit", "").strip()

    names = [n.strip() for n in form.getlist("person_name")]
    phones = [p.strip() for p in form.getlist("person_phone")]
    methods = form.getlist("person_contact_method")

    people_rows = [
        (n, p, m) for n, p, m in zip(names, phones, methods, strict=True) if n
    ]

    if not price_per_unit_raw or not people_rows:
        raise HTTPException(
            status_code=400,
            detail="Price per unit and at least one person (with a name) are required.",
        )

    try:
        price_per_unit = float(price_per_unit_raw)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Price per unit must be a number."
        ) from None

    # Generated here (not trusted from the form) so two admins adding at the
    # same moment can't accidentally submit the same stale preview number.
    account_number = generate_next_account_number()

    account_resp = (
        supabase.table("accounts")
        .insert(
            {
                "account_number": account_number,
                "address": address,
                "account_type": account_type,
                "price_per_unit": price_per_unit,
            }
        )
        .execute()
    )
    account_id = account_resp.data[0]["id"]

    for name, phone, method in people_rows:
        person_resp = (
            supabase.table("people")
            .insert(
                {
                    "name": name,
                    "phone_number": phone,
                    "contact_method": (
                        method if method in ("whatsapp", "sms") else "sms"
                    ),
                }
            )
            .execute()
        )
        person_id = person_resp.data[0]["id"]
        supabase.table("account_people").insert(
            {"account_id": account_id, "person_id": person_id}
        ).execute()
    invalidate_accounts_cache()
    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


@app.get("/accounts/{account_number}/edit")
def edit_account_form(
    request: Request, account_number: str, user: dict = Depends(require_owner)
):
    account_resp = (
        supabase.table("accounts")
        .select(
            "id, account_number, address, account_type, price_per_unit, "
            "account_people(people(id, name, phone_number, contact_method))"
        )
        .eq("account_number", account_number)
        .execute()
    )
    if not account_resp.data:
        raise HTTPException(status_code=404, detail="Account not found")

    account = account_resp.data[0]
    people = [ap["people"] for ap in account["account_people"]]

    return templates.TemplateResponse(
        request=request,
        name="account_edit.html",
        context={
            "account": account,
            "people": people,
        },
    )


@app.post("/accounts/{account_number}/edit")
async def edit_account(
    request: Request, account_number: str, user: dict = Depends(require_owner)
):
    form = await request.form()

    account_resp = (
        supabase.table("accounts")
        .select("id")
        .eq("account_number", account_number)
        .execute()
    )
    if not account_resp.data:
        raise HTTPException(status_code=404, detail="Account not found")
    account_id = account_resp.data[0]["id"]

    address = form.get("address", "").strip()
    account_type = form.get("account_type", "").strip()
    price_per_unit_raw = form.get("price_per_unit", "").strip()
    try:
        price_per_unit = float(price_per_unit_raw)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail="Price per unit must be a number."
        ) from e

    supabase.table("accounts").update(
        {
            "address": address,
            "account_type": account_type,
            "price_per_unit": price_per_unit,
        }
    ).eq("id", account_id).execute()

    # --- Remove people the owner explicitly deleted from this account ---
    removed_ids_raw = form.get("removed_person_ids", "").strip()
    removed_ids = [int(i) for i in removed_ids_raw.split(",") if i]
    for person_id in removed_ids:
        supabase.table("account_people").delete().eq("account_id", account_id).eq(
            "person_id", person_id
        ).execute()
        # Only delete the person record itself if they're not linked to any other account
        remaining_links = (
            supabase.table("account_people")
            .select("account_id")
            .eq("person_id", person_id)
            .execute()
        )
        if not remaining_links.data:
            supabase.table("people").delete().eq("id", person_id).execute()

    # --- Update existing people / insert newly-added people ---
    person_ids = form.getlist("person_id")
    names = [n.strip() for n in form.getlist("person_name")]
    phones = [p.strip() for p in form.getlist("person_phone")]
    methods = form.getlist("person_contact_method")

    for pid, name, phone, method in zip(
        person_ids, names, phones, methods, strict=True
    ):
        if not name:
            continue
        contact_method = method if method in ("whatsapp", "sms") else "sms"
        if pid:
            # Existing person: update in place
            supabase.table("people").update(
                {
                    "name": name,
                    "phone_number": phone,
                    "contact_method": contact_method,
                }
            ).eq("id", int(pid)).execute()
        else:
            # New person: insert and link to this account
            person_resp = (
                supabase.table("people")
                .insert(
                    {
                        "name": name,
                        "phone_number": phone,
                        "contact_method": contact_method,
                    }
                )
                .execute()
            )
            new_person_id = person_resp.data[0]["id"]
            supabase.table("account_people").insert(
                {"account_id": account_id, "person_id": new_person_id}
            ).execute()
    invalidate_accounts_cache()
    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


@app.post("/accounts/{account_number}/delete")
def delete_account(account_number: str, user: dict = Depends(require_owner)):

    account_resp = (
        supabase.table("accounts")
        .select("id")
        .eq("account_number", account_number)
        .execute()
    )
    if not account_resp.data:
        raise HTTPException(status_code=404, detail="Account not found")

    supabase.table("accounts").update({"deleted_at": datetime.now().isoformat()}).eq(
        "id", account_resp.data[0]["id"]
    ).execute()
    invalidate_accounts_cache()
    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/accounts/{account_number}/payment")
def record_payment(
    account_number: str,
    bill_id: int = Form(...),
    amount: float = Form(...),
    user: dict = Depends(require_owner),
):
    bill_resp = supabase.table("bills").select("*").eq("id", bill_id).execute()
    if not bill_resp.data:
        raise HTTPException(status_code=404, detail="Bill not found")
    bill = bill_resp.data[0]

    supabase.table("payments").insert(
        {
            "bill_id": bill_id,
            "amount": amount,
            "payment_date": nairobi_today().isoformat(),
        }
    ).execute()

    payments_resp = (
        supabase.table("payments").select("amount").eq("bill_id", bill_id).execute()
    )
    total_paid = sum(p["amount"] for p in payments_resp.data)

    # Compare against the FULL debt (carried-over balance + this month's charge),
    # not just this month's charge in isolation.
    total_owed_for_bill = bill["opening_balance"] + bill["amount_due"]
    new_status = (
        "paid"
        if total_paid >= total_owed_for_bill
        else ("partial" if total_paid > 0 else "unpaid")
    )
    new_closing = round(total_owed_for_bill - total_paid, 2)

    supabase.table("bills").update(
        {"status": new_status, "closing_balance": new_closing}
    ).eq("id", bill_id).execute()

    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


@app.post("/accounts/{account_number}/adjustment")
def add_adjustment(
    account_number: str,
    amount: float = Form(...),
    reason: str = Form(""),
    user: dict = Depends(require_owner),
):
    account_resp = (
        supabase.table("accounts")
        .select("id")
        .eq("account_number", account_number)
        .execute()
    )
    if not account_resp.data:
        raise HTTPException(status_code=404, detail="Account not found")

    supabase.table("adjustments").insert(
        {
            "account_id": account_resp.data[0]["id"],
            "amount": amount,
            "reason": reason or None,
        }
    ).execute()

    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


@app.post("/payments/{payment_id}/delete")
def delete_payment(
    payment_id: int,
    account_number: str = Form(...),
    user: dict = Depends(require_owner),
):
    payment_resp = supabase.table("payments").select("*").eq("id", payment_id).execute()
    if not payment_resp.data:
        raise HTTPException(status_code=404, detail="Payment not found")
    bill_id = payment_resp.data[0]["bill_id"]

    supabase.table("payments").delete().eq("id", payment_id).execute()

    bill = supabase.table("bills").select("*").eq("id", bill_id).execute().data[0]
    remaining_payments = (
        supabase.table("payments")
        .select("amount")
        .eq("bill_id", bill_id)
        .execute()
        .data
    )
    total_paid = sum(p["amount"] for p in remaining_payments)

    total_owed_for_bill = bill["opening_balance"] + bill["amount_due"]
    new_status = (
        "paid"
        if total_paid >= total_owed_for_bill
        else ("partial" if total_paid > 0 else "unpaid")
    )
    new_closing = round(total_owed_for_bill - total_paid, 2)

    supabase.table("bills").update(
        {"status": new_status, "closing_balance": new_closing}
    ).eq("id", bill_id).execute()

    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


@app.post("/bills/{bill_id}/edit")
def edit_latest_bill(
    bill_id: int,
    account_number: str = Form(...),
    current_reading: float = Form(...),
    user: dict = Depends(require_owner),
):
    bill = supabase.table("bills").select("*").eq("id", bill_id).execute().data[0]
    # SAFETY CHECK: only allow editing if this is genuinely the latest bill for this account
    latest = (
        supabase.table("bills")
        .select("id")
        .eq("account_id", bill["account_id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
        .data[0]
    )
    if latest["id"] != bill_id:
        raise HTTPException(
            status_code=403, detail="Only the most recent bill can be edited."
        )

    units_used = current_reading - bill["previous_reading"]
    if units_used < 0:
        raise HTTPException(
            status_code=400, detail="This reading would result in negative units."
        )

    amount_due = round(units_used * bill["rate_applied"], 2)
    payments = (
        supabase.table("payments")
        .select("amount")
        .eq("bill_id", bill_id)
        .execute()
        .data
    )
    total_paid = sum(p["amount"] for p in payments)
    closing_balance = round(bill["opening_balance"] + amount_due - total_paid, 2)

    supabase.table("bills").update(
        {
            "current_reading": current_reading,
            "units_used": round(units_used, 2),
            "amount_due": amount_due,
            "closing_balance": closing_balance,
        }
    ).eq("id", bill_id).execute()

    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


@app.post("/bills/{bill_id}/delete")
def delete_bill(
    bill_id: int, account_number: str = Form(...), user: dict = Depends(require_owner)
):
    bill = supabase.table("bills").select("*").eq("id", bill_id).execute().data

    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    bill = bill[0]

    # SAFETY CHECK: only allow deleting if this is genuinely the latest bill for this account
    latest = (
        supabase.table("bills")
        .select("id")
        .eq("account_id", bill["account_id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
        .data[0]
    )
    if latest["id"] != bill_id:
        raise HTTPException(
            status_code=403, detail="Only the most recent bill can be deleted."
        )

    # Delete any payments attached to this bill first (foreign key requirement)
    supabase.table("payments").delete().eq("bill_id", bill_id).execute()

    # Then delete the bill itself
    supabase.table("bills").delete().eq("id", bill_id).execute()

    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


@app.post("/bills/{bill_id}/edit-balance")
def edit_latest_bill_balance(
    bill_id: int,
    account_number: str = Form(...),
    closing_balance: float = Form(...),
    user: dict = Depends(require_owner),
):
    bill_resp = supabase.table("bills").select("*").eq("id", bill_id).execute()
    if not bill_resp.data:
        raise HTTPException(status_code=404, detail="Bill not found")
    bill = bill_resp.data[0]

    # SAFETY CHECK: only the genuinely latest bill for this account can be edited
    latest = (
        supabase.table("bills")
        .select("id")
        .eq("account_id", bill["account_id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
        .data[0]
    )
    if latest["id"] != bill_id:
        raise HTTPException(
            status_code=403, detail="Only the most recent bill can be edited."
        )

    supabase.table("bills").update({"closing_balance": closing_balance}).eq(
        "id", bill_id
    ).execute()

    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


def get_notification_progress():
    """
    How many active accounts have been notified of their latest bill, out of
    how many active accounts actually have a bill to be notified about.
    Computed fresh from the database every call - deliberately NOT tied to
    whatever queue a particular page happens to be showing, so it reads the
    same true number whether you're on the bulk send page or a single-customer
    send link.
    """
    accounts_resp = (
        supabase.table("accounts").select("id").is_("deleted_at", "null").execute()
    )
    active_ids = {a["id"] for a in accounts_resp.data}

    bills_resp = (
        supabase.table("bills")
        .select("account_id, notified_at")
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .execute()
    )
    latest_by_account = {}
    for b in bills_resp.data:
        if b["account_id"] not in latest_by_account:
            latest_by_account[b["account_id"]] = b

    billable = [b for acc_id, b in latest_by_account.items() if acc_id in active_ids]
    total_billable = len(billable)
    notified_count = sum(1 for b in billable if b["notified_at"])
    return notified_count, total_billable


@app.get("/billing/send")
def billing_send_page(request: Request, user: dict = Depends(require_owner)):
    base_url = os.environ["PUBLIC_BASE_URL"]

    # Fetch accounts and their associated people
    accounts_resp = (
        supabase.table("accounts")
        .select(
            "id, account_number, account_people(people(id, name, phone_number, contact_method))"
        )
        .is_("deleted_at", "null")
        .execute()
    )

    # Fetch all bills, ordered by newest first
    bills_resp = (
        supabase.table("bills")
        .select("*")
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .execute()
    )

    # Group bills by account ID
    bills_by_account = {}
    for b in bills_resp.data:
        bills_by_account.setdefault(b["account_id"], []).append(b)

    queue = []

    # Process each account
    for acc in accounts_resp.data:
        bills = bills_by_account.get(acc["id"], [])

        # 1. Skip if there are no bills for this account
        if not bills:
            continue

        latest_bill = bills[0]

        # 2. Skip if they have already been notified
        if latest_bill.get("notified_at"):
            continue

        # 3. Extract people and build their names
        people = [ap["people"] for ap in acc["account_people"]]
        names = ", ".join(p["name"] for p in people)

        # 4. Generate the snapshot link
        token = make_snapshot_token(acc["account_number"])
        snapshot_link = f"{base_url}/bill-view/{token}"

        # 5. Build the message
        message = build_bill_message(names, latest_bill, snapshot_link)

        # 6. Generate contact links for SMS/WhatsApp
        contacts = []
        for p in people:
            if not p.get("phone_number"):
                continue

            if p["contact_method"] == "whatsapp":
                link = f"whatsapp://send?phone={p['phone_number']}&text={urllib.parse.quote(message)}"
            else:
                link = f"sms:+{p['phone_number']}?body={urllib.parse.quote(message)}"

            contacts.append(
                {"name": p["name"], "link": link, "channel": p["contact_method"]}
            )

        # 7. Add everything to the rendering queue
        queue.append(
            {
                "bill_id": latest_bill["id"],
                "account_number": acc["account_number"],
                "names": names,
                "previous_reading": latest_bill["previous_reading"],
                "current_reading": latest_bill["current_reading"],
                "amount_due": latest_bill["amount_due"],
                "opening_balance": latest_bill["opening_balance"],
                "closing_balance": latest_bill["closing_balance"],
                "contacts": contacts,
            }
        )

    notified_count, total_billable = get_notification_progress()

    return templates.TemplateResponse(
        request=request,
        name="billing_send.html",
        context={
            "queue": queue,
            "wide": True,
            "notified_count": notified_count,
            "total_billable": total_billable,
        },
    )


@app.post("/billing/mark-sent-single")
def mark_sent_single(bill_id: int = Form(...), user: dict = Depends(require_owner)):

    supabase.table("bills").update({"notified_at": datetime.now().isoformat()}).eq(
        "id", bill_id
    ).execute()
    return {"ok": True}


@app.post("/billing/mark-sent")
def mark_sent(bill_ids: str = Form(...), user: dict = Depends(require_owner)):

    ids = [int(i) for i in bill_ids.split(",") if i]
    for bill_id in ids:
        supabase.table("bills").update({"notified_at": datetime.now().isoformat()}).eq(
            "id", bill_id
        ).execute()
    return RedirectResponse(url="/billing/send", status_code=303)


@app.post("/bills/{bill_id}/unmark-sent")
def unmark_sent(
    bill_id: int, account_number: str = Form(...), user: dict = Depends(require_owner)
):
    supabase.table("bills").update({"notified_at": None}).eq("id", bill_id).execute()
    return RedirectResponse(url=f"/accounts/{account_number}/detail", status_code=303)


@app.get("/bill-snapshot/{token}")
def bill_snapshot(token: str):
    account_number = read_snapshot_token(token)
    if account_number is None:
        raise HTTPException(status_code=404, detail="Invalid or expired link")

    png_bytes = generate_snapshot_png(
        account_number
    )  # no year passed - defaults to the bill's own year
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="Account not found")

    return Response(content=png_bytes, media_type="image/png")


@app.get("/accounts/{account_number}/send")
def billing_send_single(
    request: Request, account_number: str, user: dict = Depends(require_owner)
):
    account_resp = (
        supabase.table("accounts")
        .select(
            "id, account_number, account_people(people(id, name, phone_number, contact_method))"
        )
        .eq("account_number", account_number)
        .is_("deleted_at", "null")
        .execute()
    )
    if not account_resp.data:
        raise HTTPException(status_code=404, detail="Account not found")
    account = account_resp.data[0]
    people = [ap["people"] for ap in account["account_people"]]

    latest_bill_resp = (
        supabase.table("bills")
        .select("*")
        .eq("account_id", account["id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
    )
    if not latest_bill_resp.data:
        raise HTTPException(status_code=404, detail="This account has no bills yet.")
    latest_bill = latest_bill_resp.data[0]

    names = ", ".join(p["name"] for p in people)
    raw_url = os.getenv("PUBLIC_BASE_URL") or str(request.base_url)
    base_url = raw_url.strip().rstrip("/")
    token = make_snapshot_token(account["account_number"])
    snapshot_link = f"{base_url}/bill-view/{token}"
    message = build_bill_message(names, latest_bill, snapshot_link)

    contacts = []
    for p in people:
        if not p["phone_number"]:
            continue
        if p["contact_method"] == "whatsapp":
            link = f"whatsapp://send?phone={p['phone_number']}&text={urllib.parse.quote(message)}"
        else:
            link = f"sms:+{p['phone_number']}?body={urllib.parse.quote(message)}"
        contacts.append(
            {"name": p["name"], "link": link, "channel": p["contact_method"]}
        )

    queue = [
        {
            "bill_id": latest_bill["id"],
            "account_number": account["account_number"],
            "names": names,
            "previous_reading": latest_bill["previous_reading"],
            "current_reading": latest_bill["current_reading"],
            "amount_due": latest_bill["amount_due"],
            "opening_balance": latest_bill["opening_balance"],
            "closing_balance": latest_bill["closing_balance"],
            "contacts": contacts,
        }
    ]

    notified_count, total_billable = get_notification_progress()

    return templates.TemplateResponse(
        request=request,
        name="billing_send.html",
        context={
            "queue": queue,
            "wide": True,
            "notified_count": notified_count,
            "total_billable": total_billable,
        },
    )


MONTH_NAMES_FULL = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


@app.get("/reports/billing-export")
def export_billing_report(user: dict = Depends(require_owner)):
    year = nairobi_today().year

    accounts_resp = (
        supabase.table("accounts")
        .select("id, account_number, account_people(people(name))")
        .execute()
    )
    accounts = accounts_resp.data

    all_bills_resp = (
        supabase.table("bills")
        .select("*")
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .execute()
    )
    all_bills = all_bills_resp.data

    bills_by_account = {}
    for b in all_bills:
        bills_by_account.setdefault(b["account_id"], []).append(b)
    # Sorted newest-first by the query, so bills_by_account[id][0] is always that account's latest bill.

    relevant_bill_ids = set()
    for acc in accounts:
        acc_bills = bills_by_account.get(acc["id"], [])
        if acc_bills:
            relevant_bill_ids.add(acc_bills[0]["id"])
        for b in acc_bills:
            if b["billing_year"] == year:
                relevant_bill_ids.add(b["id"])

    payments_resp = (
        supabase.table("payments")
        .select("*")
        .in_("bill_id", list(relevant_bill_ids))
        .execute()
        if relevant_bill_ids
        else type("", (), {"data": []})()
    )
    paid_by_bill = {}
    for p in payments_resp.data:
        paid_by_bill[p["bill_id"]] = paid_by_bill.get(p["bill_id"], 0) + p["amount"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Billing Report"

    headers = [
        "Account Number",
        "Names",
        "Previous Balance",
        "This Month's Bill",
        "Amount Paid (Latest Bill)",
        "Total Owed",
    ]
    for m in MONTH_NAMES_FULL:
        headers += [f"{m} Billed", f"{m} Paid"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for acc in accounts:
        names = ", ".join(ap["people"]["name"] for ap in acc["account_people"])
        acc_bills = bills_by_account.get(acc["id"], [])

        if not acc_bills:
            ws.append([acc["account_number"], names, 0, 0, 0, 0] + [0] * 24)
            continue

        latest_bill = acc_bills[0]
        year_bills = {
            b["billing_month"]: b for b in acc_bills if b["billing_year"] == year
        }

        row = [
            acc["account_number"],
            names,
            latest_bill["opening_balance"],
            latest_bill["amount_due"],
            paid_by_bill.get(latest_bill["id"], 0),
            max(latest_bill["closing_balance"], 0),
        ]
        for m in range(1, 13):
            b = year_bills.get(m)
            row += [b["amount_due"], paid_by_bill.get(b["id"], 0)] if b else [0, 0]
        ws.append(row)

    for col in ws.columns:
        max_len = max(len(str(cell.value)) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = max_len + 2

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename=billing_report_{year}.xlsx"
        },
    )


@app.get("/stats")
def stats_page(request: Request, user: dict = Depends(require_owner)):
    stats = compute_stats()
    return templates.TemplateResponse(
        request=request, name="stats.html", context={"wide": True, **stats}
    )


@app.get("/accounts/close-list")
def close_list_page(request: Request, user: dict = Depends(require_owner)):
    accounts_resp = (
        supabase.table("accounts")
        .select("id, account_number, account_people(people(name))")
        .execute()
    )

    bills_resp = (
        supabase.table("bills")
        .select("account_id, closing_balance")
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .execute()
    )

    latest_balance_by_account = {}
    for b in bills_resp.data:
        if b["account_id"] not in latest_balance_by_account:
            latest_balance_by_account[b["account_id"]] = b["closing_balance"]

    rows = []
    for acc in accounts_resp.data:
        names = [ap["people"]["name"] for ap in acc["account_people"]]
        balance = latest_balance_by_account.get(acc["id"], 0)
        rows.append(
            {
                "account_number": acc["account_number"],
                "names": ", ".join(names),
                "balance": balance,
            }
        )

    rows.sort(key=lambda r: r["balance"], reverse=True)  # default: worst debt first

    return templates.TemplateResponse(
        request=request, name="close_list.html", context={"rows": rows, "wide": True}
    )


# AI implementation
@app.post("/insights/query")
def insights_query(question: str = Form(...), user: dict = Depends(require_owner)):
    answer = ask_ai(question)
    return {"answer": answer}


@app.get("/insights/daily")
def insights_daily(user: dict = Depends(require_owner)):
    insight = get_or_create_daily_insight()
    return {"insight": insight}


@app.get("/insights/full-analysis")
def insights_full_analysis(user: dict = Depends(require_owner)):
    analysis = full_analysis()
    return {"analysis": analysis}

    # Mpesa intergration


class MpesaInitiateRequest(BaseModel):
    phone_number: str


@app.post("/bill-view/{token}/mpesa/initiate")
def bill_view_mpesa_initiate(token: str, body: MpesaInitiateRequest):
    try:
        checkout_request_id = initiate_bill_payment(token, body.phone_number)
        checkout_request_id = initiate_bill_payment(token, body.phone_number)
    except InvalidToken as e:
        raise HTTPException(status_code=404, detail="This link isn't valid.") from e
    except AccountNotFound as e:
        raise HTTPException(status_code=404, detail="Account not found") from e
    except NoBillFound as e:
        raise HTTPException(
            status_code=404, detail="No bill found for this account."
        ) from e
    except NothingOwing as e:
        raise HTTPException(
            status_code=400, detail="This account has nothing owing."
        ) from e
    except MpesaRequestFailed as e:
        raise HTTPException(
            status_code=502, detail="Could not reach M-Pesa. Try again shortly."
        ) from e

    return {"checkout_request_id": checkout_request_id}


@app.get("/bill-view/{token}/mpesa/status/{checkout_request_id}")
def bill_view_mpesa_status(token: str, checkout_request_id: str):
    try:
        return get_transaction_status(token, checkout_request_id)
    except InvalidToken as e:
        raise HTTPException(status_code=404, detail="This link isn't valid.") from e
    except TransactionNotFound as e:
        raise HTTPException(status_code=404, detail="Transaction not found.") from e


@app.post("/mpesa/callback")
def mpesa_callback(payload: dict):
    process_callback(payload)
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
