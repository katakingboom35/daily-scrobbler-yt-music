import os
import time

import pylast
import ytmusicapi
from ytmusicapi.parsers.playlists import parse_playlist_items


API_KEY = os.getenv("LASTFM_API_KEY")
API_SECRET = os.getenv("LASTFM_API_SECRET")
username = os.getenv("LASTFM_USERNAME")
password_hash = pylast.md5(os.getenv("LASTFM_PASSWORD"))


def to_scrobble(entry: dict) -> dict:
    artists_data = entry.get("artists") or []
    artists = ", ".join(a.get("name", "") for a in artists_data if a.get("name"))
    primary_artist = artists_data[0].get("name", "") if artists_data else artists

    album = (entry.get("album") or {}).get("name", "")
    duration_seconds = entry.get("duration_seconds", 180)

    return {
        "artist": artists,
        "title": entry["title"],
        "timestamp": int(time.time()),
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
    return songs


def scrobble_tracks(network, tracks):
    if not tracks:
        print("0 tracks to scrobble")
        return

    network.scrobble_many(tracks)
    print(f"Scrobbled {len(tracks)} tracks to Last.fm")


def main():
    browser_json_path = "browser.json"
    browser_json_raw = os.getenv("BROWSER_JSON")

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

    history = [
        entry for entry in history
        if entry.get("played") == "Yesterday"
    ]

    scrobbles = [to_scrobble(entry) for entry in history]

    print(f"{len(scrobbles)} tracks to scrobble")
    scrobble_tracks(lastfm, scrobbles)


if __name__ == "__main__":
    main()
