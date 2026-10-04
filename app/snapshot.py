# app/snapshot.py
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.database import supabase

FONTS_DIR = os.path.join(os.path.dirname(__file__), "static", "fonts")

FONT_CANDIDATES = [os.path.join(FONTS_DIR, "CourierPrime-Regular.ttf")]
BOLD_FONT_CANDIDATES = [os.path.join(FONTS_DIR, "CourierPrime-Bold.ttf")]


def _load_font(candidates, size):
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


FONT = _load_font(FONT_CANDIDATES, 20)
FONT_SMALL = _load_font(FONT_CANDIDATES, 16)
FONT_BOLD = _load_font(BOLD_FONT_CANDIDATES, 22)

MONTH_NAMES = [
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


def build_year_snapshot(
    account_number,
    names,
    year,
    bills_by_month,
    billing_month,
    billing_year,
    previous_balance,
    previous_reading,
    current_reading,
    units_used,
    this_month_bill,
    amount_paid,
    total_owed,
):
    W, H = 500, 780
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)

    month_label = f"{MONTH_NAMES[billing_month - 1]} {billing_year}"

    y = 24
    draw.text(
        (W / 2, y), "WATER BILL SNAPSHOT", font=FONT_BOLD, fill="black", anchor="ma"
    )
    y += 36
    draw.text(
        (W / 2, y),
        f"{account_number} — {names}",
        font=FONT_SMALL,
        fill="black",
        anchor="ma",
    )

    y += 24
    draw.text((W / 2, y), f"Year: {year}", font=FONT_SMALL, fill="black", anchor="ma")
    y += 30

    box_height = 34
    draw.rectangle([(30, y), (W - 30, y + box_height)], fill=(240, 240, 240))
    reading_text = f"{month_label} reading: {previous_reading:.1f} -> {current_reading:.1f}  ({units_used:.1f} units)"
    draw.text(
        (W / 2, y + box_height / 2),
        reading_text,
        font=FONT_SMALL,
        fill="black",
        anchor="mm",
    )
    y += box_height + 16

    draw.line([(30, y), (W - 30, y)], fill="black", width=2)
    y += 20

    draw.text((40, y), "MONTH", font=FONT, fill="black")
    draw.text((250, y), "BILLED", font=FONT, fill="black")
    draw.text((380, y), "PAID", font=FONT, fill="black")
    y += 30
    draw.line([(30, y), (W - 30, y)], fill="black", width=1)
    y += 10

    for m in range(1, 13):
        row = bills_by_month.get(m)
        billed_str = f"{row['amount_due']:.2f}" if row else "-"
        paid_str = f"{row['paid']:.2f}" if row else "-"
        draw.text((40, y), MONTH_NAMES[m - 1], font=FONT_SMALL, fill="black")
        draw.text((250, y), billed_str, font=FONT_SMALL, fill="black")
        draw.text((380, y), paid_str, font=FONT_SMALL, fill="black")
        y += 28

    y += 10
    draw.line([(30, y), (W - 30, y)], fill="black", width=2)
    y += 26

    month_label = f"{MONTH_NAMES[billing_month - 1]} {billing_year}"

    draw.text((40, y), "Previous Balance:", font=FONT, fill="black")
    draw.text((330, y), f"{previous_balance:.2f}", font=FONT, fill="black")
    y += 32

    draw.text(
        (40, y), f"This Month's Bill ({month_label}):", font=FONT_SMALL, fill="black"
    )
    y += 26
    draw.text((330, y - 26), f"{this_month_bill:.2f}", font=FONT, fill="black")

    draw.text((40, y), "Amount You've Paid:", font=FONT, fill="black")
    draw.text((330, y), f"{amount_paid:.2f}", font=FONT, fill="black")
    y += 32

    draw.line([(30, y), (W - 30, y)], fill="black", width=2)
    y += 26

    draw.text((40, y), "TOTAL NOW OWED:", font=FONT_BOLD, fill="black")
    draw.text((320, y), f"{total_owed:.2f}", font=FONT_BOLD, fill="black")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def get_year_snapshot_data(account_number: str, year: int):
    account_resp = (
        supabase.table("accounts")
        .select("id, account_number, account_people(people(name))")
        .eq("account_number", account_number)
        .execute()
    )
    if not account_resp.data:
        return None
    account = account_resp.data[0]
    names = ", ".join(ap["people"]["name"] for ap in account["account_people"])

    latest_resp = (
        supabase.table("bills")
        .select("*")
        .eq("account_id", account["id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
    )
    if not latest_resp.data:
        return None
    latest_bill = latest_resp.data[0]

    if year is None:
        year = latest_bill["billing_year"]

    # --- Month-by-month table data - now uses the resolved `year`, not
    # a guessed one ---
    year_bills_resp = (
        supabase.table("bills")
        .select("*")
        .eq("account_id", account["id"])
        .eq("billing_year", year)
        .order("billing_month")
        .execute()
    )
    year_bills = year_bills_resp.data

    year_bill_ids = [b["id"] for b in year_bills]
    year_payments_resp = (
        supabase.table("payments").select("*").in_("bill_id", year_bill_ids).execute()
        if year_bill_ids
        else type("", (), {"data": []})()
    )

    paid_by_bill = {}
    for p in year_payments_resp.data:
        paid_by_bill[p["bill_id"]] = paid_by_bill.get(p["bill_id"], 0) + p["amount"]

    bills_by_month = {}
    for b in year_bills:
        bills_by_month[b["billing_month"]] = {
            "amount_due": b["amount_due"],
            "paid": paid_by_bill.get(b["id"], 0),
        }

    # --- Simple bottom summary data: the account's ACTUAL latest bill overall ---
    latest_resp = (
        supabase.table("bills")
        .select("*")
        .eq("account_id", account["id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
    )
    if not latest_resp.data:
        return None
    latest_bill = latest_resp.data[0]

    latest_payments_resp = (
        supabase.table("payments")
        .select("amount")
        .eq("bill_id", latest_bill["id"])
        .execute()
    )
    amount_paid_latest = sum(p["amount"] for p in latest_payments_resp.data)

    return {
        "account_number": account["account_number"],
        "names": names,
        "year": year,
        "bills_by_month": bills_by_month,
        "billing_month": latest_bill["billing_month"],
        "billing_year": latest_bill["billing_year"],
        "previous_balance": latest_bill["opening_balance"],
        "previous_reading": latest_bill["previous_reading"],
        "current_reading": latest_bill["current_reading"],
        "units_used": latest_bill["units_used"],
        "this_month_bill": latest_bill["amount_due"],
        "amount_paid": amount_paid_latest,
        "total_owed": max(latest_bill["closing_balance"], 0),
    }


def generate_snapshot_png(account_number: str, year: int | None = None) -> bytes | None:
    data = get_year_snapshot_data(account_number, year)
    if data is None:
        return None
    return build_year_snapshot(
        data["account_number"],
        data["names"],
        data["year"],
        data["bills_by_month"],
        data["billing_month"],
        data["billing_year"],
        data["previous_balance"],
        data["previous_reading"],
        data["current_reading"],
        data["units_used"],
        data["this_month_bill"],
        data["amount_paid"],
        data["total_owed"],
    )
