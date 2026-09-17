"""
KRAKEN -- FIR REGISTRY
======================
Intake console ka dimaag. Ek FIR (poora passage ya bhara hua form) yahan aata
hai aur yeh module:

  1. spaCy NER + regex se saari entities nikaalta hai (log, phone, account,
     jagah, gaadi, IPC section),
  2. case number issue karta hai aur case file disk par likhta hai,
  3. nikaali hui entities ko live graph mein merge karta hai -> profile aur
     network apne aap ban jaate hain,
  4. purani case files se cross-match karta hai -- agar koi pehle se known
     banda naye FIR mein aaya to us purane case ke liye "NEW CLUE" alert
     banata hai,
  5. evidence images aur accused photos sambhalta hai.

PDF banana fir_pdf.py ka kaam hai; yahan sirf usko call karte hain.
"""

import base64
import binascii
import io
import json
import os
import re
import threading
import uuid
from datetime import datetime

import kraken_data

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CASE_DIR = os.path.join(BASE_DIR, "case_files")
EVIDENCE_DIR = os.path.join(CASE_DIR, "evidence")
PHOTO_DIR = os.path.join(CASE_DIR, "photos")
PDF_DIR = os.path.join(CASE_DIR, "pdf")
CASES_PATH = os.path.join(CASE_DIR, "cases.json")

_lock = threading.RLock()
_nlp = None
_nlp_failed = False

# Ek image ki upper limit -- base64 payload browser se aata hai, isliye
# server ko bhi apni seema rakhni chahiye.
MAX_IMAGE_BYTES = 6 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


# ============================================================ extraction ====

# +91 optional, 10 digit Indian mobile. Word boundary se lamba account number
# galti se phone na ban jaye.
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")
# Masked account (dataset ka format) + "account no 123456789012" jaisa khula form.
ACCOUNT_MASKED_RE = re.compile(r"\bXXXX\d{4}\b", re.I)
ACCOUNT_PLAIN_RE = re.compile(
    r"(?:a/c|acc(?:ount)?)\.?\s*(?:no\.?|number|#)?\s*:?\s*(\d{9,18})(?!\d)", re.I
)
IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
FIR_NO_RE = re.compile(r"FIR\s*(?:No\.?|Number)?\s*:?\s*(\d+\s*/\s*\d{4})", re.I)
# Sub-section brackets bhi chahiye -- "Section 21(c), 29 NDPS" pehle sirf
# "21" deta tha kyunki "(c)" par pattern ruk jaata tha.
_SECTION_ATOM = r"\d+[A-Z]?(?:\([a-zA-Z0-9]+\))?"
IPC_RE = re.compile(
    r"(?:u/s|under\s+section|section|sec\.?)\s*:?\s*"
    r"((?:%s)(?:\s*[,/&]\s*(?:%s))*)" % (_SECTION_ATOM, _SECTION_ATOM),
    re.I,
)
LABELLED_PLACE_RE = re.compile(
    r"(?:police\s+station|p\.?s\.?|district|place\s+of\s+occurrence|"
    r"place\s+of\s+incident|scene\s+of\s+crime)\s*[:\-]\s*([^,.\n;]{2,48})",
    re.I,
)
VEHICLE_RE = re.compile(r"\b([A-Z]{2}[\s-]?\d{1,2}[\s-]?[A-Z]{1,3}[\s-]?\d{4})\b")
DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    re.I,
)

RELATIONSHIP_KEYWORDS = [
    "co-accused", "along with", "in association with", "accomplice",
    "known associate of", "together with", "aided by", "conspired with",
    "absconding with", "partner of",
]

# "residing at <pata>, co-accused ..." -- is clause ke andar jo naam hai wo
# jagah hai, insaan nahi. (Yeh bug ner_extractor.py mein pehle pakda gaya tha.)
#
# Span ko kas ke baandhna zaroori hai: pehle yeh sirf relationship keyword ya
# "." par rukta tha, to "residing at 22 Station Road, Berhampore, states that
# Rajesh Kumar, along with ..." mein poora accused ka naam bhi pate ke andar
# aa jaata tha aur wo LOCATION ban jaata tha. Ab newline aur reporting verb
# par bhi rukta hai, aur 90 char se lamba pata nahi maanta.
ADDRESS_VERBS = ("states", "stated", "reports", "reported", "alleges",
                 "alleged", "complains", "complained", "informed", "deposed")
ADDRESS_SPAN_RE = re.compile(
    r"(?:residing at|r/o|resident of|address)\s+(.{0,90}?)"
    r"(?:,\s*(?:%s)\b|[.\n]|\s+(?:%s)\b)"
    % ("|".join(re.escape(k) for k in RELATIONSHIP_KEYWORDS),
       "|".join(ADDRESS_VERBS)),
    re.I | re.S,
)

# spaCy kabhi kabhi headers/ranks ko PERSON bol deta hai -- inhe chhaan do.
NON_PERSON_TOKENS = {
    "fir", "ipc", "crpc", "sho", "asi", "psi", "si", "dsp", "sp", "dy", "acp",
    "police", "station", "thana", "complainant", "accused", "victim", "witness",
    "sir", "madam", "shri", "smt", "kumari", "mr", "mrs", "ms", "dr",
    "kraken", "case", "report", "statement", "annexure", "exhibit",
    # FIR ki bhasha ke aam shabd -- spaCy inhe sentence ke shuru mein PERSON
    # tag kar deta hai ("Handset +9198... was recovered").
    "handset", "consignment", "vehicle", "account", "contact", "number",
    "funds", "fund", "advance", "advances", "ransom", "deposit", "deposits",
    "notes", "garage", "cash", "sim", "otp", "counterfeit", "verification",
    "processing", "payment", "payments", "amount", "sum", "money", "transfer",
    "mobile", "phone", "call", "calls", "statementof", "informant", "subject",
}


def _nlp_model():
    """spaCy ko pehli baar maangne par hi load karo -- app start fast rahe."""
    global _nlp, _nlp_failed
    if _nlp is not None or _nlp_failed:
        return _nlp
    with _lock:
        if _nlp is None and not _nlp_failed:
            try:
                import spacy
                _nlp = spacy.load("en_core_web_sm")
            except Exception:
                # Model missing ho to regex-only mode mein chalte raho.
                _nlp_failed = True
                _nlp = None
    return _nlp


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" ,.;:'\"")


def _dedupe(values):
    """Order bachate hue case-insensitive duplicates hatao."""
    seen, out = set(), []
    for value in values:
        cleaned = _clean(value)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _looks_like_person(name):
    name = _clean(name)
    if len(name) < 3 or len(name) > 60:
        return False
    if any(ch.isdigit() for ch in name):
        return False
    words = [w for w in re.split(r"\s+", name) if w]
    if not words or len(words) > 4:
        return False
    # Har token stopword ho to insaan nahi.
    if all(w.lower().strip(".") in NON_PERSON_TOKENS for w in words):
        return False
    # Poora naam chhote ALL-CAPS tokens ka ho to wo acronym hai ("NDPS IPC"),
    # aadmi nahi. Asli naam yahan Title Case mein aate hain.
    if all(w.isupper() and len(w) <= 5 for w in words):
        return False
    return True


def _strip_titles(name):
    """"Shri Rajesh Kumar" -> "Rajesh Kumar"."""
    words = [w for w in re.split(r"\s+", _clean(name)) if w]
    while words and words[0].lower().strip(".") in NON_PERSON_TOKENS:
        words.pop(0)
    return " ".join(words)


def _address_spans(text):
    return [m.span(1) for m in ADDRESS_SPAN_RE.finditer(text)]


def _inside(span, spans):
    start, end = span
    return any(s <= start and end <= e for s, e in spans)


def extract_entities(text):
    """Free-text FIR -> structured entities. spaCy na ho to regex se kaam chalta hai."""
    text = text or ""
    persons, locations, orgs = [], [], []

    nlp = _nlp_model()
    if nlp is not None:
        doc = nlp(text)
        addr_spans = _address_spans(text)
        for ent in doc.ents:
            span = (ent.start_char, ent.end_char)
            if ent.label_ == "PERSON":
                # Pata ke andar ka naam jagah hai, aadmi nahi.
                if _inside(span, addr_spans):
                    locations.append(ent.text)
                    continue
                candidate = _strip_titles(ent.text)
                if _looks_like_person(candidate):
                    persons.append(candidate)
            elif ent.label_ in ("GPE", "LOC", "FAC"):
                locations.append(ent.text)
            elif ent.label_ == "ORG":
                # "WB 24 AB 1234" ka "WB" aur "IPC"/"FIR" jaise tokens ORG
                # ban jaate the -- wo entity nahi, formatting hai.
                token = _clean(ent.text)
                if token.lower() not in NON_PERSON_TOKENS and not (
                        len(token) <= 3 and token.isupper()):
                    orgs.append(token)

    locations.extend(m.group(1) for m in LABELLED_PLACE_RE.finditer(text))

    phones = [m.group(1) for m in PHONE_RE.finditer(text)]
    accounts = ACCOUNT_MASKED_RE.findall(text) + [
        m.group(1) for m in ACCOUNT_PLAIN_RE.finditer(text)
    ]

    fir_match = FIR_NO_RE.search(text)
    sections = []
    for match in IPC_RE.finditer(text):
        sections.extend(re.split(r"\s*[,/&]\s*", match.group(1)))

    persons = _drop_partial_names(_dedupe(persons))
    # "Parganas" aur "South 24 Parganas" dono aa jaate the -- tukda hata do.
    locations = _drop_partial_names(_dedupe(locations))

    # spaCy dono taraf galti karta hai: jagah ko PERSON ("Maheshtala" ek tower
    # hai) aur insaan ko GPE ("Oni Kannan" location list mein chala gaya tha,
    # aur wahan se person list se bhi kat gaya). Live graph ko sach maano, aur
    # "Police Station: X" jaise labelled fields ko pakki jagah maano.
    known_people = _known_people()
    labelled = {_clean(m.group(1)).lower() for m in LABELLED_PLACE_RE.finditer(text)}

    # asli log locations se bahar (jab tak wo labelled place na ho)
    locations = [q for q in locations
                 if q.lower() not in known_people or q.lower() in labelled]

    place_keys = {q.lower() for q in locations} | _known_places()
    # Jagahein persons se bahar. Precedence:
    #   1. Is document ka labelled field ("Police Station: Maheshtala") sabse
    #      upar -- source dataset mein kuch tower names galti se persons mein
    #      bhi hain, lekin yahan wo saaf taur par jagah hai.
    #   2. Warna graph ka confirmed insaan bacha rahe.
    persons = [q for q in persons
               if q.lower() not in labelled
               and (q.lower() not in place_keys or q.lower() in known_people)]

    return {
        "persons": persons,
        "locations": locations,
        "organisations": _dedupe(orgs),
        "phones": _dedupe(phones),
        "accounts": _dedupe(accounts),
        "vehicles": _dedupe(m.group(1) for m in VEHICLE_RE.finditer(text)),
        "ifsc": _dedupe(IFSC_RE.findall(text)),
        "dates": _dedupe(m.group(1) for m in DATE_RE.finditer(text)),
        "sections": _dedupe(sections),
        "fir_number": _clean(fir_match.group(1)) if fir_match else None,
        "relationships": _relationships(text, persons),
        "ownership": _ownership(text, persons),
    }


def _known_people():
    """Live graph ke confirmed person names, lowercase."""
    try:
        return {str(n).lower() for n in kraken_data.graph().persons}
    except Exception:
        return set()


def _known_places():
    """Live graph ki jagahein (towers/addresses), lowercase."""
    try:
        return {str(p).lower() for p in kraken_data.graph().locations}
    except Exception:
        return set()


def _drop_partial_names(persons):
    """
    spaCy ek hi aadmi ko do baar de deta hai -- "Suresh Mehta" aur akela
    "Mehta". Agar koi single-token naam kisi lambe naam ka hissa hai to usko
    hata do, warna graph mein do alag nodes ban jaate hain.
    """
    out = []
    for name in persons:
        tokens = name.split()
        if len(tokens) == 1:
            token = tokens[0].lower()
            longer = any(
                other is not name and token in [t.lower() for t in other.split()]
                for other in persons
            )
            if longer:
                continue
        out.append(name)
    return out


def _person_positions(text, persons):
    """Har naam ke saare character offsets -- relationship pairing ke liye."""
    spots = []
    for person in persons:
        for match in re.finditer(re.escape(person), text, re.I):
            spots.append((match.start(), match.end(), person))
    spots.sort()
    return spots


def _relationships(text, persons):
    """
    "A ... co-accused ... B" type links.

    Pehle yeh har consecutive jodi ko link kar deta tha, isliye complainant bhi
    accused ke saath jud jaata tha. Ab keyword ke aas-paas ka naam hi uthate
    hain: keyword se just pehle wala aur just baad wala.
    """
    out = []
    if len(persons) < 2:
        return out

    spots = _person_positions(text, persons)
    if len(spots) < 2:
        return out

    seen = set()
    for keyword in RELATIONSHIP_KEYWORDS:
        for match in re.finditer(re.escape(keyword), text, re.I):
            before = [s for s in spots if s[1] <= match.start()]
            after = [s for s in spots if s[0] >= match.end()]
            if not before or not after:
                continue
            left, right = before[-1][2], after[0][2]
            if left.lower() == right.lower():
                continue
            key = tuple(sorted((left.lower(), right.lower())))
            if key in seen:
                continue
            seen.add(key)
            out.append({"person_a": left, "person_b": right, "type": keyword})
    return out


def _ownership(text, persons):
    """Kis aadmi ke saath kaunsa phone/account likha hai."""
    out = []
    for person in persons:
        escaped = re.escape(person)
        phone = re.search(
            escaped + r"[^.]{0,120}?(?:contact|mobile|phone|no\.?)\s*:?\s*"
            r"(?:\+?91[\s-]?)?([6-9]\d{9})", text, re.I | re.S)
        if phone:
            out.append({"type": "OWNS_PHONE", "person": person, "value": phone.group(1)})
        account = re.search(
            escaped + r"[^.]{0,120}?(?:a/c|acc(?:ount)?)\.?\s*(?:no\.?)?\s*:?\s*"
            r"(XXXX\d{4}|\d{9,18})", text, re.I | re.S)
        if account:
            out.append({"type": "OWNS_ACCOUNT", "person": person, "value": account.group(1)})
    return out


def compose_narrative(form):
    """
    Form mode: bhare hue fields se ek padhne laayak FIR passage banao, taaki
    dono modes ek hi extraction pipeline se guzrein aur PDF bhi ek jaisa dikhe.
    """
    form = form or {}
    get = lambda k: _clean(form.get(k))
    lines = []

    if get("fir_number"):
        lines.append("FIR No. %s" % get("fir_number"))
    header = []
    if get("police_station"):
        header.append("Police Station: %s" % get("police_station"))
    if get("district"):
        header.append("District: %s" % get("district"))
    if get("incident_date"):
        header.append("Date of Offence: %s" % get("incident_date"))
    if header:
        lines.append(", ".join(header) + ".")

    if get("complainant"):
        who = "Complainant: %s" % get("complainant")
        if get("complainant_phone"):
            who += ", contact %s" % get("complainant_phone")
        if get("complainant_address"):
            who += ", residing at %s" % get("complainant_address")
        lines.append(who + ".")

    accused = [a for a in re.split(r"[,\n;]+", form.get("accused") or "") if _clean(a)]
    if accused:
        joined = ", co-accused ".join(_clean(a) for a in accused)
        lines.append("Accused: %s." % joined)

    if get("offence"):
        section = " under Section %s IPC" % get("sections") if get("sections") else ""
        lines.append("Nature of offence: %s%s." % (get("offence"), section))
    elif get("sections"):
        lines.append("Registered under Section %s IPC." % get("sections"))

    if get("location"):
        lines.append("Place of occurrence: %s." % get("location"))

    for label, key in (("Phones", "phones"), ("Accounts", "accounts"), ("Vehicles", "vehicles")):
        values = [v for v in re.split(r"[,\s\n;]+", form.get(key) or "") if _clean(v)]
        if values:
            lines.append("%s involved: %s." % (label, ", ".join(_clean(v) for v in values)))

    if get("narrative"):
        lines.append("")
        lines.append(get("narrative"))

    return "\n".join(lines).strip()


# ========================================================= case file store ==

def _ensure_dirs():
    for path in (CASE_DIR, EVIDENCE_DIR, PHOTO_DIR, PDF_DIR):
        os.makedirs(path, exist_ok=True)


def _blank_db():
    return {"version": 1, "sequence": 0, "cases": [], "notifications": [], "photos": {}}


def _load_db():
    """Disk se case database. Corrupt/missing file par khaali DB."""
    if not os.path.exists(CASES_PATH):
        return _blank_db()
    try:
        with open(CASES_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return _blank_db()
    if not isinstance(data, dict):
        return _blank_db()
    base = _blank_db()
    base.update(data)
    # Purani file mein koi key missing ho to default rakh do.
    for key, default in (("cases", []), ("notifications", []), ("photos", {})):
        if not isinstance(base.get(key), type(default)):
            base[key] = default
    return base


def _write_db(db):
    """Atomic write -- aadha likha hua file kabhi nahi bachega."""
    _ensure_dirs()
    temporary = CASES_PATH + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(db, handle, indent=2)
        os.replace(temporary, CASES_PATH)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass


def _next_case_no(db):
    db["sequence"] = int(db.get("sequence") or 0) + 1
    return "KRK-%s-%04d" % (datetime.now().year, db["sequence"])


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ============================================================== evidence ====

def _decode_image(payload):
    """
    Browser se aaya {name, type, data:<base64 ya data-URL>} -> raw bytes.
    Bad input par ValueError -- caller usko 400 bana deta hai.
    """
    raw = (payload or {}).get("data") or ""
    if raw.startswith("data:"):
        head, _, raw = raw.partition(",")
        mime = head[5:].split(";")[0].strip().lower()
    else:
        mime = _clean((payload or {}).get("type")).lower()

    mime = mime or "image/png"
    if mime not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Unsupported image type: %s" % mime)

    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Image payload is not valid base64.")
    if not blob:
        raise ValueError("Image payload is empty.")
    if len(blob) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds %d MB limit." % (MAX_IMAGE_BYTES // (1024 * 1024)))
    return blob, mime


def _save_image(directory, blob, mime, stem):
    _ensure_dirs()
    name = "%s%s" % (stem, ALLOWED_IMAGE_TYPES[mime])
    path = os.path.join(directory, name)
    with open(path, "wb") as handle:
        handle.write(blob)
    return name


def _store_evidence(case_no, images):
    """Evidence list ko disk par likho; har entry ka metadata wapas do."""
    stored = []
    for index, item in enumerate(images or [], start=1):
        blob, mime = _decode_image(item)
        stem = "%s-E%02d-%s" % (case_no, index, uuid.uuid4().hex[:8])
        filename = _save_image(EVIDENCE_DIR, blob, mime, stem)
        stored.append({
            "id": stem,
            "file": filename,
            "mime": mime,
            "bytes": len(blob),
            "caption": _clean(item.get("caption")) or _clean(item.get("name")) or "Evidence %d" % index,
        })
    return stored


def set_person_photo(name, image):
    """Accused/profile photo save karo. Wapas public URL deta hai."""
    person = _clean(name)
    if not person:
        raise ValueError("Person name is required.")
    blob, mime = _decode_image(image)
    stem = "P-%s" % re.sub(r"[^A-Za-z0-9]+", "_", person).strip("_").lower()[:48]
    filename = _save_image(PHOTO_DIR, blob, mime, stem)
    with _lock:
        db = _load_db()
        db.setdefault("photos", {})[person.lower()] = {
            "person": person,
            "file": filename,
            "mime": mime,
            "updated_at": _now(),
        }
        _write_db(db)
    return {"person": person, "url": "/case-files/photos/%s" % filename}


def get_person_photo(name):
    key = _clean(name).lower()
    if not key:
        return None
    entry = _load_db().get("photos", {}).get(key)
    if not entry:
        return None
    return {"person": entry.get("person"), "url": "/case-files/photos/%s" % entry["file"]}


def all_photos():
    """{lowercased name: url} -- network graph ek hi call mein sab le leta hai."""
    return {
        key: "/case-files/photos/%s" % entry["file"]
        for key, entry in _load_db().get("photos", {}).items()
        if entry.get("file")
    }


# ========================================================= cross-matching ===

def _graph_hit(value):
    """Ye entity live graph mein pehle se hai kya?"""
    try:
        found, kind = kraken_data.find_entity(value)
    except Exception:
        return None, None
    return found, kind


def _find_matches(extracted, cases, skip_case_no=None):
    """
    Nayi FIR ki entities ko (a) live graph aur (b) purani case files se milao.
    Har match ek "clue" hai.
    """
    matches = []
    candidates = (
        [(p, "person") for p in extracted["persons"]]
        + [(p, "phone") for p in extracted["phones"]]
        + [(a, "bank") for a in extracted["accounts"]]
        + [(v, "vehicle") for v in extracted["vehicles"]]
    )

    for value, kind in candidates:
        prior_cases = []
        for case in cases:
            if skip_case_no and case.get("case_no") == skip_case_no:
                continue
            if _case_mentions(case, value):
                prior_cases.append({
                    "case_no": case.get("case_no"),
                    "fir_number": case.get("fir_number"),
                    "title": case.get("title"),
                })

        found, graph_kind = (None, None)
        if kind in ("person", "phone", "bank"):
            found, graph_kind = _graph_hit(value)

        if not prior_cases and not found:
            continue

        matches.append({
            "value": found or value,
            "kind": graph_kind or kind,
            "in_graph": bool(found),
            "prior_cases": prior_cases,
        })
    return matches


def _case_mentions(case, value):
    needle = _clean(value).lower()
    if not needle:
        return False
    extracted = case.get("extracted") or {}
    for key in ("persons", "phones", "accounts", "vehicles"):
        for item in extracted.get(key) or []:
            if _clean(item).lower() == needle:
                return True
    return False


def _build_notifications(case, matches):
    """Match list -> bell ke liye alert rows."""
    notes = []
    stamp = _now()
    new_fir = case.get("fir_number") or case.get("case_no")

    # Har purane case ke liye ek alert, uske saare naye clues ek saath.
    by_prior = {}
    for match in matches:
        for prior in match["prior_cases"]:
            key = prior["case_no"]
            by_prior.setdefault(key, {"case": prior, "values": []})
            by_prior[key]["values"].append(match["value"])

    for key, bundle in by_prior.items():
        values = _dedupe(bundle["values"])
        notes.append({
            "id": uuid.uuid4().hex,
            "created_at": stamp,
            "kind": "new_clue",
            "tone": "high",
            "case_no": key,
            "source_case": case["case_no"],
            "title": "%s — NEW CLUE" % key,
            "detail": "%s also named in %s (%s)."
                      % (", ".join(values[:3]) + (" +%d more" % (len(values) - 3) if len(values) > 3 else ""),
                         new_fir, case["case_no"]),
            "entities": values,
            "href": "/intake?case=%s" % key,
            "read": False,
        })

    # Graph mein pehle se maujood, lekin kisi registered case mein nahi ->
    # ye bhi clue hai (source dataset ka known subject).
    graph_only = [
        m["value"] for m in matches if m["in_graph"] and not m["prior_cases"]
    ]
    if graph_only:
        notes.append({
            "id": uuid.uuid4().hex,
            "created_at": stamp,
            "kind": "known_subject",
            "tone": "warn",
            "case_no": case["case_no"],
            "source_case": case["case_no"],
            "title": "%s — KNOWN SUBJECT" % case["case_no"],
            "detail": "%s already on file in the case database."
                      % (", ".join(graph_only[:3])
                         + (" +%d more" % (len(graph_only) - 3) if len(graph_only) > 3 else "")),
            "entities": graph_only,
            "href": "/profile?name=%s" % graph_only[0],
            "read": False,
        })

    return notes


# ============================================================== register ====

def _split_values(raw):
    """Comma / newline / semicolon se alag kiye hue saaf values."""
    return [v for v in (_clean(x) for x in re.split(r"[,\n;]+", str(raw or ""))) if v]


def _apply_form_fields(extracted, form):
    """Form ke explicit fields ko extraction ke upar merge karo (NER se pehle)."""
    form = form or {}

    people = []
    if _clean(form.get("complainant")):
        people.append(_clean(form["complainant"]))
    people.extend(_split_values(form.get("accused")))
    if people:
        # Explicit naam pehle; NER ke woh naam hatao jo inhi ka tukda hain.
        tokens = {t.lower() for name in people for t in name.split()}
        keep = [n for n in extracted["persons"]
                if not (len(n.split()) == 1 and n.lower() in tokens)]
        extracted["persons"] = _dedupe(people + keep)

    for field, key in (("phones", "phones"), ("complainant_phone", "phones"),
                       ("accounts", "accounts"), ("vehicles", "vehicles")):
        values = _split_values(form.get(field))
        if values:
            extracted[key] = _dedupe(values + extracted[key])

    sections = _split_values(form.get("sections"))
    if sections:
        extracted["sections"] = _dedupe(sections + extracted["sections"])
    place = _clean(form.get("location"))
    if place:
        extracted["locations"] = _dedupe([place] + extracted["locations"])
    if _clean(form.get("fir_number")):
        extracted["fir_number"] = _clean(form["fir_number"])

    # Naye naamon ke liye ownership/relationship dobara nikaalo
    extracted["relationships"] = _relationships(compose_narrative(form), extracted["persons"])
    extracted["ownership"] = _ownership(compose_narrative(form), extracted["persons"])


def extract_for(mode, narrative, form):
    """Preview aur register -- dono ke liye ek hi extraction path."""
    extracted = extract_entities(narrative)
    if (mode or "").lower() == "form":
        _apply_form_fields(extracted, form)
    return extracted


def register_fir(payload):
    """
    Intake ka main entry point.

    payload = {
      mode: "passage" | "form",
      narrative: str,          # passage mode
      form: {...},             # form mode
      officer: str,
      evidence: [{name, type, data, caption}]
    }
    """
    payload = payload or {}
    mode = (payload.get("mode") or "passage").lower()
    form = payload.get("form") or {}

    if mode == "form":
        narrative = compose_narrative(form)
    else:
        narrative = _clean_block(payload.get("narrative"))

    if not narrative or len(narrative) < 12:
        raise ValueError("FIR text is empty. Write the incident details or fill the form.")

    # Form mode mein officer ne naam khud type kiye hain -- unke liye NER par
    # bharosa karna galat hai ("Farid Khan" short narrative mein sirf "Khan"
    # ban ke aa raha tha). extract_for() explicit fields ko preference deta hai.
    extracted = extract_for(mode, narrative, form)

    if not (extracted["persons"] or extracted["phones"] or extracted["accounts"]):
        raise ValueError(
            "No persons, phone numbers or accounts could be extracted. "
            "Name at least one subject so a profile can be created."
        )

    with _lock:
        db = _load_db()
        case_no = _next_case_no(db)
        prior_cases = list(db.get("cases") or [])

        try:
            evidence = _store_evidence(case_no, payload.get("evidence"))
        except ValueError:
            db["sequence"] -= 1   # case number waste mat karo
            raise

        matches = _find_matches(extracted, prior_cases)

        case = {
            "case_no": case_no,
            "fir_number": extracted["fir_number"] or _clean(form.get("fir_number")) or None,
            "title": _case_title(extracted, form),
            "registered_at": _now(),
            "officer": _clean(payload.get("officer")) or "DUTY OFFICER",
            "mode": mode,
            "narrative": narrative,
            "form": {k: _clean(v) for k, v in form.items()} if mode == "form" else {},
            "extracted": extracted,
            "evidence": evidence,
            "matches": matches,
            "pdf": None,
        }

        notifications = _build_notifications(case, matches)
        db["cases"].append(case)
        db["notifications"] = (db.get("notifications") or []) + notifications
        _write_db(db)

    # Graph merge lock ke bahar -- kraken_data apna lock khud leta hai.
    merge = _merge_into_graph(extracted)

    # PDF banao; fail ho to case phir bhi registered rahe.
    pdf_name = None
    try:
        import fir_pdf
        pdf_name = fir_pdf.build_case_pdf(case, EVIDENCE_DIR, PDF_DIR)
    except Exception:
        pdf_name = None

    if pdf_name:
        with _lock:
            db = _load_db()
            for row in db["cases"]:
                if row["case_no"] == case_no:
                    row["pdf"] = pdf_name
                    break
            _write_db(db)
        case["pdf"] = pdf_name

    return {
        "case": case,
        "notifications": notifications,
        "merge": merge,
        "pdf_url": "/api/fir/%s/pdf" % case_no if pdf_name else None,
    }


def _clean_block(text):
    """Passage ka whitespace saaf karo par line breaks bachao."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def _case_title(extracted, form):
    offence = _clean((form or {}).get("offence"))
    if offence:
        return offence.upper()[:60]
    if extracted["sections"]:
        return "IPC %s" % "/".join(extracted["sections"][:3])
    if extracted["persons"]:
        return "SUBJECT: %s" % extracted["persons"][0].upper()
    return "UNCLASSIFIED INTAKE"


def _merge_into_graph(extracted):
    """
    Nikaali hui entities live graph mein daalo -> Network/Profile pages turant
    naye nodes dikhate hain. kraken_data.add_intake hi ek batch banata hai.
    """
    before = kraken_data.stats()
    kraken_data.add_intake(
        extracted["persons"], extracted["phones"], extracted["accounts"]
    )
    after = kraken_data.stats()
    return {
        "persons": after["persons"] - before["persons"],
        "phones": after["phones"] - before["phones"],
        "accounts": after["accounts"] - before["accounts"],
        "links": after["confirmed_links"] - before["confirmed_links"],
    }


# ================================================================ reads =====

def list_cases(limit=None):
    cases = list(reversed(_load_db().get("cases") or []))
    if limit:
        cases = cases[:limit]
    return [_case_summary(c) for c in cases]


def _case_summary(case):
    extracted = case.get("extracted") or {}
    return {
        "case_no": case.get("case_no"),
        "fir_number": case.get("fir_number"),
        "title": case.get("title"),
        "registered_at": case.get("registered_at"),
        "officer": case.get("officer"),
        "persons": extracted.get("persons") or [],
        "phones": extracted.get("phones") or [],
        "accounts": extracted.get("accounts") or [],
        "evidence_count": len(case.get("evidence") or []),
        "match_count": len(case.get("matches") or []),
        "pdf_url": "/api/fir/%s/pdf" % case["case_no"] if case.get("pdf") else None,
    }


def get_case(case_no):
    needle = _clean(case_no).lower()
    for case in _load_db().get("cases") or []:
        if (case.get("case_no") or "").lower() == needle:
            return case
    return None


def case_pdf_path(case_no):
    case = get_case(case_no)
    if not case or not case.get("pdf"):
        return None
    path = os.path.join(PDF_DIR, case["pdf"])
    return path if os.path.exists(path) else None


def cases_for_person(name):
    """Profile page: is bande ka naam kin cases mein aaya."""
    needle = _clean(name).lower()
    if not needle:
        return []
    out = []
    for case in _load_db().get("cases") or []:
        if _case_mentions(case, needle):
            out.append(_case_summary(case))
    return list(reversed(out))


def notifications(unread_only=False):
    rows = list(reversed(_load_db().get("notifications") or []))
    if unread_only:
        rows = [r for r in rows if not r.get("read")]
    return rows


def mark_read(ids=None):
    """ids=None -> sab padh liye."""
    wanted = set(ids or [])
    with _lock:
        db = _load_db()
        changed = 0
        for row in db.get("notifications") or []:
            if row.get("read"):
                continue
            if wanted and row.get("id") not in wanted:
                continue
            row["read"] = True
            changed += 1
        if changed:
            _write_db(db)
    return changed


def delete_case(case_no):
    """Case file hatao (graph merge wapas lena alag kaam hai)."""
    needle = _clean(case_no).lower()
    with _lock:
        db = _load_db()
        before = len(db.get("cases") or [])
        db["cases"] = [c for c in db.get("cases") or []
                       if (c.get("case_no") or "").lower() != needle]
        db["notifications"] = [n for n in db.get("notifications") or []
                               if (n.get("source_case") or "").lower() != needle]
        if len(db["cases"]) == before:
            return False
        _write_db(db)
    return True


# ============================================================== seeding =====
# "Previous FIRs" tab khaali na dikhe. Agar case database mein kuch nahi hai to
# live graph ke ASLI entities se purane FIR bana dete hain -- wahi log, wahi
# phone/account, wahi co-accused jodiyan jo dataset mein pehle se hain. Isliye
# cross-match turant kaam karta hai aur network/profile se sab match karta hai.

OFFENCE_TEMPLATES = [
    ("Investment fraud", "420, 406",
     "{a} induced the complainant to transfer funds into account {acct} on the promise of "
     "guaranteed monthly returns. No returns were paid and the number {phone} was switched off."),
    ("Extortion", "384, 506",
     "{a} contacted the complainant from {phone} and demanded protection money, threatening "
     "harm to the complainant's family. A part payment was routed to account {acct}."),
    ("Criminal breach of trust", "406, 409",
     "{a}, entrusted with cash collections, diverted the takings into account {acct} instead of "
     "depositing them. Contact number on record is {phone}."),
    ("Cheating by personation", "419, 420",
     "{a} represented himself as an officer of a government scheme and collected processing fees, "
     "later traced to account {acct}. The number used was {phone}."),
    ("Hawala / unlawful money transfer", "420, 120B",
     "Funds were moved through informal channels on the instructions of {a}. Account {acct} "
     "received several structured deposits. Handset {phone} was used to coordinate the transfers."),
    ("Forgery of valuable security", "467, 468, 471",
     "{a} produced forged documents to open account {acct} and operate it. Verification calls "
     "were answered on {phone}."),
    ("Cyber fraud", "66D IT Act, 420",
     "The complainant received a call from {phone} in which {a} obtained one-time passwords and "
     "debited the complainant's account. The money was credited to {acct}."),
    ("Criminal conspiracy", "120B, 420",
     "{a} coordinated a group that approached traders with fictitious supply orders. Advances "
     "were collected in account {acct}; the contact number used throughout was {phone}."),
    ("Counterfeit currency", "489B, 489C",
     "Counterfeit notes were recovered during a check. {a} was found in possession and the "
     "handset {phone} showed contact with the supplier. Account {acct} shows matching deposits."),
    ("Vehicle theft and disposal", "379, 411",
     "A vehicle reported stolen was traced to a garage operated on the instructions of {a}. "
     "Sale proceeds were deposited in account {acct}. Contact {phone}."),
    ("Kidnapping for ransom", "364A, 342",
     "A ransom demand was made from {phone} by {a}. The deposit was directed to account {acct}."),
    ("Narcotics — commercial quantity", "21(c), 29 NDPS",
     "A consignment was intercepted on information. {a} is named as the consignee. Payments "
     "were settled through account {acct}; the handset {phone} was recovered."),
]

STATIONS = [
    ("Berhampore", "Murshidabad"), ("Sealdah", "Kolkata"), ("Bhiwandi", "Thane"),
    ("Haridwar", "Haridwar"), ("Hosur", "Krishnagiri"), ("Panchkula", "Panchkula"),
    ("Faridabad", "Faridabad"), ("Shimoga", "Shivamogga"), ("Nizamabad", "Nizamabad"),
    ("Kottayam", "Kottayam"), ("Maheshtala", "South 24 Parganas"), ("Tumkur", "Tumakuru"),
]

OFFICERS = ["SI D. BOSE", "ASI R. PAL", "SI M. IYER", "INSP. K. RANA",
            "SI A. GREWAL", "ASI T. NAIR"]


def _seed_candidates(g):
    """Asli co-accused jodiyan pehle; kam padein to top-influence log."""
    pairs = []
    for (a, b, kind) in g.pair_edges:
        if kind == "co_accused":
            pairs.append((a, b))
    pairs.sort(key=lambda p: -(g.influence(p[0]) + g.influence(p[1])))

    ranked = sorted(g.persons, key=lambda n: (-g.influence(n), n))
    for i in range(0, len(ranked) - 1, 2):
        pair = (ranked[i], ranked[i + 1])
        if pair not in pairs:
            pairs.append(pair)
    return pairs


def _first(values, fallback, used=None):
    """Pehli usable value; `used` mein jo already hai usko chhod do."""
    used = used or set()
    for value in sorted(values or ()):        # set aata hai -> sorted = stable
        cleaned = _clean(value)
        if cleaned and cleaned not in used:
            return cleaned
    return fallback


def build_seed_cases(count=12):
    """Graph ki asli entities se purane FIR ka text banao (register nahi karta)."""
    g = kraken_data.graph()
    pairs = _seed_candidates(g)
    if not pairs:
        return []

    # Jagah ke naam mein kabhi kabhi insaan ghus jaate hain -- unhe hatao.
    person_names = {p.lower() for p in g.persons}
    places = [p for p in sorted(g.locations)
              if p.lower() not in person_names and len(p) > 3][:40]

    drafts = []
    year = datetime.now().year
    for index in range(min(count, len(pairs))):
        primary, associate = pairs[index]
        rec_a = g.persons.get(primary, {})
        rec_b = g.persons.get(associate, {})

        offence, sections, body = OFFENCE_TEMPLATES[index % len(OFFENCE_TEMPLATES)]
        station, district = STATIONS[index % len(STATIONS)]
        officer = OFFICERS[index % len(OFFICERS)]

        phone = _first(rec_a.get("phones"), "+919800000%03d" % (index + 11))
        account = _first(rec_a.get("accounts"), "XXXX%04d" % (4100 + index))
        # A aur B kabhi kabhi ek hi SIM share karte hain -- B ke liye alag lo.
        phone_b = _first(rec_b.get("phones"), "+919700000%03d" % (index + 11),
                         used={phone})
        towers = rec_a.get("towers") or rec_b.get("towers") or places
        place = _first(towers, station)

        # Complainant graph ka hissa nahi -- ye naya naam hai, isliye cross-match
        # sirf asli accused par lagta hai.
        complainant = ["R. Mondal", "S. Iyer", "P. Ahuja", "N. Bhatt", "T. Sen",
                       "V. Rathore", "J. Fernandes", "D. Chauhan", "L. Menon",
                       "A. Qureshi", "H. Kulkarni", "B. Das"][index % 12]

        day = (index % 27) + 1
        month = (index % 12) + 1
        narrative = (
            "FIR No. {fir}\n"
            "Police Station: {station}, District: {district}, Date of Offence: {dd:02d}/{mm:02d}/{yy}.\n\n"
            "Complainant {comp}, contact +9198{tail:08d}, states that {body}\n"
            "{a}, along with {b}, is named in the report. {b} is contactable on {phone_b}.\n"
            "Place of occurrence: {place}. Registered under Section {sections}{act}."
        ).format(
            fir="%d/%d" % (1200 + index, year),
            station=station, district=district, dd=day, mm=month, yy=year,
            comp=complainant, tail=10000000 + index * 7919,
            body=body.format(a=primary, acct=account, phone=phone),
            a=primary, b=associate, phone_b=phone_b, place=place, sections=sections,
            # NDPS/IT Act apne hi statute ke under aate hain, IPC ke nahi.
            act="" if any(t in sections for t in ("NDPS", "IT Act")) else " IPC",
        )

        drafts.append({"narrative": narrative, "officer": officer, "offence": offence})
    return drafts


def seed_cases(count=12):
    """Purane FIR bana kar normal pipeline se register karo (PDF + alerts ke saath)."""
    registered = []
    for draft in build_seed_cases(count):
        try:
            result = register_fir({
                "mode": "passage",
                "narrative": draft["narrative"],
                "officer": draft["officer"],
            })
            registered.append(result["case"]["case_no"])
        except ValueError:
            continue          # ek draft se entity na nikle to aage badho
    return registered


def ensure_seeded(minimum=10):
    """
    Database khaali ho to seed karo.

    Pehle yahan ek process-level flag tha ("ek hi baar check karo"), lekin uska
    matlab ye tha ki server chalte-chalte agar case file delete ho jaye to
    seeding dobara kabhi nahi hoti thi -- page hamesha khaali dikhta. Ab har
    baar DB dekhte hain; lock ke andar hone se do parallel request mil kar
    duplicate cases nahi banatin.
    """
    with _lock:
        if _load_db().get("cases"):
            return []
        return seed_cases(max(minimum, 12))
