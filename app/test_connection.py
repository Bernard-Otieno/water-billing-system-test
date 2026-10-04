from app.database import supabase

response = (
    supabase.table("bills")
    .select("*, accounts(account_people(people(name)))")
    .execute()
)
beatrice_bills = [
    b
    for b in response.data
    if any("Beatrice" in p["people"]["name"] for p in b["accounts"]["account_people"])
]
print(len(beatrice_bills), beatrice_bills)
