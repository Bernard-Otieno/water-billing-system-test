import base64
import os
from datetime import datetime

import requests

# Optional: Try loading a .env file if available locally
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def get_config(key: str, default: str | None = None) -> str:
    """Fetch configuration dynamically from environment variables."""
    value = os.getenv(key, default)
    if value is None:
        raise KeyError(
            f"Missing required environment variable: '{key}'. "
            "Ensure it is defined in your environment or .env file."
        )
    return value


def get_access_token() -> str:
    consumer_key = get_config("MPESA_CONSUMER_KEY")
    consumer_secret = get_config("MPESA_CONSUMER_SECRET")
    base_url = get_config("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke")

    token_url = f"{base_url}/oauth/v1/generate?grant_type=client_credentials"
    credentials = f"{consumer_key}:{consumer_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()

    response = requests.get(
        token_url,
        headers={"Authorization": f"Basic {encoded}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def initiate_stk_push(phone_number: str, amount: float, account_reference: str) -> dict:
    shortcode = get_config("MPESA_SHORTCODE")
    passkey = get_config("MPESA_PASSKEY")
    callback_url = get_config("MPESA_CALLBACK_URL")
    base_url = get_config("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke")
    transaction_type = get_config("MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline")

    stk_push_url = f"{base_url}/mpesa/stkpush/v1/processrequest"
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password_str = f"{shortcode}{passkey}{timestamp}"
    password = base64.b64encode(password_str.encode()).decode()

    token = get_access_token()

    payload = {
        "BusinessShortCode": shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": transaction_type,
        "Amount": int(amount),
        "PartyA": phone_number,
        "PartyB": shortcode,
        "PhoneNumber": phone_number,
        "CallBackURL": callback_url,
        "AccountReference": account_reference,
        "TransactionDesc": "Water bill payment",
    }

    response = requests.post(
        stk_push_url,
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
