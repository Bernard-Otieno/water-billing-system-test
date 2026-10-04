import base64
import os
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

# 1. Generate OAuth Access Token
consumer_key = os.environ.get("MPESA_CONSUMER_KEY")
consumer_secret = os.environ.get("MPESA_CONSUMER_SECRET")

auth_string = f"{consumer_key}:{consumer_secret}"
encoded_auth = base64.b64encode(auth_string.encode()).decode()

token_url = (
    "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
)
token_resp = requests.get(token_url, headers={"Authorization": f"Basic {encoded_auth}"})

if token_resp.status_code != 200:
    print(f"OAuth Error ({token_resp.status_code}):", token_resp.text)
    exit()

token = token_resp.json()["access_token"]
print("OAuth Status:", token_resp.status_code)
print("Token acquired successfully.")

# 2. Prepare STK Push Parameters
shortcode = os.environ.get("MPESA_SHORTCODE", "174379")
passkey = os.environ.get(
    "MPESA_PASSKEY", "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
)

timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
password_raw = f"{shortcode}{passkey}{timestamp}"
password = base64.b64encode(password_raw.encode()).decode()

payload = {
    "BusinessShortCode": shortcode,
    "Password": password,
    "Timestamp": timestamp,
    "TransactionType": "CustomerPayBillOnline",
    "Amount": 1,
    "PartyA": "254708374149",
    "PartyB": shortcode,
    "PhoneNumber": "254708374149",
    "CallBackURL": os.environ.get("MPESA_CALLBACK_URL"),
    "AccountReference": "TEST001",
    "TransactionDesc": "Water bill payment",
}

# 3. Trigger STK Push
stk_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
headers = {
    "Authorization": f"Bearer {token.strip()}",
    "Content-Type": "application/json",
}

stk_resp = requests.post(stk_url, json=payload, headers=headers)

print("STK Push Status Code:", stk_resp.status_code)
print("STK Push Response Body:", stk_resp.text)
