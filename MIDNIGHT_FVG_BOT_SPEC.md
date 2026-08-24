# Tapşırıq (Claude üçün): Midnight FVG strategiyasını canlı bota çevir

Bu sənəd başqa bir komputerdə/sessiyada Claude-a birbaşa verilə bilən
tam tapşırıqdır: **"Midnight FVG"** strategiyasını (aşağıda dəqiq təsvir
olunub) bu repo-nun mövcud canlı-trading infrastrukturuna inteqrasiya
edərək bot halına gətirmək.

Bu strategiya artıq `scripts/first_fvg_backtest.py`-də tam backtest
olunub və 4.1 illik tarixi datada ən sabit/güclü nəticəni verib (bax
`BACKTEST_FINDINGS.md`). İndiki məqsəd onu **canlı (MT5-ə qoşulmuş)
bota** çevirməkdir — `strategy/ny_open_accumulation_breakout.py` və
`run_live_accumulation_breakout.py` faylları başqa bir strategiya üçün
artıq bu iş tərzini nümunə göstərir, eyni pattern-i təkrarla.

---

## 1. Strategiyanın DƏQİQ qaydaları (yekun, sınanmış versiya)

**Sessiya**: hər gün 00:00–00:30 (New York yerli vaxtı).

**Addım 1 — FVG tapılması**: 00:00-dan başlayaraq 1-dəqiqəlik şamlarda
ilk Fair Value Gap-ı (3-şamlıq boşluq strukturu) tap:
- **Bullish FVG**: `şam[i+1].low − şam[i-1].high ≥ 3.0` (xal/point)
- **Bearish FVG**: `şam[i-1].low − şam[i+1].high ≥ 3.0` (xal/point)
- Boşluq (gap) ölçüsü **wick-ə görədir** (high/low), body (open/close)
  yox.
- **Bias filtri YOXDUR** — hansı istiqamətdə ilk FVG yaranarsa, o
  istiqamətdə davam olunur.
- **Displacement (güclü şam) filtri YOXDUR** — hər real 3-şamlıq boşluq
  (min-gap şərtini keçən) FVG sayılır, orta şamın "enerjili" olması
  ayrıca tələb olunmur.
- Gündə yalnız **bir** trade (ilk uyğun FVG).

**Addım 2 — Giriş (retest)**: FVG formalaşdıqdan sonra, gələn şamlar
arasında ilk dəfə qiymət FVG-nin **yaxın kənarına** toxunanda (birbaşa
toxunuş, təsdiq şamı GÖZLƏNİLMİR):
- Bullish FVG → giriş = FVG-nin **üst** kənarı (`şam[i+1].low`)
- Bearish FVG → giriş = FVG-nin **alt** kənarı (`şam[i-1].low`)
- Retest 5 şam ərzində baş verməzsə, o günkü setup ləğv olunur.

**Addım 3 — Stop Loss**: FVG-ni yaradan (**orta**, yəni `şam[i]`) şamın
öz wick-i:
- Bullish FVG (LONG) → SL = orta şamın **low**-u
- Bearish FVG (SHORT) → SL = orta şamın **high**-ı
- Buffer YOXDUR — dəqiq bu qiymətdir.

**Addım 4 — Take Profit**: sabit **2.5R** (giriş qiymətindən risk
məsafəsinin 2.5 mislidir, istiqamətə uyğun).

**Addım 5 — Vaxt/çıxış**: TP və ya SL toxunana qədər gözlə. Əgər gün
ərzində heç biri toxunmasa, günün son barında (və ya növbəti gün
00:00-a qədər) flat bağla (bu, çox nadir hal — backtest-də 409 trade-dən
cəmi 1-i "EOD" ilə bağlanıb).

**Risk ölçüsü**: hesab balansının 1%-i (yaxud istifadəçinin seçdiyi
faiz) hər trade-də.

---

## 2. Kritik texniki qeydlər (bunları buraxsan nəticə səhv olacaq)

### 2.1 Broker vaxt zonası
MT5-dən gələn bar vaxt damğaları **UTC kimi işarələnib, amma əslində
broker server vaxtıdır** (bu broker/hesab üçün: `Europe/Bucharest`
təqvimini izləyir — Avropa DST tarixlərinə görə dəyişir, ABŞ DST-nə
görə YOX). Bunu tətbiq etmə ardıcıllığı:

```python
from zoneinfo import ZoneInfo
BROKER_TZ = ZoneInfo("Europe/Bucharest")
naive_from_mt5 = datetime.fromtimestamp(int(row["time"]))  # broker vaxtı, naive
broker_local = naive_from_mt5.replace(tzinfo=BROKER_TZ)
true_utc = broker_local.astimezone(ZoneInfo("UTC"))
ny_time = true_utc.astimezone(ZoneInfo("America/New_York"))  # sessiya yoxlaması BUNUNLA aparılır
```

Əgər başqa broker/hesabla işləyirsənsə, bu offset-i YENİDƏN yoxla —
fərqli ola bilər. Yoxlama üsulu: gündəlik texniki fasilənin (adətən
~23:55-01:00 broker vaxtında) hansı tarixdə 1 saat sürüşdüyünə bax, bunu
Avropa DST tarixləri (mart/oktyabr sonu) ilə tutuşdur.

### 2.2 MT5 data limitləri
- `mt5.terminal_info().maxbars` default `100000`-dur — bu, uzun tarixi
  data endirməyə mane olur. `%APPDATA%\MetaQuotes\Terminal\<hash>\config\common.ini`
  faylında `[Charts]` bölməsində `MaxBars=100000`-ı `5000000`-a
  qaldır (**UTF-16 kodlaması ilə**, terminalı əvvəlcə bağla, sonra aç).
- M1 tarixi data bu broker üçün 2022-07-06-dan başlayır (daha əvvələ
  sorğu heç nə qaytarmır) — bu, real broker limitidir.

### 2.3 Demo hesab
Əgər `.env`-dəki `MT5_LOGIN`/`MT5_PASSWORD` işləmirsə ("Invalid
account" xətası MT5 terminalının öz log faylında,
`%APPDATA%\MetaQuotes\Terminal\<hash>\logs\<tarix>.log`), demo hesab
müddəti bitib — MT5 terminalında **File → Open an Account** ilə yeni
demo hesab yarat, `.env`-i yenilə.

---

## 3. Kodlaşdırma tapşırığı — addım-addım

### 3.1 Data
Əgər `data/history/USTEC_M1.csv` yoxdursa (`.gitignore`-dadır, repo-ya
push olunmayıb), `data/download_history.py`-dan istifadə edərək yenidən
yüklə (yuxarıdakı MaxBars düzəlişindən sonra):
```bash
python -c "
from datetime import UTC, datetime
from pathlib import Path
import MetaTrader5 as mt5
from data.download_history import download_symbol
mt5.initialize()
download_symbol('USTEC', 'M1', datetime(2022,7,6,tzinfo=UTC), datetime.now(UTC), Path('data/history'))
mt5.shutdown()
"
```

### 3.2 Strategiya sinifi
`strategy/midnight_fvg.py` yarat, **`strategy/ny_open_accumulation_breakout.py`**
faylının strukturunu nümunə götür (eyni `TradeSetupStrategy` interfeysi,
`RejectionReason` diaqnostikası, config dataclass pattern). Amma bu
strategiya **daha sadədir**, çünki bias filtri yoxdur:
- **Cross-timeframe `DailyContext` lazım DEYİL** (bias/PDH-PDL/swing
  axtarışına ehtiyac yoxdur, çünki bias filtri sıfırlanıb) — sadəcə
  gündə bir dəfə (00:00 keçəndə) daxili state-i sıfırla (`_fvg_found`,
  `_entry_taken` və s. günlük bayraqlar), sonra 00:00-00:30 pəncərəsində
  `market_state.bars_view()`-dən 3-şamlıq FVG axtar (yuxarı bölmə 1-dəki
  düsturla), retest-i izlə, tap olduqda `TradeSetup` qaytar.
- Referens məntiq üçün `scripts/first_fvg_backtest.py`-in
  `find_first_fvg()`, `process_session()` funksiyalarına bax — həmin
  məntiqi bar-bar (incremental) formaya çevir (necə ki
  `NyOpenAccumulationBreakoutStrategy.evaluate()` `accumulation_bars`-ı
  tədricən topladığı kimi).

### 3.3 Canlı runner
`run_live_midnight_fvg.py` yarat, **`run_live_accumulation_breakout.py`**-nin
BİRƏBİR eyni strukturunu təkrarla:
- Eyni iki-qatlı demo-hesab təhlükəsizlik yoxlaması
  (`_ensure_explicit_demo_configuration`, `_ensure_demo_trade_mode`).
- Eyni `--paper` bayrağı (`PaperBroker` ilə, real order göndərmədən).
- Eyni kill-switch inteqrasiyası (`risk/kill_switch.py`).
- Fərq: bu strategiya cross-timeframe context tələb etmədiyi üçün,
  `_evaluate_for_new_trade()` daha sadə ola bilər (yalnız M1 barları
  ötür, əlavə `compute_daily_context()` çağırışı LAZIM DEYİL).

### 3.4 Doğrulama (məcburi addım)
Kodu yazdıqdan sonra, **`scripts/replay_live_strategy_check.py`**-nin
nümunəsi ilə canlı sinifi tarixi data üzərindən şam-şam keçir və
`scripts/first_fvg_backtest.py`-in artıq doğrulanmış batch nəticəsi ilə
tutuşdur (ən azı bir neçə ayın tarix/qiymətlərini əl ilə yoxla — bax
`BACKTEST_FINDINGS.md`-dəki nümunə spot-check metodu). Fərq varsa,
səbəbini tap və izah et, sadəcə "təxminən oxşardır" demə.

---

## 4. Yekun parametrlər (kodda literal olaraq bunlar olmalıdır)

```python
SESSION_START = time(0, 0)   # NY
SESSION_END   = time(0, 30)  # NY
MIN_GAP_POINTS = 3.0
ENTRY_MODE = "touch"          # confirmation-candle YOX (backtest-də zəif çıxdı)
SL_RULE = "displacement_candle_wick"  # FVG-nin öz kənarı YOX
FIXED_TP_R = 2.5
RISK_PCT = 0.01               # istifadəçi ilə razılaşdırıla bilər
RETEST_WINDOW_CANDLES = 5
USE_BIAS_FILTER = False
REQUIRE_DISPLACEMENT = False
```

---

## 5. Təhlükəsizlik tələbləri (danışıqsız)

- **Heç vaxt real hesabda avtomatik order göndərmə** — yalnız istifadəçi
  özü, açıq şəkildə xahiş etdikdə, VƏ yalnız demo hesabın iki qatlı
  təsdiqi keçdikdən sonra.
- Botu **əvvəlcə `--paper` rejimində** uzun müddət işlət, nəticələri
  müşahidə et, sonra istifadəçi ilə razılaşaraq demo hesaba keç.
- `BACKTEST_FINDINGS.md`-dəki risk qeydlərini (ilk 2.5 il zəif, son ay
  demək olar həmişə pis, nümunə ölçüsü hələ kiçikdir) istifadəçiyə
  xatırlat — bu, "sübut olunmuş" strategiya deyil, "indiyədək ən yaxşı
  test nəticəsi verən" strategiyadır.
