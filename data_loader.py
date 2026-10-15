from pathlib import Path
import pandas as pd

DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
REQUIRED_FILES = {
    "suppliers": "suppliers.xlsx",
    "vehicles": "vehicles.xlsx",
    "dispatch_plan": "dispatch_plan.xlsx",
    "fruits": "fruits.xlsx",
    "inventory_batches": "inventory_batches.xlsx",
    "transport_routes": "transport_routes.xlsx",
    "customers": "customers.xlsx",
    "customer_orders": "customer_orders.xlsx",
    "suppliers_products": "suppliers_products.xlsx",
}

def load_data(data_dir=DEFAULT_DATA_DIR):
    data_dir = Path(data_dir)
    missing = [str(data_dir / name) for name in REQUIRED_FILES.values() if not (data_dir / name).exists()]
    if missing:
        raise FileNotFoundError("Missing required source workbook(s):\\n" + "\\n".join(missing))
    return {key: pd.read_excel(data_dir / filename) for key, filename in REQUIRED_FILES.items()}
