"""Go / no-go check for a freshly built trading machine.

Run this on the VPS after installing Python, the venv and MT5, and BEFORE
enabling any Scheduled Task. Every check here corresponds to something that has
actually gone wrong in this project and cost real trading days:

  - AutoTrading switched off in the terminal. Real orders come back
    retcode 10027 and the bots log a rejection nobody reads. Cost: every Demo
    order silently refused until someone noticed.
  - A symbol that does not exist under the expected name. After a broker
    change, NAS100 was gone (it is NDX100 here) and the bots polled a
    nonexistent symbol for a week.
  - A stale kill-switch or day_start_equity copied from another machine.
    A leftover baseline from a different account read as a 99% loss and halted
    ALL real trading for days.
  - Missing tzdata. Windows has no IANA database; every strategy here is
    defined against America/New_York and raises without it.
  - A system clock that lies. On 2026-09-21 this VPS's clock jumped two hours
    forward for 2m04s (a host agent wrote its local wall clock into the guest's
    UTC) and nothing noticed. The same check catches a stale BROKER_TZ, which is
    due to diverge from a US-DST broker between 2026-10-25 and 2026-11-01.

It places no orders and changes nothing. Exit code 0 means safe to enable the
paper tasks.

Usage:
    .venv\\Scripts\\python.exe deploy\\preflight.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))
sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).parent.parent


def _deployed_symbols() -> tuple[str, ...]:
    """The symbols the launchers name, read from the launchers themselves.

    This check exists because a broker change once left the bots polling a symbol that no longer
    existed (see the module docstring). A list kept by hand here would go stale at exactly that
    moment -- and did: it still named the previous broker's six when the launchers had moved on.
    These are this repo's names; section 5 asks MT5 for whatever THIS broker calls each of them.
    """
    from backtest.live_replay.configs import parse_bat
    names = set()
    for path in sorted(REPO.glob("run_live_orb_*.bat")):
        try:
            config = parse_bat(path)
        except ValueError as exc:  # a malformed launcher is its own problem, reported below
            warnings.append(f"{path.name} oxunmadi: {exc}")
            continue
        names.add(config.symbol)
    return tuple(sorted(names))


problems: list[str] = []
warnings: list[str] = []

SYMBOLS = _deployed_symbols()


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def bad(msg: str) -> None:
    print(f"  [XXX]  {msg}")
    problems.append(msg)


def warn(msg: str) -> None:
    print(f"  [ ! ]  {msg}")
    warnings.append(msg)


print("=" * 72)
print("PREFLIGHT -- yeni masinda ticarete hazirliq yoxlamasi")
print("=" * 72)

# ---------------------------------------------------------------- 1. Python
print("\n1) Python ve paketler")
v = sys.version_info
if v >= (3, 11):
    ok(f"Python {v.major}.{v.minor}.{v.micro}")
else:
    bad(f"Python {v.major}.{v.minor} cox kohnedir -- 3.11+ lazimdir")

for mod, label in (("pandas", "pandas"), ("numpy", "numpy"),
                   ("dotenv", "python-dotenv"), ("MetaTrader5", "MetaTrader5")):
    try:
        __import__(mod)
        ok(f"{label} qurulub")
    except ImportError:
        bad(f"{label} QURULMAYIB -- pip install -r deploy\\requirements-live.txt")

# tzdata is not importable by name on every install; test what actually matters.
try:
    from zoneinfo import ZoneInfo
    ZoneInfo("America/New_York")
    ok("America/New_York saat qursagi oxunur (tzdata)")
except Exception as exc:  # noqa: BLE001
    bad(f"America/New_York OXUNMUR ({type(exc).__name__}) -- pip install tzdata")

# ------------------------------------------------------------------ 2. .env
print("\n2) Konfiqurasiya")
env = REPO / ".env"
if not env.exists():
    bad(".env FAYLI YOXDUR -- is masasindan kopyalayin (git-de saxlanmir)")
else:
    text = env.read_text(encoding="utf-8", errors="replace")
    missing = [k for k in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")
               if not any(ln.strip().startswith(k + "=") for ln in text.splitlines())]
    if missing:
        bad(f".env natamamdir, catismayan acarlar: {', '.join(missing)}")
    else:
        ok(".env var ve MT5 acarlarini ehtiva edir")

# Which broker this machine trades. Since 2026-09-21 the launchers name the symbol this repo
# uses and .env's MT5_SERVER decides the ticker (config/brokers.py), so a machine whose server
# matches no captured profile has no way to resolve one -- and every bot on it would refuse to
# start. Better to say so here than to find it in the logs.
PROFILE = None
try:
    import config.brokers as machine
    PROFILE = machine.local()
    ok(f"broker: {PROFILE.name} ({PROFILE.server}) -- ticker-ler bundan hell olunur")
except Exception as exc:  # noqa: BLE001 - the message is the whole point
    bad(f"BROKER TEYIN OLUNMADI: {exc}")

TICKERS: dict[str, str] = {}
if PROFILE is not None:
    for s in SYMBOLS:
        try:
            TICKERS[s] = PROFILE.ticker(s)
        except Exception as exc:  # noqa: BLE001
            bad(f"{s}: {exc}")

# ------------------------------------------------- 3. leftovers from old box
print("\n3) Kohne masindan qalan fayllar (kopyalanmamalidir)")
flags = list((REPO / "risk").glob("*.flag")) if (REPO / "risk").exists() else []
if flags:
    bad(f"KILL-SWITCH FLAG VAR ({len(flags)} eded) -- silin, ticareti bloklayir: "
        + ", ".join(f.name for f in flags))
else:
    ok("kill-switch flag yoxdur")

# Paper state files are per-machine and are recreated on first poll, so their
# mere presence means nothing -- warning about them every run just trained the
# reader to ignore this section. The trap is narrower: the LIVE tracker's
# day_start_equity carried over from a DIFFERENT account. On 2026-09-02 it held
# 99998.14 against a ~5000 account, which reads as a 95% loss and halted every
# real order for days. That is what gets checked, against the live equity.
live_state = REPO / "risk" / "daily_risk_state.json"
if live_state.exists():
    try:
        d = json.loads(live_state.read_text(encoding="utf-8"))
        base = float(d.get("day_start_equity", 0))
    except (OSError, ValueError, TypeError) as exc:
        # Deliberately narrow. A bare `except Exception` here already swallowed
        # a NameError once and silently turned this whole check into a no-op --
        # a check that cannot fail loudly is worse than no check.
        warn(f"daily_risk_state.json oxunmadi ({type(exc).__name__}) -- el ile baxin")
        base = 0.0
    _live_baseline = base          # compared against real equity in section 4
else:
    _live_baseline = None
    ok("canli daily_risk_state yoxdur (ilk qacisda yaranacaq)")

# Paper broker state was TRACKED IN GIT until 2026-09-10, so `git clone` handed
# a new machine the old one's virtual balance and, worse, its OPEN positions.
# The first VPS build inherited an open NDX100 paper trade and closed it at SL
# while the workstation still held the same one. Untracked now, but a machine
# cloned before that fix still carries them, and the check costs nothing.
paper = list((REPO / "risk").glob("paper_broker_state_*.json")) if (REPO / "risk").exists() else []
inherited = []
for f in paper:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt file is not this check's business
        continue
    if d.get("positions"):
        inherited.append(f"{f.name} ({len(d['positions'])} aciq movqe)")
if inherited:
    warn("paper state ACIQ MOVQE ile gelib -- basqa masinin islemleridir, silin: "
         + ", ".join(inherited))
elif paper:
    warn(f"{len(paper)} paper state faylı var (aciq movqe yoxdur) -- kohne masindan "
         "gelibse silin ki, forward qeydi temiz baslasin")
else:
    ok("paper state qaliqi yoxdur")

# -------------------------------------------------------------------- 4. MT5
print("\n4) MT5 terminal ve hesab")
try:
    import MetaTrader5 as mt5  # noqa: N813
    if not mt5.initialize():
        bad(f"MT5 initialize ALINMADI: {mt5.last_error()} -- terminal aciqdirmi?")
    else:
        try:
            ti, ai = mt5.terminal_info(), mt5.account_info()
            if ai is None:
                bad("MT5 hesaba giris edilmeyib")
            else:
                ok(f"hesab {ai.login} @ {ai.server}  equity {ai.equity:.2f} {ai.currency}")
                if PROFILE is not None and ai.server != PROFILE.server:
                    # The bots refuse to trade on this (mt5/connector.ensure_logged_into): every
                    # ticker and every state-file name is derived from .env's server, so a
                    # terminal on another account means the wrong symbols and the wrong files.
                    bad(f"TERMINAL BASQA HESABDADIR: .env {PROFILE.server} deyir, terminal ise "
                        f"{ai.server} -- botlar bu veziyyetde islemekden imtina edir")
                if _live_baseline:
                    drift = abs(_live_baseline - ai.equity) / max(ai.equity, 1)
                    if drift > 0.5:
                        bad(f"KILL-SWITCH TELESI: daily_risk_state.json-da gun-baslangic "
                            f"equity {_live_baseline:.2f}, hesabda ise {ai.equity:.2f} "
                            f"({drift*100:.0f}% ferq) -- basqa hesabdan qalib, SILIN")
                    else:
                        ok(f"canli risk baseline hesabla uygundur ({_live_baseline:.2f})")
                if ai.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
                    warn(f"hesab DEMO deyil (trade_mode={ai.trade_mode}) -- "
                         "runnerlerin demo qoruyucusu real orderi rədd edecek")
                else:
                    ok("hesab DEMO -- demo qoruyucusu kecir")
                if not ai.trade_allowed:
                    bad("hesab uzre ticaret QADAGANDIR (account trade_allowed=False)")

            if ti is None:
                bad("terminal_info oxunmadi")
            elif not ti.trade_allowed:
                bad("ALQORITMIK TICARET BAGLIDIR -- terminalda Ctrl+E basin. "
                    "Bagli qalarsa real orderler sessizce retcode 10027 ile redd olunur")
            else:
                ok("AutoTrading aciqdir")

            print("\n5) Simvollar")
            for name, s in sorted(TICKERS.items()):
                label = s if s == name else f"{name} -> {s}"
                si = mt5.symbol_info(s)
                if si is None:
                    bad(f"{label} TAPILMADI -- broker adlandirmasi ferqli ola biler, "
                        f"scripts/capture_symbol_specs.py ile yeniden goturun")
                elif not si.visible:
                    # Not blocking: MT5Connector.fetch_recent_bars calls
                    # mt5.symbol_select(symbol, True) before every fetch
                    # (mt5/connector.py:106), so the bot adds it to Market Watch
                    # itself on first run. Reported only so a genuinely absent
                    # symbol is not confused with a merely unselected one --
                    # that case raises [XXX] above instead.
                    warn(f"{label} Market Watch-da gorunmur -- bloklayici DEYIL, "
                         "bot ilk qacisda ozu elave edir (connector.py:106)")
                else:
                    ok(f"{label} hazir (trade_mode={si.trade_mode})")

            # ------------------------------------------------ 6. saat ve BROKER_TZ
            # Bu masinin saati 2026-09-21 07:24 UTC-de 2 saat irelie atildi (host agenti
            # qonagin UTC-sine oz LOKAL saatini yazdi) ve Windows Time 2 deqiqe sonra geri
            # qaytardi. Hec bir yoxlama dinmedi. Eyni sey 09:30 NY-de olsa, opening range
            # sehv barlardan yigilar. Hemin olcu BROKER_TZ-i de yoxlayir: Europe/Bucharest
            # 2026-10-25-de UTC+2-ye kecir, New York ise 2026-11-01-de -- ABS qrafikine
            # baxan bir broker o hefte bir saat kenarda qalar. Bax mt5/clock.py.
            print("\n6) Saat ve BROKER_TZ")
            if not TICKERS:
                warn("yoxlanacaq simvol yoxdur -- saat olculmedi")
            for s in sorted(TICKERS.values()):
                try:
                    from mt5.clock import measure
                    verdict = measure(s)
                except Exception as exc:  # noqa: BLE001 - never block preflight on this
                    warn(f"{s}: saat yoxlanmadi ({type(exc).__name__}: {exc})")
                    continue
                if verdict.wrong:
                    bad(verdict.detail)
                elif verdict.measurable:
                    ok(verdict.detail)
                else:
                    warn(verdict.detail)
        finally:
            mt5.shutdown()
except ImportError:
    bad("MetaTrader5 paketi yoxdur, MT5 yoxlamalari atlandi")

# ----------------------------------------------------------------- 7. verdict
print("\n" + "=" * 72)
if problems:
    print(f"NETICE: {len(problems)} PROBLEM -- taskları ACMAYIN")
    for p in problems:
        print(f"   - {p}")
    sys.exit(1)

print("NETICE: HAZIRDIR -- paper taskları acila biler")
if warnings:
    print(f"\n({len(warnings)} xeberdarliq, bloklayici deyil:)")
    for w in warnings:
        print(f"   - {w}")
# Report what IS running rather than a generic next step. This used to close
# with "next: install the paper tasks" on every run, including on a healthy
# machine mid-session -- which reads as "your Demo bots are not on yet" and
# invites a pointless re-install.
try:
    import subprocess
    _q = ("Get-ScheduledTask | Where-Object { $_.TaskName -match '^(Orb|Fvg)' } | "
          "ForEach-Object { $_.TaskName + '=' + $_.State }")
    _out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", _q],
                          capture_output=True, text=True, timeout=30).stdout
    _tasks = dict(l.strip().split("=", 1) for l in _out.splitlines() if "=" in l)
except Exception as exc:  # noqa: BLE001 - cosmetic; never block on this
    print(f"\n(task siyahisi oxunmadi: {type(exc).__name__})")
    _tasks = {}

if _tasks:
    _demo = {k: v for k, v in _tasks.items() if k.endswith("_Demo")}
    _paper = {k: v for k, v in _tasks.items() if k.endswith("_Paper")}
    _live = sum(1 for v in _demo.values() if v != "Disabled")
    print(f"\nQURULU TASKLAR: {_live}/{len(_demo)} Demo aciq, "
          f"{sum(1 for v in _paper.values() if v != 'Disabled')}/{len(_paper)} Paper aciq")
    for k in sorted(_demo):
        print(f"   {'ACIQ  ' if _demo[k] != 'Disabled' else 'bagli '} {k}")
    if _live == 0:
        # Every Demo off is the normal state for a machine that handed its bots
        # over to another one -- the workstation looks exactly like this after
        # the 2026-09-10 VPS migration. Saying "the rest are intentionally off"
        # here would be wrong and would hide a genuinely dead machine.
        print("\nBu masinda HEC BIR Demo bot islemir -- baska masina verilibse normaldir,")
        print("olmasa acin: powershell -ExecutionPolicy Bypass -File .\\deploy\\install_tasks.ps1")
    else:
        print("\nBagli olanlar qesdendir -- simvolu basqa bot tutub, bax deploy/demo_roster.txt")
        print("Elave addim lazim deyil.")
else:
    print("\nHec bir Orb task qurulmayib. Evvelce YALNIZ paper:")
    print("  powershell -ExecutionPolicy Bypass -File .\\deploy\\install_tasks.ps1 -PaperOnly")
    print("  bir sessiya izleyin, sonra -PaperOnly-siz tekrarlayin.")
sys.exit(0)
