from pathlib import Path
import pandas as pd

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"

REQUIRED_FILES = {
    "customer_orders": "customer_orders.xlsx",
    "customers": "customers.xlsx",
    "dispatch_plan": "dispatch_plan.xlsx",
    "fruits": "fruits.xlsx",
    "suppliers": "suppliers.xlsx",
    "suppliers_products": "suppliers_products.xlsx",
    "transport_routes": "transport_routes.xlsx",
    "vehicles": "vehicles.xlsx",
    "inventory_batches": "inventory_batches.xlsx",
}

def load_data(data_dir=DEFAULT_DATA_DIR):
    data_dir = Path(data_dir)
    data = {}
    missing = []
    for key, filename in REQUIRED_FILES.items():
        path = data_dir / filename
        if not path.exists():
            missing.append(str(path))
            continue
        data[key] = pd.read_excel(path)
    if missing:
        raise FileNotFoundError("Missing required workbook(s):\n" + "\n".join(missing))
    return data
