import pandas as pd


def _remaining_stock_to_frame(remaining_stock):
    """Normalize optimizer residual stock (Series/dict/DataFrame/list) to batch_id + remaining_kg."""
    if remaining_stock is None:
        return pd.DataFrame(columns=["batch_id", "remaining_kg"])

    if isinstance(remaining_stock, pd.Series):
        # optimizer.build_plan returns a Series indexed by batch_id.
        frame = remaining_stock.rename("remaining_kg").rename_axis("batch_id").reset_index()
    elif isinstance(remaining_stock, pd.DataFrame):
        frame = remaining_stock.copy()
        if "batch_id" not in frame.columns and frame.index.name == "batch_id":
            frame = frame.reset_index()
        if "remaining_kg" not in frame.columns:
            # Accept a one-value-column table where the batch ID is explicit.
            value_cols = [c for c in frame.columns if c != "batch_id"]
            if "batch_id" in frame.columns and len(value_cols) == 1:
                frame = frame.rename(columns={value_cols[0]: "remaining_kg"})
            else:
                raise ValueError("Remaining-stock DataFrame must contain 'batch_id' and 'remaining_kg'.")
    elif isinstance(remaining_stock, dict):
        frame = pd.DataFrame({"batch_id": list(remaining_stock.keys()), "remaining_kg": list(remaining_stock.values())})
    else:
        frame = pd.DataFrame(remaining_stock)
        if not {"batch_id", "remaining_kg"}.issubset(frame.columns):
            raise ValueError("Unsupported remaining-stock format; expected batch_id and remaining_kg.")

    if frame.empty:
        return pd.DataFrame(columns=["batch_id", "remaining_kg"])
    if not {"batch_id", "remaining_kg"}.issubset(frame.columns):
        raise ValueError("Remaining-stock data is missing 'batch_id' or 'remaining_kg'.")
    frame = frame[["batch_id", "remaining_kg"]].copy()
    frame["batch_id"] = frame["batch_id"].astype(str)
    frame["remaining_kg"] = pd.to_numeric(frame["remaining_kg"], errors="coerce").fillna(0).clip(lower=0)
    # Avoid duplicate IDs causing ambiguous mappings; sum quantities by batch.
    return frame.groupby("batch_id", as_index=False)["remaining_kg"].sum()


def build_discount_recommendations(data, remaining_stock, now=None):
    """Rule-based recommendations only; does not apply discounts or alter source prices."""
    batches = data["inventory_batches"].copy()
    fruits = data["fruits"][["fruit_id", "fruit_name"]].copy()
    batches["batch_id"] = batches["batch_id"].astype(str)
    batches["expiry_date"] = pd.to_datetime(batches["expiry_date"], errors="coerce")

    if now is None:
        order_dates = pd.to_datetime(data["customer_orders"].get("order_date"), errors="coerce")
        now = order_dates.min() if order_dates is not None else pd.NaT
    now = pd.to_datetime(now, errors="coerce")
    if pd.isna(now):
        now = pd.Timestamp.now()

    rem_frame = _remaining_stock_to_frame(remaining_stock)
    if not rem_frame.empty:
        batches = batches.merge(rem_frame, on="batch_id", how="left", suffixes=("", "_plan"))
        # Missing batch IDs in a supplied plan mean no residual stock recorded, not original stock.
        batches["remaining_kg"] = batches["remaining_kg"].fillna(0)
    else:
        # No plan residuals supplied: use original inventory as a clearly conservative fallback.
        batches["remaining_kg"] = pd.to_numeric(batches["quantity_kg"], errors="coerce").fillna(0).clip(lower=0)

    batches = batches.merge(fruits, on="fruit_id", how="left")
    batches["hours_to_expiry"] = (batches["expiry_date"] - now).dt.total_seconds() / 3600
    batches["recommended_discount_pct"] = 0
    active_stock = batches["remaining_kg"] > 1e-8
    batches.loc[active_stock & (batches["hours_to_expiry"] <= 48) & (batches["hours_to_expiry"] > 24), "recommended_discount_pct"] = 10
    batches.loc[active_stock & (batches["hours_to_expiry"] <= 24) & (batches["hours_to_expiry"] > 0), "recommended_discount_pct"] = 20

    batches["recommendation"] = "No discount trigger under current rule"
    batches.loc[active_stock & (batches["hours_to_expiry"] <= 48) & (batches["hours_to_expiry"] > 0), "recommendation"] = "Consider markdown; verify demand and cold-chain feasibility"
    batches.loc[batches["hours_to_expiry"] <= 0, "recommendation"] = "Expired: remove from sale; do not recommend discount"
    return batches[["batch_id", "fruit_name", "remaining_kg", "expiry_date", "hours_to_expiry", "recommended_discount_pct", "recommendation"]].sort_values(["hours_to_expiry", "remaining_kg"])
