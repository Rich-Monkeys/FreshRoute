from pathlib import Path
import io
import zipfile
import streamlit as st
import pandas as pd
import plotly.express as px
from data_loader import load_data
from optimizer import build_plan, evaluate_plan, audit_plan, build_markdown_opportunities, input_quality_issues, prepare_data

st.set_page_config(page_title="FreshRoute | Decision Support", page_icon="🍃", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
:root { --fr-green:#176b55; --fr-mint:#e9f5ef; --fr-ink:#16342b; }
.block-container {padding-top:1.4rem; padding-bottom:2rem; max-width:1500px;}
.hero {background:linear-gradient(115deg,#123c32 0%,#176b55 62%,#43a582 100%);padding:1.65rem 1.8rem;border-radius:18px;color:white;margin-bottom:1.1rem;}
.hero h1 {color:white;font-size:2.2rem;margin:0 0 .35rem 0;letter-spacing:-.03em;}
.hero p {color:#e4f4ed;margin:0;font-size:1.02rem;}
div[data-testid="stMetric"] {background:#f5faf7;border:1px solid #d9e9e0;padding:13px 15px;border-radius:13px;}
div[data-testid="stMetric"] {background:#f5faf7 !important;color:#124734 !important;border:1px solid #cfe3d8 !important;}
div[data-testid="stMetric"] label, div[data-testid="stMetric"] label p,
div[data-testid="stMetricLabel"], div[data-testid="stMetricLabel"] p,
div[data-testid="stMetricValue"], div[data-testid="stMetricValue"] div,
div[data-testid="stMetricDelta"] {color:#124734 !important;}
/* Keep KPI cards readable even when the app itself is in dark mode. */
div[data-testid="stMetric"] * {color:#124734 !important;}
/* Sidebar navigation uses full-width rectangular buttons. */
section[data-testid="stSidebar"] div.stButton > button {width:100%; text-align:left; border-radius:7px; min-height:2.7rem; border:1px solid #cfe3d8; background:#f5faf7; color:#124734; font-weight:600;}
section[data-testid="stSidebar"] div.stButton > button:hover {border-color:#176b55; color:#0e5a42; background:#e5f3eb;}
section[data-testid="stSidebar"] div.stButton > button[kind="primary"] {background:#176b55 !important; color:#ffffff !important; border-color:#176b55 !important;}
section[data-testid="stSidebar"] div.stButton > button[kind="primary"] * {color:#ffffff !important;}
.stTabs [data-baseweb="tab-list"] {gap:8px;}
.stTabs [data-baseweb="tab"] {border-radius:10px;padding:9px 15px;}
.small-note {color:#60766c;font-size:.88rem;}
</style>
<div class="hero">
  <h1>🍃 FreshRoute</h1>
  <p>Intelligent Decision Support for Perishable Produce Distribution</p>
</div>
""", unsafe_allow_html=True)

@st.cache_data
def get_data():
    return load_data()

try:
    raw_data = get_data()
    data = prepare_data(raw_data)
except Exception as exc:
    st.error(f"Could not load the FreshRoute source workbooks: {exc}")
    st.info("Confirm that all nine Excel files are inside the project's data/ folder.")
    st.stop()

orders = data["customer_orders"]
batches = data["inventory_batches"]
routes = data["transport_routes"]
vehicles = data["vehicles"]
demand_total = float(pd.to_numeric(orders["quantity_requested_kg"], errors="coerce").fillna(0).clip(lower=0).sum())
stock_total = float(pd.to_numeric(batches["quantity_kg"], errors="coerce").fillna(0).clip(lower=0).sum())
quality_issues = input_quality_issues(raw_data)

with st.sidebar:
    st.markdown("## 🍃 FreshRoute")
    st.caption("Operations workspace")
    nav_pages = ["Overview", "Inventory & Expiry", "Distribution Planner", "Performance & Discounts", "Data & Quality", "About & Downloads"]
    if "freshroute_page" not in st.session_state:
        st.session_state["freshroute_page"] = "Overview"
    for nav_page in nav_pages:
        if st.button(nav_page, key="nav_" + nav_page, type="primary" if st.session_state["freshroute_page"] == nav_page else "secondary", use_container_width=True):
            st.session_state["freshroute_page"] = nav_page
    page = st.session_state["freshroute_page"]

def fmt_inr(x):
    return f"₹{float(x):,.2f}"

def show_table(frame, key=None, height=380):
    st.dataframe(frame, use_container_width=True, hide_index=True, height=height)

if page == "Overview":
    st.subheader("Operations overview")
    st.write("A decision-support view of demand, available stock, routing coverage and perishability risk.")
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Customer orders", f"{len(orders):,}")
    c2.metric("Total demand", f"{demand_total:,.0f} kg")
    c3.metric("Available inventory", f"{stock_total:,.0f} kg")
    c4.metric("Stock coverage", f"{100*stock_total/demand_total:.1f}%" if demand_total else "—")
    if demand_total > stock_total:
        st.warning(f"Demand exceeds stock by {demand_total-stock_total:,.0f} kg before fruit matching, deadlines, expiry, routes and vehicle constraints.")
    elif stock_total > demand_total:
        st.info(f"Aggregate stock exceeds demand by {stock_total-demand_total:,.0f} kg; expiry and compatibility can still prevent full fulfilment.")
    col1,col2 = st.columns(2)
    with col1:
        by_fruit = orders.groupby("fruit_type",dropna=False)["quantity_requested_kg"].sum().reset_index().sort_values("quantity_requested_kg",ascending=False)
        st.plotly_chart(px.bar(by_fruit,x="fruit_type",y="quantity_requested_kg",title="Demand by fruit",labels={"fruit_type":"Fruit","quantity_requested_kg":"Demand (kg)"},color="quantity_requested_kg",color_continuous_scale=["#cde9da","#176b55"]),use_container_width=True)
    with col2:
        fruit_lookup=data["fruits"][["fruit_id","fruit_name"]].drop_duplicates("fruit_id")
        inv=batches.merge(fruit_lookup,on="fruit_id",how="left").groupby("fruit_name",dropna=False)["quantity_kg"].sum().reset_index().sort_values("quantity_kg",ascending=False)
        st.plotly_chart(px.bar(inv,x="fruit_name",y="quantity_kg",title="Inventory by fruit",labels={"fruit_name":"Fruit","quantity_kg":"Stock (kg)"},color="quantity_kg",color_continuous_scale=["#d8eee3","#26856b"]),use_container_width=True)
    st.markdown("### What the model considers")
    a,b,c,d=st.columns(4)
    a.markdown("**1 · Demand**\n\nOrder quantity and delivery deadline")
    b.markdown("**2 · Perishability**\n\nBatch expiry and FEFO ordering")
    c.markdown("**3 · Feasibility**\n\nRoutes, vehicle capacity and refrigeration")
    d.markdown("**4 · Economics**\n\nRevenue, purchase, handling, transport and residual write-off")
    st.caption("The planner is a transparent heuristic, not a mathematical proof of global optimality. Results are estimates based on the supplied workbooks and explicit model assumptions.")

elif page == "Data & Quality":
    st.subheader("Source data & input quality")
    st.write("The app loads the nine latest workbooks supplied with FreshRoute. Source files are read-only; the planner works on copies.")
    if quality_issues:
        st.error("Input quality issues were detected.")
        show_table(pd.DataFrame({"Issue":quality_issues}))
    else:
        st.success("Input quality checks passed for the tested fields.")
    table_names = {
        "Customer orders":"customer_orders","Customers":"customers","Fruit catalogue":"fruits",
        "Inventory batches":"inventory_batches","Transport routes":"transport_routes","Vehicles":"vehicles",
        "Supplier directory":"suppliers","Supplier-fruit capacity":"suppliers_products","Supplied dispatch plan":"dispatch_plan"
    }
    selected=st.selectbox("Choose a source table",list(table_names.keys()))
    frame=raw_data[table_names[selected]]
    st.caption(f"{len(frame):,} rows · {len(frame.columns)} columns")
    show_table(frame,height=430)
    st.download_button("Download selected table as CSV",frame.to_csv(index=False).encode("utf-8-sig"),f"{table_names[selected]}.csv","text/csv")
    with st.expander("Input checks performed"):
        st.markdown("- Required columns are present.\n- Inventory quantities are non-negative and received/expiry dates are valid.\n- Route distance and travel time are non-negative.\n- Vehicle capacity is positive and transport cost rate is non-negative.\n- Duplicate batch IDs are flagged.")
        st.caption("These checks do not prove that every source field is correct or that the dataset is representative of real operations.")

elif page == "Distribution Planner":
    st.subheader("Distribution Planner")
    st.write("Build dispatch schedules, inspect fulfilment, diagnose unmet demand, and compare FreshRoute with a common-feasibility FEFO baseline.")
    with st.expander("Planning assumptions", expanded=False):
        st.markdown("- Allocations cannot exceed order demand or batch inventory.\n- Each trip respects vehicle capacity and storage compatibility.\n- Arrivals must be before batch expiry.\n- Vehicles have a turnaround allowance between trips.\n- Transport cost uses route distance × vehicle cost-per-km rate.\n- If late deliveries are enabled, late allocations are shown separately and do not count as on-time/pre-expiry fulfilment revenue in the primary KPI.")
    allow_late = st.toggle("Allow late deliveries when feasible", value=False, help="When enabled, the planner may allocate stock to orders even if arrival is after the requested delivery deadline. Late allocations remain labelled as late and are excluded from eligible on-time revenue.")
    if st.button("Run Distribution Planner", type="primary"):
        with st.spinner("Generating FreshRoute and FEFO plans and validating constraints..."):
            results = {}
            for strategy, label in [("fefo", "Feasible FEFO baseline"), ("fresh", "FreshRoute")]:
                alloc, trips, plan_orders, remaining, remaining_demand = build_plan(raw_data, strategy=strategy, allow_late=allow_late)
                metrics = evaluate_plan(raw_data, alloc, trips, plan_orders, remaining, remaining_demand, label, allow_late=allow_late)
                audit = audit_plan(raw_data, alloc, trips, plan_orders, remaining, allow_late=allow_late)
                results[strategy] = {"alloc": alloc, "trips": trips, "orders": plan_orders, "remaining": remaining, "remaining_demand": remaining_demand, "metrics": metrics, "audit": audit}
            st.session_state["freshroute_results"] = results
            st.session_state["freshroute_allow_late"] = allow_late
    results = st.session_state.get("freshroute_results")
    if not results:
        st.info("Click **Run Distribution Planner** to generate a new plan from the current source data.")
    else:
        selected_plan = st.radio("View plan", ["FreshRoute", "Feasible FEFO baseline"], horizontal=True)
        key = "fresh" if selected_plan == "FreshRoute" else "fefo"
        r = results[key]
        m = r["metrics"]
        st.success(f"Plan generated: {m['Allocated_kg']:,.0f} kg allocated across {m['Trips']:,} trip records.")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Demand fulfilled", f"{m['Demand_fulfilment_pct']:.1f}%")
        c2.metric("On-time & pre-expiry", f"{m['On_time_pre_expiry_kg']:,.0f} kg")
        c3.metric("Trips", f"{m['Trips']:,}")
        c4.metric("Unmet demand", f"{m['Unmet_demand_kg']:,.0f} kg")
        tab1, tab2, tab3, tab4 = st.tabs(["Dispatch Schedule", "Order Fulfilment", "Unfulfilled Diagnostics", "Remaining Stock / Allocations"])
        with tab1:
            st.markdown("### Dispatch Schedule")
            trips_view = r["trips"].sort_values(["dispatch_datetime", "vehicle_id"]) if not r["trips"].empty else r["trips"]
            show_table(trips_view, height=390)
            if not trips_view.empty:
                st.download_button("Download Dispatch Plan", trips_view.to_csv(index=False).encode("utf-8-sig"), f"{key}_dispatch_plan.csv", "text/csv", key=f"{key}_dispatch_dl")
        with tab2:
            st.markdown("### Order Fulfilment")
            alloc_by_order = r["alloc"].groupby("order_id", as_index=False)["qty_kg"].sum() if not r["alloc"].empty else pd.DataFrame(columns=["order_id", "qty_kg"])
            fulfil = r["orders"][["order_id", "customer_id", "destination", "fruit_type", "quantity_requested_kg"]].merge(alloc_by_order, on="order_id", how="left")
            fulfil["qty_kg"] = fulfil["qty_kg"].fillna(0)
            fulfil["unfulfilled_kg"] = (fulfil["quantity_requested_kg"] - fulfil["qty_kg"]).clip(lower=0)
            fulfil["status"] = fulfil.apply(lambda x: "Fulfilled" if x["unfulfilled_kg"] <= 1e-6 else ("Partially fulfilled" if x["qty_kg"] > 1e-6 else "Unfulfilled"), axis=1)
            show_table(fulfil, height=390)
            st.download_button("Download Order Fulfilment", fulfil.to_csv(index=False).encode("utf-8-sig"), f"{key}_order_fulfilment.csv", "text/csv", key=f"{key}_fulfil_dl")
        with tab3:
            st.markdown("### Unfulfilled Orders Diagnostic")
            st.caption("Suggested causes are diagnostic leads, not guaranteed root causes.")
            diagnostic = fulfil[fulfil["unfulfilled_kg"] > 1e-6].copy()
            if diagnostic.empty:
                st.success("All orders are fulfilled under this plan.")
            else:
                route_destinations = set(raw_data["transport_routes"]["destination"].dropna().astype(str).str.strip().str.casefold())
                stock_by_fruit = batches.assign(quantity_kg=pd.to_numeric(batches["quantity_kg"], errors="coerce").fillna(0)).merge(data["fruits"][["fruit_id", "fruit_name"]], on="fruit_id", how="left").groupby("fruit_id")["quantity_kg"].sum().to_dict()
                order_fruit = r["orders"][["order_id", "fruit_id"]].drop_duplicates("order_id")
                diagnostic = diagnostic.merge(order_fruit, on="order_id", how="left")
                def diagnose(row):
                    if str(row.get("destination", "")).strip().casefold() not in route_destinations:
                        return "No matching destination route found"
                    fruit_stock = float(stock_by_fruit.get(row.get("fruit_id"), 0))
                    if fruit_stock < float(row.get("quantity_requested_kg", 0)):
                        return "Possible fruit-stock shortage; inspect batch-level remaining stock"
                    return "Check delivery deadline, batch expiry, vehicle capacity and storage compatibility"
                diagnostic["possible_cause"] = diagnostic.apply(diagnose, axis=1)
                d1, d2 = st.columns(2)
                d1.metric("Orders with unmet demand", f"{len(diagnostic):,}")
                d2.metric("Total unfulfilled demand", f"{diagnostic['unfulfilled_kg'].sum():,.0f} kg")
                show_table(diagnostic[["order_id", "destination", "quantity_requested_kg", "qty_kg", "unfulfilled_kg", "status", "possible_cause"]], height=390)
                st.download_button("Download Unfulfilled Diagnostics", diagnostic.to_csv(index=False).encode("utf-8-sig"), f"{key}_unfulfilled_diagnostics.csv", "text/csv", key=f"{key}_diagnostics_dl")
        with tab4:
            st.markdown("### Remaining Stock")
            remaining_frame = r["remaining"].rename("remaining_kg").rename_axis("batch_id").reset_index()
            show_table(remaining_frame, height=300)
            st.download_button("Download Remaining Stock", remaining_frame.to_csv(index=False).encode("utf-8-sig"), f"{key}_remaining_stock.csv", "text/csv", key=f"{key}_remaining_dl")
            st.markdown("### Batch Allocations")
            show_table(r["alloc"], height=300)
            if not r["alloc"].empty:
                st.download_button("Download Batch Allocations", r["alloc"].to_csv(index=False).encode("utf-8-sig"), f"{key}_batch_allocations.csv", "text/csv", key=f"{key}_alloc_dl")
        st.markdown("### Feasibility Audit")
        show_table(r["audit"], height=250)
        fails = r["audit"][r["audit"]["Status"] != "PASS"]
        if fails.empty:
            st.success("No failures detected by the implemented audit checks.")
        else:
            st.warning(f"{len(fails)} audit check(s) need review. See the evidence before making performance claims.")

elif page == "Performance & Discounts":
    st.subheader("KPI comparison & claim integrity")
    results=st.session_state.get("freshroute_results")
    if not results:
        st.info("Run both plans in Distribution Planner first.")
    else:
        kpi=pd.DataFrame([results["fefo"]["metrics"],results["fresh"]["metrics"]])
        cols=st.columns(4)
        fresh=results["fresh"]["metrics"]; fefo=results["fefo"]["metrics"]
        cols[0].metric("FreshRoute on-time/pre-expiry",f"{fresh['On_time_pre_expiry_fulfilment_pct']:.1f}%")
        cols[1].metric("FEFO on-time/pre-expiry",f"{fefo['On_time_pre_expiry_fulfilment_pct']:.1f}%")
        cols[2].metric("FreshRoute net contribution",fmt_inr(fresh["Net_contribution_INR"]))
        delta=fresh["Net_contribution_INR"]-fefo["Net_contribution_INR"]
        cols[3].metric("Net contribution difference",fmt_inr(delta),delta=f"{delta:+,.2f} INR")
        st.markdown("### FreshRoute KPI Summary")
        current_metrics = fresh
        summary_rows = [
            ("Demand (kg)", current_metrics["Demand_kg"]),
            ("Allocated (kg)", current_metrics["Allocated_kg"]),
            ("Unfulfilled (kg)", current_metrics["Unmet_demand_kg"]),
            ("Fulfilment rate (%)", current_metrics["Demand_fulfilment_pct"]),
            ("On-time & pre-expiry (kg)", current_metrics["On_time_pre_expiry_kg"]),
            ("Revenue (INR)", current_metrics["Revenue_INR"]),
            ("Purchase cost (INR)", current_metrics["Purchase_cost_INR"]),
            ("Handling cost (INR)", current_metrics["Handling_cost_INR"]),
            ("Transport cost (INR)", current_metrics["Transport_cost_INR"]),
            ("Net contribution (INR)", current_metrics["Net_contribution_INR"]),
        ]
        show_table(pd.DataFrame(summary_rows, columns=["Metric", "Value"]), height=300)
        display_cols=["Strategy","Demand_kg","Allocated_kg","Demand_fulfilment_pct","On_time_pre_expiry_kg","Unmet_demand_kg","Expired_by_order_horizon_kg","Residual_inventory_kg","Revenue_INR","Purchase_cost_INR","Handling_cost_INR","Transport_cost_INR","Residual_spoilage_writeoff_INR","Net_contribution_INR","Projected_writeoff_no_future_demand_INR","Net_contribution_projected_no_future_demand_INR","Constraint_failures"]
        st.markdown("### Baseline vs FreshRoute")
        show_table(kpi[display_cols],height=310)
        st.download_button("Download KPI Comparison", kpi[display_cols].to_csv(index=False).encode("utf-8-sig"), "freshroute_kpi_comparison.csv", "text/csv")
        st.markdown("### On-time vs late deliveries")
        on_time = float(fresh.get("On_time_pre_expiry_kg", 0))
        allocated = float(fresh.get("Allocated_kg", 0))
        late_or_ineligible = max(0.0, allocated - on_time)
        st.plotly_chart(px.pie(pd.DataFrame({"Delivery status": ["On-time & pre-expiry", "Late / not eligible"], "Quantity (kg)": [on_time, late_or_ineligible]}), names="Delivery status", values="Quantity (kg)", hole=0.55, title="Allocated quantity by delivery eligibility"), use_container_width=True)
        st.markdown("### Discount recommendations")
        from metrics_friend import build_discount_recommendations
        discount_plan = results["fresh"]["remaining"]
        discounts = build_discount_recommendations(raw_data, discount_plan)
        show_table(discounts, height=330)
        st.caption("Rule-based recommendations only. Discounts are not automatically applied; expired stock must be removed from sale.")
        st.download_button("Download discount recommendations CSV", discounts.to_csv(index=False).encode("utf-8-sig"), "freshroute_discount_recommendations.csv", "text/csv")

        st.markdown("### Economic interpretation")
        st.caption("Net contribution = eligible revenue − purchase cost − handling cost − estimated transport cost. The projected no-future-demand measure additionally subtracts the write-off value of all residual stock. It is a scenario, not a measured financial outcome.")
        if delta>0: st.success("FreshRoute has higher modelled net contribution than the FEFO comparator under these assumptions.")
        elif delta<0: st.warning("FreshRoute has lower modelled net contribution than the FEFO comparator under these assumptions.")
        else: st.info("Both strategies have equal modelled net contribution under these assumptions.")
        spoil=pd.DataFrame([
            {"Measure":"Residual stock expiring by order horizon (plan-based estimate)","FEFO (kg)":fefo["Expired_by_order_horizon_kg"],"FreshRoute (kg)":fresh["Expired_by_order_horizon_kg"],"Reduction from FEFO (kg)":fefo["Expired_by_order_horizon_kg"]-fresh["Expired_by_order_horizon_kg"]},
            {"Measure":"Projected residual stock if no future demand occurs (scenario only)","FEFO (kg)":fefo["Projected_spoilage_no_future_demand_kg"],"FreshRoute (kg)":fresh["Projected_spoilage_no_future_demand_kg"],"Reduction from FEFO (kg)":fefo["Projected_spoilage_no_future_demand_kg"]-fresh["Projected_spoilage_no_future_demand_kg"]}
        ])
        st.markdown("### Spoilage claim gate")
        show_table(spoil)
        if (spoil["Reduction from FEFO (kg)"]>1e-6).all():
            st.success("Both displayed measures show a modelled reduction versus FEFO; describe them as estimates/scenarios, not observed real-world spoilage.")
        else:
            st.warning("At least one measure does not show a reduction. Do not claim a spoilage reduction for that measure.")
        st.markdown("### Validation checks")
        a1,a2=st.columns(2)
        with a1:
            st.markdown("**FreshRoute**"); show_table(results["fresh"]["audit"])
        with a2:
            st.markdown("**Feasible FEFO baseline**"); show_table(results["fefo"]["audit"])
        st.markdown("### Supplied dispatch plan — diagnostic only")
        st.caption("The supplied dispatch plan is retained as a separate reference and is not treated as a fair comparator until independently validated. It is not overwritten.")
        baseline=raw_data["dispatch_plan"].copy()
        show_table(baseline.head(30),height=300)
        st.download_button("Download supplied dispatch plan diagnostic CSV",baseline.to_csv(index=False).encode("utf-8-sig"),"supplied_dispatch_plan_diagnostic.csv","text/csv")

elif page == "Inventory & Expiry":
    st.subheader("Inventory & Expiry")
    st.write("Inspect inventory batches and shelf life, then review plan-based residual stock and hypothetical markdown opportunities.")
    inventory_view = batches.merge(data["fruits"][["fruit_id", "fruit_name", "storage_requirement"]], on="fruit_id", how="left")
    inventory_view["expiry_date"] = pd.to_datetime(inventory_view["expiry_date"], errors="coerce")
    inventory_view["received_date"] = pd.to_datetime(inventory_view["received_date"], errors="coerce")
    inventory_view["days_to_expiry"] = (inventory_view["expiry_date"].dt.normalize() - pd.Timestamp.today().normalize()).dt.days
    fruit_options = sorted(inventory_view["fruit_name"].dropna().unique())
    selected_fruits = st.multiselect("Filter inventory by fruit", fruit_options, key="inventory_fruit_filter")
    if selected_fruits:
        inventory_view = inventory_view[inventory_view["fruit_name"].isin(selected_fruits)]
    inv1, inv2, inv3 = st.columns(3)
    inv1.metric("Batches shown", f"{len(inventory_view):,}")
    inv2.metric("Quantity shown", f"{inventory_view['quantity_kg'].sum():,.0f} kg")
    inv3.metric("Expiring within 72 hours", f"{inventory_view.loc[inventory_view['days_to_expiry'].between(0, 3), 'quantity_kg'].sum():,.0f} kg")
    st.dataframe(inventory_view.sort_values("expiry_date"), use_container_width=True, hide_index=True, height=320)
    st.caption("Expiry dates are from the supplied dataset, not live sensor readings. Current-date expiry indicators are informational; plan-based analysis below uses the planner outputs.")
    st.download_button("Download filtered inventory CSV", inventory_view.to_csv(index=False).encode("utf-8-sig"), "freshroute_inventory_expiry.csv", "text/csv")
    st.divider()
    st.subheader("Residual Inventory & Markdown")
    results=st.session_state.get("freshroute_results")
    if not results:
        st.info("Run both plans in Distribution Planner first.")
    else:
        st.markdown("### Residual inventory & markdown opportunities")
        chosen=st.radio("Plan for residual-stock analysis",["FreshRoute","Feasible FEFO baseline"],horizontal=True, key="expiry_plan_choice")
        key="fresh" if chosen=="FreshRoute" else "fefo"
        remaining=results[key]["remaining"]
        uptake=st.slider("Hypothetical markdown uptake (%)",min_value=0,max_value=100,value=50,step=5)
        markdown=build_markdown_opportunities(raw_data,remaining,uptake_pct=uptake)
        if markdown.empty:
            st.success("No positive residual inventory remains in this plan.")
        else:
            c1,c2,c3=st.columns(3)
            c1.metric("Residual stock",f"{markdown['remaining_kg'].sum():,.0f} kg")
            c2.metric("Stock within 72 hours of expiry",f"{markdown.loc[markdown['hours_to_expiry_from_plan_start']<=72,'remaining_kg'].sum():,.0f} kg")
            c3.metric("Hypothetical markdown revenue",fmt_inr(markdown["assumed_markdown_revenue_inr"].sum()))
            st.warning("Markdown revenue is hypothetical only. Uptake is a user-set assumption, not observed customer demand; it is excluded from the primary KPI.")
            show_table(markdown,height=390)
            st.download_button("Download markdown scenario CSV",markdown.to_csv(index=False).encode("utf-8-sig"),f"{key}_markdown_scenario.csv","text/csv")

elif page == "About & Downloads":
    st.subheader("Model notes & project downloads")
    st.markdown("""
**FreshRoute V12-aligned Streamlit MVP**

- Uses the latest supplied nine Excel source workbooks.
- Compares a feasible FEFO baseline with a FreshRoute service-and-economics heuristic using common feasibility rules.
- Keeps the supplied dispatch plan as a separate diagnostic reference.
- Reports residual expiry and hypothetical markdown scenarios separately from observed results.
- Includes demand, stock, route, capacity, deadline, expiry and vehicle-overlap audits.
""")
    st.markdown("### Important limitations")
    st.markdown("- The planner is a greedy heuristic; it does not guarantee a global optimum.\n- `refrigerated` is enforced only when explicitly required by the fruit storage text. Exact temperature bands are not present in the data.\n- The model's output depends on the supplied dataset and cost assumptions. Validate source data and rerun the latest notebook in a clean runtime before treating the notebook's exported workbook as authoritative.\n- A lower residual-stock estimate is not proof of actual spoilage prevented; future demand and markdown uptake are scenario assumptions.")
    st.markdown("### Download source data")
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        for p in sorted(Path("data").glob("*.xlsx")): z.write(p,arcname=f"data/{p.name}")
    st.download_button("Download all source workbooks (ZIP)",buf.getvalue(),"freshroute_source_data.zip","application/zip")
    ref_dir=Path("output_reference")
    if ref_dir.exists():
        out=io.BytesIO()
        with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
            for p in sorted(ref_dir.glob("*.xlsx")): z.write(p,arcname=f"output/{p.name}")
        st.download_button("Download supplied historical output workbooks (ZIP)",out.getvalue(),"freshroute_historical_outputs.zip","application/zip")
    st.caption("Historical output workbooks are included for traceability only; they are not assumed to be freshly generated by this Streamlit run.")
