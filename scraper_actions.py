import os
import sys
import requests
import json
import time

# --- Ayarlar Ortam Değişkenlerinden (GitHub Secrets) Alınacak ---
API_URL = os.getenv('API_URL')
API_SECRET_KEY = os.getenv('API_SECRET_KEY')
EPIC_REFRESH_TOKEN = os.getenv('EPIC_REFRESH_TOKEN')

# --- Sabitler ---
EPIC_BASIC_AUTH = 'ZWM2ODRiOGM2ODdmNDc5ZmFkZWEzY2IyYWQ4M2Y1YzY6ZTFmMzFjMjExZjI4NDEzMTg2MjYyZDM3YTEzZmM4NGQ='
SONGS_API_URL = 'https://fortnitecontent-website-prod07.ol.epicgames.com/content/api/pages/fortnite-game/spark-tracks'
SEASON = 10
PAGES_TO_SCAN = 10  # Top 1000
BATCH_SIZE = 400   # Sunucuya tek seferde kaç skor gönderilecek

# --- Global Değişkenler ---
session = requests.Session()
session.verify = False # SSL hatalarını yoksay

def refresh_eg1_token():
    """Refresh token kullanarak yeni bir EG1 access token ve account_id alır."""
    print("[AUTH] Refresh token kullanılarak yeni bir access token alınıyor...")
    url = 'https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': f'Basic {EPIC_BASIC_AUTH}'
    }
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': EPIC_REFRESH_TOKEN,
        'token_type': 'eg1'
    }
    try:
        response = session.post(url, headers=headers, data=data)
        response.raise_for_status()
        data = response.json()
        print("[AUTH] Yeni access token ve account_id başarıyla alındı.")
        return data.get('access_token'), data.get('account_id')
    except requests.exceptions.RequestException as e:
        print(f"[HATA] Token yenilenemedi: {e.response.text if e.response else e}")
        return None, None

def get_all_songs():
    """Tüm şarkıların listesini API'den alır."""
    print("[BİLGİ] Tüm şarkıların listesi çekiliyor...")
    try:
        response = session.get(SONGS_API_URL)
        response.raise_for_status()
        all_tracks_data = response.json()
        temp_tracks = [value['track'] for value in all_tracks_data.values() if isinstance(value, dict) and 'track' in value]
        print(f"[BİLGİ] {len(temp_tracks)} şarkı bulundu.")
        return temp_tracks
    except requests.exceptions.RequestException as e:
        print(f"[HATA] Şarkı listesi alınamadı: {e}")
        return None

def get_account_names(account_ids, token):
    """Verilen account ID listesi için kullanıcı adlarını çeker."""
    if not account_ids: return {}
    unique_ids = list(set(account_ids))
    print(f"  > {len(unique_ids)} oyuncunun kullanıcı adı sorgulanıyor...")
    all_user_names = {}
    try:
        for i in range(0, len(unique_ids), 100):
            batch_ids = unique_ids[i:i + 100]
            params = '&'.join([f'accountId={uid}' for uid in batch_ids])
            url = f'https://account-public-service-prod.ol.epicgames.com/account/api/public/account?{params}'
            headers = {'Authorization': f'Bearer {token}'}
            response = session.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            for user in response.json():
                account_id, display_name = user.get('id'), user.get('displayName')
                if not display_name and 'externalAuths' in user:
                    for p_data in user['externalAuths'].values():
                        if ext_name := p_data.get('externalDisplayName'): display_name = f"[{p_data.get('type', 'platform').upper()}] {ext_name}"; break
                if account_id: all_user_names[account_id] = display_name or 'Bilinmeyen'
        return all_user_names
    except requests.exceptions.RequestException as e:
        print(f"  > Kullanıcı adları alınırken hata: {e}"); return {}

def parse_entry(raw_entry):
    """API'den gelen ham veriyi işleyerek best_run'ı bulur."""
    best_score = -1; best_run_stats = None
    for session_data in raw_entry.get("sessionHistory", []):
        stats = session_data.get("trackedStats", {}); current_score = stats.get("SCORE", 0)
        if current_score >= best_score: best_score = current_score; best_run_stats = stats
    if best_run_stats:
        return {"accuracy": int(best_run_stats.get("ACCURACY", 0) / 10000),"score": best_run_stats.get("SCORE", 0),"difficulty": best_run_stats.get("DIFFICULTY"),"stars": best_run_stats.get("STARS_EARNED"),"fullcombo": best_run_stats.get("FULL_COMBO") == 1}
    return None

def send_batch_to_api(scores_batch):
    """Biriktirilen skorları PHP API'sine gönderir."""
    if not scores_batch: return
    print(f"\n[API] {len(scores_batch)} adet skor sunucuya gönderiliyor...")
    headers = {'Content-Type': 'application/json', 'X-Api-Key': API_SECRET_KEY}
    try:
        response = session.post(API_URL, headers=headers, data=json.dumps(scores_batch))
        response.raise_for_status()
        print(f"[API] Sunucu yanıtı: {response.json().get('message', 'Mesaj yok')}")
    except requests.exceptions.RequestException as e:
        print(f"[API] HATA: Sunucuya veri gönderilemedi: {e.response.text if e.response else e}")

def main(instrument_to_scan, access_token, account_id):
    """Ana script fonksiyonu."""
    all_songs = get_all_songs()
    if not all_songs:
        return
        
    scores_batch = []
    total_songs = len(all_songs)
    season_number = SEASON # Sezonu sayı olarak alalım
    
    print(f"\n--- {instrument_to_scan} için {total_songs} şarkı taranacak ---")
    
    for i, song in enumerate(all_songs):
        song_id = song.get('sn')
        event_id = song.get('su')
        
        if not event_id or not song_id:
            continue
            
        print(f"-> Şarkı {i+1}/{total_songs}: {song.get('tt')} ({instrument_to_scan})")
        
        for page_num in range(PAGES_TO_SCAN):
            try:
                season_str = f"season{season_number:03d}"
                url = (f"https://events-public-service-live.ol.epicgames.com/api/v1/leaderboards/FNFestival/"
                       f"{season_str}_{event_id}/{event_id}_{instrument_to_scan}/{account_id}?page={page_num}")
                
                headers = {'Authorization': f'Bearer {access_token}'}
                response = session.get(url, headers=headers, timeout=10)
                
                if response.status_code == 404:
                    break  # Bu enstrüman için başka sayfa yok
                response.raise_for_status()
                raw_entries = response.json().get('entries', [])
                if not raw_entries:
                    break  # Sayfa boş

                account_ids = [entry['teamId'] for entry in raw_entries]
                user_names = get_account_names(account_ids, access_token)
                
                for entry in raw_entries:
                    best_run = parse_entry(entry)
                    username = user_names.get(entry['teamId'])
                    if best_run and username and username != 'Bilinmeyen':
                        # --- HATA BURADAYDI, DÜZELTİLDİ ---
                        score_data = {
                            "userName": username,
                            "song_id": song_id,
                            "instrument": instrument_to_scan,
                            "season": season_number,
                            **best_run
                        }
                        scores_batch.append(score_data)
                
                if len(scores_batch) >= BATCH_SIZE:
                    send_batch_to_api(scores_batch)
                    scores_batch = [] # Batch'i temizle
                
                time.sleep(1.5) # API'yi yormamak için GÜVENLİK amaçlı bekleme

            except requests.exceptions.RequestException as e:
                print(f" > Hata: Sayfa {page_num + 1} çekilemedi. Atlanıyor. Hata: {e}")
                break

    # Döngü bittikten sonra kalan skorları gönder
    if scores_batch:
        send_batch_to_api(scores_batch)

    print(f"\n[BİTTİ] {instrument_to_scan} için tarama tamamlandı.")

if __name__ == "__main__":
    if not all([API_URL, API_SECRET_KEY, EPIC_REFRESH_TOKEN]):
        print("[HATA] Gerekli secret'lar (API_URL, API_SECRET_KEY, EPIC_REFRESH_TOKEN) ayarlanmamış."); sys.exit(1)

    if len(sys.argv) < 2:
        print("Kullanım: python scraper_actions.py [enstrüman_adı]"); sys.exit(1)
    
    instrument_to_scan = sys.argv[1]
    
    access_token, account_id = refresh_eg1_token()
    if not access_token or not account_id:
        print("[HATA] Script durduruluyor çünkü geçerli bir token alınamadı."); sys.exit(1)

    main(instrument_to_scan, access_token, account_id)


