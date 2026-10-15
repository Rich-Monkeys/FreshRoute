# FreshRoute — Streamlit Decision Support App (V12-aligned)

This project combines the strongest reusable parts of the supplied Streamlit GUI (navigation, KPI cards, charts, source tables, downloadable outputs) with the final FreshRoute notebook's V10/V12 evaluation approach.

## Features
- Operations overview: demand, inventory, fruit-level charts.
- Data & Quality: inspect all nine source tables and validate core input fields.
- Distribution Planner: generate a feasible FEFO comparator and a FreshRoute heuristic plan under the same hard constraints.
- KPI & Validation: economic metrics, plan audits, residual-expiry claim gate, and supplied-dispatch-plan diagnostic.
- Expiry & Markdown: residual inventory and explicitly hypothetical markdown scenarios.
- About & Downloads: download source workbooks and supplied historical output workbooks.
- CSV download buttons for trip schedules, order fulfilment, allocations, audits and markdown scenario.

## Run locally
1. Install Python 3.10+.
2. Extract this folder.
3. Open a terminal in this folder.
4. Install dependencies: `pip install -r requirements.txt`
5. Start the app: `streamlit run app.py`

## Deploy to GitHub / Streamlit Community Cloud
1. Upload the contents of this folder to a GitHub repository (keep `app.py`, `optimizer.py`, `data_loader.py`, `requirements.txt`, `data/`, and optionally `output_reference/`).
2. In Streamlit Community Cloud, select the repository and set the main file path to `app.py` (or `FreshRoute_Streamlit/app.py` if you upload the parent directory instead).
3. Deploy. The app reads the Excel files from the included `data/` folder, so no Google Drive or Colab mount is needed.

## Model alignment and limitations
- The planning approach follows the notebook's final V12 framing: a feasible FEFO comparator, a FreshRoute service-and-economics heuristic, shared feasibility constraints, plan-based expiry estimates, projected no-future-demand write-off, and separate hypothetical markdown scenarios.
- The supplied dispatch plan is a diagnostic reference, not a fair comparator unless independently validated.
- The planner is a greedy heuristic, not a globally optimal solver.
- The model uses the vehicle's supplied `transport_cost_per_km_inr` rate multiplied by route distance for estimated trip transport cost, as in the notebook's V10 comparison logic. Verify that the source rate unit is intended to be INR per km per trip.
- Refrigeration is enforced only when storage text explicitly requires it. The data does not provide exact temperature ranges.
- `Projected_spoilage_no_future_demand_kg` assumes no future orders/sales and residual stock remains unsold until expiry. It is a scenario, not observed spoilage.
- Discount uptake and markdown revenue are hypothetical and excluded from the primary KPI.
- The notebook's output workbooks are stored under `output_reference/` for traceability only; the app does not treat them as freshly regenerated results.

## Source files
The nine Excel workbooks in `data/` are the latest supplied input files. Files in `output_reference/` are the supplied historical output workbooks and are not overwritten.
