import datetime as dt
import re
import config as S

def hashtag(s): return "#" + re.sub(r"[^\w]", "", s)

def _next(ep):
    return str(int(ep) + 1) if str(ep).isdigit() else ""

def make_title(show, ep, date_, headline, source_ep=None, source_date=None):
    """Target date/episode two days ahead; source episode remains clearly identified as recap context."""
    short = " ".join(date_.split()[:2]) if date_ else ""
    source_ep = source_ep or str(max(1, int(ep)-2)) if str(ep).isdigit() else (source_ep or "?")
    base = f"{show['en']} Ep {ep} ({short}) | परसों की कहानी + अनुमान"
    if source_ep and source_ep != ep: base += f" | स्रोत Ep {source_ep} रिकैप"
    t = f"{base} | {headline}" if headline else base
    return t if len(t) <= 98 else base[:98]

def make_tags(show, ep, date_, kws, limit=480):
    en, hi = show["en"], show["hi"]
    cand = [en, hi, f"{en} {date_}", f"{en} episode {ep}", f"{en} full episode {ep}", f"{en} today episode", f"{en} episode summary",
            f"{en} story in hindi", f"{en} next episode", f"{en} kal ka episode", f"{en} update", f"{hi} आज का एपिसोड", f"{hi} कहानी",
            f"{hi} एपिसोड {ep}"] + ([f"{en} episode {ep} prediction", f"{hi} एपिसोड {ep} अनुमान"] if _next(ep) else []) + show["alt"] + list(kws) + S.BASE_TAGS
    out, seen, total = [], set(), 0
    for t in cand:
        t = re.sub(r"[<>\"]", "", str(t)).strip(); k = t.lower()
        if not t or k in seen: continue
        cost = len(t) + 1 + (2 if " " in t else 0)
        if total + cost > limit: continue
        seen.add(k); out.append(t); total += cost
    return out

def mmss(s): return f"{int(s) // 60:02d}:{int(s) % 60:02d}"

def chapters(starts):
    """starts: [(seconds, title)]. YouTube needs a 00:00 start and >=10 s between chapters."""
    out, last = [], -99
    for t, name in starts:
        if not out or t - last >= 10: out.append((t, name)); last = t
    return "\n".join(f"{mmss(t)} {n}" for t, n in out) if len(out) >= 3 else ""

def _search_date(date_):
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(date_, fmt).date()
        except ValueError:
            pass
    return None

def _format_search_date(date_):
    return date_.strftime("%d %b %Y") if date_ else ""

def description_search_terms(show, ep, date_, source_ep, source_date):
    en, hi = show["en"], show["hi"]
    source_day = _search_date(source_date)
    target_day = _search_date(date_)
    if source_day:
        days = {1: source_day + dt.timedelta(days=1), 2: source_day + dt.timedelta(days=2)}
    elif target_day:
        days = {1: target_day - dt.timedelta(days=1), 2: target_day}
    else:
        days = {}

    source_number = int(source_ep) if str(source_ep).isdigit() else None
    target_number = int(ep) if str(ep).isdigit() else None
    if source_number is not None:
        episodes = {1: source_number + 1, 2: source_number + 2}
    elif target_number is not None:
        episodes = {1: target_number - 1, 2: target_number}
    else:
        episodes = {}

    groups = {}
    for offset in (1, 2):
        day = _format_search_date(days.get(offset))
        episode = episodes.get(offset)
        if not day or episode is None:
            groups[offset] = []
            continue
        short_day = days[offset].strftime("%d %B")
        full_day = days[offset].strftime("%d %B %Y")
        groups[offset] = [
            f"{en} {day} episode {episode}",
            f"{en} {episode} episode",
            f"{en} serial episode {episode}",
            f"{en} today episode {episode}",
            f"{en} {short_day}",
            f"{en} episode {episode} full episode",
            f"{en} serial {episode} episode",
            f"{en} today episode {short_day}",
            f"{en} {short_day} full episode",
            f"{en} {short_day} episode",
            f"{en} {short_day} ka",
            f"{en} natak {episode} episode",
            f"{en} natak episode {episode}",
            f"{en} new show full episode {episode}",
            f"{en} serial {episode}",
            f"{en} today full episode {short_day}",
            f"Drama {en} {short_day}",
            f"{en} aaj ka episode {episode}",
            f"{en} episode {episode} full",
            f"{en} full episode {episode}",
            f"{en} full episode {short_day}",
            f"{en} ka {episode} episode",
            f"{en} ka {episode} bhag",
            f"{en} ka {short_day} ka episode",
            f"{short_day} full episode {en}",
            f"{short_day} ka {en} episode",
            f"{full_day} {en}",
            f"{short_day} {en} drama",
            f"{short_day} {en} episode",
            f"{en} natak {episode} bhag",
            f"{en} today episodes",
            f"{en} today full episode {episode}",
            f"{en} full episode {episode} {day}",
            f"{en} episode {episode} recap",
            f"{en} episode {episode} prediction",
            f"{hi} {day} एपिसोड {episode}",
            f"{hi} पूरा एपिसोड {episode}",
            f"{hi} एपिसोड {episode} अनुमान",
            f"{hi} {short_day} का एपिसोड {episode}",
        ]
        terms = list(dict.fromkeys(groups[offset]))
        groups[offset] = terms[:26] + terms[-5:]
    general = [
        en,
        hi,
        f"{en} serial",
        f"{en} aaj ka episode",
        f"{en} serial full episode",
        f"{en} natak",
        f"{en} drama",
        "Hindi serial",
        "family drama serial",
        "Dangal TV serial",
    ]
    groups["general"] = list(dict.fromkeys(general))
    return groups

def make_description(show, ep, date_, ep_url, hook, starts, prediction, kws, tags, source_ep=None, source_date=None):
    source_ep = source_ep or (str(max(1, int(ep)-2)) if str(ep).isdigit() else "?")
    search_terms = description_search_terms(show, ep, date_, source_ep, source_date)
    source_date = source_date or date_
    hashtags = [hashtag(show["en"]), hashtag(show["match"]), "#DangalTV", "#DangalOriginals",
                "#HindiSerial", "#SerialRecap", "#TodayEpisode"]
    d = f"{show['en']} | {source_date} | Episode {source_ep} Recap | Dangal TV\n"
    d += f"{show['en']} | {show['hi']} | Episode {ep} Story + Prediction | {date_}\n"
    d += f"\n{show['hi']} के आधिकारिक एपिसोड {source_ep} पर आधारित रिकैप।\n"
    d += " ".join(dict.fromkeys(hashtags)) + "\n"
    d += "खोज-शब्द / Search terms:\n"
    d += "General: " + ", ".join(search_terms["general"]) + "\n"
    d += "+1 day / episode: " + ", ".join(search_terms[1]) + "\n"
    d += "+2 days / episode: " + ", ".join(search_terms[2]) + "\n\n"
    d += f"{hook}\n\n📺 {show['en']} ({show['hi']}) | लक्ष्य: {date_} | अगले एपिसोड {ep} का अनुमान।\n"
    d += f"यह रिकैप {source_date or 'दो दिन पहले'} के एपिसोड {source_ep} पर आधारित है; आगे की बातें अनुमान हैं, पुष्टि किए हुए स्पॉइलर नहीं।\n"
    ch = chapters(starts)
    if ch: d += f"\nअध्याय:\n{ch}\n"
    d += f"\n⚠ एपिसोड {ep} में क्या हो सकता है - सिर्फ़ अनुमान, असली एपिसोड नहीं:\n" + "\n".join(prediction) + "\n"
    d += f"\n▶ आधिकारिक पूरा एपिसोड यहाँ देखें: {ep_url}\n"
    d += ("\nडिस्क्लेमर: यह एक फ़ैन-निर्मित रिकैप है। शो के सभी अधिकार मूल निर्माताओं/चैनल के पास हैं। इसमें शो का कोई वीडियो, ऑडियो या संगीत इस्तेमाल नहीं हुआ। "
          "चित्र AI से बनाए गए हैं और किसी कलाकार की छवि नहीं हैं; आवाज़ AI की है।\n")
    return d[:4900]
