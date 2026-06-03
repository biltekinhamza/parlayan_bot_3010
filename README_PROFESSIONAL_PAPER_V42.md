# Parlayan Bot Professional Paper v4.3

Bu sürüm hâlâ canlı emir göndermez. Amaç: kendimizi kandırmadan, profesyonel seviyede paper-trade araştırması yapmak.

## GitHub araştırmasından alınan ve projeye uyarlanan fikirler

- Pump detector projelerindeki websocket/anomali mantığı projeye **Fast Pump Alarm** olarak uyarlandı.
- Velocity/momentum bot fikirleri projeye **price_velocity / volume_velocity / trade_count_velocity** olarak eklendi.
- Paper trading/backtest projelerindeki dürüst muhasebe fikri projeye **gross/net PnL, fee, slippage, runup/drawdown, time in trade** olarak eklendi.
- Trailing stop projelerindeki kâr merdiveni fikri projeye **Smart Trailing Ladder** olarak eklendi.
- Günlük backtest raporu fikri projeye **Daily Signal Report** endpoint’i olarak eklendi.

## Yeni Modüller

### 1. Velocity Engine

Dosya: `app/velocity_engine.py`

Hesaplanan metrikler:

- `price_velocity_1m_pct`
- `price_velocity_5m_pct`
- `price_velocity_15m_pct`
- `momentum_acceleration`
- `volume_velocity`
- `trade_count_velocity`
- `velocity_score`
- `fast_alarm_score`
- `fast_alarm`

Bu metrikler `market_snapshots.extra` içine yazılır.

### 2. Fast Pump Alarm

Scanner artık sadece periyodik skor üretmez. Ek olarak hızlı anomali alarmı yazar:

- Event type: `FAST_PUMP_ALERT`
- Endpoint: `/api/research/fast-alerts`

### 3. Velocity Entry Profile

Decision engine yeni profili destekler:

- `VELOCITY_ALARM_ENTRY`

Bu profil FOMO kovalamaz; yine 24h/FOMO/risk filtrelerinden geçmek zorundadır.

### 4. Smart Trailing Ladder

Config:

```yaml
smart_trailing_ladder:
  - runup_pct: 3.5
    lock_pct: 0.0
    label: break_even
  - runup_pct: 5.0
    lock_pct: 2.0
    label: lock_2
  - runup_pct: 8.0
    lock_pct: 4.0
    label: lock_4
  - runup_pct: 12.0
    lock_pct: 7.0
    label: lock_7
  - runup_pct: 15.0
    lock_pct: 10.0
    label: tight_trailing
```

### 5. Dürüst Paper PnL

Trade kapanınca `paper_trades.context` içine şunlar yazılır:

- `gross_pnl_pct`
- `fee_pct`
- `entry_slippage_pct`
- `exit_slippage_pct`
- `total_slippage_pct`
- `net_pnl_pct`
- `net_pnl_usdt`
- `max_runup_pct`
- `max_drawdown_pct`
- `time_in_trade_min`

### 6. Daily Signal Report

Endpoint:

```text
/api/research/daily-report
```

Döner:

- Günlük sinyal dağılımı
- Net/gross PnL
- Exit reason dağılımı
- En iyi 10 trade
- En kötü 10 trade
- Recent trades

## Kurulum

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Kontrol

```bash
docker logs -f parlayan_bot
```

SQL:

```sql
SELECT strategy_version, status, COUNT(*)
FROM paper_trades
GROUP BY strategy_version, status;

SELECT event_type, COUNT(*)
FROM signal_events
WHERE ts > now() - interval '1 hour'
GROUP BY event_type
ORDER BY COUNT(*) DESC;
```

## Not

Canlı trade modülü yoktur. Bu sürüm araştırma, paper execution ve risk doğrulama içindir.
