# Parlayan Bot Professional Paper V4.5 Upgrade

Bu paket V4.4 üzerine profesyonel strateji öğrenme katmanlarını ekler.

## Eklenen Ana Modüller

1. `app/follow_through_engine.py`
   - Giriş sonrası hareketin devam edip etmediğini ölçer.
   - Runup, velocity, directional volume ve momentum acceleration ile zayıf işlemleri erken ayıklar.

2. `app/immediate_failure_exit.py`
   - Stop-loss beklemeden bariz başarısız breakoutları kapatır.
   - Amaç küçük zararı büyümeden kesmektir.

3. `app/adaptive_profit_engine.py`
   - Güçlü trendlerde sabit take-profit yerine hedef uzatma ve kâr kilitleme sağlar.

4. `app/pattern_memory_engine.py`
   - +10 / +20 / +30 / +50 / +100 hareketleri yakalar.
   - Hareketten önceki, trigger anındaki ve hareket sonucundaki metrikleri veri setine yazar.

5. `app/market_dna_engine.py`
   - Pattern Memory verilerinden istatistiksel profil üretir.
   - Hangi metrik kombinasyonlarının hangi piyasa rejiminde çalıştığını gösterir.

6. `app/directional_volume.py`
   - Public kline verilerinden alıcı/satıcı hacim baskısı proxy skoru üretir.

## Veritabanı Değişikliği Gerekli mi?

Evet. Pattern Memory ve Market DNA için yeni tablolar gerekir. Değişiklikler geriye uyumludur ve mevcut tabloları bozmaz.

Yeni tablolar:

- `coin_lifecycle_events`
- `pattern_memory_samples`
- `market_dna_profiles`

Migration dosyası:

```text
db/migrations/v45_pattern_memory_market_dna.sql
```

Ana schema dosyası da aynı idempotent `CREATE TABLE IF NOT EXISTS` bloklarıyla güncellendi.

## Yeni API Endpointleri

Araştırma endpointleri:

```text
/api/research/pattern-memory
/api/research/market-dna
/api/research/market-dna/refresh
```

Dokümantasyon uyum alias endpointleri:

```text
/api/reports/daily
/api/reports/velocity
/api/reports/pump-alarms
/api/reports/decision-quality
/api/reports/reject-outcomes
/api/reports/pattern-memory
/api/reports/market-dna
```

## Stratejik Kazanım

V4.5 ile sistem sadece coin bulmaz; hangi coin hareketinden önce hangi metriklerin kıpırdadığını ölçer, bunları tekrar eden pattern olarak kaydeder ve daha sonra strateji kalibrasyonu için Market DNA profilleri üretir.

Bu katman canlı trade emri vermez. Paper trading research ve karar destek sistemi olarak çalışır.
