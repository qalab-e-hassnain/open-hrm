"""
Pakistan salaried-individual income tax engine.

Data-driven: the slab table below is the SINGLE place to edit rates. It holds
the salaried-individual slabs for Pakistan's Federal Budget 2026-27 (FY2027 /
tax year 2027). Update these rows if the gazetted figures change — the
calculation functions don't change.

Each slab: (lower_bound, upper_bound, base_tax_at_lower_bound, rate_on_excess)
Bounds are ANNUAL taxable income in PKR.
"""
from typing import List, Tuple

# (lower, upper, base_tax, rate) — FY2027 salaried slabs
SALARY_TAX_SLABS: List[Tuple[float, float, float, float]] = [
    (0,          600_000,      0,          0.00),
    (600_000,    1_200_000,    0,          0.01),
    (1_200_000,  2_200_000,    6_000,      0.11),
    (2_200_000,  3_200_000,    116_000,    0.20),
    (3_200_000,  4_100_000,    316_000,    0.25),
    (4_100_000,  5_600_000,    541_000,    0.29),
    (5_600_000,  7_000_000,    976_000,    0.32),
    (7_000_000,  float("inf"), 1_424_000,  0.35),
]

# Annual tax-free threshold: income up to the top of the 0% slab is exempt from
# salary tax. For salaried individuals this is the relevant "exempt income".
TAX_FREE_THRESHOLD_ANNUAL: float = SALARY_TAX_SLABS[1][0]  # 600,000

def calculate_annual_income_tax(annual_income: float) -> float:
    """Annual income tax for a given annual taxable salary income (PKR)."""
    if annual_income is None or annual_income <= 0:
        return 0.0
    for lower, upper, base, rate in SALARY_TAX_SLABS:
        if lower < annual_income <= upper:
            return round(base + (annual_income - lower) * rate, 2)
    # Above the top bound → use the last (highest) slab.
    lower, upper, base, rate = SALARY_TAX_SLABS[-1]
    return round(base + (annual_income - lower) * rate, 2)

def calculate_monthly_income_tax(monthly_taxable_income: float) -> float:
    """
    Simple monthly income tax withheld: annualize the monthly taxable salary,
    run it through the annual slabs, then divide back to a monthly figure.
    This is the FIRST-MONTH case of the cumulative method below.
    """
    if monthly_taxable_income is None or monthly_taxable_income <= 0:
        return 0.0
    annual_tax = calculate_annual_income_tax(monthly_taxable_income * 12)
    return round(annual_tax / 12, 2)

def pk_tax_year_position(month: int) -> int:
    """Position of a calendar month within the Pakistan tax year (Jul=1 … Jun=12)."""
    return month - 6 if month >= 7 else month + 6

def cumulative_monthly_tax(
    tax_deducted_so_far: float,
    taxable_this_month: float,
    months_remaining: int,
) -> float:
    """
    Monthly tax deduction. Assume the CURRENT salary holds for the whole fiscal
    year (salary x 12), tax that with the annual slabs, subtract whatever tax
    has already been deducted this tax year, then split the balance evenly over
    the months still remaining (this month included). Decimals are truncated.
    """
    
    if months_remaining <= 0 or not taxable_this_month or taxable_this_month <= 0:
        return 0.0
    annual_tax = calculate_annual_income_tax(taxable_this_month * 12)
    remaining_tax = max(0.0, annual_tax - (tax_deducted_so_far or 0.0))
    return float(int(remaining_tax / months_remaining))  # truncate decimals
