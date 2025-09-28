import os
import sys
import requests
import json
import time

# SSL uyarılarını gizle
try:
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    pass

# --- Ayarlar Ortam Değişkenlerinden (GitHub Secrets) Alınacak ---
API_URL = os.getenv('API_URL')
API_SECRET_KEY = os.getenv('API_SECRET_KEY')
EPIC_REFRESH_TOKEN = os.getenv('EPIC_REFRESH_TOKEN')

# --- Sabitler ---
EPIC_BASIC_AUTH = 'ZWM2ODRiOGM2ODdmNDc5ZmFkZWEzY2IyYWQ4M2Y1YzY6ZTFmMzF- ...' # Tam halini kullanın
SONGS_API_URL = 'https://fortnitecontent-website-prod07.ol.epicgames.com/content/api/pages/fortnite-game/spark-tracks'
SEASON = 10
PAGES_TO_SCAN = 10
BATCH_SIZE = 400
INSTRUMENTS_TO_SCAN = ["Solo_Guitar", "Solo_Drums", "Solo_Bass", "Solo_Vocals", "Solo_PeripheralGuitar", "Solo_PeripheralBass"]

# --- Global Değişkenler ---
session = requests.Session()
session.verify = False

# --- YENİ: Token Yönetimi için Global Değişkenler ---
ACCESS_TOKEN = None
ACCOUNT_ID = None
TOKEN_EXPIRY_TIME = 0 # Token'ın ne zaman geçersiz olacağını tutar

def print_progress_bar(iteration, total, length=50):
    percent = ("{0:.1f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = '█' * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r|{bar}| {percent}% Complete')
    sys.stdout.flush()

# --- GÜNCELLENDİ: Token Yenileme Fonksiyonu ---
def refresh_token_if_needed():
    """Token'ın süresi dolmuşsa yeniler."""
    global ACCESS_TOKEN, ACCOUNT_ID, TOKEN_EXPIRY_TIME
    
    # Eğer mevcut zaman, token'ın son kullanma zamanını geçtiyse...
    if time.time() > TOKEN_EXPIRY_TIME:
        print("\n[AUTH] Access token süresi doldu veya ilk çalıştırma. Yenileniyor...")
        url = 'https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token'
        headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Authorization': f'Basic {EPIC_BASIC_AUTH}'}
        data = {'grant_type': 'refresh_token', 'refresh_token': EPIC_REFRESH_TOKEN, 'token_type': 'eg1'}
        
        try:
            response = session.post(url, headers=headers, data=data)
            response.raise_for_status()
            token_data = response.json()
            
            ACCESS_TOKEN = token_data.get('access_token')
            ACCOUNT_ID = token_data.get('account_id')
            # Token ömrünü 2 saatten biraz az (örn: 7000 saniye) olarak ayarla ki tam sınırda sorun çıkmasın
            TOKEN_EXPIRY_TIME = time.time() + (token_data.get('expires_in', 7200) - 200)
            
            if not ACCESS_TOKEN or not ACCOUNT_ID:
                print("[HATA] Token yenileme yanıtı beklenen formatta değil.")
                return False
                
            print("[AUTH] Token başarıyla yenilendi.")
            return True
        except requests.exceptions.RequestException as e:
            print(f"[HATA] Token yenilenemedi: {e.response.text if e.response else e}")
            return False
    return True # Token hala geçerli

def get_all_songs():
    print("[BİLGİ] Tüm şarkıların listesi çekiliyor...")
    # ... (içerik aynı) ...
    try:
        response = session.get(SONGS_API_URL)
        response.raise_for_status()
        temp_tracks = [value['track'] for value in response.json().values() if isinstance(value, dict) and 'track' in value]
        print(f"[BİLGİ] {len(temp_tracks)} şarkı bulundu.")
        return temp_tracks
    except requests.exceptions.RequestException as e:
        print(f"[HATA] Şarkı listesi alınamadı: {e}"); return None

# --- GÜNCELLENDİ: Daha Detaylı Hata Mesajı ---
def get_account_names(account_ids):
    if not account_ids: return {}
    unique_ids = list(set(account_ids)); print(f"  > {len(unique_ids)} oyuncunun kullanıcı adı sorgulanıyor...", end='', flush=True)
    all_user_names = {}
    try:
        if not refresh_token_if_needed(): raise Exception("Token yenilenemedi.")
        
        for i in range(0, len(unique_ids), 100):
            params = '&'.join([f'accountId={uid}' for uid in unique_ids[i:i + 100]])
            url = f'https://account-public-service-prod.ol.epicgames.com/account/api/public/account?{params}'
            headers = {'Authorization': f'Bearer {ACCESS_TOKEN}'}
            response = session.get(url, headers=headers, timeout=15); response.raise_for_status()
            for user in response.json():
                account_id, display_name = user.get('id'), user.get('displayName')
                if not display_name and 'externalAuths' in user:
                    for p_data in user['externalAuths'].values():
                        if ext_name := p_data.get('externalDisplayName'): display_name = f"[{p_data.get('type', 'platform').upper()}] {ext_name}"; break
                if account_id: all_user_names[account_id] = display_name or 'Bilinmeyen'
        print(" Tamamlandı.")
        return all_user_names
    except requests.exceptions.RequestException as e:
        # Daha detaylı hata mesajı
        print(f" Hata: {e}")
        return {}
    except Exception as e:
        print(f" Genel Hata: {e}")
        return {}
        
def parse_entry(raw_entry):
    # ... (içerik aynı) ...
    best_score = -1; best_run_stats = None
    for session_data in raw_entry.get("sessionHistory", []):
        stats = session_data.get("trackedStats", {}); current_score = stats.get("SCORE", 0)
        if current_score >= best_score: best_score = current_score; best_run_stats = stats
    if best_run_stats:
        return {"accuracy": int(best_run_stats.get("ACCURACY", 0) / 10000),"score": best_run_stats.get("SCORE", 0),"difficulty": best_run_stats.get("DIFFICULTY"),"stars": best_run_stats.get("STARS_EARNED"),"fullcombo": best_run_stats.get("FULL_COMBO") == 1}
    return None
    
def send_batch_to_api(scores_batch):
    # ... (içerik aynı, tekrar deneme mantığı ile) ...
    if not scores_batch: return
    print(f"\n[API] {len(scores_batch)} adet skor sunucuya gönderiliyor...")
    headers = {'Content-Type': 'application/json', 'X-Api-Key': API_SECRET_KEY}
    retries = 3
    for i in range(retries):
        try:
            response = session.post(API_URL, headers=headers, data=json.dumps(scores_batch)); response.raise_for_status()
            print(f"[API] Sunucu yanıtı: {response.json().get('message', 'Mesaj yok')}")
            return
        except requests.exceptions.RequestException as e:
            print(f"[API] HATA (Deneme {i+1}/{retries}): {e}")
            if i < retries - 1: time.sleep(5)
            else: print("[API] Tüm denemeler başarısız oldu.")

def main(instrument_to_scan):
    all_songs = get_all_songs()
    if not all_songs: return
        
    scores_batch = []; total_songs = len(all_songs); season_number = SEASON
    
    print(f"\n--- {instrument_to_scan} için {total_songs} şarkı taranacak ---")
    
    for i, song in enumerate(all_songs):
        song_id, event_id = song.get('sn'), song.get('su')
        if not event_id or not song_id: continue
            
        print(f"\n-> Şarkı {i+1}/{total_songs}: {song.get('tt')}")
        
        for page_num in range(PAGES_TO_SCAN):
            try:
                if not refresh_token_if_needed(): raise Exception("Token yenilenemedi.")
                
                print_progress_bar(page_num + 1, PAGES_TO_SCAN)

                season_str = f"season{season_number:03d}"
                url = (f"https://events-public-service-live.ol.epicgames.com/api/v1/leaderboards/FNFestival/"
                       f"{season_str}_{event_id}/{event_id}_{instrument_to_scan}/{ACCOUNT_ID}?page={page_num}")
                
                headers = {'Authorization': f'Bearer {ACCESS_TOKEN}'}
                response = session.get(url, headers=headers, timeout=10)
                
                if response.status_code == 404: break
                response.raise_for_status()
                raw_entries = response.json().get('entries', [])
                if not raw_entries: break

                account_ids = [entry['teamId'] for entry in raw_entries]
                user_names = get_account_names(account_ids)
                
                for entry in raw_entries:
                    best_run = parse_entry(entry)
                    username = user_names.get(entry['teamId'])
                    if best_run and username and username != 'Bilinmeyen':
                        score_data = {"userName": username, "song_id": song_id, "instrument": instrument_to_scan, "season": season_number}
                        score_data.update(best_run)
                        scores_batch.append(score_data)
                
                if len(scores_batch) >= BATCH_SIZE:
                    send_batch_to_api(scores_batch); scores_batch = []
                
                time.sleep(1.5)

            except Exception as e:
                print(f"\n > Sayfa {page_num + 1} işlenirken hata oluştu: {e}")
                break
        print()

    if scores_batch:
        send_batch_to_api(scores_batch)

    print(f"\n[BİTTİ] {instrument_to_scan} için tarama tamamlandı.")

if __name__ == "__main__":
    if not all([API_URL, API_SECRET_KEY, EPIC_REFRESH_TOKEN]):
        print("[HATA] Gerekli secret'lar ayarlanmamış."); sys.exit(1)
    if len(sys.argv) < 2:
        print("Kullanım: python scraper_actions.py [enstrüman_adı]"); sys.exit(1)
    
    main(sys.argv[1])
