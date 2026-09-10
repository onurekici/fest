import configparser
import copy
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

DAILY_API_URL = "https://fngw-mcp-gc-livefn.ol.epicgames.com/fortnite/api/calendar/v1/timeline"
CONTENT_API_URL = (
    "https://fortnitecontent-website-prod07.ol.epicgames.com/content/api/pages/fortnite-game/spark-tracks"
)
OAUTH_TOKEN_URL = "https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token"

SETLIST_NAME_MAP = {
    1: "Daily Vibes",
    2: "Spotlight",
    3: "Festival Selects",
}

SETLIST_FILENAME_MAP = {
    "Daily Vibes": "daily_vibes.json",
    "Spotlight": "spotlight.json",
    "Festival Selects": "festival_selects.json",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_ini_values(path: str = "token.ini") -> Dict[str, str]:
    if not os.path.exists(path):
        return {}

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if "EPIC_GAMES" not in parser:
        return {}

    section = parser["EPIC_GAMES"]
    out: Dict[str, str] = {}
    for key in ("EPIC_BASIC_AUTH", "EPIC_DAILY"):
        raw = section.get(key, "")
        if raw and raw.strip():
            out[key] = raw.strip()
    return out


def get_timeline(basic_auth_b64: str, refresh_token: str) -> dict:
    oauth_headers = {
        "Authorization": f"basic {basic_auth_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    oauth_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    oauth_response = requests.post(
        OAUTH_TOKEN_URL, headers=oauth_headers, data=oauth_data, timeout=30
    )
    if oauth_response.status_code != 200:
        raise RuntimeError(
            f"OAuth basarisiz ({oauth_response.status_code}): {oauth_response.text[:800]}"
        )

    oauth_json = oauth_response.json()
    access_token = oauth_json.get("access_token")
    token_type = oauth_json.get("token_type", "bearer")
    if not access_token:
        raise RuntimeError(f"OAuth cevabinda access_token yok: {oauth_json}")

    timeline_headers = {
        "Authorization": f"{token_type} {access_token}",
        "Accept": "application/json",
        "User-Agent": "DeviceAuthGenerator/1.3.0 Windows/10.0.26220",
    }
    timeline_response = requests.get(DAILY_API_URL, headers=timeline_headers, timeout=30)
    if timeline_response.status_code != 200:
        raise RuntimeError(
            f"Timeline basarisiz ({timeline_response.status_code}): {timeline_response.text[:800]}"
        )
    return timeline_response.json()


def fetch_spark_catalog() -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """spark-tracks ham JSON: track_id -> tam değer; ayrıca track.sn ile indeks."""
    response = requests.get(CONTENT_API_URL, timeout=30)
    response.raise_for_status()
    payload = response.json()

    by_track_id: Dict[str, dict] = {}
    by_sn: Dict[str, dict] = {}

    if not isinstance(payload, dict):
        return by_track_id, by_sn

    for track_id, item in payload.items():
        if not isinstance(item, dict) or "track" not in item:
            continue
        track = item.get("track")
        if not isinstance(track, dict):
            continue
        sn = track.get("sn")
        if not sn:
            continue

        ref = copy.deepcopy(item)
        by_track_id[track_id] = ref
        by_sn[str(sn)] = ref

    return by_track_id, by_sn


def spark_track_stub(shortname: str) -> dict:
    """API'de yoksa Spark şablonuna yakın minimal kayıt (gerekirse alan eklenir)."""
    return {
        "_title": shortname,
        "track": {
            "_type": "SparkTrack",
            "sn": shortname,
            "tt": shortname,
            "an": "Not Found",
        },
        "_noIndex": False,
        "_templateName": "track",
        "_setlistExportMissing": True,
    }


def resolve_spark_entry(
    shortname: str,
    by_track_id: Dict[str, dict],
    by_sn: Dict[str, dict],
) -> dict:
    """Önce top-level id (buddyholly), sonra track.sn ile eşle; Spark yapısını koru."""
    if shortname in by_track_id:
        return copy.deepcopy(by_track_id[shortname])
    if shortname in by_sn:
        return copy.deepcopy(by_sn[shortname])
    return spark_track_stub(shortname)


def parse_latest_active_state(states: List[dict]) -> Optional[dict]:
    now = datetime.now(timezone.utc)
    valid_states = []

    for state in states:
        valid_from = state.get("validFrom")
        if not valid_from:
            continue
        dt = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
        if dt <= now:
            valid_states.append(state)

    if not valid_states:
        return None

    valid_states.sort(
        key=lambda s: datetime.fromisoformat(s["validFrom"].replace("Z", "+00:00")),
        reverse=True,
    )
    return valid_states[0]


def extract_setlists(
    timeline: dict,
    by_track_id: Dict[str, dict],
    by_sn: Dict[str, dict],
) -> Dict[str, dict]:
    channels = timeline.get("channels", {})
    client_events = channels.get("client-events", {})
    states = client_events.get("states", [])
    if not isinstance(states, list):
        return {}

    active_state = parse_latest_active_state(states)
    if not active_state:
        return {}

    now = datetime.now(timezone.utc)
    result: Dict[str, dict] = {}

    for event in active_state.get("activeEvents", []):
        if not isinstance(event, dict):
            continue

        event_type = event.get("eventType", "")
        active_since = event.get("activeSince")
        active_until = event.get("activeUntil")

        if not event_type.startswith("Sparks_CuratedSetlist"):
            continue
        if not active_since or not active_until or ":" not in event_type:
            continue

        since_dt = datetime.fromisoformat(active_since.replace("Z", "+00:00"))
        until_dt = datetime.fromisoformat(active_until.replace("Z", "+00:00"))
        if not (since_dt <= now <= until_dt):
            continue

        left, right = event_type.split(":", 1)
        setlist_index = int(left.split(",")[0].split("_")[-1])
        setlist_name = SETLIST_NAME_MAP.get(setlist_index, f"Setlist {setlist_index}")

        shortnames = [value.strip() for value in right.split(",") if value.strip()]
        songs = []
        for shortname in shortnames:
            songs.append(resolve_spark_entry(shortname, by_track_id, by_sn))

        result[setlist_name] = {
            "index": setlist_index,
            "activeSince": active_since,
            "activeUntil": active_until,
            "songCount": len(songs),
            "songs": songs,
        }

    return dict(sorted(result.items(), key=lambda pair: pair[1]["index"]))


def write_outputs(output_dir: str, setlists: Dict[str, dict]) -> None:
    os.makedirs(output_dir, exist_ok=True)
    generated_at = utc_now_iso()

    merged_payload = {
        "generatedAt": generated_at,
        "setlists": setlists,
    }
    merged_path = os.path.join(output_dir, "setlists.json")
    with open(merged_path, "w", encoding="utf-8") as f:
        json.dump(merged_payload, f, ensure_ascii=False, indent=2)

    for setlist_name, payload in setlists.items():
        filename = SETLIST_FILENAME_MAP.get(setlist_name)
        if not filename:
            continue

        file_payload = {
            "generatedAt": generated_at,
            "name": setlist_name,
            "activeSince": payload.get("activeSince"),
            "activeUntil": payload.get("activeUntil"),
            "songCount": payload.get("songCount"),
            "songs": payload.get("songs", []),
        }
        file_path = os.path.join(output_dir, filename)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(file_payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    ini = load_ini_values()
    basic_auth = os.getenv("EPIC_BASIC_AUTH", ini.get("EPIC_BASIC_AUTH", "")).strip()
    daily = os.getenv("EPIC_DAILY", ini.get("EPIC_DAILY", "")).strip()

    if not basic_auth:
        raise RuntimeError("EPIC_BASIC_AUTH eksik (secret veya token.ini [EPIC_GAMES]).")
    if not daily:
        raise RuntimeError("EPIC_DAILY eksik (secret veya token.ini [EPIC_GAMES]). Epic refresh_token olmali.")

    timeline = get_timeline(basic_auth, daily)
    by_track_id, by_sn = fetch_spark_catalog()
    setlists = extract_setlists(timeline, by_track_id, by_sn)
    write_outputs("songs", setlists)
    print("OK: songs/*.json yazildi.")


if __name__ == "__main__":
    main()
