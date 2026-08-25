# Midnight FVG botunu Windows VPS-ə köçürmə bələdçisi

Bu bələdçi botu evdəki kompüterdən asılı olmadan, 7/24 bulud serverdə işə salmaq üçündür.

**Vacib məhdudiyyət:** Mənim (Claude) alətlərim yalnız BU kompüterə (lokal maşınıza) qoşuludur —
uzaq VPS-ə RDP/SSH ilə birbaşa qoşula bilmirəm. Ona görə VPS-i siz qurmalısınız (hesab açmaq və
ödəniş sizin adınıza olmalıdır), sonra ya bu bələdçini addım-addım özünüz izləyirsiniz, ya da VPS-in
içində yeni bir Claude Code sessiyası açıb (əgər varsa) ona bu təlimatı göstərirsiniz.

## 1-ci addım: VPS seçimi

Bu bot çox yüngüldür (Python skripti + MT5 terminalı) — ən ucuz tariflər kifayətdir:

| Provayder | Təxmini qiymət | Qeyd |
|---|---|---|
| Contabo Windows VPS | ~$10-15/ay (Windows lisenziyası ayrıca) | Ən populyar ucuz seçim |
| Vultr Windows Cloud Compute | ~$10-15/ay (Windows lisenziyası ayrıca) | Bir çox region seçimi |
| Amazon Lightsail (Windows) | sabit paket qiyməti, ~$8-16/ay | Bütün xərclər bir paketdə |
| Xüsusi "Forex VPS" provayderləri (FXVPS, CNSForexVPS və s.) | ~$15-30/ay | MT5 adətən öncədən quraşdırılıb, texniki bilik az tələb edir |

**Minimum spesifikasiya:** Windows Server 2019/2022, 2 vCPU, 4 GB RAM (2 GB da işləyər, 4 GB rahatdır).
Region önəmli deyil — bu strategiya real-vaxt tick sürəti tələb etmir (hər 2 dəqiqədə bir yoxlayır).

## 2-ci addım: VPS-ə qoşulma və əsas alətlərin qurulması

VPS-i aldıqdan sonra provayder sizə bir IP ünvanı + Administrator istifadəçi adı/parolu verəcək.
Windows-un öz "Remote Desktop Connection" (mstsc.exe) proqramı ilə qoşulun.

VPS-in içində (RDP pəncərəsində) bunları quraşdırın:
1. **Python 3.13** — https://www.python.org/downloads/ (quraşdırarkən "Add python.exe to PATH" işarələyin)
2. **Git** — https://git-scm.com/download/win
3. **MetaTrader 5 terminalı** — FXTM-in öz saytından (ya da MT5-in ümumi quraşdırıcısından), sonra
   demo hesabınızın login/server məlumatları ilə **bir dəfə əl ilə** daxil olun (bu, terminalın
   broker ilə əlaqəni yadda saxlaması üçündür).

## 3-cü addım: Layihəni VPS-ə köçürmə

VPS-in içindən (RDP pəncərəsindən) PowerShell açıb:

```powershell
git clone https://github.com/Quliyeva1o/tradebot.git C:\tradebot
cd C:\tradebot
```

(Əgər repo private-dirsə, GitHub-da bir Personal Access Token yaradıb clone zamanı istifadə etməlisiniz.)

`.env` faylını **manual olaraq özünüz** VPS-ə köçürün (məs. şifrələnmiş USB, ya da VPS-in RDP
clipboard-u ilə əl ilə yazaraq) — bu fayl MT5 login/parolunuzu saxlayır, ona görə mən bunu sizin
adınızdan heç vaxt köçürməməliyəm və siz də onu açıq mesajla paylaşmamalısınız.

## 4-cü addım: Avtomatik qurulum skripti

Aşağıdakı `deploy_vps.ps1` skripti (bu repoda, `scripts/deploy_vps.ps1`) venv yaradır, asılılıqları
quraşdırır və Task Scheduler tapşırıqlarını (Demo + Paper, hər 2 dəqiqədə bir) qurur — bu
kompüterdə etdiyim eyni addımlar. VPS-in içindən, `C:\tradebot` qovluğunda işə salın:

```powershell
cd C:\tradebot
.\scripts\deploy_vps.ps1
```

## 5-ci addım: Yoxlama

```powershell
schtasks /run /tn "MidnightFVG_NAS100_Paper"
Get-Content .\logs\run_live_midnight_fvg.log -Tail 10
```

"NO SIGNAL" və ya "RESULT: SIGNAL ..." sətri görünürsə, bot düzgün işləyir.

## Vacib xatırlatma

- VPS-i işlək saxlamaq sizin (provayderin) məsuliyyətidir — VPS söndürülməz, yalnız broker
  tərəfindən bağlana bilər (məs. ödəniş bitəndə).
- MT5 terminalı VPS-də bir dəfə açıq/login olmalıdır (adətən avtomatik yenidən qoşulur, amma
  Windows yenidən başladıqdan sonra bir dəfə əl ilə yoxlamaq faydalıdır).
- Bu, DEMO hesabdır — real pul riski yoxdur, amma yenə də mütəmadi (məs. həftədə bir) logları
  yoxlamağı tövsiyə edirəm.
