import pandas as pd

def build_discount_recommendations(data, remaining_stock, now=None):
    """Rule-based recommendations only; this does not apply discounts or alter source prices."""
    batches = data["inventory_batches"].copy()
    fruits = data["fruits"][["fruit_id", "fruit_name"]].copy()
    batches["expiry_date"] = pd.to_datetime(batches["expiry_date"], errors="coerce")
    if now is None:
        now = pd.to_datetime(data["customer_orders"]["order_date"], errors="coerce").min()
    if pd.isna(now):
        now = pd.Timestamp.now()
    rem = remaining_stock.set_index("batch_id")["remaining_kg"].to_dict() if not remaining_stock.empty else {}
    batches["remaining_kg"] = batches.apply(lambda r: rem.get(r["batch_id"], r["quantity_kg"]), axis=1)
    batches = batches.merge(fruits, on="fruit_id", how="left")
    batches["hours_to_expiry"] = (batches["expiry_date"] - now).dt.total_seconds() / 3600
    batches["recommended_discount_pct"] = 0
    batches.loc[(batches["remaining_kg"] > 0) & (batches["hours_to_expiry"] <= 48) & (batches["hours_to_expiry"] > 24), "recommended_discount_pct"] = 10
    batches.loc[(batches["remaining_kg"] > 0) & (batches["hours_to_expiry"] <= 24) & (batches["hours_to_expiry"] > 0), "recommended_discount_pct"] = 20
    batches["recommendation"] = "No discount trigger under current rule"
    batches.loc[(batches["remaining_kg"] > 0) & (batches["hours_to_expiry"] <= 48) & (batches["hours_to_expiry"] > 0), "recommendation"] = "Consider markdown; verify demand and cold-chain feasibility"
    batches.loc[batches["hours_to_expiry"] <= 0, "recommendation"] = "Expired: remove from sale; do not recommend discount"
    return batches[["batch_id", "fruit_name", "remaining_kg", "expiry_date", "hours_to_expiry", "recommended_discount_pct", "recommendation"]].sort_values(["hours_to_expiry", "remaining_kg"])
