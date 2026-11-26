"""Trading fees on both exchanges, with each one's rounding. Sources in DECISIONS.md D6.

Kalshi (fee schedule PDF effective 2026-07-07, kalshi-fee-schedule.pdf):
    taker  = round_up(M_taker × 0.07   × C × P × (1 − P))
    maker  = round_up(M_maker × 0.0175 × C × P × (1 − P))      M_maker is 0 unless the series says otherwise
  P in dollars, C contracts. Rounded UP so that fee + position cost lands on the member's balance precision:
  $0.01 for ordinary members (the PDF's table: 1 contract at $0.50 → $0.02; 100 at $0.45 → $1.74), $0.0001 for
  direct members. Per fill. Per-series parameters: fee_type, fee_multiplier (T3 columns); event overrides.

Polymarket US (docs.polymarket.us/fees, effective 2026-11-14; API program from 2026-11-21):
    taker  = Θ × C × p × (1 − p),  Θ = 0.0695 for API orders regardless of volume
    maker  = 0 below $10M/month maker volume (rebates above)
  Rounded to the nearest $0.01 with banker's rounding, per order. 70% of taker fees are rebated weekly through
  2026-12-31: `polyus_taker` returns the gross fee (what leaves the balance at the fill); `rebate` is separate.
  Per-market `feeCoefficient` (T3) is kept as a parameter; the API program's 0.0695 is the default.

All functions take price in CENTS and return dollars.
"""
import math
from decimal import ROUND_HALF_EVEN, Decimal

KALSHI_TAKER_COEF = 0.07
KALSHI_MAKER_COEF = 0.0175
KALSHI_BALANCE_PRECISION = 0.01          # 0.0001 for direct members
KALSHI_MAKER_MULT = {"quadratic": 0.0, "quadratic_with_maker_fees": 1.0, "quadratic_with_combo_maker_fees": 2.0}

POLYUS_THETA_API = 0.0695
POLYUS_TAKER_REBATE = 0.70               # API program through 2026-12-31
POLYUS_MAKER_THETA = 0.0                 # maker tier 1 (< $10M / 30 days)


def _ceil_to(x, step):
    # guard against float noise: 1.7325000000000002 must still ceil to 1.74, 1.75 must stay 1.75
    return math.ceil(round(x / step, 9) - 1e-12) * step if x > 0 else 0.0


def kalshi_fee(price_c, qty, *, taker=True, fee_type="quadratic", fee_multiplier=1.0, precision=KALSHI_BALANCE_PRECISION):
    """Fee in dollars for one fill of `qty` contracts at `price_c` cents."""
    if fee_type not in KALSHI_MAKER_MULT:
        raise ValueError(f"Kalshi fee_type {fee_type!r} not modelled (flat-fee series): do not trade it blind")
    p = price_c / 100.0
    if taker:
        m, coef = (fee_multiplier if fee_multiplier is not None else 1.0), KALSHI_TAKER_COEF
    else:
        m, coef = KALSHI_MAKER_MULT[fee_type] * (fee_multiplier if fee_multiplier is not None else 1.0), KALSHI_MAKER_COEF
    raw = m * coef * qty * p * (1 - p)
    return round(_ceil_to(raw, precision), 6)


def polyus_fee(price_c, qty, *, taker=True, theta=POLYUS_THETA_API):
    """Gross fee in dollars for one order of `qty` contracts at `price_c` cents (before the weekly rebate)."""
    p = price_c / 100.0
    th = theta if taker else POLYUS_MAKER_THETA
    raw = Decimal(str(th)) * Decimal(str(qty)) * Decimal(str(p)) * (Decimal(1) - Decimal(str(p)))
    return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def polyus_rebate(gross_fee, rate=POLYUS_TAKER_REBATE):
    return round(gross_fee * rate, 6)


def fee(platform, price_c, qty, *, taker=True, params=None):
    """Dispatch by platform. `params` is the market's T3 fee row: Kalshi fee_type / fee_multiplier
    (overrides already applied by the collector), Polymarket fee_coefficient (None → API default)."""
    params = params or {}
    if platform == "KALSHI":
        return kalshi_fee(price_c, qty, taker=taker, fee_type=params.get("fee_type") or "quadratic",
                          fee_multiplier=params.get("fee_multiplier", 1.0))
    if platform == "POLYUS":
        theta = params.get("theta") or POLYUS_THETA_API   # NOT feeCoefficient (0.095 on the market object; API pays 0.0695)
        return polyus_fee(price_c, qty, taker=taker, theta=theta)
    raise ValueError(platform)


def breakeven_sum_c(price_k_c, price_p_c, qty=1, k_params=None, p_params=None, k_taker=True, p_taker=True, count_rebate=False):
    """Total cost in cents per contract of buying both legs at these prices, fees included.
    An opportunity exists when this is below 100."""
    fk = fee("KALSHI", price_k_c, qty, taker=k_taker, params=k_params)
    fp = fee("POLYUS", price_p_c, qty, taker=p_taker, params=p_params)
    if count_rebate and p_taker:
        fp -= polyus_rebate(fp)
    return price_k_c + price_p_c + 100.0 * (fk + fp) / qty
