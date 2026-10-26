import datetime as dt, json, os, re, time, calendar, xml.etree.ElementTree as ET, requests
try:
    import feedparser
except ImportError:
    feedparser = None
import config as S, youtube
import publishing_state
UA = {"User-Agent": "Mozilla/5.0 (compatible; SerialRecapBot/1.0)"}
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

def _state(key): return os.path.join(S.BASE, "state", f"{key}.json")
def _status_file(key): return os.path.join(S.BASE, "state", f"{key}_status.json")

def done_ids(key):
    try: return set(json.load(open(_state(key))))
    except Exception: return set()

def is_done(key, vid, episode_no=None):
    """Check if either this video ID or this episode number has already been processed for this show."""
    done = done_ids(key)
    if vid and vid in done:
        return True
    if episode_no and str(episode_no).strip() and f"ep:{str(episode_no).strip()}" in done:
        return True
    return False

def mark_done(key, vid, episode_no=None, target_ep=None, yt_id=None):
    """Record both video_id and episode_no so re-uploads or retries never process the same episode twice."""
    os.makedirs(os.path.join(S.BASE, "state"), exist_ok=True)
    tokens = {vid} if vid else set()
    if episode_no and str(episode_no).strip() and str(episode_no).strip() != "?":
        tokens.add(f"ep:{str(episode_no).strip()}")
    if target_ep and str(target_ep).strip() and str(target_ep).strip() != "?":
        tokens.add(f"target_ep:{str(target_ep).strip()}")
    ids = sorted(done_ids(key) | tokens)
    json.dump(ids[-120:], open(_state(key), "w"), indent=2)
    record_status(key, "success", vid=vid, episode=episode_no, target_ep=target_ep, yt_id=yt_id)

def record_status(key, status, vid="", episode="", target_ep="", yt_id="", error="", log_tail=""):
    """Write structured execution status & log summary for watchdog inspection and auto-rerun decisions."""
    os.makedirs(os.path.join(S.BASE, "state"), exist_ok=True)
    now_ist = dt.datetime.now(IST)
    payload = {
        "show": key,
        "status": status,  # "running" | "success" | "failed" | "no_new_episode" | "already_uploaded"
        "date_ist": now_ist.strftime("%Y-%m-%d"),
        "timestamp_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "source_video_id": vid or "",
        "source_episode": str(episode or ""),
        "target_episode": str(target_ep or ""),
        "youtube_video_id": yt_id or "",
        "error": str(error or "")[:500],
        "log_tail": str(log_tail or "")[-1500:],
    }
    try:
        json.dump(payload, open(_status_file(key), "w"), ensure_ascii=False, indent=2)
    except Exception as e:
        print("   status write warning:", e)

def get_status(key):
    try: return json.load(open(_status_file(key)))
    except Exception: return {}

def recent_uploads(max_pages=3):
    """Latest uploads of the channel.
    1. Uses RSS feed first or YouTube Data API (via YT_API_KEY or existing YouTube OAuth token—no extra key needed!).
    2. Combines API + RSS results so even if Dangal TV posts Shorts/promos after 7-10:30 PM, no full episode is missed."""
    out, seen_ids = [], set()
    # 1. Fast RSS feed check (always 100% free, zero quota, works immediately for evening uploads)
    try:
        raw_xml = requests.get(S.FEED, headers=UA, timeout=30).content
        if feedparser is not None:
            parsed = feedparser.parse(raw_xml)
            for e in parsed.entries:
                vid = e.get("yt_videoid") or e.link.split("v=")[-1]
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    out.append({"id": vid, "title": e.title, "published": e.get("published", "")})
        else:
            root = ET.fromstring(raw_xml)
            ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
            for entry in root.findall("atom:entry", ns):
                vid_el = entry.find("yt:videoId", ns)
                tit_el = entry.find("atom:title", ns)
                pub_el = entry.find("atom:published", ns)
                if vid_el is not None and tit_el is not None and vid_el.text not in seen_ids:
                    seen_ids.add(vid_el.text)
                    out.append({"id": vid_el.text or "", "title": tit_el.text or "", "published": pub_el.text if pub_el is not None else ""})
        print(f"   uploads via RSS: {len(out)}")
    except Exception as e:
        print("   RSS warning:", str(e)[:100])

    # 2. Also query playlistItems via OAuth token (or YT_API_KEY if set) so Shorts/promos never push episodes out of the 15-item RSS window
    key = os.getenv("YT_API_KEY")
    hdr = {}
    if not key and youtube.enabled():
        try:
            hdr = {"Authorization": f"Bearer {youtube._token()}"}
        except Exception:
            hdr = {}
    if key or hdr:
        try:
            page_token = None
            for _ in range(max_pages):
                params = {"part": "snippet", "playlistId": S.UPLOADS_PLAYLIST, "maxResults": 50}
                if key:
                    params["key"] = key
                if page_token:
                    params["pageToken"] = page_token
                r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems", headers=hdr, params=params, timeout=30).json()
                for it in r.get("items", []):
                    sn = it.get("snippet", {})
                    vid = sn.get("resourceId", {}).get("videoId")
                    if vid and vid not in seen_ids:
                        seen_ids.add(vid)
                        out.append({"id": vid, "title": sn.get("title", ""), "published": sn.get("publishedAt", "")})
                page_token = r.get("nextPageToken")
                if not page_token:
                    break
            print(f"   total uploads scanned (RSS + OAuth/API): {len(out)}")
        except Exception as e:
            print("   API/OAuth scan warning:", str(e)[:100])
    return out

def _norm(s):
    return re.sub(r"[^a-z0-9\u0900-\u097F]+", " ", (s or "").lower()).strip()

def _matches_show(title, key):
    show = S.SHOWS[key]
    t = _norm(title)
    if key == "mannsundar" and ("atisundar" in t or "ati sundar" in t):
        return False
    candidates = [show["match"]] + list(show.get("alt", []))
    return any(_norm(c) in t for c in candidates if c)

def _is_full_episode(title):
    t = (title or "").lower()
    return "full episode" in t or "full ep" in t or "पूरा एपिसोड" in t

def parse_title(title):
    ep_m = (re.search(r"(?:Episode|Ep\.?|एपिसोड)\s*[:#\-]?\s*(\d+)", title or "", re.I) or
            re.search(r"#\s*(\d{2,4})\b", title or ""))
    dt_m = re.search(r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", title or "")
    return (ep_m.group(1) if ep_m else ""), (dt_m.group(1) if dt_m else "")

def find_episode(key, max_age_h=96):
    now = time.time()
    for v in recent_uploads():
        t = v["title"]
        if not _matches_show(t, key) or not _is_full_episode(t):
            continue
        ep, date_ = parse_title(t)
        if not ep:
            continue
        # Check both video_id and episode number so the same episode is never processed twice
        if is_done(key, v["id"], ep):
            continue
        try:
            pub = dt.datetime.fromisoformat(v["published"].replace("Z", "+00:00")).timestamp()
            if now - pub > max_age_h * 3600:
                continue
        except Exception:
            pass
        return {"id": v["id"], "url": f"https://www.youtube.com/watch?v={v['id']}", "title": t, "episode": ep, "date": date_}
    return None

def by_id(video_id):
    """Manual override: build the episode record from a video id using YouTube's oEmbed (title only)."""
    title = requests.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
                         headers=UA, timeout=20).json().get("title", "")
    ep, date_ = parse_title(title)
    return {"id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}", "title": title, "episode": ep, "date": date_}

def find_pending_social(key):
    """Return the newest episode whose enabled social destination had a definite failed attempt."""
    platforms = []
    if S.FACEBOOK_ENABLED: platforms.append("facebook")
    if S.INSTAGRAM_ENABLED: platforms.append("instagram")
    if not platforms:
        return None
    video_id = publishing_state.failed_platforms(key, platforms)
    if not video_id:
        return None
    try:
        episode = by_id(video_id)
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"   could not reload pending social episode {video_id}: {type(exc).__name__}: {str(exc)[:120]}")
        return None
    if not _matches_show(episode["title"], key):
        print(f"   pending social state video does not match {key}; ignoring {video_id}")
        return None
    return episode

def show_key_for(title):
    for k in S.SHOWS:
        if _matches_show(title, k):
            return k
    return None
