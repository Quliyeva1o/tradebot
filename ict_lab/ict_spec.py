"""ICT strategiyalarının qaydaları (mənbə: "ICT Strategiyaları — Giriş, SL və TP qaydaları.docx").

Bütün vaxtlar New York vaxtıdır. Bu fayl qaydaların yeganə mənbəyidir; build_db.py onu
ict_lab/ict.db bazasına yazır. Hər strategiya tək-tək test ediləcək, nəticələr
A+ / A / B+ / B dərəcələri üzrə `results` cədvəlinə düşür.
"""

GRADES = ("A+", "A", "B+", "B")

KILLZONES = {
    "asia": "20:00-00:00",
    "london": "02:00-05:00",
    "ny_am": "07:00-10:00",
    "london_close": "10:00-12:00",
    "ny_pm": "13:30-16:00",
}

CONCEPTS = [
    ("Likvidlik (BSL/SSL)", "Swing high-ların üstündəki (BSL) və swing low-ların altındakı (SSL) stoplar", "Qiymətin maqniti: həm sweep, həm hədəf"),
    ("Sweep / Raid", "Qiymət səviyyəni keçir, stopları götürür və geri qayıdır", "Girişdən əvvəl ilk şərt"),
    ("MSS", "Sweep-dən sonra son daxili swing-in güclü şam BƏDƏNİ ilə qırılması", "İstiqamət dəyişməsinin təsdiqi"),
    ("BOS", "Trend istiqamətində növbəti swing-in qırılması", "Trendin davamı"),
    ("Displacement", "Böyük bədənli, sürətli impuls şamları", "Ciddi MSS-in əlaməti; adətən FVG qoyur"),
    ("FVG", "3 şamlıq formada 1-ci şamın fitili ilə 3-cü şamın fitili arasındakı boşluq", "Giriş zonası"),
    ("Order Block (OB)", "Displacement-dən əvvəlki son əks rəngli şam", "Giriş zonası (açılış və ya 50%)"),
    ("Breaker Block", "Sweep-dən sonra qırılmış, rolu dəyişmiş OB", "Retest-də giriş"),
    ("Premium / Discount", "Diapazonun 50%-dən yuxarısı / aşağısı", "Long yalnız discount-da, short yalnız premium-da"),
    ("OTE", "Fib 0.62-0.79 geri çəkilməsi, mərkəz 0.705", "Optimal giriş zonası"),
    ("SMT Divergence", "Korrelyasiyalı alətlərdən biri yeni high/low edir, digəri etmir (NQ-ES, EURUSD-GBPUSD, DXY)", "Sweep-in əlavə təsdiqi"),
    ("Daily/Midnight Open", "00:00 NY açılışı", "Bullish gündə alış bunun altında, bearish gündə satış üstündə"),
]

BIAS_RULE = (
    "Gündəlik/4s qrafikdə qiymətin hansı likvidliyə (əvvəlki gün/həftə high-ı və ya low-u) getdiyinə "
    "baxılır. Son gündəlik şam əvvəlki high-ın üstündə bağlanıbsa bullish, low-un altındadırsa bearish. "
    "Yaxında doldurulmamış FVG-lər də hədəf sayılır."
)

GENERAL_RULES = [
    "Hər ticarətdə risk balansın 0.5-1%-i; gündəlik maksimum itki 2-3%.",
    "Minimum RR 1:2. SL-dən hədəfə məsafə bunu vermirsə, ticarət yoxdur.",
    "1R-da və ya ilk daxili likvidlikdə qismən bağla (məs. 50%), qalanı əsas hədəfə; SL-i yalnız yeni struktur yarandıqdan sonra breakeven-ə çək.",
    "Yüksək təsirli xəbərdən 30 dəqiqə əvvəl və sonra yeni giriş etmə.",
    "Bir killzone-da maksimum 1-2 ticarət; iki ardıcıl itkidən sonra həmin günü bitir.",
    "Hər modeli ən azı 50-100 setap üzrə backtest et, sonra demo hesabda yoxla.",
]

# key, ad, vaxt, qrafik, addımlar, giriş, SL, TP, etibarsızlıq
STRATEGIES = [
    dict(
        key="silver_bullet", no=1, name="Silver Bullet",
        summary="Saatlıq pəncərə içində yaranan ilk FVG-yə giriş; hədəf ən yaxın likvidlik.",
        time="03:00-04:00 (London), 10:00-11:00 (NY AM), 14:00-15:00 (NY PM)",
        tf="Kontekst 15d/1s, giriş 1d-5d",
        steps=[
            "Pəncərədən əvvəl bias-ı və hədəf likvidliyi (yaxın swing high/low, sessiya high/low) qeyd et.",
            "Pəncərə daxilində qiymət bir tərəfin likvidliyini götürsün (tercihən bias-a əks tərəfi).",
            "Displacement ilə MSS və FVG yaransın; bu FVG pəncərə vaxtında olmalıdır.",
        ],
        entry="Qiymət FVG-yə qayıdanda limit order (FVG başlanğıcı və ya 50%-i, consequent encroachment).",
        sl="FVG-ni yaradan ilk şamın ekstremumu arxasında; mühafizəkar: sweep swing-in arxası.",
        tp="Bias istiqamətində ən yaxın likvidlik. Minimum indekslərdə ~10 handle, forex-də ~10-15 pip; RR 1:2-dən aşağıdırsa girmə.",
        invalid="Pəncərə bitdi, FVG yaranmadı - ticarət yoxdur. Qiymət FVG-ni bədənlə tam keçib bağlanırsa - setap pozulub.",
    ),
    dict(
        key="london_asia_sweep", no=2, name="London Open - Asiya sweep (Judas Swing)",
        summary="London açılışı Asiya diapazonunun bir tərəfini yalançı keçir, sonra əsl istiqamətə dönür.",
        time="Asiya diapazonu 20:00-00:00, giriş London killzone 02:00-05:00",
        tf="Kontekst 1s/15d, giriş 3d-5d",
        steps=[
            "Asiya high (AS.H) və Asiya low (AS.L) qeyd et.",
            "Bias bullish-dirsə AS.L-in, bearish-dirsə AS.H-ın keçilməsini gözlə (bias-a əks hərəkət).",
            "Sweep-dən sonra displacement və MSS olsun.",
            "Fib-i impuls ayağına çək (short-da LO.H=1, impulsun dibi=0; long-da əksinə).",
        ],
        entry="OTE zonası 0.62-0.705 (və ya bu zonada olan FVG/OB).",
        sl="Sweep ekstremumunun (LO.H / LO.L) bir neçə pip arxası.",
        tp="TP1: 1:1 və ya Asiya diapazonunun əks tərəfi; TP2: əvvəlki günün high/low-u, açıq FVG, Fib -0.27 / -0.62 uzantısı.",
        invalid="Sweep olmadan birbaşa breakout (səviyyənin xaricində güclü bağlanış, geri qayıtmır). Asiya diapazonu adi gündən böyükdürsə model zəifləyir.",
    ),
    dict(
        key="power_of_3", no=3, name="Power of 3 (AMD)",
        summary="Gündəlik şam: Accumulation, Manipulation, Distribution. Giriş manipulyasiyanın sonundadır.",
        time="A - Asiya; M - London açılışı və ya NY açılışı; D - London/NY sessiyası",
        tf="Gündəlik bias, giriş 5d-15d",
        steps=[
            "Gündəlik bias-ı təyin et (bullish gün = açılışın altında low, sonra yüksəliş).",
            "Midnight Open-u (00:00) qeyd et.",
            "Bullish gündə qiymətin açılışın ALTINA enməsini, bearish gündə ÜSTÜNƏ qalxmasını gözlə.",
            "Manipulyasiya sweep ilə bitsin (Asiya low/high, əvvəlki gün səviyyəsi) və MSS gəlsin.",
        ],
        entry="MSS-dən sonrakı FVG/OB, açılış qiymətinin discount (long) və ya premium (short) tərəfində.",
        sl="Manipulyasiya ekstremumunun arxası (günün ehtimal olunan low/high-ı).",
        tp="Gündəlik hədəf likvidlik: əvvəlki gün high/low, həftəlik səviyyə və ya ADR ölçüsü.",
        invalid="Qiymət açılışın əks tərəfində saatlarla qalır və MSS vermir; ya da manipulyasiya ADR-in böyük hissəsini yeyib.",
    ),
    dict(
        key="ote", no=4, name="OTE (Optimal Trade Entry)",
        summary="Trend istiqamətində güclü impulsdan sonra 62-79% geri çəkilmədə giriş.",
        time="İstənilən killzone (ən yaxşısı London və NY AM)",
        tf="İmpuls 15d/1s, giriş 1d-5d",
        steps=[
            "Bias istiqamətində displacement ilə yeni swing yaransın (likvidlik götürüb).",
            "Fib-i swing low-dan swing high-a çək (long); short-da high-dan low-a.",
            "Səviyyələr: 0.5, 0.62, 0.705, 0.79.",
        ],
        entry="0.62-0.79 arası; ideal 0.705. Zonada FVG və ya OB varsa, giriş oradan.",
        sl="Fib 1.0-ın (impulsun başlanğıcı) arxası.",
        tp="-0.27 və -0.62 uzantıları, və ya impulsun başlanğıcındakı əks likvidlik.",
        invalid="Qiymət 0.79-u bədənlə keçib bağlanır, ya da impuls displacement-siz (yavaş, üst-üstə düşən şamlar) gəlib.",
    ),
    dict(
        key="model_2022", no=5, name="2022 Mentorship Model",
        summary="Likvidlik götürülür -> MSS -> FVG-yə giriş -> qarşı likvidlik hədəfi.",
        time="London və ya NY AM killzone",
        tf="Hədəf və likvidlik 15d/1s; giriş 1d-5d",
        steps=[
            "Yüksək TF-də hədəf likvidliyi (draw on liquidity) seç.",
            "Aşağı TF-də qiymət əks tərəfdəki likvidliyi götürsün (long üçün daxili swing low).",
            "Displacement ilə MSS: son swing high bədənlə qırılsın.",
            "Bu displacement-də FVG yaransın.",
        ],
        entry="Qiymət FVG-yə qayıdanda (FVG başlanğıcı və ya 50%).",
        sl="Sweep edilmiş swing-in (MSS-dən əvvəlki ekstremum) arxası.",
        tp="Seçilmiş hədəf likvidlik. Qismən bağlama: 1R və ya ilk daxili swing; qalanı əsas hədəfə.",
        invalid="MSS fitillə olub, bədənlə yox; FVG yoxdur; hədəfə qədər RR 1:2-dən azdır.",
    ),
    dict(
        key="unicorn", no=6, name="Unicorn Model",
        summary="Breaker Block ilə FVG-nin üst-üstə düşdüyü zonadan giriş (iki PD array bir yerdə).",
        time="Killzone-lar",
        tf="5d-15d",
        steps=[
            "Qiymət swing high/low-u sweep etsin.",
            "Əks istiqamətdə displacement ilə əvvəlki swing qırılsın; sweep-dən əvvəlki son əks şam (OB) breaker olur.",
            "Eyni displacement-də breaker-in üzərinə düşən FVG yaransın.",
        ],
        entry="Breaker ilə FVG-nin kəsişmə zonası.",
        sl="Breaker-in əks kənarının arxası (mühafizəkar: sweep ekstremumu).",
        tp="Növbəti likvidlik (əvvəlki swing, sessiya high/low).",
        invalid="FVG ilə breaker kəsişmir - bu Unicorn deyil, ayrıca OB və ya FVG setapıdır.",
    ),
    dict(
        key="turtle_soup", no=7, name="Turtle Soup",
        summary="Aşkar high/low-un yalançı qırılmasına əks giriş; breakout treyderlərinin stopları yanacaq olur.",
        time="Killzone-lar; ən güclüsü gündəlik/həftəlik səviyyələrdə",
        tf="Səviyyə 1s-gündəlik, giriş 5d-15d",
        steps=[
            "Aşkar səviyyə seç: əvvəlki gün/həftə high-low, equal highs/lows, 20 günlük high/low.",
            "Qiymət səviyyəni keçsin, amma geri qayıdıb onun içində bağlansın.",
            "Tercihən SMT divergence olsun (NQ yeni high edir, ES etmir).",
        ],
        entry="Səviyyənin içinə bağlanışdan sonra, və ya aşağı TF MSS + FVG-də.",
        sl="Sweep fitilinin ekstremumu arxası.",
        tp="Diapazonun ortası (50%), sonra diapazonun əks tərəfi.",
        invalid="Qiymət səviyyənin xaricində bədənlə bağlanıb davam edir - real breakout.",
    ),
    dict(
        key="order_block", no=8, name="Order Block girişi",
        summary="Strukturu qıran displacement-dən əvvəlki son əks şam; qiymət ora qayıdanda giriş.",
        time="Killzone-lar",
        tf="OB 15d-4s, giriş 1d-5d",
        steps=[
            "Bullish OB: yuxarı displacement-dən (BOS/MSS) əvvəlki son qırmızı şam. Bearish OB: aşağı displacement-dən əvvəlki son yaşıl şam.",
            "OB likvidlik götürüb yaranmalı və arxasında FVG qoymalıdır (keyfiyyətli OB).",
            "OB discount-da (long) və ya premium-da (short) olmalıdır.",
        ],
        entry="OB-nin açılış qiyməti və ya 50%-i (mean threshold).",
        sl="OB-nin ekstremumunun arxası (bullish OB low-u, bearish OB high-ı).",
        tp="Növbəti likvidlik; ilk qismən bağlama: displacement-in başladığı swing.",
        invalid="Qiymət OB-nin 50%-ni bədənlə keçib bağlanır.",
    ),
    dict(
        key="breaker_block", no=9, name="Breaker Block girişi",
        summary="Uğursuz OB rolunu dəyişir: köhnə dəstək müqavimətə, köhnə müqavimət dəstəyə çevrilir.",
        time="Killzone-lar",
        tf="5d-1s",
        steps=[
            "Bullish breaker: qiymət swing low-u sweep edir, yuxarı qalxıb sweep-dən əvvəlki swing high-ı qırır; həmin swing high-ı yaradan son yaşıl şam bullish breaker olur.",
            "Bearish breaker: əksi - swing high sweep, əvvəlki swing low qırılır; o low-dan əvvəlki son qırmızı şam.",
        ],
        entry="Qiymət breaker zonasına geri qayıdanda (retest).",
        sl="Breaker-in 50%-nin və ya əks kənarının arxası; mühafizəkar: sweep ekstremumu.",
        tp="Növbəti likvidlik hovuzu.",
        invalid="Sweep olmadan yaranmış breaker zəifdir; qiymət breaker-i bədənlə keçib bağlanırsa - çıx.",
    ),
    dict(
        key="ny_open_cbdr", no=10, name="NY Open modeli və CBDR proyeksiyası",
        summary="NY açılışında London high/low sweep edilir; CBDR standart dəyişmələri günün ehtimal ekstremumunu göstərir.",
        time="CBDR 14:00-20:00 (əvvəlki gün); NY AM 07:00-10:00, indekslərdə 09:30 açılışı",
        tf="1d-5d",
        steps=[
            "CBDR (14:00-20:00) diapazonunun high/low-unu ölç. Diapazon kiçikdirsə (forex < ~40 pip) proyeksiya etibarlıdır.",
            "Diapazon ölçüsünü 1, 2, 3 dəfə yuxarı və aşağı proyeksiya et (SD). Bullish gündə low adətən -1/-2 SD, bearish gündə high +1/+2 SD ətrafında.",
            "NY açılışında qiymət London high/low-unu və ya 08:30 xəbər şamının ekstremumunu sweep etsin; SD ilə üst-üstə düşməsi əlavə təsdiqdir.",
            "MSS + FVG gözlə.",
        ],
        entry="FVG və ya OTE.",
        sl="Sweep ekstremumunun arxası.",
        tp="London-un əks ekstremumu, əvvəlki gün high/low, və ya əks tərəfdəki SD proyeksiyası.",
        invalid="Böyük xəbər günlərində (NFP, CPI, FOMC) CBDR proyeksiyaları tez-tez pozulur. 'Venom' modelinin qaydaları ictimai mənbələrdə ziddiyyətlidir - ayrıca yoxlamaq lazımdır.",
    ),
    dict(
        key="pdh_pdl_raid", no=11, name="Əvvəlki gün high/low (PDH/PDL) raid",
        summary="Killzone-da əvvəlki günün high və ya low-u götürülür, sonra qiymət əks tərəfə gedir.",
        time="London və NY AM",
        tf="Səviyyə gündəlik, giriş 1d-5d",
        steps=[
            "PDH və PDL qeyd et; bias-a görə hansının sweep olunacağını gözlə (bullish = PDL raid, bearish = PDH raid).",
            "Raid killzone daxilində olsun; SMT divergence əlavə təsdiqdir.",
            "MSS + FVG yaransın.",
        ],
        entry="FVG, OB və ya OTE.",
        sl="Raid fitilinin arxası.",
        tp="TP1: əvvəlki günün 50%-i; TP2: əvvəlki günün əks ekstremumu (PDL raid -> PDH).",
        invalid="Raid killzone-dan kənarda olub, ya da gündəlik şam səviyyənin xaricində bağlanıb.",
    ),
]
