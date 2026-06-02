# Parlayan Bot — Professional Paper V4.1

Bu sürüm canlı trade yapmaz. Amaç kendimizi kandırmayan, profesyonel paper-trade ve araştırma altyapısıdır.

## Eklenen kritik düzeltmeler

1. Reject logging
   - Her `REJECT`, `WATCH`, `RISK_BLOCK`, `TRADE_REJECT` sebebi `signal_events` tablosuna yazılır.
   - Endpoint: `/api/research/reject-reasons`

2. Unrealized PnL risk hesabı
   - Günlük risk artık sadece kapanmış işlemlerden oluşmaz.
   - Açık pozisyonların tahmini unrealized PnL'i günlük PnL ve equity içine eklenir.
   - Endpoint: `/api/risk/portfolio-global`

3. Session restart koruması
   - Yeni session açılsa bile eski session açık pozisyonları global riskte sayılır.
   - Aynı sembolde eski session açık trade varsa tekrar giriş engellenir.
   - Pozisyon monitörü eski session açık pozisyonları da günceller.

4. Gerçekçi slippage modeli
   - Entry fiyatı adverse BUY fill ile yukarı kaydırılır.
   - Exit fiyatı adverse SELL fill ile aşağı kaydırılır.
   - Stop exit'lerinde ekstra slippage uygulanır.
   - PnL hesabında slippage artık gerçek fill fiyatına yansır; ayrıca ikinci kez düşülmez.

5. Faz bazlı stop sistemi
   - Recovery / volume wakeup / early momentum / FOMO fazları farklı stop ve grace period kullanır.
   - FOMO, DANGER ve DISTRIBUTION için stop grace yoktur.

6. Pump Detective v2
   - Sadece zirveye değil, pump başlangıç kırılımına bakar.
   - `%30+` yapan coinlerde ilk `%8 24h` kırılımını breakout kabul eder.
   - Breakout'tan 30/60/120 dakika önceki ortak davranışları çıkarır.
   - Endpoint: `/api/research/pump-detective-v2`

## Doğrulama sorguları

```sql
SELECT strategy_version, status, COUNT(*)
FROM paper_trades
GROUP BY strategy_version, status
ORDER BY strategy_version, status;

SELECT event_type, COUNT(*)
FROM signal_events
WHERE ts > now() - interval '1 hour'
GROUP BY event_type
ORDER BY COUNT(*) DESC;

SELECT * FROM paper_equity_curve
ORDER BY ts DESC
LIMIT 5;
```

## Önemli

Bu sürüm pozitif PnL'i otomatik başarı kabul etmez. Önce reject reason, unrealized drawdown, session carryover ve execution fill kalitesi kontrol edilir.
