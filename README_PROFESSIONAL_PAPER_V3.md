# Parlayan Bot — Professional Paper v3

Bu sürüm canlı Binance emri vermez. Amaç, yükselme ihtimali olan coinlerin davranışını profesyonel trader mantığıyla ölçmek, paper-trade etmek ve raporlamak.

## Bu sürüm ne yapar?

- Binance public market verisini tarar.
- Her taramada coin snapshot kaydı tutar.
- Her coin için `pre_pump_score` hesaplar.
- Coinin fazını belirler:
  - `WATCH`
  - `VOLUME_WAKEUP`
  - `EARLY_MOMENTUM`
  - `ACCUMULATION_BREAKOUT`
  - `LATE_FOMO`
  - `DANGER`
- Uygun adaylarda sanal işlem açar.
- Trailing stop, break-even, stop-loss ve max süre ile sanal işlemi kapatır.
- Risk manager günlük zarar limiti, açık işlem limiti ve kötü faz filtresi uygular.
- Rapor dışa aktarımına 48 saatlik zaman çizelgesi ekler.

## En önemli fark

Eski sürüm daha çok "24 saatte yükselmiş coinleri" görüyordu.  
Bu sürüm "hacim uyanıyor ama fiyat henüz çok kaçmamış mı?" sorusuna bakar.

## Yeni API uçları

- `/api/research/summary`
- `/api/research/pre-pump`
- `/api/research/timeline/{symbol}`
- `/api/research/events`
- `/api/risk/daily`

## Rapor analizi

```bash
python export_report.py
```

Rapor zipinin içinde artık şunlar da vardır:

- `timelines_48h.json`
- `signal_events.json`
- `equity_curve.json`

Coin yaşam döngüsü analizi:

```bash
python scripts/analyze_coin_lifecycle.py --timelines timelines_48h.json --out coin_lifecycle_report.json
```

## Güvenlik

- API key yok.
- Secret yok.
- Gerçek BUY/SELL yok.
- Sadece paper-trade.


## v3 Session Ayrımı

Bu sürüm her uygulama başlangıcında otomatik yeni bir paper-trading session açar.

Varsayılan strateji etiketi:

```text
strategy_version: professional_paper_v3
mode: paper
```

Dashboard'daki işlem istatistikleri varsayılan olarak sadece aktif session kayıtlarını gösterir. Eski veritabanındaki işlemler silinmez; ancak yeni performans ölçümüne karışmaz.

Ek API endpointleri:

```text
GET /api/session/current
GET /api/session/list
GET /api/trades/summary?all_time=true
GET /api/trades/recent?all_time=true
GET /api/trades/open?all_time=true
```

Amaç: eski paper-trade sonuçları ile yeni `professional_paper_v3` strateji sonuçlarını birbirinden ayırmak.
