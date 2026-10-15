"""Scenario-based markdown optimizer for FreshRoute.

This is an explicit what-if model, not a trained demand forecast. Demand response
is controlled by user assumptions and should be calibrated with historical sales
before being interpreted as a real-world prediction.
"""
from __future__ import annotations
import pandas as pd

DISCOUNT_OPTIONS = [0, 10, 20, 30, 40, 50]


def optimize_discounts(data, remaining_stock, base_sell_through=0.50,
                       price_elasticity=1.5, demand_uncertainty=0.20,
                       discount_options=None, now=None):
    """Compare markdown options by batch and return recommendations + full scenarios.

    Expected contribution = sales revenue - cost of all remaining inventory
    (sold unit cost + write-off cost for unsold units) - handling cost on sold units.
    Transport cost is excluded because the model does not allocate it to individual
    residual batches. Demand uncertainty is represented by low/base/high scenarios.
    """
    batches = data['inventory_batches'].copy()
    fruits = data['fruits'][['fruit_id', 'fruit_name', 'market_price_per_kg_inr']].copy()
    batches['batch_id'] = batches['batch_id'].astype(str)
    batches['expiry_date'] = pd.to_datetime(batches['expiry_date'], errors='coerce')
    if now is None:
        dates = pd.to_datetime(data['customer_orders'].get('order_date'), errors='coerce')
        now = dates.min() if dates is not None else pd.NaT
    now = pd.to_datetime(now, errors='coerce')
    if pd.isna(now):
        now = pd.Timestamp.now()

    if isinstance(remaining_stock, pd.Series):
        rem = remaining_stock.rename('remaining_kg').rename_axis('batch_id').reset_index()
    elif isinstance(remaining_stock, pd.DataFrame):
        rem = remaining_stock.copy()
        if 'batch_id' not in rem.columns and rem.index.name == 'batch_id': rem = rem.reset_index()
        if 'remaining_kg' not in rem.columns:
            vals = [c for c in rem.columns if c != 'batch_id']
            if 'batch_id' in rem.columns and len(vals) == 1: rem = rem.rename(columns={vals[0]:'remaining_kg'})
        if not {'batch_id','remaining_kg'}.issubset(rem.columns):
            raise ValueError('Remaining stock must contain batch_id and remaining_kg.')
    elif isinstance(remaining_stock, dict):
        rem = pd.DataFrame({'batch_id': list(remaining_stock.keys()), 'remaining_kg': list(remaining_stock.values())})
    else:
        rem = pd.DataFrame(remaining_stock)
        if not {'batch_id','remaining_kg'}.issubset(rem.columns):
            raise ValueError('Unsupported remaining-stock format.')
    rem['batch_id'] = rem['batch_id'].astype(str)
    rem['remaining_kg'] = pd.to_numeric(rem['remaining_kg'], errors='coerce').fillna(0).clip(lower=0)
    rem = rem.groupby('batch_id', as_index=False)['remaining_kg'].sum()
    b = batches.merge(rem, on='batch_id', how='left')
    b['remaining_kg'] = b['remaining_kg'].fillna(0)
    b = b.merge(fruits, on='fruit_id', how='left')

    orders = data['customer_orders'].copy()
    orders['selling_price_per_kg_inr'] = pd.to_numeric(orders['selling_price_per_kg_inr'], errors='coerce')
    price_by_fruit = orders.groupby('fruit_id')['selling_price_per_kg_inr'].median().to_dict()
    b['price_per_kg_inr'] = b.apply(lambda r: price_by_fruit.get(r['fruit_id'], r['market_price_per_kg_inr']), axis=1)
    b['price_per_kg_inr'] = pd.to_numeric(b['price_per_kg_inr'], errors='coerce').fillna(0).clip(lower=0)
    for c in ['unit_cost_inr_per_kg','handling_cost_per_kg_inr']:
        b[c] = pd.to_numeric(b[c], errors='coerce').fillna(0).clip(lower=0)
    b['hours_to_expiry'] = (b['expiry_date'] - now).dt.total_seconds()/3600

    discounts = discount_options or DISCOUNT_OPTIONS
    demand_cases = [
        ('Low demand', max(0.0, base_sell_through*(1-demand_uncertainty)), 1/3),
        ('Base demand', max(0.0, base_sell_through), 1/3),
        ('High demand', min(1.0, base_sell_through*(1+demand_uncertainty)), 1/3),
    ]
    scenario_rows = []
    for _, row in b.iterrows():
        qty = float(row['remaining_kg'])
        expired = pd.isna(row['hours_to_expiry']) or row['hours_to_expiry'] <= 0
        for disc in discounts:
            for case_name, base_rate, weight in demand_cases:
                if expired or qty <= 1e-8:
                    sold = 0.0
                    revenue = 0.0
                    contribution = -qty*float(row['unit_cost_inr_per_kg'])
                else:
                    # Transparent assumed demand response: each 100% discount fraction
                    # scales sell-through by the configured elasticity, capped at 100%.
                    sell_rate = min(1.0, max(0.0, base_rate*(1 + price_elasticity*disc/100)))
                    sold = qty*sell_rate
                    revenue = sold*float(row['price_per_kg_inr'])*(1-disc/100)
                    handling = sold*float(row['handling_cost_per_kg_inr'])
                    # Remaining unsold quantity is assumed written off at acquisition cost.
                    contribution = revenue - sold*float(row['unit_cost_inr_per_kg']) - handling - (qty-sold)*float(row['unit_cost_inr_per_kg'])
                scenario_rows.append({
                    'batch_id':str(row['batch_id']), 'fruit_name':row.get('fruit_name'),
                    'remaining_kg':qty, 'hours_to_expiry':row['hours_to_expiry'],
                    'discount_pct':int(disc), 'demand_scenario':case_name,
                    'assumed_sell_through_pct':round((sold/qty*100) if qty else 0,2),
                    'expected_sales_kg':sold, 'estimated_revenue_inr':revenue,
                    'estimated_contribution_inr':contribution, 'scenario_weight':weight,
                    'expired_batch':expired,
                })
    scenarios = pd.DataFrame(scenario_rows)
    if scenarios.empty:
        return pd.DataFrame(), scenarios
    scenarios["weighted_sales"] = scenarios["expected_sales_kg"] * scenarios["scenario_weight"]
    scenarios["weighted_revenue"] = scenarios["estimated_revenue_inr"] * scenarios["scenario_weight"]
    scenarios["weighted_contribution"] = scenarios["estimated_contribution_inr"] * scenarios["scenario_weight"]
    keys = ["batch_id", "fruit_name", "remaining_kg", "hours_to_expiry", "discount_pct", "expired_batch"]
    expected = scenarios.groupby(keys, dropna=False).agg(
        expected_sales_kg=("weighted_sales", "sum"),
        expected_revenue_inr=("weighted_revenue", "sum"),
        expected_contribution_inr=("weighted_contribution", "sum"),
    ).reset_index()
    merge_keys = ["batch_id", "fruit_name", "remaining_kg", "hours_to_expiry", "discount_pct", "expired_batch"]
    for case_name, out_col in [("Low demand", "low_demand_contribution_inr"), ("Base demand", "base_demand_contribution_inr"), ("High demand", "high_demand_contribution_inr")]:
        case = scenarios[scenarios["demand_scenario"] == case_name][merge_keys + ["estimated_contribution_inr"]].rename(columns={"estimated_contribution_inr": out_col})
        expected = expected.merge(case, on=merge_keys, how="left")
    # Expired batches cannot be discounted or sold; recommend remove from sale.
    eligible = expected[~expected['expired_batch'] & (expected['remaining_kg'] > 1e-8)].copy()
    if eligible.empty:
        recommendations = expected[expected['discount_pct'].eq(0)].copy()
        recommendations['recommended_discount_pct'] = 0
        recommendations['recommendation'] = 'No eligible unexpired residual stock; expired stock must be removed from sale.'
    else:
        idx = eligible.groupby('batch_id')['expected_contribution_inr'].idxmax()
        best = eligible.loc[idx].copy()
        best['recommended_discount_pct'] = best['discount_pct']
        best['recommendation'] = best['recommended_discount_pct'].map(lambda x: f"Best tested option under assumptions: {int(x)}% markdown")
        expired_rows = expected[expected['expired_batch'] & expected['discount_pct'].eq(0)].copy()
        expired_rows['recommended_discount_pct'] = 0
        expired_rows['recommendation'] = 'Expired: remove from sale; do not discount.'
        zero_rows = expected[(expected['remaining_kg'] <= 1e-8) & expected['discount_pct'].eq(0)].copy()
        zero_rows['recommended_discount_pct'] = 0
        zero_rows['recommendation'] = 'No residual stock to discount.'
        recommendations = pd.concat([best, expired_rows, zero_rows], ignore_index=True, sort=False)
    columns = ['batch_id','fruit_name','remaining_kg','hours_to_expiry','recommended_discount_pct','expected_sales_kg','expected_revenue_inr','expected_contribution_inr','low_demand_contribution_inr','base_demand_contribution_inr','high_demand_contribution_inr','recommendation']
    return recommendations[[c for c in columns if c in recommendations.columns]].sort_values(['hours_to_expiry','remaining_kg'], ascending=[True,False]), scenarios
