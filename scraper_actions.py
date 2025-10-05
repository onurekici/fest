import os
import sys
import requests
import json
import time
import re

# SSL uyarılarını gizle
try:
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    pass

# --- İLERLEME ÇUBUĞU FONKSİYONU ---
def print_progress_bar (iteration, total, prefix = 'Progress:', suffix = 'Complete', decimals = 1, length = 100, fill = '█', printEnd = "\r"):
    """Terminale ilerleme çubuğu oluşturmak için döngü içinde çağrılır."""
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / total))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()
    if iteration == total: 
        sys.stdout.write(printEnd)
        sys.stdout.write('\n')
        sys.stdout.flush()
# --- İLERLEME ÇUBUĞU BİTİŞİ ---


# --- Ayarlar Ortam Değişkenlerinden (GitHub Secrets) Alınacak ---
EPIC_REFRESH_TOKEN = os.getenv('EPIC_REFRESH_TOKEN')
EPIC_BASIC_AUTH = os.getenv('EPIC_BASIC_AUTH')

# --- Sabitler ---
SONGS_API_URL = 'https://fortnitecontent-website-prod07.ol.epicgames.com/content/api/pages/fortnite-game/spark-tracks'
SEASON = 10
PAGES_TO_SCAN = 5

# --- Global Değişkenler ---
session = requests.Session()
session.verify = False
ACCESS_TOKEN = None
ACCOUNT_ID = None
TOKEN_EXPIRY_TIME = 0

def sanitize_filename(name):
    """Bir string'i dosya/klasör adı olarak kullanılabilir hale getirir."""
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()

def refresh_token_if_needed():
    """Token'ın süresi dolmuşsa veya hiç yoksa yeniler."""
    global ACCESS_TOKEN, ACCOUNT_ID, TOKEN_EXPIRY_TIME
    if time.time() > TOKEN_EXPIRY_TIME:
        print("\n[AUTH] Access token yenileniyor...")
        try:
            response = session.post(
                'https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token',
                headers={'Content-Type': 'application/x-www-form-urlencoded', 'Authorization': f'Basic {EPIC_BASIC_AUTH}'},
                data={'grant_type': 'refresh_token', 'refresh_token': EPIC_REFRESH_TOKEN, 'token_type': 'eg1'}
            )
            response.raise_for_status()
            token_data = response.json()
            ACCESS_TOKEN = token_data.get('access_token')
            ACCOUNT_ID = token_data.get('account_id')
            TOKEN_EXPIRY_TIME = time.time() + (token_data.get('expires_in', 7200) - 200)
            if not ACCESS_TOKEN or not ACCOUNT_ID:
                print("[HATA] Token yenileme yanıtı beklenen formatta değil.")
                return False
            print("[AUTH] Token başarıyla yenilendi.")
            return True
        except requests.exceptions.RequestException as e:
            print(f"[HATA] Token yenilenemedi: {e.response.text if e.response else e}")
            return False
    return True

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

def get_account_names(account_ids):
    """Verilen account ID listesi için kullanıcı adlarını çeker (429 Hata çözümü ile)."""
    if not account_ids: return {}
    unique_ids = list(set(account_ids))
    print(f"  > {len(unique_ids)} oyuncunun kullanıcı adı sorgulanıyor...")
    all_user_names = {}
    
    try:
        if not refresh_token_if_needed(): raise Exception("Token yenilenemedi.")
        max_retries = 3
        
        for i in range(0, len(unique_ids), 100):
            batch_ids = unique_ids[i:i + 100]
            
            response = None
            for attempt in range(max_retries):
                try:
                    params = '&'.join([f'accountId={uid}' for uid in batch_ids])
                    url = f'https://account-public-service-prod.ol.epicgames.com/account/api/public/account?{params}'
                    headers = {'Authorization': f'Bearer {ACCESS_TOKEN}'}
                    response = session.get(url, headers=headers, timeout=15)
                    response.raise_for_status()
                    break 
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 429 and attempt < max_retries - 1:
                        wait_time = 2 ** attempt * 5 
                        print(f"  [429 UYARI] Çok fazla istek. {wait_time} saniye bekleniyor... ({attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue 
                    else:
                        raise e
            else:
                raise Exception(f"Kullanıcı adı API'si {max_retries} denemeden sonra başarısız oldu.")

            for user in response.json():
                account_id, display_name = user.get('id'), user.get('displayName')
                if not display_name and 'externalAuths' in user:
                    for p_data in user['externalAuths'].values():
                        if ext_name := p_data.get('externalDisplayName'):
                            display_name = f"[{p_data.get('type', 'platform').upper()}] {ext_name}"
                            break
                if account_id: all_user_names[account_id] = display_name or 'Bilinmeyen'
                
            if i + 100 < len(unique_ids):
                time.sleep(1) 
                
        return all_user_names
    except Exception as e:
        print(f" > Kullanıcı adı alınırken Hata: {e}")
        return {}


def parse_entry(raw_entry):
    """API'den gelen ham veriyi işleyerek best_run'ı bulur."""
    best_score = -1
    best_run_stats = None
    for session_data in raw_entry.get("sessionHistory", []):
        stats = session_data.get("trackedStats", {})
        current_score = stats.get("SCORE", 0)
        if current_score >= best_score:
            best_score = current_score
            best_run_stats = stats
    if best_run_stats:
        return {
            "accuracy": int(best_run_stats.get("ACCURACY", 0) / 10000),
            "score": best_run_stats.get("SCORE", 0),
            "difficulty": best_run_stats.get("DIFFICULTY"),
            "stars": best_run_stats.get("STARS_EARNED"),
            "fullcombo": best_run_stats.get("FULL_COMBO") == 1
        }
    return None

# DEĞİŞİKLİK 1: Fonksiyon artık çıktıların yazılacağı ana klasörü parametre olarak alıyor.
def main(instrument_to_scan, output_base_dir):
    """Ana script fonksiyonu."""
    all_songs = get_all_songs()
    if not all_songs:
        return
    
    season_number = SEASON
    total_songs = len(all_songs)
    print(f"\n--- {instrument_to_scan} için {total_songs} şarkı taranacak ---")

    for i, song in enumerate(all_songs):
        song_id = song.get('sn')
        event_id = song.get('su')
        
        if not event_id or not song_id:
            continue
            
        print(f"\n-> Şarkı {i+1}/{total_songs}: {song.get('tt')}")

        for page_num in range(PAGES_TO_SCAN):
            try:
                print_progress_bar(page_num + 1, PAGES_TO_SCAN, prefix = f"Sayfa {page_num + 1}:", length = 30)
                
                if not refresh_token_if_needed():
                    raise Exception("Token yenilenemedi, bu şarkı atlanıyor.")
                
                season_str = f"season{season_number:03d}"
                url = f"https://events-public-service-live.ol.epicgames.com/api/v1/leaderboards/FNFestival/{season_str}_{event_id}/{event_id}_{instrument_to_scan}/{ACCOUNT_ID}?page={page_num}"
                
                headers = {'Authorization': f'Bearer {ACCESS_TOKEN}'}
                response = session.get(url, headers=headers, timeout=10)
                
                if response.status_code == 404: 
                    sys.stdout.write('\n')
                    break 
                response.raise_for_status()
                raw_entries = response.json().get('entries', [])
                if not raw_entries: 
                    sys.stdout.write('\n')
                    break

                # DEĞİŞİKLİK 2: Dosya yolu, parametre olarak gelen output_base_dir kullanılarak oluşturuluyor.
                # Örnek: ./output/leaderboards/season10/song_id
                dir_path = f"{output_base_dir}/leaderboards/season{season_number}/{song_id}"
                os.makedirs(dir_path, exist_ok=True)
                
                account_ids = [entry['teamId'] for entry in raw_entries]
                user_names = get_account_names(account_ids) 
                
                parsed_data = {'entries': []}
                for entry in raw_entries:
                    parsed_entry = parse_entry(entry)
                    if parsed_entry: # Sadece geçerli parsed_entry varsa ekle
                        parsed_entry['userName'] = user_names.get(entry['teamId'])
                        parsed_data['entries'].append(parsed_entry)

                file_path = f"{dir_path}/{instrument_to_scan}_{page_num}.json"
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(parsed_data, f, ensure_ascii=False, indent=4)
                
                sys.stdout.write('\n')
                print(f"  > Sayfa {page_num+1} -> {file_path} dosyasına kaydedildi.")
                
                time.sleep(2) 

            except Exception as e:
                sys.stdout.write('\n')
                print(f" > Sayfa {page_num + 1} işlenirken hata oluştu: {e}")
                break
        print() 

    print(f"\n[BİTTİ] {instrument_to_scan} için tarama tamamlandı.")

# DEĞİŞİKLİK 3: Script'in ana çalışma bloğu, komut satırı argümanlarını işleyecek şekilde güncellendi.
if __name__ == "__main__":
    if not EPIC_REFRESH_TOKEN or not EPIC_BASIC_AUTH:
        print("[HATA] Gerekli secret'lar (EPIC_REFRESH_TOKEN, EPIC_BASIC_AUTH) ayarlanmamış."); sys.exit(1)
        
    if len(sys.argv) < 2:
        print("Kullanım: python scraper_actions.py [enstrüman_adı] [isteğe_bağlı_çıktı_klasörü]"); sys.exit(1)
    
    # Argümanları al
    instrument = sys.argv[1]
    # Eğer ikinci argüman (çıktı klasörü) verilmişse onu kullan, 
    # verilmemişse (örneğin yerel test için) mevcut klasörü (.) kullan.
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    
    main(instrument, output_dir)
