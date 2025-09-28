# scraper_actions.py (Dosya Oluşturan Versiyon)
import os
import sys
import requests
import json
import time

try:
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    pass

# --- Ayarlar ve Sabitler ---
EPIC_REFRESH_TOKEN = os.getenv('EPIC_REFRESH_TOKEN')
EPIC_BASIC_AUTH = 'ZWM2ODRiOGM2ODdmNDc5ZmFkZWEzY2IyYWQ4M2Y1YzY6ZTFmMzFjMjExZjI4NDEzMTg2MjYyZDM3YTEzZmM4NGQ='
SONGS_API_URL = 'https://fortnitecontent-website-prod07.ol.epicgames.com/content/api/pages/fortnite-game/spark-tracks'
SEASON = 10
PAGES_TO_SCAN = 10 # Top 1000 için 10 sayfa

session = requests.Session()
session.verify = False

ACCESS_TOKEN = None
ACCOUNT_ID = None
TOKEN_EXPIRY_TIME = 0

def refresh_token_if_needed():
    # ... (Bu fonksiyon bir önceki kod ile aynı, değişiklik yok) ...
    global ACCESS_TOKEN, ACCOUNT_ID, TOKEN_EXPIRY_TIME
    if time.time() > TOKEN_EXPIRY_TIME:
        print("\n[AUTH] Access token yenileniyor...")
        try:
            response = session.post('https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token', headers={'Content-Type': 'application/x-www-form-urlencoded', 'Authorization': f'Basic {EPIC_BASIC_AUTH}'}, data={'grant_type': 'refresh_token', 'refresh_token': EPIC_REFRESH_TOKEN, 'token_type': 'eg1'})
            response.raise_for_status()
            token_data = response.json()
            ACCESS_TOKEN, ACCOUNT_ID = token_data.get('access_token'), token_data.get('account_id')
            TOKEN_EXPIRY_TIME = time.time() + token_data.get('expires_in', 7200) - 200
            if not ACCESS_TOKEN or not ACCOUNT_ID: return False
            print("[AUTH] Token başarıyla yenilendi.")
            return True
        except requests.exceptions.RequestException as e:
            print(f"[HATA] Token yenilenemedi: {e.response.text if e.response else e}")
            return False
    return True

# ... (get_all_songs, get_account_names, parse_entry fonksiyonları önceki kod ile aynı) ...

def main(instrument_to_scan):
    all_songs = get_all_songs()
    if not all_songs: return

    season_number = SEASON
    total_songs = len(all_songs)
    print(f"\n--- {instrument_to_scan} için {total_songs} şarkı taranacak ---")

    for i, song in enumerate(all_songs):
        song_id, event_id = song.get('sn'), song.get('su')
        if not event_id or not song_id: continue
            
        print(f"\n-> Şarkı {i+1}/{total_songs}: {song.get('tt')}")

        for page_num in range(PAGES_TO_SCAN):
            try:
                if not refresh_token_if_needed(): raise Exception("Token yenilenemedi.")
                
                season_str = f"season{season_number:03d}"
                url = f"https://events-public-service-live.ol.epicgames.com/api/v1/leaderboards/FNFestival/{season_str}_{event_id}/{event_id}_{instrument_to_scan}/{ACCOUNT_ID}?page={page_num}"
                
                headers = {'Authorization': f'Bearer {ACCESS_TOKEN}'}
                response = session.get(url, headers=headers, timeout=10)
                
                if response.status_code == 404: break
                response.raise_for_status()
                raw_entries = response.json().get('entries', [])
                if not raw_entries: break

                # --- DOSYA YAZMA MANTIĞI ---
                # 1. Klasör yolunu oluştur
                dir_path = f"leaderboards/season{season_number}/{song_id}"
                os.makedirs(dir_path, exist_ok=True)
                
                # 2. Kullanıcı adlarını al ve veriyi işle
                account_ids = [entry['teamId'] for entry in raw_entries]
                user_names = get_account_names(account_ids)
                parsed_data = {'entries': []}
                for entry in raw_entries:
                    parsed_entry = parse_entry(entry)
                    parsed_entry['userName'] = user_names.get(entry['teamId'])
                    parsed_data['entries'].append(parsed_entry)

                # 3. Dosyayı yaz
                file_path = f"{dir_path}/{instrument_to_scan}_{page_num}.json"
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(parsed_data, f, ensure_ascii=False, indent=4)
                
                print(f"  > Sayfa {page_num} -> {file_path} dosyasına kaydedildi.")
                time.sleep(1.5)

            except Exception as e:
                print(f"\n > Sayfa {page_num + 1} işlenirken hata oluştu: {e}")
                break
        print()

    print(f"\n[BİTTİ] {instrument_to_scan} için tarama tamamlandı.")

if __name__ == "__main__":
    if not EPIC_REFRESH_TOKEN:
        print("[HATA] Gerekli secret (EPIC_REFRESH_TOKEN) ayarlanmamış."); sys.exit(1)
    if len(sys.argv) < 2:
        print("Kullanım: python scraper_actions.py [enstrüman_adı]"); sys.exit(1)
    
    main(sys.argv[1])
