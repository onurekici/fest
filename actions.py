import os
import sys
import requests
import json
import time
import re
import datetime 
from cryptography.fernet import Fernet
from configparser import ConfigParser
from typing import Any, Dict, Optional

# Windows konsolunda (cp1254 vb.) Unicode blok karakterleri hata verebiliyor.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# SSL uyarılarını gizle
try:
    from urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    pass

# --- İLERLEME ÇUBUĞU ---
def print_progress_bar (iteration, total, prefix = 'Progress:', suffix = 'Complete', decimals = 1, length = 50, fill = '#', printEnd = "\r"):
    if total == 0: total = 1
    percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / total))
    filledLength = int(length * iteration // total)
    bar = fill * filledLength + '-' * (length - filledLength)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()
    if iteration == total: 
        sys.stdout.write(printEnd)
        sys.stdout.write('\n')
        sys.stdout.flush()

# --- AYARLAR ---
EPIC_REFRESH_TOKEN = os.getenv("EPIC_REFRESH_TOKEN")
EPIC_BASIC_AUTH = os.getenv("EPIC_BASIC_AUTH")
DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY")

DEFAULT_TIMEOUT_S = 20
MAX_RETRIES = 6

def _load_token_ini_if_needed() -> None:
    """
    token.ini varsa ve env boşsa oradan doldur.
    token.pyw zaten token.ini üretiyor ama script env'den okuyordu.
    """
    global EPIC_REFRESH_TOKEN, EPIC_BASIC_AUTH
    if EPIC_REFRESH_TOKEN and EPIC_BASIC_AUTH:
        return
    ini_path = os.path.join(os.path.dirname(__file__), "token.ini")
    if not os.path.exists(ini_path):
        ini_path = "token.ini"
    if not os.path.exists(ini_path):
        return
    cfg = ConfigParser()
    try:
        cfg.read(ini_path, encoding="utf-8")
        if cfg.has_section("EPIC_GAMES"):
            if not EPIC_BASIC_AUTH:
                EPIC_BASIC_AUTH = cfg.get("EPIC_GAMES", "EPIC_BASIC_AUTH", fallback=None)
            if not EPIC_REFRESH_TOKEN:
                EPIC_REFRESH_TOKEN = cfg.get("EPIC_GAMES", "EPIC_REFRESH_TOKEN", fallback=None)
    except Exception:
        return

SONGS_API_URL = 'https://fortnitecontent-website-prod07.ol.epicgames.com/content/api/pages/fortnite-game/spark-tracks'
DEFAULT_SEASON = 15
PAGES_TO_SCAN = 5

session = requests.Session()
session.verify = True
session.headers.update(
    {
        # Basit bir UA bile bazı edge/WAF bloklarını azaltabiliyor.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    }
)
ACCESS_TOKEN = None
ACCOUNT_ID = None
TOKEN_EXPIRY_TIME = 0

def _request(method: str, url: str, *, headers: Optional[Dict[str, str]] = None, data=None, timeout: int = DEFAULT_TIMEOUT_S) -> requests.Response:
    """
    403/429/5xx durumlarında kontrollü retry + backoff uygular.
    HTML 403 genelde edge/WAF blok; kısa bekleyip tekrar denemek gerekiyor.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            r = session.request(method, url, headers=headers, data=data, timeout=timeout)

            # 401: access token süresi dolmuş olabilir, refresh deneyelim (caller da tekrar deneyebilir)
            if r.status_code == 401:
                return r

            # 429: rate limit. Retry-After varsa onu bekle.
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                sleep_s = int(ra) if (ra and ra.isdigit()) else min(60, 2 ** attempt)
                time.sleep(sleep_s)
                continue

            # 403 + HTML: edge/waf blok ihtimali. Giderek artan cooldown.
            if r.status_code == 403 and "<html" in (r.text or "").lower():
                time.sleep(min(120, 5 * (attempt + 1) ** 2))
                continue

            # 5xx: geçici olabilir
            if 500 <= r.status_code <= 599:
                time.sleep(min(30, 2 ** attempt))
                continue

            return r
        except Exception as e:
            last_exc = e
            time.sleep(min(10, 2 ** attempt))
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("request failed without exception")

def refresh_token_if_needed():
    global ACCESS_TOKEN, ACCOUNT_ID, TOKEN_EXPIRY_TIME
    _load_token_ini_if_needed()
    if not EPIC_BASIC_AUTH or not EPIC_REFRESH_TOKEN:
        return False
    if time.time() > TOKEN_EXPIRY_TIME:
        try:
            response = _request(
                "POST",
                "https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {EPIC_BASIC_AUTH}",
                },
                data={"grant_type": "refresh_token", "refresh_token": EPIC_REFRESH_TOKEN, "token_type": "eg1"},
                timeout=DEFAULT_TIMEOUT_S,
            )
            if response.status_code != 200:
                return False
            token_data: Dict[str, Any] = response.json()
            ACCESS_TOKEN = token_data.get('access_token')
            ACCOUNT_ID = token_data.get('account_id')
            TOKEN_EXPIRY_TIME = time.time() + (token_data.get('expires_in', 7200) - 200)
            return True
        except:
            return False
    return True

def get_account_names(account_ids):
    if not account_ids: return {}
    unique_ids = list(set(account_ids))
    all_user_names = {}
    
    for i in range(0, len(unique_ids), 100):
        batch_ids = unique_ids[i:i + 100]
        while True:
            try:
                if not refresh_token_if_needed(): time.sleep(10); continue
                params = '&'.join([f'accountId={uid}' for uid in batch_ids])
                url = f'https://account-public-service-prod.ol.epicgames.com/account/api/public/account?{params}'
                response = _request("GET", url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}, timeout=DEFAULT_TIMEOUT_S)
                if response.status_code == 401:
                    TOKEN_EXPIRY_TIME = 0
                    continue
                response.raise_for_status()
                for user in response.json():
                    uid = user.get('id')
                    d_name = user.get('displayName')
                    if not d_name and 'externalAuths' in user:
                        for auth in user['externalAuths'].values():
                            if 'externalDisplayName' in auth:
                                d_name = auth['externalDisplayName']
                                break
                    all_user_names[uid] = d_name or 'Gizli'
                break 
            except:
                time.sleep(30); continue
    return all_user_names

def parse_entry(raw_entry):
    entry = {"rank": raw_entry.get("rank"), "teamId": raw_entry.get("teamId"), "userName": None, "best_run": {}, "sessions": []}
    _bestScoreYet = -1
    _bestRun = {}
    for s in raw_entry.get("sessionHistory", []):
        stats = s.get("trackedStats", {})
        score = stats.get("SCORE", 0)
        valid_entry = {
            "accuracy": int(stats.get("ACCURACY", 0) / 10000), "score": score,
            "difficulty": stats.get("DIFFICULTY"), "instrument": stats.get("INSTRUMENT_0"), 
            "stars": stats.get("STARS_EARNED"), "fullcombo": True if stats.get("FULL_COMBO") == 1 else False
        }
        if score > _bestScoreYet:
            _bestRun = valid_entry
            _bestScoreYet = score
        entry["sessions"].append({"time": time.time(), "valid": valid_entry})
    entry["best_run"] = _bestRun
    return entry if _bestRun else None

def main(instrument, output_dir):
    if not DATA_ENCRYPTION_KEY: print("HATA: Key eksik!"); return
    fernet = Fernet(DATA_ENCRYPTION_KEY.encode())

    # En başta token almayı zorla: token yoksa boş boş progress basmasın.
    if not refresh_token_if_needed() or not ACCESS_TOKEN or not ACCOUNT_ID:
        print("HATA: Epic access token alınamadı. Secrets/env değişkenleri job'a aktarılmıyor olabilir.")
        print("Beklenen env: EPIC_BASIC_AUTH, EPIC_REFRESH_TOKEN (ve DATA_ENCRYPTION_KEY).")
        return
    
    # Sezonları Belirle (seasons.json var mı kontrol et)
    seasons_to_process = [DEFAULT_SEASON]
    if os.path.exists("seasons.json"):
        try:
            with open("seasons.json", "r") as f:
                data = json.load(f)
                seasons_to_process = data.get("seasons", [DEFAULT_SEASON])
        except:
            pass

    try:
        resp = session.get(SONGS_API_URL).json()
        songs = [v['track'] for v in resp.values() if isinstance(v, dict) and 'track' in v]
        print(f"Başarılı: {len(songs)} adet şarkı bulundu.") # BUNU EKLE
    except Exception as e:
        print(f"Şarkı listesi çekilemedi HATA: {e}") # BUNU EKLE
        songs = []

    for current_season in seasons_to_process:
        print(f"\n--- SEZON {current_season} | {instrument} Başlıyor ---")
        
        for i, song in enumerate(songs):
            sid, eid = song.get('sn'), song.get('su')
            if not sid or not eid: continue
            
            print_progress_bar(i + 1, len(songs), prefix=f"S{current_season} - Şarkı {i+1}/{len(songs)}:", suffix=song.get('tt', '')[:20], length=20)

            for page in range(PAGES_TO_SCAN):
                if not refresh_token_if_needed():
                    print("\nHATA: Token yenilenemedi (refresh failed). Bu noktada veri çekmek mümkün değil.")
                    return
                # Sezon formatını 001, 002... şeklinde ayarlar
                # Sezon 1 ise 'evergreen', değilse 'season002', 'season003' vb. formatını kullan
                season_prefix = "evergreen" if current_season == 1 else f"season{current_season:03d}"
                if current_season == 1:
                    event_id = f"evergreen_{eid}"
                else:
                    event_id = f"season{current_season:03d}_{eid}"

                # 2. URL'yi oluştur (Dikkat: FNFestival/ sonrasında sabit bir 'season' kelimesi yok)
                # Fortnite client loglarına göre bu endpoint ek query param'larla çağrılıyor.
                # Örn: ?page=0&rank=0&teamAccountIds=&appId=Fortnite&showLiveSessions=false
                url = (
                    "https://events-public-service-live.ol.epicgames.com/api/v1/leaderboards/FNFestival/"
                    f"{event_id}/{eid}_{instrument}/{ACCOUNT_ID}"
                    f"?page={page}&rank=0&teamAccountIds=&appId=Fortnite&showLiveSessions=false"
                )
                
                try:
                    r = _request("GET", url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}, timeout=DEFAULT_TIMEOUT_S)

                    # Token geçersizleştiyse refresh edip aynı sayfayı tekrar dene
                    if r.status_code == 401:
                        TOKEN_EXPIRY_TIME = 0
                        if refresh_token_if_needed():
                            r = _request("GET", url, headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}, timeout=DEFAULT_TIMEOUT_S)
                    
                    # EĞER 200 DÖNMEZSE NEDENİNİ YAZDIR
                    if r.status_code != 200: 
                        print(f"\nSunucu Hatası: {r.status_code} | URL: {url}")
                        print(f"Sunucu Yanıtı: {r.text}")
                        break
                        
                    raw = r.json().get('entries', [])
                    if not raw: break
                    
                    u_names = get_account_names([e['teamId'] for e in raw])
                    parsed = {'entries': []}
                    for e in raw:
                        pe = parse_entry(e)
                        if pe:
                            pe['userName'] = u_names.get(e['teamId'], "Gizli")
                            parsed['entries'].append(pe)

                    # KAYIT YOLU: leaderboards/seasonX/şarkı/..
                    dir_path = f"{output_dir}/leaderboards/season{current_season}/{sid}"
                    os.makedirs(dir_path, exist_ok=True)
                    
                    json_str = json.dumps(parsed, ensure_ascii=False, indent=4)
                    encrypted = fernet.encrypt(json_str.encode())
                    
                    with open(f"{dir_path}/{instrument}_{page}.json", 'wb') as f:
                        f.write(encrypted)
                    
                    # Daha hızlı ama hâlâ güvenli aralık (GitHub egress throttle için)
                    time.sleep(0.35 + (0.1 * (page % 3)))
                except Exception as e:
                    print(f"\nİstek Hatası: {type(e).__name__}: {e} | URL: {url}")
                    break

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ".")
