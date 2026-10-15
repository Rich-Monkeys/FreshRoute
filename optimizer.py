"""FreshRoute V12-aligned transparent heuristic planner and evaluation helpers.

This is a Streamlit-ready implementation based on the final notebook's V10
feasible FEFO/FreshRoute comparison and V12 evaluation rules. It is a heuristic,
not a globally optimal solver. It never modifies the supplied source workbooks.
"""
from __future__ import annotations
import numpy as np
import pandas as pd

TURNAROUND_HOURS = 0.5
REFRIGERATED_REQUIRED_TOKEN = "refrigerated"

def _truthy(value):
    if pd.isna(value): return False
    if isinstance(value, (bool, np.bool_)): return bool(value)
    return str(value).strip().casefold() in {"true", "1", "yes", "y"}

def _compatible(storage, refrigerated):
    text = str(storage).strip().casefold()
    return not (REFRIGERATED_REQUIRED_TOKEN in text and "or" not in text) or bool(refrigerated)

def prepare_data(data):
    """Create normalized copies with destination and storage fields joined."""
    d = {k: v.copy() for k, v in data.items()}
    orders = d["customer_orders"]
    customers = d["customers"]
    fruits = d["fruits"]
    # Avoid duplicate join columns if a caller has already prepared a frame.
    orders = orders.drop(columns=[c for c in ["destination", "storage_requirement"] if c in orders.columns], errors="ignore")
    orders = orders.merge(customers[["customer_id","destination"]].drop_duplicates("customer_id"),
                          on="customer_id", how="left", validate="many_to_one")
    orders = orders.merge(fruits[["fruit_id","storage_requirement"]].drop_duplicates("fruit_id"),
                          on="fruit_id", how="left", validate="many_to_one")
    for col in ["order_date","required_delivery_date"]:
        orders[col] = pd.to_datetime(orders[col], errors="coerce")
    batches = d["inventory_batches"]
    for col in ["received_date","expiry_date"]:
        batches[col] = pd.to_datetime(batches[col], errors="coerce")
    for frame, cols in [
        (orders, ["quantity_requested_kg","selling_price_per_kg_inr"]),
        (batches, ["quantity_kg","unit_cost_inr_per_kg","handling_time_hour","handling_cost_per_kg_inr"]),
        (d["transport_routes"], ["distance_km","estimated_travel_time_hours"]),
        (d["vehicles"], ["capacity_kg","transport_cost_per_km_inr"]),
    ]:
        for col in cols:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    d["customer_orders"] = orders
    return d

def build_plan(data, strategy="fresh", delay_hours=0.0, demand_multiplier=1.0, expiry_shift_hours=0.0, price_multiplier=1.0, allow_late=False):
    """Run the same-feasibility heuristic for either 'fefo' or 'fresh' strategy."""
    d = prepare_data(data)
    orders = d["customer_orders"].copy()
    batches = d["inventory_batches"].copy()
    routes = d["transport_routes"].copy()
    vehicles = d["vehicles"].copy()
    orders["quantity_requested_kg"] = orders["quantity_requested_kg"].fillna(0).clip(lower=0) * float(demand_multiplier)
    orders["selling_price_per_kg_inr"] = orders["selling_price_per_kg_inr"].fillna(0) * float(price_multiplier)
    batches["expiry_date"] = batches["expiry_date"] + pd.to_timedelta(expiry_shift_hours, unit="h")
    routes["estimated_travel_time_hours"] = routes["estimated_travel_time_hours"] + float(delay_hours)
    if orders.empty:
        return pd.DataFrame(), pd.DataFrame(), orders, pd.Series(dtype=float), {}
    start_time = orders["order_date"].min()
    if pd.isna(start_time):
        start_time = batches["received_date"].min()
    if pd.isna(start_time): start_time = pd.Timestamp.now().normalize()
    stock = batches.set_index("batch_id")["quantity_kg"].fillna(0).clip(lower=0).to_dict()
    remaining_demand = orders.set_index("order_id")["quantity_requested_kg"].to_dict()
    order_idx = orders.set_index("order_id")
    vehicle_ready = {}
    for _, v in vehicles.iterrows():
        cap = v.get("capacity_kg")
        if pd.notna(cap) and cap > 0:
            vehicle_ready[str(v["vehicle_id"])] = start_time
    route_groups = {}
    for _, r in routes.dropna(subset=["destination","distance_km","estimated_travel_time_hours"]).iterrows():
        if r["distance_km"] < 0 or r["estimated_travel_time_hours"] < 0: continue
        key = str(r["destination"]).strip().casefold()
        route_groups.setdefault(key, []).append(r)
    trips, allocations = [], []
    horizon = orders["required_delivery_date"].max()
    max_iterations = max(1, len(orders))  # bounded runtime for interactive use
    for _ in range(max_iterations):
        options = []
        for oid, rem in remaining_demand.items():
            if rem <= 1e-8: continue
            order = order_idx.loc[oid]
            if pd.isna(order.get("required_delivery_date")) or pd.isna(order.get("order_date")): continue
            route_options = route_groups.get(str(order.get("destination","")).strip().casefold(), [])
            if not route_options: continue
            available = batches[batches["fruit_id"].eq(order["fruit_id"])].copy()
            available = available[
                available["received_date"].notna() & (available["received_date"] <= order["order_date"]) &
                available["expiry_date"].notna() & (available["expiry_date"] > order["order_date"])
            ].copy()
            available["remaining_kg"] = available["batch_id"].map(stock).fillna(0)
            available = available[available["remaining_kg"] > 1e-8]
            if available.empty: continue
            horizon_expiring = float(available.loc[available["expiry_date"] <= horizon, "remaining_kg"].sum()) if pd.notna(horizon) else 0.0
            risk_fraction = horizon_expiring / max(float(available["remaining_kg"].sum()), 1.0)
            deadline_hours = max(0.0, (order["required_delivery_date"] - order["order_date"]).total_seconds()/3600)
            urgency = 1.0/(1.0+deadline_hours)
            for route in route_options:
                travel = float(route["estimated_travel_time_hours"])
                distance = float(route["distance_km"])
                for _, vehicle in vehicles.iterrows():
                    cap = vehicle.get("capacity_kg")
                    rate = vehicle.get("transport_cost_per_km_inr")
                    if pd.isna(cap) or cap <= 0 or pd.isna(rate) or rate < 0: continue
                    if not _compatible(order.get("storage_requirement"), _truthy(vehicle.get("has_refrigeration"))): continue
                    vid = str(vehicle["vehicle_id"])
                    handling = max(0.0, float(available["handling_time_hour"].fillna(0).max()))
                    depart = max(order["order_date"], start_time, vehicle_ready.get(vid,start_time))
                    arrival = depart + pd.Timedelta(hours=handling+travel)
                    if arrival > order["required_delivery_date"] and not allow_late: continue
                    eligible = available[available["expiry_date"] >= arrival]
                    eligible_kg = float(eligible["remaining_kg"].sum())
                    qty = min(float(rem), float(cap), eligible_kg)
                    if qty <= 1e-8: continue
                    trip_cost = distance*float(rate)
                    weights = eligible["remaining_kg"].to_numpy(dtype=float)
                    avg_purchase = float(np.average(eligible["unit_cost_inr_per_kg"].fillna(0), weights=weights)) if weights.sum() else 0
                    avg_handling = float(np.average(eligible["handling_cost_per_kg_inr"].fillna(0), weights=weights)) if weights.sum() else 0
                    unit_margin = float(order["selling_price_per_kg_inr"])-avg_purchase-avg_handling-trip_cost/max(qty,1.0)
                    if strategy == "fefo":
                        score = 1000*risk_fraction + 10*urgency + 10*(qty/float(cap))
                    else:
                        score = 100*(qty/max(float(rem),1.0)) + 250*risk_fraction + 10*urgency + .10*unit_margin + 10*(qty/float(cap)) - .02*(trip_cost/max(qty,1.0))
                    options.append((score,qty,arrival,trip_cost,vid,str(oid),vehicle,route,depart,handling,eligible,unit_margin))
        if not options: break
        options.sort(key=lambda x:(-x[0],-x[1],x[2],x[3]/max(x[1],1e-8),x[4],x[5]))
        score,qty,arrival,trip_cost,vid,oid,vehicle,route,depart,handling,eligible,margin = options[0]
        trip_id = f"{strategy.upper()}_{len(trips)+1:04d}"
        left = qty
        order = order_idx.loc[oid]
        allocated_here = 0.0
        for _, batch in eligible.sort_values(["expiry_date","received_date","batch_id"]).iterrows():
            take = min(left, max(0.0,float(stock.get(batch["batch_id"],0))))
            if take <= 1e-8: continue
            stock[batch["batch_id"]] -= take
            left -= take
            allocated_here += take
            allocations.append({
                "trip_id":trip_id,"order_id":oid,"batch_id":batch["batch_id"],"qty_kg":take,
                "arrival_datetime":arrival,"decision_score":score,"route_id":route["route_id"],
                "vehicle_id":vid,"required_delivery_date":order["required_delivery_date"],
                "decision_reason":"Feasible route/vehicle; demand, capacity, shelf-life, expiry risk and economic factors considered."
            })
            if left <= 1e-8: break
        if allocated_here <= 1e-8: break
        remaining_demand[oid] = max(0.0, float(remaining_demand[oid])-allocated_here)
        vehicle_ready[vid] = arrival + pd.Timedelta(hours=TURNAROUND_HOURS)
        trips.append({
            "trip_id":trip_id,"order_id":oid,"destination":order["destination"],"fruit_id":order["fruit_id"],
            "fruit_type":order.get("fruit_type"),"route_id":route["route_id"],"vehicle_id":vid,
            "vehicle_capacity_kg":float(vehicle["capacity_kg"]),"trip_load_kg":allocated_here,
            "distance_km":float(route["distance_km"]),"travel_hours":float(route["estimated_travel_time_hours"]),
            "handling_hours":handling,"dispatch_datetime":depart,"arrival_datetime":arrival,
            "required_delivery_date":order["required_delivery_date"],
            "transport_cost_inr":float(route["distance_km"])*float(vehicle["transport_cost_per_km_inr"]),
            "on_time":bool(arrival <= order["required_delivery_date"])
        })
    alloc_df = pd.DataFrame(allocations)
    trips_df = pd.DataFrame(trips)
    # Build complete order summary, including orders that could not be served.
    summary=[]
    for _, order in orders.iterrows():
        oid=order["order_id"]; req=float(order["quantity_requested_kg"] or 0)
        alloc=float(req-remaining_demand.get(oid,req))
        alloc=max(0,min(req,alloc))
        summary.append({"order_id":oid,"customer_id":order["customer_id"],"destination":order["destination"],
                        "fruit_type":order["fruit_type"],"quantity_requested_kg":req,"allocated_kg":alloc,
                        "unfulfilled_kg":max(0,req-alloc),"status":"Fulfilled" if alloc>=req-1e-6 else ("Partially fulfilled" if alloc>1e-6 else "Unfulfilled")})
    order_summary=pd.DataFrame(summary)
    return alloc_df,trips_df,orders,pd.Series(stock,dtype=float),remaining_demand

def evaluate_plan(data, allocations, trips, orders, remaining_stock, remaining_demand, strategy, allow_late=False):
    """V12-style primary and projected no-future-demand KPIs; scenarios remain separate."""
    d=prepare_data(data); batches=d["inventory_batches"].copy()
    horizon=orders["required_delivery_date"].max()
    batch_meta=batches.set_index("batch_id")
    aa=allocations.copy()
    if not aa.empty:
        aa=aa.merge(orders[["order_id","selling_price_per_kg_inr"]].drop_duplicates("order_id"),on="order_id",how="left",validate="many_to_one")
        aa=aa.merge(batches[["batch_id","expiry_date","unit_cost_inr_per_kg","handling_cost_per_kg_inr","fruit_id"]],on="batch_id",how="left",validate="many_to_one")
        aa["arrival_datetime"]=pd.to_datetime(aa["arrival_datetime"])
        aa["on_time_pre_expiry"]=(aa["arrival_datetime"]<=aa["required_delivery_date"])&(aa["arrival_datetime"]<=aa["expiry_date"])
        aa["revenue_inr"]=np.where(aa["on_time_pre_expiry"],aa["qty_kg"]*aa["selling_price_per_kg_inr"],0.0)
        aa["purchase_cost_inr"]=aa["qty_kg"]*aa["unit_cost_inr_per_kg"].fillna(0)
        aa["handling_cost_inr"]=aa["qty_kg"]*aa["handling_cost_per_kg_inr"].fillna(0)
    else:
        aa=pd.DataFrame(columns=["qty_kg","on_time_pre_expiry","revenue_inr","purchase_cost_inr","handling_cost_inr"])
    residual=pd.Series(remaining_stock,dtype=float).clip(lower=0)
    expiry=pd.to_datetime(batch_meta["expiry_date"],errors="coerce")
    unit_cost=pd.to_numeric(batch_meta["unit_cost_inr_per_kg"],errors="coerce").fillna(0)
    expired_ids=[bid for bid in residual.index if pd.notna(expiry.get(bid)) and pd.notna(horizon) and expiry[bid]<=horizon]
    expired_residual=float(residual.reindex(expired_ids).fillna(0).sum())
    writeoff=float((residual.reindex(expired_ids).fillna(0)*unit_cost.reindex(expired_ids).fillna(0)).sum())
    demand=float(pd.to_numeric(orders["quantity_requested_kg"],errors="coerce").fillna(0).sum())
    allocated=float(allocations["qty_kg"].sum()) if not allocations.empty else 0.0
    on_time=float(aa.loc[aa["on_time_pre_expiry"],"qty_kg"].sum()) if not aa.empty else 0.0
    late_or_expired=float(allocated-on_time)
    revenue=float(aa["revenue_inr"].sum()) if not aa.empty else 0.0
    purchase=float(aa["purchase_cost_inr"].sum()) if not aa.empty else 0.0
    handling=float(aa["handling_cost_inr"].sum()) if not aa.empty else 0.0
    transport=float(trips["transport_cost_inr"].sum()) if not trips.empty else 0.0
    projected_spoilage=float(residual.sum())
    projected_writeoff=float((residual.reindex(unit_cost.index).fillna(0)*unit_cost).sum())
    net=revenue-purchase-handling-transport-writeoff
    projected_net=revenue-purchase-handling-transport-projected_writeoff
    return {
        "Strategy":strategy,"Demand_kg":demand,"Allocated_kg":allocated,"Unmet_demand_kg":max(0,demand-allocated),
        "Demand_fulfilment_pct":100*allocated/demand if demand else 0,
        "On_time_pre_expiry_kg":on_time,"Late_or_expired_allocated_kg":late_or_expired,
        "On_time_pre_expiry_fulfilment_pct":100*on_time/demand if demand else 0,
        "Revenue_INR":revenue,"Purchase_cost_INR":purchase,"Handling_cost_INR":handling,
        "Transport_cost_INR":transport,"Residual_spoilage_writeoff_INR":writeoff,"Net_contribution_INR":net,
        "Expired_by_order_horizon_kg":expired_residual,"Residual_inventory_kg":projected_spoilage,
        "Projected_spoilage_no_future_demand_kg":projected_spoilage,
        "Projected_writeoff_no_future_demand_INR":projected_writeoff,
        "Net_contribution_projected_no_future_demand_INR":projected_net,
        "Trips":int(len(trips)),"Constraint_failures":int((audit_plan(data,allocations,trips,orders,remaining_stock,allow_late=allow_late)["Status"]!="PASS").sum())
    }

def audit_plan(data, allocations, trips, orders, remaining_stock, allow_late=False):
    d=prepare_data(data); batches=d["inventory_batches"]; vehicles=d["vehicles"]; routes=d["transport_routes"]
    checks=[]
    def add(name,passed,evidence): checks.append({"Check":name,"Status":"PASS" if bool(passed) else "FAIL","Evidence":str(evidence)})
    # Demand cap
    requested=orders.set_index("order_id")["quantity_requested_kg"].to_dict()
    used=allocations.groupby("order_id")["qty_kg"].sum().to_dict() if not allocations.empty else {}
    demand_ok=all(float(q)<=float(requested.get(k,0))+1e-6 for k,q in used.items())
    add("Allocations do not exceed order demand",demand_ok,f"{len(used)} allocated orders checked")
    initial=batches.set_index("batch_id")["quantity_kg"].fillna(0).to_dict()
    batch_used=allocations.groupby("batch_id")["qty_kg"].sum().to_dict() if not allocations.empty else {}
    stock_ok=all(float(q)<=float(initial.get(k,0))+1e-6 for k,q in batch_used.items())
    add("Batch stock not exceeded",stock_ok,f"{len(batch_used)} batches checked")
    capacity_ok=trips.empty or bool((trips["trip_load_kg"]<=trips["vehicle_capacity_kg"]+1e-6).all())
    add("Vehicle capacity not exceeded",capacity_ok,"Per-trip load compared with vehicle capacity")
    expiry_ok=True
    if not allocations.empty:
        aa=allocations.merge(batches[["batch_id","expiry_date"]],on="batch_id",how="left")
        expiry_ok=bool((pd.to_datetime(aa["arrival_datetime"])<=pd.to_datetime(aa["expiry_date"])).all())
    add("Arrival is before batch expiry",expiry_ok,"Allocation-level arrival vs batch expiry")
    deadline_ok=trips.empty or bool((pd.to_datetime(trips["arrival_datetime"])<=pd.to_datetime(trips["required_delivery_date"])).all())
    add("All planned trips meet delivery deadlines", deadline_ok or allow_late, "Late arrivals are permitted by the selected planner option" if allow_late else "Hard-deadline planner; late deliveries are not silently accepted")
    # Vehicle availability/overlap
    overlap=True
    if not trips.empty:
        for _,g in trips.sort_values("dispatch_datetime").groupby("vehicle_id"):
            recs=list(g.to_dict("records"))
            for prev,cur in zip(recs,recs[1:]):
                prev_end=pd.to_datetime(prev["arrival_datetime"])+pd.Timedelta(hours=TURNAROUND_HOURS)
                if pd.to_datetime(cur["dispatch_datetime"]) < prev_end: overlap=False
    add("No same-vehicle schedule overlap",overlap,f"Turnaround allowance {TURNAROUND_HOURS}h")
    return pd.DataFrame(checks)

def build_markdown_opportunities(data, remaining_stock, uptake_pct=50.0):
    d=prepare_data(data); batches=d["inventory_batches"].copy(); fruits=d["fruits"]
    market=fruits[["fruit_id","market_price_per_kg_inr"]].drop_duplicates("fruit_id")
    orders=d["customer_orders"]; start=orders["order_date"].min()
    out=batches.copy()
    out["remaining_kg"]=out["batch_id"].map(pd.Series(remaining_stock)).fillna(0).clip(lower=0)
    out=out.merge(fruits[["fruit_id","fruit_name","market_price_per_kg_inr"]],on="fruit_id",how="left")
    out["hours_to_expiry_from_plan_start"]=(pd.to_datetime(out["expiry_date"])-start).dt.total_seconds()/3600
    h=out["hours_to_expiry_from_plan_start"]
    out["recommended_discount_pct"]=np.select([h<=24,h<=48,h<=72],[20,10,5],default=0)
    out["risk_band"]=np.select([h<=24,h<=48,h<=72],["Critical <=24h","High <=48h","Moderate <=72h"],default="Lower immediate expiry risk")
    out["assumed_uptake_pct"]=float(uptake_pct)
    out["assumed_sellable_kg"]=out["remaining_kg"]*float(uptake_pct)/100
    out["assumed_discount_price_inr_per_kg"]=out["market_price_per_kg_inr"]*(1-out["recommended_discount_pct"]/100)
    out["assumed_markdown_revenue_inr"]=out["assumed_sellable_kg"]*out["assumed_discount_price_inr_per_kg"]
    out["is_scenario_only_not_actual_sales"]=True
    out["recommendation_reason"]=np.where(out["recommended_discount_pct"]>0,"Scenario only: residual stock has short estimated time to expiry; response is not observed.","No immediate markdown under the 72-hour rule; continue monitoring.")
    return out[out["remaining_kg"]>1e-8].sort_values("hours_to_expiry_from_plan_start")

def input_quality_issues(data):
    issues=[]
    checks={"inventory_batches":{"batch_id","quantity_kg","received_date","expiry_date"},
            "transport_routes":{"estimated_travel_time_hours","distance_km","destination"},
            "vehicles":{"capacity_kg","transport_cost_per_km_inr"},
            "customer_orders":{"order_id","quantity_requested_kg","required_delivery_date","fruit_id"}}
    for name,cols in checks.items():
        missing=cols-set(data[name].columns)
        if missing: issues.append(f"MISSING_REQUIRED_COLUMNS_{name.upper()}")
    if issues: return issues
    inv=data["inventory_batches"]
    q=pd.to_numeric(inv["quantity_kg"],errors="coerce")
    if q.isna().any() or (q<0).any(): issues.append("INVALID_INVENTORY_QUANTITY")
    rec=pd.to_datetime(inv["received_date"],errors="coerce"); exp=pd.to_datetime(inv["expiry_date"],errors="coerce")
    if rec.isna().any() or exp.isna().any() or (exp<=rec).any(): issues.append("INVALID_INVENTORY_DATES")
    r=data["transport_routes"]
    t=pd.to_numeric(r["estimated_travel_time_hours"],errors="coerce"); dist=pd.to_numeric(r["distance_km"],errors="coerce")
    if t.isna().any() or (t<0).any(): issues.append("INVALID_TRAVEL_TIME")
    if dist.isna().any() or (dist<0).any(): issues.append("INVALID_DISTANCE")
    v=data["vehicles"]; cap=pd.to_numeric(v["capacity_kg"],errors="coerce"); rate=pd.to_numeric(v["transport_cost_per_km_inr"],errors="coerce")
    if cap.isna().any() or (cap<=0).any(): issues.append("INVALID_VEHICLE_CAPACITY")
    if rate.isna().any() or (rate<0).any(): issues.append("INVALID_TRANSPORT_RATE")
    if inv["batch_id"].duplicated().any(): issues.append("DUPLICATE_BATCH_ID")
    return sorted(set(issues))
