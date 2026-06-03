# Parlayan Bot Professional Paper V4.6.1

Bu paket V4.6 üzerine güvenlik düzeltmesi ekler.

## Yeni katmanlar

1. Stable Asset Exclusion Layer
   - USDEUSDT, USDCUSDT, FDUSDUSDT, DAIUSDT, RLUSDUSDT gibi stable/synthetic pariteleri taramadan ve trade kararından çıkarır.
   - Amaç Pattern Memory, Market DNA ve Discovery Entry veri kalitesini korumaktır.

2. Adaptive DNA Confidence Brake
   - Market DNA artık 8 sample ile araştırma sinyali üretebilir.
   - Ancak eşikleri tam gevşetmez.
   - `full_weight_samples: 50` olana kadar DNA etkisi kontrollü kalır.

3. Discovery Guard
   - 24h/5m fiyat hareketi neredeyse sıfır olan pariteleri engeller.
   - Directional volume ve up-volume ikisi de zayıfsa discovery girişi açmaz.

## Kurulum

```powershell
docker compose down
docker compose up -d --build
```

## Kontrol

```powershell
curl http://localhost:3010/api/status
curl http://localhost:3010/api/reports/stable-filter
curl http://localhost:3010/api/reports/adaptive-dna
curl http://localhost:3010/api/trades/open
```

## Mevcut açık USDEUSDT pozisyonu

V4.6.1 yeni USDEUSDT işlemi açmaz. Daha önce V4.6 ile açılmış açık USDEUSDT pozisyonu varsa onu manuel kapatmak veya botun stop/monitor mantığına bırakmak gerekir.
