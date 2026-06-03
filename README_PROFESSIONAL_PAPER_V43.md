# Parlayan Bot Professional Paper v4.3

Bu sürüm canlı emir göndermez. Amaç paper-trade sonuçlarını daha dürüst okumak ve bot kararlarının doğru olup olmadığını ölçmektir.

## Yeni modüller

### Decision Quality Engine
`signal_events` kararlarını sonradan takip eder. Her reject, alert veya entry kararından sonra 1h / 4h / 12h içinde fiyatın ne yaptığı ölçülür.

Yeni tablo:
- `decision_outcomes`

Yeni endpointler:
- `/api/research/decision-outcomes/refresh`
- `/api/research/decision-quality`
- `/api/research/danger-quality`

### Reject Outcome Tracker
DANGER, cooldown, liquidity, volume ve diğer reject nedenlerinin gerçekten doğru olup olmadığını ölçer.

Örnek soru:
- DANGER diye reddedilen coinlerin kaçı sonradan +5% yaptı?
- Cooldown yüzünden kaçan coin var mı?
- Volume filtresi fazla sert mi?

### Near Miss Analytics
Botun reddettiği ama sonradan yükselen coinleri listeler.

Endpoint:
- `/api/research/near-misses`

### Pre-Pump Alert Quality
PRE_PUMP_ALERT ve FAST_PUMP_ALERT kayıtlarının sonradan gerçekten hareket üretip üretmediğini ölçer.

Endpoint:
- `/api/research/pre-pump-alert-quality`

### Market Regime Engine
BTC/ETH hareketi, altcoin genişliği ve DANGER yoğunluğuna göre piyasa rejimi çıkarır.

Rejimler:
- `RISK_OFF`
- `ALT_RISK_ON`
- `ALT_ROTATION`
- `HOT_FOMO_MARKET`
- `NEUTRAL`

Yeni tablo:
- `market_regime_snapshots`

Endpoint:
- `/api/research/market-regime`

## Kurulum

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

## İlk doğrulama

```bash
docker exec -it parlayan_bot grep -R "professional_paper_v43" /app
```

```sql
SELECT session_name, strategy_version, status, started_at
FROM paper_sessions
ORDER BY started_at DESC
LIMIT 5;
```

```sql
SELECT COUNT(*) FROM decision_outcomes;
SELECT COUNT(*) FROM market_regime_snapshots;
```

## Önemli analiz endpointleri

```text
http://localhost:3010/api/research/decision-quality?hours=36&horizon_minutes=240
http://localhost:3010/api/research/danger-quality?hours=36&horizon_minutes=240
http://localhost:3010/api/research/near-misses?hours=36&horizon_minutes=240&min_upside_pct=5
http://localhost:3010/api/research/pre-pump-alert-quality?hours=36&horizon_minutes=240
http://localhost:3010/api/research/market-regime?hours=24
```

## Ana hedef

Yeni indikatör eklemek değil; mevcut kararların doğruluğunu ölçmek.

Bu sürümden sonra filtreleri hisle değil veriyle ayarlayacağız.
