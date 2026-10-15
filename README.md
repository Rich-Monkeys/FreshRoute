# FreshRoute Streamlit App

A Streamlit decision-support prototype for perishable-produce distribution.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Pages

- **Overview** — demand, inventory and fruit-level charts.
- **Inventory & Expiry** — batch-level stock and expiry review, plus residual inventory and hypothetical markdown scenarios.
- **Distribution Planner** — friend-version workflow retained and expanded: run planner, dispatch schedule, order fulfilment, unfulfilled-order diagnostics, remaining stock, batch allocations and feasibility audit. It compares FreshRoute with a common-rule FEFO comparator.
- **Performance & Discounts** — KPI cards, KPI summary, on-time/pre-expiry eligibility chart, baseline comparison, discount recommendations, economic interpretation, spoilage claim gate, and audits.
- **Data & Quality** — inspect the nine source workbooks and run input-quality checks.
- **About & Downloads** — model assumptions, limitations and source/reference downloads.

## Data

Place the nine source Excel files in `data/`: `suppliers.xlsx`, `vehicles.xlsx`, `dispatch_plan.xlsx`, `fruits.xlsx`, `inventory_batches.xlsx`, `transport_routes.xlsx`, `customers.xlsx`, `customer_orders.xlsx`, and `suppliers_products.xlsx`.

## Important interpretation notes

- The planning algorithm is a greedy heuristic, not a proof of global optimality.
- FEFO is a model-generated comparator under the same implemented feasibility rules; the supplied dispatch plan is retained as a separate diagnostic reference.
- Late deliveries are optional. When enabled, late allocations remain visible but are not counted as on-time/pre-expiry fulfilment or eligible revenue in the primary evaluation.
- Markdown revenue is hypothetical and is not included in primary KPIs.
- A reduction in modelled residual stock is not proof of real-world spoilage prevented.
- Confirm that cost units and source data are appropriate before using the results for operational decisions.
