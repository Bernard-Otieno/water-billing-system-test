from app.auth import hash_password
from app.database import supabase

users_to_create = [
    {"username": "admin", "password": "admin12345", "role": "owner"},
    {"username": "casper", "password": "casper12345", "role": "meter_reader"},
]

for u in users_to_create:
    supabase.table("users").insert(
        {
            "username": u["username"],
            "password_hash": hash_password(u["password"]),
            "role": u["role"],
        }
    ).execute()
    print(f"Created user: {u['username']} ({u['role']})")
