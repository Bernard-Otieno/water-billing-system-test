from app.database import supabase


def get_featured_ads():
    """Every active Pro/Growth ad, uncapped.

    No exclusivity or category dedup - every paying Pro/Growth advertiser
    who's currently active stacks in the Featured slot on /bill-view.
    Revenue-maximizing by design: more active advertisers, more shown.

    NOTE: this does not enforce "one per category" - if that's still a
    promise made to advertisers, it needs to be handled at booking time,
    not here. There's also no way yet to tell which specific tier/payment
    period an ad is currently in beyond the `active` flag - a real
    tracking system (e.g. `expires_at` or a payments-linked status) is a
    separate piece of work for later.
    """
    resp = (
        supabase.table("ads")
        .select("*")
        .in_("tier", ["pro", "growth"])
        .eq("active", True)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data


def get_starter_ads():
    """All active Starter-tier ads - the small text-mention list.

    No category dedup here: Starter is a lighter-weight mention tier, not
    the exclusive featured slot, so multiple businesses in the same
    category are fine.
    """
    resp = (
        supabase.table("ads")
        .select("*")
        .eq("tier", "starter")
        .eq("active", True)
        .execute()
    )
    return resp.data
