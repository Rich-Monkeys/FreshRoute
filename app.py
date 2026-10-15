
import streamlit as st
import pandas as pd
import plotly.express as px

from data_loader import load_data
from optimizer import build_plan, calculate_metrics
from metrics import build_discount_recommendations

st.set_page_config(
    page_title="FreshRoute",
    page_icon="🌿",
    layout="wide"
)

st.markdown("""
<style>
.block-container {
    padding-top: 1.5rem;
    max-width: 1450px;
}
.hero {
    background: linear-gradient(120deg, #153f35, #32806b);
    color: white;
    padding: 28px;
    border-radius: 18px;
    margin-bottom: 24px;
}
.hero h1 { color: white; margin-bottom: 5px; }
.hero p { color: #e2f2eb; margin-bottom: 0; }
div[data-testid="stMetric"] {
    background: #f3f8f5;
    border: 1px solid #dceae2;
    padding: 16px;
    border-radius: 12px;
}
</style>

<div class="hero">
    <h1>🌿 FreshRoute</h1>
    <p>Intelligent Perishable Produce Distribution Decision Support</p>
</div>
""", unsafe_allow_html=True)


@st.cache_data
def get_data():
    return load_data()


try:
    data = get_data()
except Exception as exc:
    st.error(f"Unable to load source data: {exc}")
    st.stop()


st.sidebar.title("FreshRoute")
page = st.sidebar.radio(
    "Navigate",
    [
        "Overview",
        "Inventory & Expiry",
        "Distribution Planner",
        "Performance & Discounts",
    ],
)

orders = data["customer_orders"]
batches = data["inventory_batches"]

total_demand = pd.to_numeric(
    orders["quantity_requested_kg"], errors="coerce"
).fillna(0).sum()

total_stock = pd.to_numeric(
    batches["quantity_kg"], errors="coerce"
).fillna(0).sum()

if "plan_cache" not in st.session_state:
    st.session_state.plan_cache = None


# --------------------------------------------------
# OVERVIEW
# --------------------------------------------------

if page == "Overview":

    st.subheader("Operations Overview")

    c1, c2, c3, c4 = st.columns(4)

    c1.metric("Total Demand", f"{total_demand:,.0f} kg")
    c2.metric("Available Inventory", f"{total_stock:,.0f} kg")
    c3.metric("Customer Orders", f"{len(orders):,}")
    c4.metric(
        "Inventory / Demand",
        f"{100 * total_stock / total_demand:.1f}%"
        if total_demand else "—"
    )

    if total_demand > total_stock:
        st.warning(
            f"Demand exceeds stock by "
            f"{total_demand - total_stock:,.0f} kg. "
            "The system must prioritise available inventory."
        )

    left, right = st.columns(2)

    with left:
        demand_chart = (
            orders.groupby("fruit_type", as_index=False)
            ["quantity_requested_kg"].sum()
            .sort_values("quantity_requested_kg", ascending=False)
        )

        st.plotly_chart(
            px.bar(
                demand_chart,
                x="fruit_type",
                y="quantity_requested_kg",
                title="Demand by Fruit",
                labels={
                    "fruit_type": "Fruit",
                    "quantity_requested_kg": "Demand (kg)",
                },
            ),
            use_container_width=True,
        )

    with right:
        inventory_chart = (
            batches.merge(
                data["fruits"][["fruit_id", "fruit_name"]],
                on="fruit_id",
                how="left",
            )
            .groupby("fruit_name", as_index=False)["quantity_kg"].sum()
            .sort_values("quantity_kg", ascending=False)
        )

        st.plotly_chart(
            px.bar(
                inventory_chart,
                x="fruit_name",
                y="quantity_kg",
                title="Inventory by Fruit",
                labels={
                    "fruit_name": "Fruit",
                    "quantity_kg": "Inventory (kg)",
                },
            ),
            use_container_width=True,
        )


# --------------------------------------------------
# INVENTORY AND EXPIRY
# --------------------------------------------------

elif page == "Inventory & Expiry":

    st.subheader("Inventory Batches & Shelf Life")

    inventory = batches.merge(
        data["fruits"][["fruit_id", "fruit_name", "storage_requirement"]],
        on="fruit_id",
        how="left",
    )

    inventory["expiry_date"] = pd.to_datetime(
        inventory["expiry_date"], errors="coerce"
    )

    inventory["received_date"] = pd.to_datetime(
        inventory["received_date"], errors="coerce"
    )

    fruit_options = sorted(
        inventory["fruit_name"].dropna().unique()
    )

    selected_fruits = st.multiselect(
        "Filter by fruit",
        fruit_options,
    )

    if selected_fruits:
        inventory = inventory[
            inventory["fruit_name"].isin(selected_fruits)
        ]

    st.dataframe(
        inventory.sort_values("expiry_date"),
        use_container_width=True,
        hide_index=True,
    )

    st.caption(
        "Expiry dates are taken from the supplied dataset. "
        "They are estimates and do not represent live sensor readings."
    )


# --------------------------------------------------
# DISTRIBUTION PLANNER
# --------------------------------------------------

elif page == "Distribution Planner":

    st.subheader("Distribution Planner")

    st.write(
        "The planner uses the current greedy allocation heuristic, "
        "with batch selection, vehicle capacity, travel time and "
        "expiry checks."
    )

    allow_late = st.toggle(
        "Allow late deliveries when feasible",
        value=True,
    )

    if st.button("Run Distribution Planner", type="primary"):

        with st.spinner("Generating distribution plan..."):

            trips, fulfilment, remaining = build_plan(
                data,
                allow_late=allow_late,
            )

            st.session_state.plan_cache = (
                trips,
                fulfilment,
                remaining,
                allow_late,
            )

    if st.session_state.plan_cache is None:

        st.info("Click the button above to generate a plan.")

    else:

        trips, fulfilment, remaining, _ = (
            st.session_state.plan_cache
        )

        allocated = (
            fulfilment["allocated_kg"].sum()
            if not fulfilment.empty else 0
        )

        st.success(
            f"Plan generated: {allocated:,.0f} kg allocated "
            f"across {len(trips)} trip records."
        )

        st.markdown("### Dispatch Schedule")

        if not trips.empty:

            st.dataframe(
                trips,
                use_container_width=True,
                hide_index=True,
            )

            st.download_button(
                "Download Dispatch Plan",
                trips.to_csv(index=False).encode("utf-8-sig"),
                "freshroute_dispatch_plan.csv",
                "text/csv",
            )

        st.markdown("### Order Fulfilment")

        st.dataframe(
            fulfilment,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download Order Fulfilment",
            fulfilment.to_csv(index=False).encode("utf-8-sig"),
            "freshroute_order_fulfilment.csv",
            "text/csv",
        )


        # ------------------------------------------
        # UNFULFILLED ORDERS DIAGNOSTIC
        # ------------------------------------------

        st.markdown("### Unfulfilled Orders Diagnostic")
        st.caption(
            "This diagnostic identifies unmet orders and suggests "
            "where to investigate. Suggested causes are indicative, "
            "not guaranteed root causes."
        )

        if not fulfilment.empty:
            diagnostic = fulfilment.copy()

            diagnostic["unfulfilled_kg"] = pd.to_numeric(
                diagnostic["unfulfilled_kg"], errors="coerce"
            ).fillna(0).clip(lower=0)

            diagnostic = diagnostic[
                diagnostic["unfulfilled_kg"] > 1e-6
            ].copy()

            if diagnostic.empty:
                st.success("All orders have been fulfilled.")
            else:
                # Add order and destination information for diagnostics.
                order_details = orders[
                    ["order_id", "customer_id", "fruit_id"]
                ].drop_duplicates("order_id")

                diagnostic = diagnostic.merge(
                    order_details,
                    on="order_id",
                    how="left",
                    validate="one_to_one",
                )

                customer_details = data["customers"][
                    ["customer_id", "destination"]
                ].drop_duplicates("customer_id")

                diagnostic = diagnostic.merge(
                    customer_details,
                    on="customer_id",
                    how="left",
                    validate="many_to_one",
                )

                fruit_stock = (
                    batches.assign(
                        quantity_kg=pd.to_numeric(
                            batches["quantity_kg"], errors="coerce"
                        ).fillna(0)
                    )
                    .groupby("fruit_id")["quantity_kg"]
                    .sum()
                    .to_dict()
                )

                route_destinations = set(
                    data["transport_routes"]["destination"]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .str.casefold()
                )

                def suggest_reason(row):
                    destination = str(
                        row.get("destination", "")
                    ).strip().casefold()

                    if not destination or destination not in route_destinations:
                        return "No matching destination route found"

                    fruit_id = row.get("fruit_id")
                    stock_for_fruit = float(
                        fruit_stock.get(fruit_id, 0)
                    )
                    requested = float(
                        pd.to_numeric(
                            pd.Series([row.get("quantity_requested_kg")]),
                            errors="coerce",
                        ).fillna(0).iloc[0]
                    )

                    if stock_for_fruit < requested:
                        return (
                            "Possible fruit-stock shortage; check "
                            "remaining batch-level inventory"
                        )

                    return (
                        "Check delivery deadline, batch expiry, "
                        "vehicle capacity and storage compatibility"
                    )

                diagnostic["possible_cause"] = diagnostic.apply(
                    suggest_reason, axis=1
                )

                display_columns = [
                    "order_id",
                    "destination",
                    "quantity_requested_kg",
                    "allocated_kg",
                    "unfulfilled_kg",
                    "status",
                    "possible_cause",
                ]

                st.metric(
                    "Orders with unmet demand",
                    f"{len(diagnostic):,}",
                )
                st.metric(
                    "Total unfulfilled demand",
                    f"{diagnostic['unfulfilled_kg'].sum():,.0f} kg",
                )

                st.dataframe(
                    diagnostic[display_columns],
                    use_container_width=True,
                    hide_index=True,
                )

                st.download_button(
                    "Download Unfulfilled Diagnostics",
                    diagnostic[display_columns].to_csv(
                        index=False
                    ).encode("utf-8-sig"),
                    "freshroute_unfulfilled_diagnostic.csv",
                    "text/csv",
                )
        else:
            st.info(
                "Run the planner first to generate order fulfilment data."
            )

        st.markdown("### Remaining Stock")

        st.dataframe(
            remaining,
            use_container_width=True,
            hide_index=True,
        )


# --------------------------------------------------
# PERFORMANCE AND DISCOUNTS
# --------------------------------------------------

elif page == "Performance & Discounts":

    st.subheader("Performance & Economic Analysis")

    if st.session_state.plan_cache is None:

        st.info(
            "Open Distribution Planner and run the planner first."
        )

    else:

        trips, fulfilment, remaining, _ = (
            st.session_state.plan_cache
        )

        current = calculate_metrics(
            data,
            trips,
            fulfilment,
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Fulfilment Rate",
            f"{current['Fulfilment rate (%)']:.2f}%",
        )

        c2.metric(
            "Allocated",
            f"{current['Allocated (kg)']:,.0f} kg",
        )

        c3.metric(
            "Transport Cost",
            f"₹{current['Transport cost (INR)']:,.2f}",
        )

        c4.metric(
            "Net Contribution",
            f"₹{current['Net contribution (INR)']:,.2f}",
        )

        st.caption(
            "Net contribution = revenue − purchase cost − handling "
            "cost − estimated transport cost. This is not net profit "
            "unless all required costs are included."
        )

        st.markdown("### FreshRoute KPI Summary")

        current_df = pd.DataFrame({
            "Metric": list(current.keys()),
            "Value": list(current.values()),
        })

        st.dataframe(
            current_df,
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("### On-time vs Late Deliveries")

        on_time = current["On-time allocated (kg)"]
        late = current["Late allocated (kg)"]

        st.plotly_chart(
            px.pie(
                pd.DataFrame({
                    "Status": ["On time", "Late"],
                    "Quantity (kg)": [on_time, late],
                }),
                names="Status",
                values="Quantity (kg)",
                hole=0.55,
            ),
            use_container_width=True,
        )

        st.markdown("### Discount Recommendations")

        discounts = build_discount_recommendations(
            data,
            remaining,
        )

        st.dataframe(
            discounts,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download Discount Recommendations",
            discounts.to_csv(index=False).encode("utf-8-sig"),
            "freshroute_discount_recommendations.csv",
            "text/csv",
        )

        # ------------------------------------------
        # BASELINE COMPARISON
        # ------------------------------------------

        st.markdown("### Baseline vs FreshRoute")

        baseline = data["dispatch_plan"].copy()

        baseline = baseline.merge(
            data["inventory_batches"][
                [
                    "batch_id",
                    "unit_cost_inr_per_kg",
                    "handling_cost_per_kg_inr",
                ]
            ],
            on="batch_id",
            how="left",
        )

        baseline = baseline.merge(
            orders[
                [
                    "order_id",
                    "selling_price_per_kg_inr",
                    "required_delivery_date",
                ]
            ],
            on="order_id",
            how="left",
            suffixes=("", "_order"),
        )

        baseline = baseline.merge(
            data["transport_routes"][
                ["route_id", "distance_km"]
            ],
            on="route_id",
            how="left",
        )

        baseline = baseline.merge(
            data["vehicles"][
                ["vehicle_id", "has_refrigeration"]
            ],
            on="vehicle_id",
            how="left",
        )

        quantity = pd.to_numeric(
            baseline["allocated_quantity_kg"],
            errors="coerce",
        ).fillna(0).clip(lower=0)

        selling_price = pd.to_numeric(
            baseline["selling_price_per_kg_inr"],
            errors="coerce",
        ).fillna(0)

        purchase_price = pd.to_numeric(
            baseline["unit_cost_inr_per_kg"],
            errors="coerce",
        ).fillna(0)

        handling_rate = pd.to_numeric(
            baseline["handling_cost_per_kg_inr"],
            errors="coerce",
        ).fillna(0)

        distance = pd.to_numeric(
            baseline["distance_km"],
            errors="coerce",
        ).fillna(0)

        refrigerated = (
            baseline["has_refrigeration"]
            .astype(str)
            .str.casefold()
            .isin(["true", "1", "yes"])
        )

        multiplier = refrigerated.map({
            True: 1.3,
            False: 1.0,
        })

        baseline["Revenue"] = quantity * selling_price
        baseline["Purchase Cost"] = quantity * purchase_price
        baseline["Handling Cost"] = quantity * handling_rate

        baseline["Transport Cost"] = (
            quantity * distance * 0.05 * multiplier
        )

        baseline["Net Contribution"] = (
            baseline["Revenue"]
            - baseline["Purchase Cost"]
            - baseline["Handling Cost"]
            - baseline["Transport Cost"]
        )

        baseline_allocated = quantity.sum()

        baseline_metrics = {
            "Allocated (kg)": baseline_allocated,
            "Unfulfilled (kg)": max(
                0,
                total_demand - baseline_allocated,
            ),
            "Fulfilment rate (%)": (
                100 * baseline_allocated / total_demand
                if total_demand else 0
            ),
            "Revenue (INR)": baseline["Revenue"].sum(),
            "Purchase cost (INR)": baseline["Purchase Cost"].sum(),
            "Handling cost (INR)": baseline["Handling Cost"].sum(),
            "Transport cost (INR)": baseline["Transport Cost"].sum(),
            "Net contribution (INR)": baseline["Net Contribution"].sum(),
        }

        comparison = pd.DataFrame([
            {"Plan": "Baseline", **baseline_metrics},
            {"Plan": "FreshRoute", **current},
        ])

        st.dataframe(
            comparison,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download KPI Comparison",
            comparison.to_csv(index=False).encode("utf-8-sig"),
            "freshroute_kpi_comparison.csv",
            "text/csv",
        )

        st.caption(
            "Important: both plans use the same displayed economic "
            "formula. Baseline feasibility still needs independent "
            "validation before claiming FreshRoute is better."
        )
