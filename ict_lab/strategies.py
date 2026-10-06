"""The 11 ICT models as detector configs (rules: ict_spec.py). Times are New York minutes of day."""
from ict_lab.core import Cfg

LONDON = ("london", 120, 300)
NY_AM = ("ny_am", 420, 600)
NY_PM = ("ny_pm", 810, 960)
KILLZONES = [LONDON, NY_AM]
ALL_KZ = [LONDON, NY_AM, NY_PM]
LIQ = ("swing", "pd", "asia", "london")

CFGS = {
    "silver_bullet": Cfg("silver_bullet", [("lon_sb", 180, 240), ("ny_am_sb", 600, 660), ("ny_pm_sb", 840, 900)],
                         LIQ, "fvg_ce", sl="fvg_c1", tp="nearest", fill_after=0),
    "london_asia_sweep": Cfg("london_asia_sweep", [LONDON], ("asia",), "fib", tp="asia_opp", fib_r=0.62,
                             mss_grace=60, fill_after=240),
    "power_of_3": Cfg("power_of_3", KILLZONES, ("asia", "pd"), "fvg_ce", tp="po3", mo_rule=True, mss_grace=60),
    "ote": Cfg("ote", KILLZONES, LIQ, "fib", tp="ext", fib_r=0.705, fib_ext=0.27, mss_grace=60),
    "model_2022": Cfg("model_2022", KILLZONES, LIQ + ("pw",), "fvg_ce", tp="draw", mss_grace=60),
    "unicorn": Cfg("unicorn", ALL_KZ, LIQ, "unicorn", tp="nearest", mss_grace=60),
    "turtle_soup": Cfg("turtle_soup", KILLZONES, ("pd", "pw", "d20"), "close_back", tp="range_mid"),
    "order_block": Cfg("order_block", ALL_KZ, LIQ, "ob", tp="nearest", mss_grace=60),
    "breaker_block": Cfg("breaker_block", ALL_KZ, LIQ, "breaker", tp="nearest", mss_grace=60),
    "ny_open_cbdr": Cfg("ny_open_cbdr", [NY_AM], ("london",), "fvg_ce", tp="ny", mss_grace=60, sd_feature=True),
    "pdh_pdl_raid": Cfg("pdh_pdl_raid", KILLZONES, ("pd",), "fvg_ce", tp="pd_opp", mss_grace=60),
}
VARIANTS = {"turtle_soup": ("doc", "opp", "r2"), "pdh_pdl_raid": ("doc", "tp1", "r2")}
DEFAULT_VARIANTS = ("doc", "r2")
