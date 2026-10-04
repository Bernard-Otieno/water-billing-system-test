from dotenv import load_dotenv

load_dotenv()

from app.mpesa_service import get_access_token

token = get_access_token()
print("Got token:", token[:20], "...")
