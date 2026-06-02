# Parlayan Bot — Professional Paper v4

Bu sürüm canlı Binance emri vermez. `market_data_only: true` korunmuştur.

## v4'te gelen ana değişiklikler

- `strategy_version: professional_paper_v41`
- Pump Detective analizinden çıkan kazanan bölgelere göre yeni giriş filtresi
- `RECOVERY_COMPRESSION`, `VOLUME_WAKEUP`, `EARLY_MOMENTUM`, `ACCUMULATION_BREAKOUT`, `MOMENTUM_EXPANSION`, `FOMO`, `DISTRIBUTION`, `DANGER` fazları
- Açıklanabilir `pre_pump_score` bileşenleri:
  - recovery compression
  - volume component
  - volume acceleration
  - momentum component
  - RSI component
  - 24h position component
  - quality component
  - late/FOMO penalty
- 24h %35 üstü FOMO giriş engeli
- RSI 52-68 ve 24h %4-%30 bölgesine öncelik
- PORTAL tipi recovery adayları için ayrı izleme/giriş profili
- Adaptif paper stop:
  - ilk dakikalarda stop grace period
  - recovery entry için daha geniş stop
  - momentum entry için ayrı stop
- Yeni araştırma endpointleri:
  - `/api/research/pump-detective?threshold_pct=30&minutes_before=60`
  - `/api/research/winning-patterns?all_time=true`
- Yeni script:
  - `python scripts/pump_detective_v4.py --threshold 30 --minutes-before 60 --out pump_detective_60m.json`

## Kurulum

```bash
docker compose up --build
```

Dashboard:

```text
http://localhost:3010
```

## Önerilen analiz komutları

```bash
docker exec -t parlayan_db pg_dump -U parlayan parlayan > parlayan_dump.sql
```

Container içinde/host ortamında:

```bash
python scripts/pump_detective_v4.py --threshold 30 --minutes-before 30 --out pump_30m.json
python scripts/pump_detective_v4.py --threshold 30 --minutes-before 60 --out pump_60m.json
python scripts/pump_detective_v4.py --threshold 30 --minutes-before 120 --out pump_120m.json
```

## Not

Bu sürüm bir yatırım tavsiyesi veya kâr garantisi değildir. Amaç, gerçek para riske atmadan sinyal kalitesini ve coin davranışlarını ölçmektir.
