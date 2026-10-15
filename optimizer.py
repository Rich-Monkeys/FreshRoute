
import pandas as pd
import numpy as np

# Explicit modelling assumptions.
# Keep these consistent with the Baseline KPI calculation.
BASE_COST_RATE_INR_PER_KG_KM = 0.05
REFRIGERATED_MULTIPLIER = 1.30
STANDARD_MULTIPLIER = 1.00
TURNAROUND_HOURS = 0.5


def _bool(value):
    if pd.isna(value):
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().casefold() in {
        "true", "1", "yes", "y", "refrigerated"
    }


def _compatible(storage_requirement, refrigerated):
    requirement = str(storage_requirement).strip().casefold()

    # Explicit refrigeration requirement.
    if "refrigerated" in requirement and "or" not in requirement:
        return refrigerated

    # The supplied data does not define exact temperature ranges
    # for other storage requirements.
    return True


def _number(value, default=0.0):
    """Convert a value to a finite number safely."""
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _on_time(arrival, deadline):
    """
    If a deadline is stored exactly at midnight, treat it as a
    date-only deadline and allow arrival during that calendar day.
    Otherwise, respect the exact deadline time.
    """
    if pd.isna(deadline):
        return False

    if deadline == deadline.normalize():
        return arrival.normalize() <= deadline.normalize()

    return arrival <= deadline


def build_plan(data, allow_late=False):
    """
    Greedy distribution planner.

    Constraints:
    - Do not allocate more than remaining batch inventory.
    - Do not exceed vehicle capacity on an individual trip.
    - Respect vehicle refrigeration compatibility where explicit.
    - Do not schedule arrival after batch expiry.
    - Track each vehicle's next available time.
    - Prefer on-time trips when feasible.
    - Evaluate multiple routes to a destination.

    Returns:
        trips, order_summary, remaining_stock
    """

    orders = data["customer_orders"].copy()
    customers = data["customers"].copy()
    fruits = data["fruits"].copy()
    batches = data["inventory_batches"].copy()
    routes = data["transport_routes"].copy()
    vehicles = data["vehicles"].copy()

    # -----------------------------
    # Parse dates and numeric fields
    # -----------------------------
    for column in ["order_date", "required_delivery_date"]:
        orders[column] = pd.to_datetime(
            orders[column], errors="coerce"
        )

    for column in ["received_date", "expiry_date"]:
        batches[column] = pd.to_datetime(
            batches[column], errors="coerce"
        )

    numeric_columns = [
        (orders, [
            "quantity_requested_kg",
            "selling_price_per_kg_inr",
        ]),
        (batches, [
            "quantity_kg",
            "unit_cost_inr_per_kg",
            "handling_time_hour",
            "handling_cost_per_kg_inr",
        ]),
        (routes, [
            "distance_km",
            "estimated_travel_time_hours",
        ]),
        (vehicles, [
            "capacity_kg",
            "transport_cost_per_km_inr",
        ]),
    ]

    for frame, columns in numeric_columns:
        for column in columns:
            frame[column] = pd.to_numeric(
                frame[column], errors="coerce"
            )

    # -----------------------------
    # Join order destination and fruit information
    # -----------------------------
    orders = orders.merge(
        customers[["customer_id", "destination"]],
        on="customer_id",
        how="left",
        validate="many_to_one",
    )

    orders = orders.merge(
        fruits[[
            "fruit_id",
            "fruit_name",
            "storage_requirement",
        ]],
        on="fruit_id",
        how="left",
        validate="many_to_one",
    )

    orders["destination_key"] = (
        orders["destination"]
        .astype(str)
        .str.strip()
        .str.casefold()
    )

    routes["destination_key"] = (
        routes["destination"]
        .astype(str)
        .str.strip()
        .str.casefold()
    )

    routes = routes.dropna(
        subset=[
            "estimated_travel_time_hours",
            "distance_km",
        ]
    ).copy()

    routes = routes[
        (routes["estimated_travel_time_hours"] >= 0)
        & (routes["distance_km"] >= 0)
    ]

    # Keep ALL routes per destination rather than only the fastest.
    route_options = {
        destination: group.sort_values(
            ["estimated_travel_time_hours", "distance_km"]
        ).to_dict("records")
        for destination, group in routes.groupby(
            "destination_key", sort=False
        )
    }

    # -----------------------------
    # Inventory and vehicle states
    # -----------------------------
    batches = batches.copy()

    stock_left = (
        batches.set_index("batch_id")["quantity_kg"]
        .fillna(0)
        .clip(lower=0)
        .to_dict()
    )

    batch_lookup = batches.set_index("batch_id").to_dict("index")

    if orders["order_date"].notna().any():
        planning_start = orders["order_date"].min()
    elif batches["received_date"].notna().any():
        planning_start = batches["received_date"].min()
    else:
        planning_start = (
            pd.Timestamp.now().normalize()
            + pd.Timedelta(hours=8)
        )

    vehicle_ready = {}

    for _, vehicle in vehicles.iterrows():
        capacity = vehicle.get("capacity_kg")

        if pd.notna(capacity) and capacity > 0:
            vehicle_ready[vehicle["vehicle_id"]] = planning_start

    rows = []
    order_results = []
    trip_number = 0

    # Earlier deadlines first; larger orders break ties.
    ordered = orders.sort_values(
        [
            "required_delivery_date",
            "order_date",
            "quantity_requested_kg",
        ],
        ascending=[True, True, False],
        na_position="last",
    )

    # -----------------------------
    # Plan each order
    # -----------------------------
    for _, order in ordered.iterrows():

        demand = max(
            0.0,
            _number(order.get("quantity_requested_kg")),
        )

        allocated_total = 0.0
        order_id = order["order_id"]

        destination_key = order["destination_key"]
        possible_routes = route_options.get(destination_key, [])

        if not possible_routes:
            order_results.append({
                "order_id": order_id,
                "quantity_requested_kg": demand,
                "allocated_kg": 0.0,
                "unfulfilled_kg": demand,
                "status": "No matching route",
            })
            continue

        while demand - allocated_total > 1e-6:

            # Get available batches for the required fruit.
            candidates = batches[
                batches["fruit_id"] == order["fruit_id"]
            ].copy()

            candidates["stock_left"] = (
                candidates["batch_id"]
                .map(stock_left)
                .fillna(0)
            )

            candidates = candidates[
                candidates["stock_left"] > 1e-6
            ].sort_values(
                ["expiry_date", "received_date"],
                na_position="last",
            )

            if candidates.empty:
                break

            best = None

            # Evaluate vehicle, route and batch combinations.
            for _, vehicle in vehicles.iterrows():

                vehicle_id = vehicle["vehicle_id"]
                capacity = vehicle.get("capacity_kg")

                if (
                    vehicle_id not in vehicle_ready
                    or pd.isna(capacity)
                    or capacity <= 0
                ):
                    continue

                refrigerated = _bool(
                    vehicle.get("has_refrigeration")
                )

                if not _compatible(
                    order.get("storage_requirement"),
                    refrigerated,
                ):
                    continue

                multiplier = (
                    REFRIGERATED_MULTIPLIER
                    if refrigerated
                    else STANDARD_MULTIPLIER
                )

                for route in possible_routes:

                    travel_hours = _number(
                        route.get("estimated_travel_time_hours"),
                        default=-1,
                    )

                    distance = _number(
                        route.get("distance_km"),
                        default=-1,
                    )

                    if travel_hours < 0 or distance < 0:
                        continue

                    for _, batch in candidates.iterrows():

                        batch_id = batch["batch_id"]

                        # Dispatch cannot precede the planning start,
                        # order date or batch receipt date.
                        start_candidates = [planning_start]

                        if pd.notna(order["order_date"]):
                            start_candidates.append(
                                order["order_date"]
                            )

                        if pd.notna(batch["received_date"]):
                            start_candidates.append(
                                batch["received_date"]
                            )

                        handling_hours = max(
                            0.0,
                            _number(
                                batch.get("handling_time_hour")
                            ),
                        )

                        ready_time = (
                            max(start_candidates)
                            + pd.Timedelta(
                                hours=handling_hours
                            )
                        )

                        departure = max(
                            ready_time,
                            vehicle_ready[vehicle_id],
                        )

                        arrival = departure + pd.Timedelta(
                            hours=travel_hours
                        )

                        expiry = batch["expiry_date"]

                        # Reject expired-on-arrival allocations.
                        if (
                            pd.notna(expiry)
                            and arrival > expiry
                        ):
                            continue

                        deadline = order[
                            "required_delivery_date"
                        ]

                        on_time = _on_time(arrival, deadline)

                        if not allow_late and not on_time:
                            continue

                        # This is the same explicit assumption used
                        # in the current GUI's KPI calculation.
                        estimated_unit_transport_cost = (
                            distance
                            * BASE_COST_RATE_INR_PER_KG_KM
                            * multiplier
                        )

                        # Priorities:
                        # 1. On-time delivery
                        # 2. Earliest expiry (FEFO)
                        # 3. Earlier arrival
                        # 4. Lower estimated unit transport cost
                        # 5. Stable vehicle ID tie-break
                        rank = (
                            not on_time,
                            (
                                pd.Timestamp.max
                                if pd.isna(expiry)
                                else expiry
                            ),
                            arrival,
                            estimated_unit_transport_cost,
                            str(vehicle_id),
                            str(route.get("route_id", "")),
                            str(batch_id),
                        )

                        if best is None or rank < best["rank"]:
                            best = {
                                "rank": rank,
                                "vehicle": vehicle,
                                "batch": batch,
                                "route": route,
                                "departure": departure,
                                "arrival": arrival,
                                "on_time": on_time,
                                "travel_hours": travel_hours,
                                "distance": distance,
                                "multiplier": multiplier,
                            }

            if best is None:
                break

            vehicle = best["vehicle"]
            batch = best["batch"]
            route = best["route"]

            vehicle_id = vehicle["vehicle_id"]
            batch_id = batch["batch_id"]

            quantity = min(
                demand - allocated_total,
                max(0.0, _number(stock_left.get(batch_id))),
                max(0.0, _number(vehicle.get("capacity_kg"))),
            )

            if quantity <= 1e-6:
                break

            trip_number += 1

            selling_price = _number(
                order.get("selling_price_per_kg_inr")
            )

            purchase_price = _number(
                batch.get("unit_cost_inr_per_kg")
            )

            handling_cost_rate = _number(
                batch.get("handling_cost_per_kg_inr")
            )

            transport_cost = (
                quantity
                * best["distance"]
                * BASE_COST_RATE_INR_PER_KG_KM
                * best["multiplier"]
            )

            rows.append({
                "trip_id": f"PLAN-{trip_number:04d}",
                "order_id": order_id,
                "batch_id": batch_id,
                "fruit_id": order["fruit_id"],
                "fruit_name": order["fruit_name"],
                "destination": order["destination"],
                "route_id": route.get("route_id"),
                "vehicle_id": vehicle_id,
                "trip_load_kg": quantity,
                "vehicle_capacity_kg": float(
                    vehicle["capacity_kg"]
                ),
                "distance_km": best["distance"],
                "estimated_travel_hours": best["travel_hours"],
                "dispatch_time": best["departure"],
                "estimated_arrival_datetime": best["arrival"],
                "required_delivery_date": order[
                    "required_delivery_date"
                ],
                "on_time": bool(best["on_time"]),
                "expiry_date": batch["expiry_date"],
                "selling_price_per_kg_inr": selling_price,
                "unit_cost_inr_per_kg": purchase_price,
                "handling_cost_per_kg_inr": handling_cost_rate,
                "estimated_revenue_inr": (
                    quantity * selling_price
                ),
                "estimated_purchase_cost_inr": (
                    quantity * purchase_price
                ),
                "estimated_handling_cost_inr": (
                    quantity * handling_cost_rate
                ),
                "estimated_transport_cost_inr": transport_cost,
                "decision_reason": (
                    "FEFO batch; route, capacity and expiry checked; "
                    + (
                        "on-time delivery"
                        if best["on_time"]
                        else "late fallback"
                    )
                ),
            })

            # Update the shared inventory and vehicle schedule.
            stock_left[batch_id] -= quantity
            allocated_total += quantity

            vehicle_ready[vehicle_id] = (
                best["arrival"]
                + pd.Timedelta(hours=TURNAROUND_HOURS)
            )

        if allocated_total >= demand - 1e-6:
            status = "Fulfilled"
        elif allocated_total > 1e-6:
            status = "Partially fulfilled"
        else:
            status = "Unfulfilled"

        order_results.append({
            "order_id": order_id,
            "quantity_requested_kg": demand,
            "allocated_kg": allocated_total,
            "unfulfilled_kg": max(
                0.0, demand - allocated_total
            ),
            "status": status,
        })

    # -----------------------------
    # Build outputs
    # -----------------------------
    trips = pd.DataFrame(rows)
    order_summary = pd.DataFrame(order_results)

    if not trips.empty:
        trips["late_kg"] = np.where(
            trips["on_time"],
            0.0,
            trips["trip_load_kg"],
        )

    remaining_stock = pd.DataFrame([
        {
            "batch_id": batch_id,
            "remaining_kg": max(0.0, quantity),
        }
        for batch_id, quantity in stock_left.items()
    ])

    return trips, order_summary, remaining_stock


def calculate_metrics(data, trips, order_summary):
    """Calculate operational and economic KPIs for the generated plan."""

    orders = data["customer_orders"]

    demand = float(
        pd.to_numeric(
            orders["quantity_requested_kg"],
            errors="coerce",
        ).fillna(0).sum()
    )

    allocated = (
        float(
            pd.to_numeric(
                order_summary["allocated_kg"],
                errors="coerce",
            ).fillna(0).sum()
        )
        if not order_summary.empty
        else 0.0
    )

    if not trips.empty:
        revenue = float(
            trips["estimated_revenue_inr"].sum()
        )
        purchase = float(
            trips["estimated_purchase_cost_inr"].sum()
        )
        handling = float(
            trips["estimated_handling_cost_inr"].sum()
        )
        transport = float(
            trips["estimated_transport_cost_inr"].sum()
        )

        on_time = float(
            trips.loc[
                trips["on_time"],
                "trip_load_kg",
            ].sum()
        )

        late = float(
            trips.loc[
                ~trips["on_time"],
                "trip_load_kg",
            ].sum()
        )
    else:
        revenue = 0.0
        purchase = 0.0
        handling = 0.0
        transport = 0.0
        on_time = 0.0
        late = 0.0

    # Compute remaining stock directly from the batch-level output
    # in build_plan(), not from total inventory minus total allocation.
    total_stock = float(
        pd.to_numeric(
            data["inventory_batches"]["quantity_kg"],
            errors="coerce",
        ).fillna(0).clip(lower=0).sum()
    )

    remaining_estimate = max(0.0, total_stock - allocated)

    net_contribution = (
        revenue - purchase - handling - transport
    )

    return {
        "Demand (kg)": demand,
        "Allocated (kg)": allocated,
        "Unfulfilled (kg)": max(0.0, demand - allocated),
        "Fulfilment rate (%)": (
            100.0 * allocated / demand if demand else 0.0
        ),
        "On-time allocated (kg)": on_time,
        "Late allocated (kg)": late,
        "Revenue (INR)": revenue,
        "Purchase cost (INR)": purchase,
        "Handling cost (INR)": handling,
        "Transport cost (INR)": transport,
        "Net contribution (INR)": net_contribution,
        "Unallocated inventory estimate (kg)": remaining_estimate,
    }
