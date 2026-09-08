import json
import os
import re
import time
from collections import Counter

import pylast
import ytmusicapi
from ytmusicapi.parsers.playlists import parse_playlist_items


class BrowserSessionYTMusic(ytmusicapi.YTMusic):
    @property
    def headers(self):
        # Keep the fresh Authorization header copied from the browser.
        # Recent YouTube sessions use a newer signed header format than
        # ytmusicapi 1.12.1 regenerates internally.
        return self.base_headers


API_KEY = os.getenv("LASTFM_API_KEY")
API_SECRET = os.getenv("LASTFM_API_SECRET")
username = os.getenv("LASTFM_USERNAME")
password_hash = pylast.md5(os.getenv("LASTFM_PASSWORD"))


def to_scrobble(entry: dict, timestamp: int) -> dict | None:
    title = entry.get("title")
    artists_data = entry.get("artists") or []
    artists = ", ".join(a.get("name", "") for a in artists_data if a.get("name"))

    if not title or not artists:
        return None

    primary_artist = artists_data[0].get("name", "") if artists_data else artists
    album = (entry.get("album") or {}).get("name", "")
    duration_seconds = entry.get("duration_seconds", 180)

    return {
        "artist": artists,
        "title": title,
        "timestamp": timestamp,
        "album": album,
        "duration": duration_seconds,
        "album_artist": primary_artist,
    }


def get_history_safe(ytmusic):
    response = ytmusic._send_request(
        "browse",
        {"browseId": "FEmusic_history"}
    )

    songs = []

    def walk(obj):
        if isinstance(obj, dict):
            shelf = obj.get("musicShelfRenderer")

            if shelf:
                contents = shelf.get("contents") or []

                if contents:
                    try:
                        parsed = parse_playlist_items(contents)
                    except Exception as e:
                        print(f"Skipping unparseable history shelf: {e}")
                        parsed = []

                    title = shelf.get("title") or {}
                    played = ""

                    if title.get("runs"):
                        played = title["runs"][0].get("text", "")
                    elif title.get("simpleText"):
                        played = title["simpleText"]

                    for song in parsed:
                        song["played"] = played

                    songs.extend(parsed)

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(response)

    if not songs:
        renderer_counts = Counter()

        def inspect(obj):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key.endswith("Renderer") or key.endswith("ViewModel"):
                        renderer_counts[key] += 1
                    inspect(value)
            elif isinstance(obj, list):
                for value in obj:
                    inspect(value)

        inspect(response)
        print("Renderer types in history response:")
        for key, count in renderer_counts.most_common(25):
            print(f"  {key}: {count}")

        messages = []

        def collect_messages(obj):
            if isinstance(obj, dict):
                renderer = obj.get("messageRenderer")
                if renderer:
                    for field in ("text", "subtext"):
                        value = renderer.get(field)
                        if isinstance(value, dict):
                            if "simpleText" in value:
                                messages.append(value["simpleText"])
                            elif "runs" in value:
                                messages.append("".join(run.get("text", "") for run in value["runs"]))
                for value in obj.values():
                    collect_messages(value)
            elif isinstance(obj, list):
                for value in obj:
                    collect_messages(value)

        collect_messages(response)
        for message in messages:
            if message:
                print(f"History API message: {message[:250]}")

    return songs


def build_scrobbles(history, import_all=False):
    if import_all:
        selected = history
        print(f"One-time import mode: {len(selected)} history tracks found")
    else:
        selected = [
            entry for entry in history
            if entry.get("played") == "Yesterday"
        ]
        print(f"Daily mode: {len(selected)} tracks marked Yesterday")

    # Last.fm requires timestamps. YouTube Music history does not expose exact
    # play times here, so preserve the visible history order with unique,
    # synthetic recent timestamps (4 minutes apart).
    now = int(time.time())
    scrobbles = []

    for index, entry in enumerate(selected):
        timestamp = now - ((index + 1) * 240)
        scrobble = to_scrobble(entry, timestamp)
        if scrobble:
            scrobbles.append(scrobble)

    return scrobbles


def scrobble_tracks(network, tracks):
    if not tracks:
        print("0 tracks to scrobble")
        return

    network.scrobble_many(tracks)
    print(f"Scrobbled {len(tracks)} tracks to Last.fm")


def parse_browser_headers(raw: str) -> dict:
    raw = (raw or "").strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    headers = {}

    for name, value in re.findall(r'"([^"]+)"\s*=\s*"([^"]*)"', raw):
        headers[name] = value

    cookie_pairs = re.findall(
        r'System\.Net\.Cookie\("([^"]+)",\s*"([^"]*)",\s*"/",\s*"music\.youtube\.com"\)',
        raw,
    )
    if cookie_pairs:
        headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookie_pairs)

    user_agent = re.search(r'-UserAgent\s+"([^"]+)"', raw)
    if user_agent:
        headers["User-Agent"] = user_agent.group(1)

    if not headers.get("Authorization") or not headers.get("Cookie"):
        raise ValueError(
            "BROWSER_JSON must be JSON or the full Firefox 'Copy as PowerShell' request"
        )

    return headers


def main():
    browser_json_path = "browser.json"
    browser_json_raw = os.getenv("BROWSER_JSON")
    import_all = os.getenv("IMPORT_ALL_HISTORY", "0") == "1"

    browser_headers = parse_browser_headers(browser_json_raw or "")
    browser_headers.pop("Content-Encoding", None)
    browser_headers.pop("content-encoding", None)
    browser_headers.setdefault("x-youtube-bootstrap-logged-in", "true")
    browser_headers.setdefault("referer", "https://music.youtube.com/")
    browser_headers.setdefault("x-origin", "https://music.youtube.com")

    with open(browser_json_path, "w") as f:
        json.dump(browser_headers, f)

    auth_candidates = []
    configured_authuser = str(browser_headers.get("X-Goog-AuthUser", browser_headers.get("x-goog-authuser", "0")))
    for candidate in [configured_authuser, "0", "1", "2", "3"]:
        if candidate not in auth_candidates:
            auth_candidates.append(candidate)

    history = []
    selected_authuser = None

    for candidate in auth_candidates:
        browser_headers["X-Goog-AuthUser"] = candidate
        browser_headers["x-goog-authuser"] = candidate

        with open(browser_json_path, "w") as f:
            json.dump(browser_headers, f)

        ytmusic = BrowserSessionYTMusic(browser_json_path, language="en")
        candidate_history = get_history_safe(ytmusic)
        print(f"Auth user {candidate}: {len(candidate_history)} history tracks")

        if candidate_history:
            history = candidate_history
            selected_authuser = candidate
            break

    if selected_authuser is not None:
        print(f"Using X-Goog-AuthUser={selected_authuser}")

    lastfm = pylast.LastFMNetwork(
        api_key=API_KEY,
        api_secret=API_SECRET,
        username=username,
        password_hash=password_hash,
    )

    print(f"Total parsed YouTube Music history tracks: {len(history)}")

    scrobbles = build_scrobbles(history, import_all=import_all)
    print(f"{len(scrobbles)} tracks to scrobble")
    scrobble_tracks(lastfm, scrobbles)


if __name__ == "__main__":
    main()
