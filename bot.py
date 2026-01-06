import requests
import time
from datetime import datetime, timedelta
from collections import deque

# =============== YAPILANDIRMA ===============
import os
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TOKEN_ADDRESS = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"  # FARTCOIN Solana
CHECK_INTERVAL = 10  # Kontrol aralığı (saniye) - daha sık kontrol
SPIKE_THRESHOLD_CRITICAL = 3  # %500 artış = KRİTİK ALARM
SPIKE_THRESHOLD_WARNING = 1  # %200 artış = UYARI
COOLDOWN_MINUTES = 15  # Tekrar alarm için bekleme süresi (dakika)

# =============== GLOBAL DEĞİŞKENLER ===============
last_alert_time = None
volume_snapshots = deque(maxlen=120)  # Son 1 saat veri (30sn*120 = 1 saat)

def get_dexscreener_data(token_address):
    """DexScreener'dan token verilerini çeker"""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data.get('pairs'):
            print("❌ Token için pair bulunamadı!")
            return None
        
        # En yüksek likiditeye sahip pair'i al
        pairs = sorted(data['pairs'], key=lambda x: float(x.get('liquidity', {}).get('usd', 0)), reverse=True)
        main_pair = pairs[0]
        
        return {
            'pair_address': main_pair.get('pairAddress'),
            'dex': main_pair.get('dexId'),
            'price_usd': float(main_pair.get('priceUsd', 0)),
            'volume_m5': float(main_pair.get('volume', {}).get('m5', 0)),  # 5 dakikalık hacim
            'volume_h1': float(main_pair.get('volume', {}).get('h1', 0)),  # 1 saatlik hacim
            'volume_24h': float(main_pair.get('volume', {}).get('h24', 0)),
            'liquidity': float(main_pair.get('liquidity', {}).get('usd', 0)),
            'price_change_5m': float(main_pair.get('priceChange', {}).get('m5', 0)),
            'price_change_1h': float(main_pair.get('priceChange', {}).get('h1', 0)),
            'base_token': main_pair.get('baseToken', {}).get('symbol', 'UNKNOWN'),
            'txns_5m_buys': main_pair.get('txns', {}).get('m5', {}).get('buys', 0),
            'txns_5m_sells': main_pair.get('txns', {}).get('m5', {}).get('sells', 0)
        }
    except Exception as e:
        print(f"❌ DexScreener API hatası: {e}")
        return None

def calculate_spike(current_volume_5m):
    """5 dakikalık hacim spike'ını hesaplar"""
    global volume_snapshots
    
    # Mevcut 5dk hacmi kaydet
    now = datetime.now()
    volume_snapshots.append({
        'time': now,
        'volume_5m': current_volume_5m
    })
    
    # Yeterli veri yoksa bekle (en az 10 dakika veri = 20 snapshot)
    if len(volume_snapshots) < 3:
        return None, None, current_volume_5m
    
    # Son 1 saatin 5 dakikalık ortalama hacmini hesapla
    # (son 5 dakika hariç, çünkü onu karşılaştıracağız)
    past_volumes = [v['volume_5m'] for v in list(volume_snapshots)[:-10]]  # Son 5dk hariç
    
    if not past_volumes or all(v == 0 for v in past_volumes):
        return None, None, current_volume_5m
    
    avg_volume_5m = sum(past_volumes) / len(past_volumes)
    
    # Spike yüzdesini hesapla
    if avg_volume_5m == 0:
        return None, None, current_volume_5m
    
    spike_percent = ((current_volume_5m - avg_volume_5m) / avg_volume_5m) * 100
    
    return spike_percent, avg_volume_5m, current_volume_5m

def send_telegram_message(message):
    """Telegram'a mesaj gönderir"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Telegram mesaj hatası: {e}")
        return False

def format_number(num):
    """Sayıları okunabilir formata çevirir"""
    if num >= 1_000_000:
        return f"${num/1_000_000:.2f}M"
    elif num >= 1_000:
        return f"${num/1_000:.2f}K"
    else:
        return f"${num:.2f}"

def check_volume_spike():
    """ANI hacim artışını kontrol eder ve gerekirse alarm gönderir"""
    global last_alert_time
    
    # DexScreener'dan veri çek
    data = get_dexscreener_data(TOKEN_ADDRESS)
    if not data:
        return
    
    current_volume_5m = data['volume_m5']
    
    # Spike hesapla
    spike_percent, avg_volume, current_vol = calculate_spike(current_volume_5m)
    
    # Log
    now = datetime.now().strftime("%H:%M:%S")
    if spike_percent is not None:
        status = ""
        if spike_percent >= SPIKE_THRESHOLD_CRITICAL:
            status = "🔥 KRİTİK!"
        elif spike_percent >= SPIKE_THRESHOLD_WARNING:
            status = "⚠️ UYARI!"
        
        print(f"[{now}] {data['base_token']} | 5dk: {format_number(current_vol)} | Ort: {format_number(avg_volume)} | Spike: {spike_percent:+.1f}% {status}")
        print(f"       Fiyat: ${data['price_usd']:.8f} ({data['price_change_5m']:+.2f}%) | Alım/Satım: {data['txns_5m_buys']}/{data['txns_5m_sells']}")
    else:
        print(f"[{now}] {data['base_token']} - Veri toplama... ({len(volume_snapshots)}/20 minimum)")
        return
    
    # Cooldown kontrolü
    if last_alert_time:
        elapsed = datetime.now() - last_alert_time
        if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
            remaining = COOLDOWN_MINUTES - int(elapsed.total_seconds() / 60)
            if spike_percent >= SPIKE_THRESHOLD_CRITICAL:
                print(f"       ⏳ Cooldown aktif: {remaining} dakika kaldı (ama spike %{spike_percent:.0f}!)")
            return
    
    # ALARM KONTROLÜ
    if spike_percent >= SPIKE_THRESHOLD_WARNING:
        
        # Alarm seviyesini belirle
        if spike_percent >= SPIKE_THRESHOLD_CRITICAL:
            emoji = "🚨🔥"
            alert_level = "KRİTİK SPIKE"
            color = "🔴"
        else:
            emoji = "⚠️📊"
            alert_level = "HACIM SPIKE"
            color = "🟡"
        
        # Fiyat değişimi emoji
        price_emoji = "🚀" if data['price_change_5m'] > 5 else "📈" if data['price_change_5m'] > 0 else "📉"
        
        # Alım baskısı hesapla
        total_txns = data['txns_5m_buys'] + data['txns_5m_sells']
        buy_pressure = (data['txns_5m_buys'] / total_txns * 100) if total_txns > 0 else 0
        pressure_emoji = "🟢" if buy_pressure > 60 else "🟡" if buy_pressure > 40 else "🔴"
        
        message = f"""
{emoji} <b>{alert_level}!</b> {emoji}

💎 Token: <b>{data['base_token']}</b>
🔗 DEX: <b>{data['dex'].upper()}</b>

{color} <b>5 DAKİKALIK PATLAMA!</b>
━━━━━━━━━━━━━━━━━━━━
📊 Son 5dk hacim: <b>{format_number(current_vol)}</b>
📉 1h ortalama: <b>{format_number(avg_volume)}</b>
🔥 SPIKE: <b>%{spike_percent:+.1f}</b>

💰 Fiyat: <b>${data['price_usd']:.8f}</b>
{price_emoji} 5dk Değişim: <b>%{data['price_change_5m']:+.2f}</b>
📈 1h Değişim: <b>%{data['price_change_1h']:+.2f}</b>

🔄 Son 5dk İşlemler:
{pressure_emoji} Alım: <b>{data['txns_5m_buys']}</b> | Satım: <b>{data['txns_5m_sells']}</b>
💪 Alım Baskısı: <b>%{buy_pressure:.0f}</b>

💧 Likidite: <b>{format_number(data['liquidity'])}</b>
📊 1h Hacim: <b>{format_number(data['volume_h1'])}</b>
📊 24h Hacim: <b>{format_number(data['volume_24h'])}</b>

🔍 <a href="https://dexscreener.com/solana/{data['pair_address']}">DexScreener'da Gör</a>

⏰ {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}
"""
        
        if send_telegram_message(message):
            print(f"✅ ALARM GÖNDERİLDİ! Spike: %{spike_percent:.1f}")
            last_alert_time = datetime.now()

def main():
    """Ana döngü"""
    print("=" * 60)
    print("🔥 5 DAKİKALIK SPIKE DETECTOR BAŞLATILDI 🔥")
    print("=" * 60)
    print(f"📌 Token: {TOKEN_ADDRESS}")
    print(f"📌 Kontrol aralığı: {CHECK_INTERVAL} saniye")
    print(f"📌 Kritik eşik: %{SPIKE_THRESHOLD_CRITICAL}+ (son 1h ortalamasına göre)")
    print(f"📌 Uyarı eşik: %{SPIKE_THRESHOLD_WARNING}+ (son 1h ortalamasına göre)")
    print(f"📌 Cooldown: {COOLDOWN_MINUTES} dakika")
    print(f"📌 Veri kaynağı: DexScreener API (5dk anlık hacim)")
    print("=" * 60)
    
    # İlk veriyi al ve token bilgisini göster
    initial_data = get_dexscreener_data(TOKEN_ADDRESS)
    if initial_data:
        start_msg = f"""✅ <b>5 Dakikalık Spike Detector Başlatıldı!</b>

💎 Token: <b>{initial_data['base_token']}</b>
🔗 DEX: <b>{initial_data['dex'].upper()}</b>
💰 Fiyat: <b>${initial_data['price_usd']:.8f}</b>
💧 Likidite: <b>{format_number(initial_data['liquidity'])}</b>

⚠️ Uyarı Eşiği: <b>%{SPIKE_THRESHOLD_WARNING}+</b>
🔥 Kritik Eşiği: <b>%{SPIKE_THRESHOLD_CRITICAL}+</b>

⏱️ 10 dakika sonra aktif olacak (veri toplama)
🔄 Her {CHECK_INTERVAL} saniyede kontrol ediliyor"""
        
        send_telegram_message(start_msg)
        print("\n🚀 Bot aktif! ANI hacim patlamalarını izliyorum...\n")
    
    while True:
        try:
            check_volume_spike()
            time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            print("\n\n🛑 Bot durduruldu!")
            send_telegram_message("🛑 Spike Detector durduruldu!")
            break
        except Exception as e:
            print(f"❌ Beklenmeyen hata: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
