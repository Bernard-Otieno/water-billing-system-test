from app.database import supabase

response = supabase.table("bills").select("*").eq("account_id", 1).execute()
print(response.data)
