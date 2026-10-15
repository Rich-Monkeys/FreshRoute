# FreshRoute GUI MVP

## Contents
- `app.py`: Streamlit dashboard
- `optimizer.py`: transparent greedy FEFO/deadline scheduling heuristic
- `data_loader.py`: loads the nine supplied Excel workbooks
- `metrics.py`: rule-based markdown recommendations
- `data/`: source workbooks copied as provided

## Run locally
1. Install Python 3.10 or newer.
2. Open a terminal in this folder.
3. Run `pip install -r requirements.txt`.
4. Run `streamlit run app.py`.
5. Open the local URL printed in the terminal.

## Important modelling assumptions
- Route travel time and distance are taken from `transport_routes.xlsx`.
- A refrigerated truck is required when storage requirement contains "refrigerated"; "cool storage" is treated as compatible because no exact temperature thresholds are provided.
- Dispatch cost is estimated as `allocated kg × route distance km × ₹0.05 × vehicle multiplier`, where refrigerated multiplier = 1.30 and standard multiplier = 1.00. This is an explicit modelling assumption, not a verified real-world tariff.
- Handling time is added before departure. Arrival must not be after the batch expiry.
- The planner is a greedy heuristic, not a globally optimal solver.
- Discount recommendations are suggestions only. They do not modify selling prices or inventory.
- The source files are not overwritten.

## Data note
The supplied workbooks contain 12,425 kg of requested demand and 7,870 kg of inventory. Demand therefore exceeds stock by 4,555 kg in aggregate, before considering fruit-type matching, expiry, route, or vehicle constraints. Full fulfilment is not possible using the supplied inventory alone.
