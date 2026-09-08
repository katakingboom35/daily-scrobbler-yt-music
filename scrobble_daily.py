import os
import time
from collections import Counter

import pylast
import ytmusicapi
from ytmusicapi.parsers.playlists import parse_playlist_items


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


def main():
    browser_json_path = "browser.json"
    browser_json_raw = os.getenv("BROWSER_JSON")
    import_all = os.getenv("IMPORT_ALL_HISTORY", "0") == "1"

    with open(browser_json_path, "w") as f:
        f.write(browser_json_raw or "{}")

    ytmusic = ytmusicapi.YTMusic(browser_json_path, language="en")

    lastfm = pylast.LastFMNetwork(
        api_key=API_KEY,
        api_secret=API_SECRET,
        username=username,
        password_hash=password_hash,
    )

    history = get_history_safe(ytmusic)
    print(f"Total parsed YouTube Music history tracks: {len(history)}")

    scrobbles = build_scrobbles(history, import_all=import_all)
    print(f"{len(scrobbles)} tracks to scrobble")
    scrobble_tracks(lastfm, scrobbles)


if __name__ == "__main__":
    main()
