# Parlayan Bot Professional Paper V4.4

V4.4 kaybeden işlem analizinden çıkan gerçek eksikleri kapatır.

## Ana hedef
- Sahte momentumları erken öldürmek
- Doğru trendleri sabit take-profitte kesmeden daha uzun taşımak
- Hacim artışının alış baskısı mı satış baskısı mı olduğunu proxy ile ölçmek

## Eklenen modüller

### Directional Volume Proxy
Public Binance kline verisinden yaklaşık alış/satış baskısı çıkarır:
- `up_volume_ratio`
- `down_volume_ratio`
- `directional_volume_delta`
- `recent_green_bar_ratio`
- `close_location_score`
- `directional_volume_score`

### Follow Through Engine
Trade açıldıktan sonra ilk dakikalarda takip eder:
- max runup oluştu mu?
- işlem hiç yukarı gitti mi?
- velocity/directional flow hâlâ sağlıklı mı?

Zayıf trade erken kapatılır:
- `FAILED_BREAKOUT`
- `STALE_MOMENTUM_EXIT`

### Adaptive Profit Engine
Kazanan işlem güçlü trend gösteriyorsa sabit take-profitte hemen kapatmaz:
- kârın bir kısmını stop ile kilitler
- güçlü trendde hedefi `extended_take_profit_pct` seviyesine taşır
- çıkış nedeni: `ADAPTIVE_TAKE_PROFIT`

### V4.4 kalite raporu
Endpoint:
`/api/reports/v44-quality?hours=24&all_time=true`

## Not
Bu sürüm hâlâ canlı al-sat yapmaz. Amaç daha dürüst, daha profesyonel paper-trade araştırmasıdır.
