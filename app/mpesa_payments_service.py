from app.database import supabase
from app.mpesa_service import initiate_stk_push
from app.tokens import read_snapshot_token


class InvalidToken(Exception):
    pass


class AccountNotFound(Exception):
    pass


class NoBillFound(Exception):
    pass


class NothingOwing(Exception):
    pass


class MpesaRequestFailed(Exception):
    pass


class TransactionNotFound(Exception):
    pass


def initiate_bill_payment(token: str, phone_number: str) -> str:
    account_number = read_snapshot_token(token)
    if account_number is None:
        raise InvalidToken()

    account_resp = (
        supabase.table("accounts")
        .select("id, account_number")
        .eq("account_number", account_number)
        .is_("deleted_at", "null")
        .execute()
    )
    if not account_resp.data:
        raise AccountNotFound()
    account = account_resp.data[0]

    latest_bill_resp = (
        supabase.table("bills")
        .select("id, closing_balance")
        .eq("account_id", account["id"])
        .order("billing_year", desc=True)
        .order("billing_month", desc=True)
        .limit(1)
        .execute()
    )
    if not latest_bill_resp.data:
        raise NoBillFound()
    latest_bill = latest_bill_resp.data[0]

    amount = latest_bill["closing_balance"]
    if amount <= 0:
        raise NothingOwing()

    try:
        stk_response = initiate_stk_push(
            phone_number=phone_number,
            amount=amount,
            account_reference=account["account_number"],
        )
    except Exception as e:
        print(f"MPESA INITIATE FAILED: {type(e).__name__}: {e}")
        raise MpesaRequestFailed() from e

    supabase.table("mpesa_transactions").insert(
        {
            "bill_id": latest_bill["id"],
            "checkout_request_id": stk_response["CheckoutRequestID"],
            "merchant_request_id": stk_response["MerchantRequestID"],
            "phone_number": phone_number,
            "amount": amount,
            "status": "pending",
        }
    ).execute()

    return stk_response["CheckoutRequestID"]


def get_transaction_status(token: str, checkout_request_id: str) -> dict:
    if read_snapshot_token(token) is None:
        raise InvalidToken()

    txn_resp = (
        supabase.table("mpesa_transactions")
        .select("status, mpesa_receipt, result_desc")
        .eq("checkout_request_id", checkout_request_id)
        .execute()
    )
    if not txn_resp.data:
        raise TransactionNotFound()

    return txn_resp.data[0]


def process_callback(payload: dict) -> None:
    callback = payload["Body"]["stkCallback"]
    checkout_request_id = callback["CheckoutRequestID"]
    result_code = callback["ResultCode"]
    result_desc = callback["ResultDesc"]

    txn_resp = (
        supabase.table("mpesa_transactions")
        .select("*")
        .eq("checkout_request_id", checkout_request_id)
        .execute()
    )
    if not txn_resp.data:
        return
    txn = txn_resp.data[0]

    if result_code == 0:
        items = callback["CallbackMetadata"]["Item"]
        metadata = {item["Name"]: item.get("Value") for item in items}
        supabase.table("mpesa_transactions").update(
            {
                "status": "success",
                "mpesa_receipt": metadata.get("MpesaReceiptNumber"),
                "result_desc": result_desc,
            }
        ).eq("id", txn["id"]).execute()
    else:
        supabase.table("mpesa_transactions").update(
            {
                "status": "failed",
                "result_desc": result_desc,
            }
        ).eq("id", txn["id"]).execute()
