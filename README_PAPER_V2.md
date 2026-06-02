# Parlayan Bot Paper V2

Bu sürümde canlı Binance emir verme modülü bilinçli olarak eklenmedi.

## Yapılan değişiklikler

1. `storage.close_paper_trade()` düzeltildi.
   - Trade kapanınca bağlı `parlayan_candidates` kaydı da `CLOSED` durumuna alınır.
   - Kapanış nedeni, PnL ve son trade id candidate context içine yazılır.

2. `scripts/analyze_paper_report.py` eklendi.
   - `trades.json` üzerinden paper trade performans raporu üretir.
   - Win rate, ortalama PnL, exit reason dağılımı, en iyi/en kötü semboller hesaplanır.

## Rapor komutu

```bash
python scripts/analyze_paper_report.py --trades trades.json --out paper_performance_report.json
```

## Bu sürümün amacı

- Canlı trade yapmadan strateji kalitesini ölçmek
- Stop-loss / trailing / max-time davranışlarını gözlemek
- Hangi coinlerde ve hangi exit sebeplerinde stratejinin zayıf kaldığını görmek
- Canlı API modülüne geçmeden önce sinyal sistemini iyileştirmek

## Canlı trade bilinçli olarak yok

Bu klasörde Binance API key, signed order, gerçek BUY/SELL endpoint entegrasyonu yoktur.
