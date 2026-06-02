# 🔥 Parlayan Bot

Günlük **%7-%50** hareket eden (parlayan) coinleri tespit eden, **%7-%15** bandında alım yaparak kâr takibi gerçekleştiren Binance spot tarama botu.

---

## 🎯 Nasıl Çalışır?

```
Her 2 dakikada bir Binance'i tara
        ↓
Günlük %7+ yükselen coinleri tespit et
        ↓
Parlayan Skor hesapla (24h harekat + momentum + volume + RSI)
        ↓
%7-%50 bandında, skor ≥42 olan coinlere alım yap
        ↓
Çıkış stratejisi:
  ├── %15 → Take Profit (tam hedef)
  ├── %7'den sonra trailing stop aktif (-%3.5 gap)
  ├── %3.5'ten sonra break-even stop
  └── -%2.5 → Stop Loss
```

---

## 🚀 Kurulum

### Gereksinimler
- Docker & Docker Compose
- İnternet bağlantısı (Binance public API)
- API anahtarı **gerekmez** — sadece public market data

### Başlatma

```bash
git clone <repo>
cd parlayan_bot
docker-compose up -d
```

Dashboard: **http://localhost:3010**

---

## ⚙️ Konfigürasyon

`config/bot_settings.yaml` dosyasından tüm parametreler ayarlanabilir:

```yaml
strategy:
  parlayan:
    # Tespit eşikleri
    min_24h_change_pct: 7.0          # En az %7 günlük hareket
    min_parlayan_score_for_watch: 22  # İzleme listesi minimum skoru
    
    # Alım eşikleri
    min_24h_change_pct_for_entry: 7.0
    max_24h_change_pct_for_entry: 50.0
    min_parlayan_score_for_entry: 42

paper_trading:
  default_quote_size_usdt: 100    # İşlem başına USDT miktarı
  max_open_trades: 5              # Aynı anda max işlem sayısı
  take_profit_pct: 15.0           # Kâr hedefi
  stop_loss_pct: 2.5              # Zarar durdur
  trailing_start_pct: 7.0        # Trailing stop başlangıcı
  trailing_gap_pct: 3.5           # Trailing gap
```

---

## 📊 Parlayan Skor

Skor 0-100 arasında hesaplanır:

| Bileşen | Max Puan |
|---|---|
| 24h değişim (ana sinyal, %7'den başlar) | 35 |
| Kısa vadeli momentum (5m + 15m) | 25 |
| Volume onayı | 20 |
| RSI zonu (55-72 ideal) | 10 |
| Kalite (likidite - fake risk) | 10 |

---

## 📈 Çıkış Stratejisi

```
Alım noktası
    │
    ├──▶ +3.5%  → Break-even stop aktif (zarar etme)
    ├──▶ +7.0%  → Trailing stop aktif (%3.5 gap ile tepeyi takip et)
    ├──▶ +15.0% → Take Profit (tam hedef, işlem kapanır)
    │
    └──▶ -2.5%  → Stop Loss (hızla çık)
         Max 120 dakika bekle, sonra piyasa fiyatından çık
```

---

## 🗂️ Proje Yapısı

```
parlayan_bot/
├── app/
│   ├── main.py            # FastAPI app, lifecycle
│   ├── api.py             # REST endpoints
│   ├── scanner.py         # Ana tarama döngüsü
│   ├── feature_engine.py  # Piyasa verisi + parlayan skor
│   ├── decision_engine.py # Alım/izleme kararı
│   ├── trade_engine.py    # İşlem açma/yönetme/kapama
│   ├── storage.py         # Veritabanı işlemleri
│   ├── db.py              # PostgreSQL bağlantısı
│   ├── indicators.py      # RSI, kline analizi
│   ├── binance_client.py  # Binance public API
│   ├── models.py          # Veri modelleri
│   ├── config.py          # Config yönetimi
│   └── static/
│       └── index.html     # Dashboard
├── config/
│   └── bot_settings.yaml  # Tüm parametreler burada
├── db/
│   └── schema.sql         # PostgreSQL şeması
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## 🔒 Güvenlik

- **Paper trading** modu: Gerçek para kullanılmaz
- API anahtarı gerekmez (sadece public endpoints)
- Cooldown sistemi: Ardı ardına zarar sonrası bekleme
- Max işlem limiti: Aynı anda max 5 açık işlem
- Her sembol için max 1 açık işlem

---

## 📡 API Endpoints

| Endpoint | Açıklama |
|---|---|
| `GET /api/status` | Bot durumu |
| `POST /api/scanner/start` | Taramayı başlat |
| `POST /api/scanner/stop` | Taramayı durdur |
| `POST /api/scanner/scan-now` | Anında tara |
| `GET /api/parlayan/candidates` | Aktif adaylar |
| `GET /api/parlayan/today` | Bugünkü parlayan coinler |
| `GET /api/trades/open` | Açık işlemler |
| `GET /api/trades/recent` | Son 30 işlem |
| `GET /api/trades/summary` | İstatistikler |
| `GET /api/market/top` | Parlayan skora göre sıralı piyasa |
| `GET /api/market/gainers` | 24h yükselleri |
| `GET /api/events` | Bot olayları |
| `GET /api/config` | Mevcut config |
| `POST /api/config/update` | Config güncelle |

---

## ⚠️ Uyarı

Bu bot **paper trading** (simülasyon) modunda çalışır.
Gerçek para yatırım kararlarınızda profesyonel danışman görüşü alın.
Kripto para piyasaları yüksek risk içerir.


## Professional Paper v4.2

Yeni sürüm canlı emir göndermez. Eklenen başlıklar:

- Velocity Engine
- Fast Pump Alarm
- Dürüst net paper PnL
- Smart Trailing Ladder
- Daily Signal Report
- Velocity araştırma endpointleri

Detay: `README_PROFESSIONAL_PAPER_V42.md`
