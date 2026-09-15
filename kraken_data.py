"""
KRAKEN DATA LAYER
=================
Ek hi source of truth. Saare pages (network, predictions, syndicates, profile,
reports, command) isi module se data lete hain, isliye numbers kabhi aapas mein
mismatch nahi karte.

Kya karta hai:
  1. sample_data/extracted_entities.json padhta hai (NER ka asli output).
  2. NER noise saaf karta hai -- phone numbers/dates jo galti se "person" list
     mein aa gaye, aur "Acharya Road" jaise names jo actually locations hain.
  3. Person-level graph banata hai: FIR co-accused + CDR calls (phone->owner)
     + money transfers (account->owner) + ownership links.
  4. Adamic-Adar link prediction chalata hai -- pure Python, networkx ki zaroorat
     nahi (is venv mein networkx installed hi nahi hai).
  5. Intake page se aaya naya intel live graph mein merge karta hai.
"""

import json
import math
import os
import re
import threading
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# sample_data wali copy pehle -- wahi pipeline ka canonical output hai.
DATA_CANDIDATES = (
    os.path.join(BASE_DIR, "sample_data", "extracted_entities.json"),
    os.path.join(BASE_DIR, "extracted_entities.json"),
)

# Operator ke manual edits (intake uploads + removals) yahan save hote hain,
# taaki server restart pe bhi ledger waisa hi rahe. Source JSON kabhi nahi
# badalti -- ye alag overlay file hai.
STATE_PATH = os.path.join(BASE_DIR, "kraken_state.json")

# ---------------------------------------------------------------- cleaning ---

# FIR text se aaye role words -- "Complainant Timothy Dutt" aur "Timothy Dutt"
# ek hi insaan hai, warna graph mein duplicate nodes ban jate hain.
ROLE_WORDS = {
    "complainant", "accused", "co-accused", "deceased", "witness", "suspect",
    "shri", "smt", "sri", "mr", "mrs", "ms", "dr", "si", "asi", "psi", "hc",
    "constable", "inspector", "sub-inspector", "head", "late",
}

DATE_RE = re.compile(r"^\d{1,4}[/-]\d{1,2}[/-]\d{1,4}$")
ACCOUNT_RE = re.compile(r"^(?:xxxx|ac|acc|a/c)[-\s]?\d+$", re.I)
PINCODE_PLACE_RE = re.compile(r"^([A-Za-z][A-Za-z.\s]{2,40}?)[-\s](\d{6})$")
LOCATION_RE = re.compile(
    r"\b(road|rd|marg|zila|district|nagar|colony|chowk|bazaar|market|sector|"
    r"gali|galli|lane|street|tola|thana|station|village|city|ganj|"
    # "Kakar Path", "Borra Circle" -- Faker ke street names surname se bante
    # hain, isliye NER inhe PERSON tag kar deta tha.
    r"path|circle|vihar|puram|pura|basti|mohalla|layout|cross|chauraha)\b", re.I)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).strip(" ,.;:")


def is_phone(value):
    digits = re.sub(r"[\s()+-]", "", str(value or ""))
    return digits.isdigit() and 8 <= len(digits) <= 15


def is_account(value):
    return bool(ACCOUNT_RE.match(_clean(value)))


def is_date(value):
    return bool(DATE_RE.match(_clean(value)))


def looks_like_location(value):
    return bool(LOCATION_RE.search(_clean(value)))


def normalise_person(value):
    """Raw NER string -> saaf person name, ya None agar wo insaan hi nahi hai."""
    name = _clean(value)
    if not name:
        return None

    parts = name.split()
    while parts and parts[0].lower().strip(".") in ROLE_WORDS:
        parts.pop(0)
    name = " ".join(parts)

    if not (3 <= len(name) <= 60) or len(parts) > 5:
        return None
    if not re.search(r"[A-Za-z]", name):
        return None
    if is_phone(name) or is_account(name) or is_date(name):
        return None
    # Insaan ke naam mein digit nahi hota -- "account XXXX5554" jaisi NER
    # noise yahin ruk jati hai.
    if re.search(r"\d", name):
        return None
    # Document artifacts jo NER ko insaan lagte hain: "FIR No", "Police Station",
    # "IPC", "Rs", "Account ..." -- inme se koi bhi aadmi nahi hai.
    if re.match(r"^(account|acct|phone|mobile|number|amount|rs|rupees|fir|f\.i\.r|"
                r"ipc|crpc|section|sec|police|thana|station|chowki|case|complaint|"
                r"investigation|dated|date|no|sr|serial)\b", name, re.I):
        return None
    return name


# ------------------------------------------------------------------ graph ----

class KrakenGraph:
    """Poora intel graph, ek object mein."""

    def __init__(self):
        self.persons = {}          # canonical name -> dossier dict
        self._alias = {}           # lowercase -> canonical name
        self._first_token = {}     # "fiyaz" -> {"Fiyaz Sangha"}
        self.locations = set()
        self.phone_owner = {}      # phone  -> person
        self.account_owner = {}    # account -> person
        self.pair_edges = {}       # (a, b, kind) -> {weight, labels}
        self.place_edges = {}      # (person, place) -> {weight, labels}
        self.adjacency = defaultdict(dict)   # person -> {person: weight}
        self.counts = defaultdict(int)
        self.source_files = []

    # -- nodes --------------------------------------------------------------
    def add_person(self, raw_name):
        # FIR address "Jaipur 312984" NER ko person lagta hai -- wo location hai.
        place = PINCODE_PLACE_RE.match(_clean(raw_name))
        if place:
            self.add_location(place.group(1))
            return None

        name = normalise_person(raw_name)
        if not name:
            return None
        if looks_like_location(name):
            self.locations.add(name)
            return None

        key = name.lower()
        if key in self._alias:
            return self._alias[key]

        # NER kabhi-kabhi naam aadha kaat deta hai ("Fiyaz Sangha" -> "Fiyaz").
        # Agar us pehle word se sirf ek hi poora naam banta hai to wahi insaan
        # hai -- warna ek aadmi ke do node ban jate the.
        full = self._resolve_fragment(name)
        if full:
            self._alias[key] = full
            return full

        self._alias[key] = name
        self._index_first_token(name)
        self.persons[name] = {
            "name": name, "phones": set(), "accounts": set(), "firs": set(),
            "towers": set(), "calls": 0, "sent": 0.0, "received": 0.0,
            "intake": False, "call_log": [], "txn_log": [],
        }
        return name

    def _index_first_token(self, name):
        parts = name.split()
        if len(parts) > 1:
            self._first_token.setdefault(parts[0].lower(), set()).add(name)

    def _resolve_fragment(self, name):
        """Akela word -- agar usse sirf ek poora naam banta hai to wahi lautao."""
        parts = name.split()
        if len(parts) != 1:
            return None
        matches = self._first_token.get(parts[0].lower())
        if matches and len(matches) == 1:
            return next(iter(matches))
        return None

    # Akela generic word koi jagah nahi hai -- NER ka kachra.
    GENERIC_PLACES = {"marg", "road", "rd", "street", "lane", "zila", "nagar",
                      "ganj", "h.no", "hno", "colony", "sector", "district"}

    def add_location(self, raw_name):
        name = _clean(raw_name)
        if not name or len(name) < 4 or is_date(name) or is_phone(name):
            return
        if name.lower().strip(".") in self.GENERIC_PLACES:
            return
        self.locations.add(name)

    # -- edges --------------------------------------------------------------
    def link(self, a, b, kind, label=None, weight=1):
        """Do persons ke beech ek confirmed edge (aggregate ho jata hai)."""
        if not a or not b or a == b:
            return
        key = (a, b, kind) if a < b else (b, a, kind)
        edge = self.pair_edges.setdefault(key, {"weight": 0, "labels": set()})
        edge["weight"] += weight
        if label:
            edge["labels"].add(str(label))

        self.adjacency[a][b] = self.adjacency[a].get(b, 0) + weight
        self.adjacency[b][a] = self.adjacency[b].get(a, 0) + weight

    def link_place(self, person, place, label=None):
        """Person ko ek tower/location se jodta hai (CDR ping se)."""
        if not person or not place:
            return
        self.locations.add(place)
        edge = self.place_edges.setdefault((person, place), {"weight": 0, "labels": set()})
        edge["weight"] += 1
        if label:
            edge["labels"].add(str(label))

    # -- derived ------------------------------------------------------------
    def influence(self, name):
        """Weighted score: kitne log juday hain + call/money activity."""
        rec = self.persons.get(name, {})
        associates = len(self.adjacency.get(name, {}))
        traffic = sum(self.adjacency.get(name, {}).values())
        money = (rec.get("sent", 0) + rec.get("received", 0)) / 1_000_000.0
        return associates * 3 + traffic + len(rec.get("firs", ())) * 2 + money


def _empty_graph():
    return KrakenGraph()


def _build_graph(raw, intake_batches):
    g = KrakenGraph()

    nodes = raw.get("nodes", {}) or {}
    edges = raw.get("edges", {}) or {}

    # 0. Pehle transactions.csv ke poore naam. Ye structured CSV se aate hain,
    #    NER ke guess nahi -- isliye inhe canonical maana jata hai. Inke bina
    #    account ka owner NER ka aadha naam ("Fiyaz") ban jata tha aur asli
    #    insaan ("Fiyaz Sangha") graph se hi gayab ho jata tha.
    for txn in edges.get("money_transfers", []) or []:
        g.add_person(txn.get("from_name"))
        g.add_person(txn.get("to_name"))

    # 1. Declared nodes (NER noise yahin filter hota hai)
    #    Jo string NER ne person AUR location dono bataya hai, wo jagah hai.
    ner_locations = {_clean(x).lower() for x in (nodes.get("locations", []) or [])}
    for raw_name in nodes.get("persons", []) or []:
        if _clean(raw_name).lower() in ner_locations:
            g.add_location(raw_name)
            continue
        g.add_person(raw_name)
    for raw_name in nodes.get("locations", []) or []:
        g.add_location(raw_name)

    # 2. Ownership -- phone/account ko asli insaan se jodta hai
    for link in edges.get("ownership_links", []) or []:
        person = g.add_person(link.get("person"))
        if not person:
            continue
        if link.get("type") == "OWNS_PHONE" and is_phone(link.get("phone")):
            phone = _clean(link["phone"])
            g.persons[person]["phones"].add(phone)
            g.phone_owner[phone] = person
        elif link.get("type") == "OWNS_ACCOUNT" and link.get("account"):
            account = _clean(link["account"])
            g.persons[person]["accounts"].add(account)
            g.account_owner[account] = person

    # 3. FIR co-accused -- sabse strong confirmed link
    for link in edges.get("co_accused_links", []) or []:
        a = g.add_person(link.get("person_a"))
        b = g.add_person(link.get("person_b"))
        source = _clean(link.get("source"))
        if a:
            g.persons[a]["firs"].add(source)
        if b:
            g.persons[b]["firs"].add(source)
        if a and b:
            g.link(a, b, "co_accused", source)
            g.counts["co_accused"] += 1

    # 4. CDR calls -- phone se owner nikaal kar person-level edge
    for call in edges.get("calls", []) or []:
        src_phone, dst_phone = _clean(call.get("from")), _clean(call.get("to"))
        a, b = g.phone_owner.get(src_phone), g.phone_owner.get(dst_phone)
        tower = _clean(call.get("tower_location"))
        for person in (a, b):
            if person:
                g.persons[person]["calls"] += 1
                if tower:
                    g.persons[person]["towers"].add(tower)
                    # Tower ping = person aur jagah ke beech asli confirmed link
                    g.link_place(person, tower, "CDR ping")
        # Har taraf ka asli CDR record rakho -- profile page isi ko dikhata hai
        timestamp = _clean(call.get("timestamp"))
        try:
            duration = int(float(call.get("duration_sec") or 0))
        except (TypeError, ValueError):
            duration = 0
        for owner, own_phone, other_phone, other in (
            (a, src_phone, dst_phone, b), (b, dst_phone, src_phone, a)
        ):
            if not owner:
                continue
            g.persons[owner]["call_log"].append({
                "own_phone": own_phone,
                "counterparty": other or other_phone,
                "counterparty_phone": other_phone,
                "identified": bool(other),
                "timestamp": timestamp,
                "duration_sec": duration,
                "tower": tower,
                "direction": "out" if owner == a else "in",
            })

        if a and b:
            g.link(a, b, "call", "CDR tower: %s" % tower if tower else "CDR intercept")
            g.counts["calls_resolved"] += 1
        else:
            g.counts["calls_unresolved"] += 1

    # 5. Money transfers -- account owner, warna transfer ka apna naam field
    for txn in edges.get("money_transfers", []) or []:
        from_acc, to_acc = _clean(txn.get("from")), _clean(txn.get("to"))
        a = g.account_owner.get(from_acc) or g.add_person(txn.get("from_name"))
        b = g.account_owner.get(to_acc) or g.add_person(txn.get("to_name"))
        try:
            amount = float(txn.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if a:
            g.persons[a]["sent"] += amount
            if from_acc:
                g.persons[a]["accounts"].add(from_acc)
                g.account_owner.setdefault(from_acc, a)
        if b:
            g.persons[b]["received"] += amount
            if to_acc:
                g.persons[b]["accounts"].add(to_acc)
                g.account_owner.setdefault(to_acc, b)
        date = _clean(txn.get("date"))
        if a:
            g.persons[a]["txn_log"].append({
                "direction": "out", "amount": amount, "date": date,
                "own_account": from_acc, "counterparty_account": to_acc,
                "counterparty": b or _clean(txn.get("to_name")) or to_acc,
            })
        if b:
            g.persons[b]["txn_log"].append({
                "direction": "in", "amount": amount, "date": date,
                "own_account": to_acc, "counterparty_account": from_acc,
                "counterparty": a or _clean(txn.get("from_name")) or from_acc,
            })

        g.counts["money_volume"] += amount
        if a and b:
            g.link(a, b, "money", "Transfer Rs %s" % format(int(amount), ","))
            g.counts["transfers_resolved"] += 1
        else:
            g.counts["transfers_unresolved"] += 1

    # 6. Intake page se aaya live intel -- ek hi submission mein aaye entities
    #    ko co-mentioned maana jata hai (wahi asli signal hai).
    for batch in intake_batches:
        names = [n for n in (g.add_person(x) for x in batch.get("names", [])) if n]
        for name in names:
            g.persons[name]["intake"] = True
        for phone in batch.get("phones", []):
            phone = _clean(phone)
            if not is_phone(phone):
                continue
            for name in names:
                g.persons[name]["phones"].add(phone)
                g.phone_owner.setdefault(phone, name)
        for account in batch.get("banks", []) or batch.get("accounts", []):
            account = _clean(account)
            if not account:
                continue
            for name in names:
                g.persons[name]["accounts"].add(account)
                g.account_owner.setdefault(account, name)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                g.link(names[i], names[j], "intake", "Same intelligence report")
                g.counts["intake_links"] += 1

    return g


# -------------------------------------------------------------- AI engine ----

def adamic_adar(graph, top=15, threshold=0.1):
    """
    Adamic-Adar link prediction (pure Python).
    Logic predict_hidden_links.py jaisa hi hai, bas networkx ke bina --
    aur reasoning ab asli common associates ke naam se banti hai.
    """
    adj = {n: set(v) for n, v in graph.adjacency.items()}
    degree = {n: len(v) for n, v in adj.items()}
    scored = []

    for u in adj:
        neighbours = adj[u]
        candidates = set()
        for w in neighbours:
            candidates |= adj.get(w, set())
        candidates -= neighbours
        candidates.discard(u)

        for v in candidates:
            if v <= u:            # har pair sirf ek baar
                continue
            common = neighbours & adj.get(v, set())
            score = sum(1.0 / math.log(degree[w]) for w in common if degree.get(w, 0) > 1)
            if score > threshold:
                scored.append((score, u, v, common))

    scored.sort(key=lambda row: (-row[0], row[1], row[2]))

    predictions = []
    for score, u, v, common in scored[:top]:
        shared = sorted(common, key=lambda n: -degree.get(n, 0))
        kinds = set()
        for person in shared[:4]:
            for other in (u, v):
                for kind in ("co_accused", "call", "money", "intake"):
                    key = (person, other, kind) if person < other else (other, person, kind)
                    if key in graph.pair_edges:
                        kinds.add(kind)
        trails = {
            "co_accused": "common FIR associates",
            "call": "overlapping call partners",
            "money": "shared financial trails",
            "intake": "same intelligence report",
        }
        why = ", ".join(trails[k] for k in ("co_accused", "call", "money", "intake") if k in kinds)
        predictions.append({
            "source": u,
            "target": v,
            "confidence": round(min(99.0, (score / (score + 2)) * 100 + 65), 1),
            "reasoning": "%d shared node%s (%s): %s" % (
                len(common), "" if len(common) == 1 else "s",
                why or "network overlap", ", ".join(shared[:3])),
            "shared_nodes": shared[:5],
            "score": round(score, 3),
        })
    return predictions


# ------------------------------------------------------------------ cache ----

_lock = threading.Lock()
_state = {"graph": None, "stamp": None, "path": None, "predictions": None}
_intake_batches = []
# Manually hataye gaye entities/links. JSON file chhedne ki zaroorat nahi --
# ye overlay hai, kabhi bhi restore ho sakta hai.
_suppressed_nodes = set()      # lowercase ids (person / phone / account / location)
_suppressed_pairs = set()      # frozenset({a, b}) -- ek specific link
_state_mtime = None            # state file ka last-loaded mtime



def _dataset_path():
    for path in DATA_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _stamp(path):
    try:
        stat = os.stat(path)
        return (stat.st_mtime, stat.st_size, len(_intake_batches),
                len(_suppressed_nodes), len(_suppressed_pairs))
    except OSError:
        return None


def _sync_state_from_disk():
    """
    State file bahar se badal gayi ho (dusra process, ya tests) to usay dobara
    padho. Pehle ye sirf import par load hoti thi, isliye server ki memory aur
    disk alag ho jate the.
    """
    if _state_file_mtime() != _state_mtime:
        _load_state()          # khud _lock leta hai
        return True
    return False


def graph(force=False):
    """Cached graph. JSON file badal jaye to apne aap reload hota hai."""
    if _sync_state_from_disk():
        force = True
    path = _dataset_path()
    with _lock:
        stamp = _stamp(path) if path else ("missing", len(_intake_batches),
                                           len(_suppressed_nodes), len(_suppressed_pairs))
        if not force and _state["graph"] is not None and _state["stamp"] == stamp:
            return _state["graph"]

        raw = {}
        if path:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError):
                raw = {}

        built = _apply_suppressions(_build_graph(raw, list(_intake_batches)))
        built.source_files = [os.path.relpath(path, BASE_DIR)] if path else []
        _state.update(graph=built, stamp=stamp, path=path, predictions=None)
        return built


def predictions(top=15):
    g = graph()
    with _lock:
        if _state["predictions"] is None:
            _state["predictions"] = adamic_adar(g, top=max(top, 15))
        cached = _state["predictions"]
    return cached[:top]


def add_intake(names, phones, banks):
    """Intake page ka naya intel -- live graph mein merge."""
    batch = {
        "names": [x for x in (names or []) if _clean(x)],
        "phones": [x for x in (phones or []) if _clean(x)],
        "banks": [x for x in (banks or []) if _clean(x)],
    }
    if not any(batch.values()):
        return None
    with _lock:
        _intake_batches.append(batch)
    _save_state()
    # graph() khud _lock leta hai, isliye lock chhodne ke baad rebuild.
    return graph(force=True)


# ------------------------------------------------------- API ke payloads ----

DEFAULT_TOWER_MIN = 3

# Frontend (network.html) sirf ye 4 node types samajhta hai.
EDGE_LABELS = {
    "co_accused": "Co-accused in FIR",
    "call": "CDR call link",
    "money": "Money transfer",
    "intake": "Same intel report",
    "tower": "Tower ping",
    "owns_phone": "Registered SIM",
    "owns_account": "Bank account",
}


def graph_payload(min_weight=1, include_devices=True, tower_min=DEFAULT_TOWER_MIN):
    """
    network.html ke liye nodes + edges. Yahi /api/graph-data serve karta hai.

    tower_min=3 default: har banda lagbhag har tower se ping karta hai, to saare
    267 tower edges dikhane se graph hairball ban jata hai. 3+ pings ka matlab
    hai banda wahan baar baar tha -- wahi meaningful co-location signal hai.
    """
    g = graph()
    nodes, edges = [], []

    for name in sorted(g.persons):
        nodes.append({"id": name, "type": "person"})
    live_places = {place for (_p, place), edge in g.place_edges.items()
                   if edge["weight"] >= max(min_weight, tower_min)}
    for place in sorted(g.locations):
        # NER se aayi jagah tab hi dikhao jab wo kisi FIR address ya live tower ki ho
        if place in live_places or place not in {p for (_x, p) in g.place_edges}:
            nodes.append({"id": place, "type": "location"})
    if include_devices:
        for phone in sorted(g.phone_owner):
            nodes.append({"id": phone, "type": "phone"})
        for account in sorted(g.account_owner):
            nodes.append({"id": account, "type": "bank"})

    def push(source, target, kind, weight, labels):
        edges.append({
            "source": source,
            "target": target,
            "type": "confirmed",          # frontend isi flag se solid line kheenchta hai
            "kind": kind,
            "weight": weight,
            "label": EDGE_LABELS.get(kind, kind),
            "detail": " | ".join(sorted(labels)[:2]) if labels else "",
        })

    for (a, b, kind), edge in g.pair_edges.items():
        if edge["weight"] >= min_weight:
            push(a, b, kind, edge["weight"], edge["labels"])

    for (person, place), edge in g.place_edges.items():
        if edge["weight"] >= max(min_weight, tower_min):
            push(person, place, "tower", edge["weight"], edge["labels"])

    if include_devices:
        for phone, owner in sorted(g.phone_owner.items()):
            push(owner, phone, "owns_phone", 1, set())
        for account, owner in sorted(g.account_owner.items()):
            push(owner, account, "owns_account", 1, set())

    return {"nodes": nodes, "edges": edges}


def targets(top=12):
    """
    Top targets -- hardcoded list nahi, graph influence se nikaale gaye.
    Phones/accounts asli ownership links se aate hain, isliye profile page ko
    fake numbers generate karne ki zaroorat nahi padti.
    """
    g = graph()
    ranked = sorted(g.persons, key=lambda n: (-g.influence(n), n))[:top]
    if not ranked:
        return []

    highest = g.influence(ranked[0]) or 1.0
    dossiers = []
    for index, name in enumerate(ranked):
        rec = g.persons[name]
        score = int(round(60 + 39 * (g.influence(name) / highest)))
        if index == 0:
            badge = "MASTERMIND"
        elif index < 3:
            badge = "HIGH VALUE"
        elif score >= 80:
            badge = "THREAT"
        else:
            badge = "ASSOCIATE"

        dossiers.append({
            "id": "Kraken-%03d" % (index + 1),
            "name": name,
            "score": score,
            "color": "error" if score >= 90 else "secondary-container",
            "badge": badge,
            "phones": sorted(rec["phones"]),
            "accounts": sorted(rec["accounts"]),
            "associates": len(g.adjacency.get(name, {})),
            "firs": sorted(rec["firs"]),
            "calls": rec["calls"],
            "towers": sorted(rec["towers"]),
            "sent": round(rec["sent"], 2),
            "received": round(rec["received"], 2),
            "from_intake": rec["intake"],
        })
    return dossiers


def dossiers():
    """
    Har person ka poora dossier (sirf top 12 nahi). Profile page isi se chalta
    hai, isliye use kisi ka phone/account "generate" karne ki zaroorat nahi --
    sab asli ownership links se aata hai.
    """
    return targets(top=len(graph().persons) or 1)


def stats(tower_min=DEFAULT_TOWER_MIN):
    """
    Command/reports pages ke counters -- sab ek hi graph se.
    tower_min wahi default hai jo graph_payload use karta hai, isliye dashboard
    ka "confirmed links" aur network page ki lines ka count match karta hai.
    """
    g = graph()
    kinds = defaultdict(int)
    for (_a, _b, kind) in g.pair_edges:
        kinds[kind] += 1
    strong_towers = sum(1 for e in g.place_edges.values() if e["weight"] >= tower_min)
    return {
        "persons": len(g.persons),
        "locations": len(g.locations),
        "phones": len(g.phone_owner),
        "accounts": len(g.account_owner),
        "confirmed_links": len(g.pair_edges) + strong_towers,
        "co_accused_links": kinds["co_accused"],
        "call_links": kinds["call"],
        "money_links": kinds["money"],
        "intake_links": kinds["intake"],
        "tower_links": strong_towers,
        "tower_pings_total": len(g.place_edges),
        "calls_processed": g.counts.get("calls_resolved", 0) + g.counts.get("calls_unresolved", 0),
        "calls_resolved": g.counts.get("calls_resolved", 0),
        "transfers_processed": g.counts.get("transfers_resolved", 0) + g.counts.get("transfers_unresolved", 0),
        "firs": len({fir for rec in g.persons.values() for fir in rec["firs"] if fir}),
        "money_volume": round(g.counts.get("money_volume", 0), 2),
        "calls_unattributed": g.counts.get("calls_unresolved", 0),
        "intake_batches": len(_intake_batches),
        "source": g.source_files,
    }


def entity(name, call_limit=8, txn_limit=8):
    """
    Ek person ka poora asli record: CDR rows, transactions, associates aur
    AI predictions. Profile page ka detail panel isi se bharta hai.
    """
    g = graph()
    wanted = _clean(name).lower()
    canonical = next((p for p in g.persons if p.lower() == wanted), None)
    if not canonical:
        return None

    rec = g.persons[canonical]
    dossier = next((d for d in dossiers() if d["name"] == canonical), None) or {"name": canonical}

    # Associates + exactly kis evidence par jude hain
    associates = []
    for other, weight in sorted(g.adjacency.get(canonical, {}).items(),
                                key=lambda kv: (-kv[1], kv[0])):
        kinds, firs = [], []
        for (a, b, kind), edge in g.pair_edges.items():
            if {a, b} != {canonical, other}:
                continue
            kinds.append(kind)
            if kind == "co_accused":
                firs.extend(sorted(edge["labels"]))

        # CDR: in dono ke beech kitni calls, kitna time, kaunsa tower
        call_rows = [row for row in rec["call_log"] if row["counterparty"] == other]
        seconds = sum(row["duration_sec"] for row in call_rows)
        call_towers = sorted({row["tower"] for row in call_rows if row["tower"]})

        # Paisa: dono directions alag alag
        sent = sum(row["amount"] for row in rec["txn_log"]
                   if row["counterparty"] == other and row["direction"] == "out")
        received = sum(row["amount"] for row in rec["txn_log"]
                       if row["counterparty"] == other and row["direction"] == "in")
        txn_count = sum(1 for row in rec["txn_log"] if row["counterparty"] == other)

        # Ek line ka reason -- UI isay seedha dikhata hai
        parts = []
        if firs:
            parts.append("Co-accused in %s" % ", ".join(firs[:2]))
        if call_rows:
            # Tower list honest rakho: ek hi tower ho to naam, warna "+N more"
            if not call_towers:
                where = ""
            elif len(call_towers) == 1:
                where = " via %s" % call_towers[0]
            else:
                where = " via %s +%d more" % (call_towers[0], len(call_towers) - 1)
            parts.append("%d call%s (%d min)%s" % (
                len(call_rows), "" if len(call_rows) == 1 else "s",
                round(seconds / 60), where))
        if sent:
            parts.append("Rs %s sent" % format(int(sent), ","))
        if received:
            parts.append("Rs %s received" % format(int(received), ","))
        if "intake" in kinds and not parts:
            parts.append("Named in the same intelligence report")

        associates.append({
            "name": other,
            "weight": weight,
            "kinds": sorted(set(kinds)),
            "firs": firs,
            "calls": len(call_rows),
            "call_minutes": round(seconds / 60),
            "call_towers": call_towers,
            "sent": round(sent, 2),
            "received": round(received, 2),
            "transactions": txn_count,
            "reason": " | ".join(parts) or "Linked in the case graph",
        })

    calls = sorted(rec["call_log"], key=lambda c: c["timestamp"], reverse=True)
    txns = sorted(rec["txn_log"], key=lambda t: t["date"], reverse=True)

    # Sabse zyada baar dikhne wala tower = last known area
    tower_hits = defaultdict(int)
    for row in rec["call_log"]:
        if row["tower"]:
            tower_hits[row["tower"]] += 1
    top_tower = max(tower_hits.items(), key=lambda kv: kv[1])[0] if tower_hits else ""

    related = [p for p in predictions(50)
               if canonical in (p["source"], p["target"])]

    return {
        "dossier": dossier,
        "associates": associates,
        "calls": calls[:call_limit],
        "transactions": txns[:txn_limit],
        "call_total": len(rec["call_log"]),
        "txn_total": len(rec["txn_log"]),
        "top_tower": top_tower,
        "tower_hits": dict(sorted(tower_hits.items(), key=lambda kv: -kv[1])),
        "predictions": related,
    }


# ------------------------------------------------- COMMUNITY DETECTION ------
# Syndicates ab AI predictions ko group karke nahi bante. Ye Louvain
# modularity-maximisation hai, confirmed links par chalta hai -- wahi algorithm
# jo asli link-analysis tools (i2, Gephi) use karte hain. Pure Python, kyunki
# is venv mein networkx nahi hai.

# Evidence ki strength: FIR co-accused sabse pukka, call sabse weak.
KIND_WEIGHT = {"co_accused": 3.0, "money": 2.0, "intake": 2.0, "call": 1.0}


def _community_adjacency(g):
    """Weighted person-person adjacency (towers/devices isme nahi aate)."""
    adj = defaultdict(lambda: defaultdict(float))
    for (a, b, kind), edge in g.pair_edges.items():
        weight = KIND_WEIGHT.get(kind, 1.0) * edge["weight"]
        if weight <= 0:
            continue
        adj[a][b] += weight
        adj[b][a] += weight
    return {n: dict(v) for n, v in adj.items()}


def _louvain_one_level(adjacency, resolution, order):
    """Louvain ka local-moving phase: node ko us community mein daalo jahan
    modularity sabse zyada badhe."""
    community = {n: index for index, n in enumerate(order)}
    degrees = {n: sum(adjacency[n].values()) for n in order}
    total = sum(degrees.values())            # = 2m
    if total == 0:
        return community

    comm_degree = defaultdict(float)
    for n in order:
        comm_degree[community[n]] += degrees[n]

    improved, guard = True, 0
    while improved and guard < 50:
        improved, guard = False, guard + 1
        for n in order:
            own = community[n]
            to_comm = defaultdict(float)
            for neighbour, weight in adjacency[n].items():
                if neighbour != n:
                    to_comm[community[neighbour]] += weight

            comm_degree[own] -= degrees[n]     # pehle apni community se nikaalo
            best_comm = own
            best_gain = to_comm.get(own, 0.0) - resolution * comm_degree[own] * degrees[n] / total

            for candidate, weight in sorted(to_comm.items()):
                gain = weight - resolution * comm_degree[candidate] * degrees[n] / total
                if gain > best_gain + 1e-12:
                    best_gain, best_comm = gain, candidate

            comm_degree[best_comm] += degrees[n]
            if best_comm != own:
                community[n] = best_comm
                improved = True

    return community


def louvain_communities(adjacency, resolution=1.0, max_levels=10):
    """Multi-level Louvain. Deterministic -- har page load pe wahi answer."""
    current = {n: dict(v) for n, v in adjacency.items()}
    mapping = {n: n for n in adjacency}

    for _ in range(max_levels):
        order = sorted(current, key=str)
        community = _louvain_one_level(current, resolution, order)

        groups = defaultdict(list)
        for node, comm in community.items():
            groups[comm].append(node)
        if len(groups) == len(current):        # kuch merge nahi hua -> done
            break

        mapping = {original: community[mapping[original]] for original in mapping}

        aggregated = defaultdict(lambda: defaultdict(float))
        for node, neighbours in current.items():
            for neighbour, weight in neighbours.items():
                aggregated[community[node]][community[neighbour]] += weight
        current = {n: dict(v) for n, v in aggregated.items()}
        if len(current) <= 1:
            break

    return mapping


def modularity(adjacency, partition):
    """Partition kitna acha hai (0 = random, jitna zyada utna clear structure)."""
    total = sum(sum(v.values()) for v in adjacency.values())
    if total == 0:
        return 0.0
    inside, degree_sum = defaultdict(float), defaultdict(float)
    for node, neighbours in adjacency.items():
        comm = partition[node]
        degree_sum[comm] += sum(neighbours.values())
        for neighbour, weight in neighbours.items():
            if partition.get(neighbour) == comm:
                inside[comm] += weight
    return round(sum(inside[c] / total - (degree_sum[c] / total) ** 2 for c in degree_sum), 4)


def syndicates(resolution=1.0):
    """
    Asli syndicates: confirmed links se detect kiye gaye communities, har ek ka
    boss (highest influence) aur BFS se nikaali hui hierarchy.
    """
    g = graph()
    adjacency = _community_adjacency(g)
    if not adjacency:
        return {"syndicates": [], "modularity": 0.0, "resolution": resolution,
                "algorithm": "Louvain modularity maximisation", "unaffiliated": []}

    partition = louvain_communities(adjacency, resolution=resolution)
    quality = modularity(adjacency, partition)

    groups = defaultdict(list)
    for person, comm in partition.items():
        groups[comm].append(person)

    dossier_by_name = {d["name"]: d for d in dossiers()}
    all_predictions = predictions(60)

    results, unaffiliated = [], []
    for members in groups.values():
        if len(members) < 2:
            unaffiliated.extend(members)
            continue

        member_set = set(members)
        ranked = sorted(members, key=lambda n: (-g.influence(n), n))
        boss = ranked[0]

        # Community ke andar ka subgraph -> BFS se asli hierarchy
        sub = {n: sorted((nb for nb in g.adjacency.get(n, {}) if nb in member_set),
                         key=lambda nb: -g.adjacency[n][nb]) for n in members}
        level, parent, queue = {boss: 0}, {}, [boss]
        while queue:
            current = queue.pop(0)
            for neighbour in sub[current]:
                if neighbour not in level:
                    level[neighbour] = level[current] + 1
                    parent[neighbour] = current
                    queue.append(neighbour)

        def link_kinds(a, b):
            return sorted(kind for (x, y, kind) in g.pair_edges if {x, y} == {a, b})

        internal = external = 0
        kind_tally = defaultdict(int)
        fir_tally = defaultdict(int)
        for (a, b, kind), edge in g.pair_edges.items():
            if a in member_set and b in member_set:
                internal += 1
                kind_tally[kind] += 1
                if kind == "co_accused":
                    for label in edge["labels"]:
                        fir_tally[label] += 1
            elif a in member_set or b in member_set:
                external += 1

        roster = []
        for rank, name in enumerate(ranked):
            depth = level.get(name)
            if rank == 0:
                role = "MASTERMIND"
            elif depth == 1 and rank <= 3:
                role = "LIEUTENANT"
            elif depth is None:
                role = "PERIPHERAL"
            else:
                role = "OPERATIVE"

            record = dossier_by_name.get(name, {})
            via = parent.get(name)
            roster.append({
                "name": name,
                "role": role,
                "level": 99 if depth is None else depth,
                "via": via or "",
                "link_kinds": link_kinds(name, via) if via else [],
                "link_strength": round(g.adjacency.get(name, {}).get(via, 0), 1) if via else 0,
                "score": record.get("score", 0),
                "badge": record.get("badge", ""),
                "associates": record.get("associates", 0),
                "phones": record.get("phones", []),
                "accounts": record.get("accounts", []),
                "firs": record.get("firs", []),
                "calls": record.get("calls", 0),
                "towers": record.get("towers", []),
            })

        towers = defaultdict(int)
        turnover = 0.0
        for name in members:
            rec = g.persons[name]
            turnover += rec["sent"]
            for tower in rec["towers"]:
                towers[tower] += 1

        trail_names = {"co_accused": "FIR CO-ACCUSED", "money": "HAWALA / MONEY",
                       "call": "CDR TRAFFIC", "intake": "FIELD INTEL"}
        dominant = max(kind_tally.items(), key=lambda kv: (KIND_WEIGHT.get(kv[0], 1) * kv[1]))[0] \
            if kind_tally else "call"

        results.append({
            "boss": boss,
            "size": len(members),
            "members": roster,
            "internal_links": internal,
            "external_links": external,
            "cohesion": round(100.0 * internal / (internal + external), 1) if (internal + external) else 0.0,
            "dominant_trail": trail_names.get(dominant, dominant),
            "link_mix": dict(kind_tally),
            "shared_firs": sorted(f for f, count in fir_tally.items() if count >= 1),
            "territory": [t for t, _ in sorted(towers.items(), key=lambda kv: -kv[1])[:4]],
            "turnover": round(turnover, 2),
            "predictions": [p for p in all_predictions
                            if p["source"] in member_set and p["target"] in member_set],
        })

    # Sabse bada aur sabse gutha hua syndicate pehle
    results.sort(key=lambda s: (-s["size"], -s["cohesion"]))
    for index, syndicate in enumerate(results):
        syndicate["id"] = "SYN-%02d" % (index + 1)
        syndicate["tier"] = index + 1
        syndicate["name"] = "%s SYNDICATE" % syndicate["boss"].upper()

    return {
        "syndicates": results,
        "modularity": quality,
        "resolution": resolution,
        "algorithm": "Louvain modularity maximisation on confirmed links",
        "unaffiliated": sorted(unaffiliated),
        "evidence_weights": KIND_WEIGHT,
    }


# --------------------------------------------------- MANUAL GRAPH EDITING ---
# Intake page se operator graph ko live edit kar sakta hai: entity add karo,
# hatao, wapas laao, ya poori last upload undo karo. Source JSON file kabhi
# modify nahi hoti -- sab overlay hai.

def _apply_suppressions(g):
    """Hataye gaye nodes/links ko graph se nikaal do (edges ke saath)."""
    if not _suppressed_nodes and not _suppressed_pairs:
        return g

    dead = {x.lower() for x in _suppressed_nodes}

    for name in [n for n in g.persons if n.lower() in dead]:
        del g.persons[name]
        g._alias.pop(name.lower(), None)
    g.locations = {loc for loc in g.locations if loc.lower() not in dead}
    g.phone_owner = {ph: own for ph, own in g.phone_owner.items()
                     if ph.lower() not in dead and own in g.persons}
    g.account_owner = {ac: own for ac, own in g.account_owner.items()
                       if ac.lower() not in dead and own in g.persons}

    g.pair_edges = {
        (a, b, kind): edge for (a, b, kind), edge in g.pair_edges.items()
        if a in g.persons and b in g.persons and frozenset((a, b)) not in _suppressed_pairs
    }
    g.place_edges = {
        (person, place): edge for (person, place), edge in g.place_edges.items()
        if person in g.persons and place in g.locations
    }

    adjacency = defaultdict(dict)
    for (a, b, _kind), edge in g.pair_edges.items():
        adjacency[a][b] = adjacency[a].get(b, 0) + edge["weight"]
        adjacency[b][a] = adjacency[b].get(a, 0) + edge["weight"]
    g.adjacency = adjacency

    # Dossier ke andar bhi hataye gaye device/account nahi dikhne chahiye
    for rec in g.persons.values():
        rec["phones"] = {ph for ph in rec["phones"] if ph.lower() not in dead}
        rec["accounts"] = {ac for ac in rec["accounts"] if ac.lower() not in dead}
        rec["call_log"] = [row for row in rec["call_log"]
                           if str(row["counterparty_phone"]).lower() not in dead
                           and str(row["own_phone"]).lower() not in dead]
        rec["txn_log"] = [row for row in rec["txn_log"]
                          if str(row["counterparty_account"]).lower() not in dead
                          and str(row["own_account"]).lower() not in dead]
    return g


def _classify(value):
    """Ek id kis tarah ka node hai."""
    value = _clean(value)
    if is_phone(value):
        return "phone"
    if is_account(value):
        return "bank"
    g = graph()
    if any(loc.lower() == value.lower() for loc in g.locations):
        return "location"
    return "person"


def find_entity(value):
    """Graph mein ye id kis naam se maujood hai (case-insensitive)."""
    g = graph()
    needle = _clean(value).lower()
    if not needle:
        return None, None
    for name in g.persons:
        if name.lower() == needle:
            return name, "person"
    for phone in g.phone_owner:
        if phone.lower() == needle:
            return phone, "phone"
    for account in g.account_owner:
        if account.lower() == needle:
            return account, "bank"
    for place in g.locations:
        if place.lower() == needle:
            return place, "location"
    return None, None


def remove_entity(value):
    """Entity (aur uske saare links) graph se hata do."""
    found, kind = find_entity(value)
    if not found:
        return None
    before = stats()
    with _lock:
        _suppressed_nodes.add(found)
    _save_state()
    after = stats(); graph()          # rebuild ho chuka hai
    return {
        "removed": found, "type": kind,
        "links_dropped": before["confirmed_links"] - after["confirmed_links"],
        "stats": after,
    }


def remove_link(a, b):
    """Do logon ke beech ka link hata do (dono nodes graph mein rehte hain)."""
    left, _ = find_entity(a)
    right, _ = find_entity(b)
    if not left or not right:
        return None
    with _lock:
        _suppressed_pairs.add(frozenset((left, right)))
    _save_state()
    graph(force=True)
    return {"removed_link": [left, right], "stats": stats()}


def restore_entity(value):
    """Hataya hua entity wapas le aao."""
    needle = _clean(value).lower()
    with _lock:
        match = next((x for x in _suppressed_nodes if x.lower() == needle), None)
        if match:
            _suppressed_nodes.discard(match)
    if not match:
        return None
    _save_state()
    graph(force=True)
    return {"restored": match, "stats": stats()}


def restore_all():
    """Saare removals undo -- graph wapas original + intake state mein."""
    with _lock:
        count = len(_suppressed_nodes) + len(_suppressed_pairs)
        _suppressed_nodes.clear()
        _suppressed_pairs.clear()
    _save_state()
    graph(force=True)
    return {"restored": count, "stats": stats()}


def undo_last_intake():
    """Aakhri upload poora undo."""
    with _lock:
        batch = _intake_batches.pop() if _intake_batches else None
    if batch is None:
        return None
    _save_state()
    graph(force=True)
    return {"undone": batch, "stats": stats()}


def manual_log():
    """
    Intake page ka ledger: manually add kiye gaye entities aur hataye gaye
    entities, taaki UI dono list kar sake with remove/restore buttons.
    """
    g = graph()
    added = []
    for index, batch in enumerate(_intake_batches, start=1):
        for name in batch.get("names", []):
            canonical = normalise_person(name)
            if canonical and canonical in g.persons:
                added.append({"id": canonical, "type": "person", "batch": index,
                              "links": len(g.adjacency.get(canonical, {}))})
        for phone in batch.get("phones", []):
            phone = _clean(phone)
            if phone in g.phone_owner:
                added.append({"id": phone, "type": "phone", "batch": index,
                              "owner": g.phone_owner[phone]})
        for account in batch.get("banks", []):
            account = _clean(account)
            if account in g.account_owner:
                added.append({"id": account, "type": "bank", "batch": index,
                              "owner": g.account_owner[account]})

    removed = [{"id": x, "type": _classify(x)} for x in sorted(_suppressed_nodes)]
    dropped_links = [sorted(pair) for pair in _suppressed_pairs]

    return {
        "added": added,
        "removed": removed,
        "removed_links": dropped_links,
        "batches": len(_intake_batches),
        "state": state_info(),
        "stats": stats(),
    }


# ------------------------------------------------------ STATE PERSISTENCE ---
# Ledger ab restart ke baad bhi zinda rehta hai.

def _state_payload():
    """Lock ke andar call karo -- current overlay ka snapshot."""
    return {
        "version": 1,
        "intake_batches": [dict(batch) for batch in _intake_batches],
        "suppressed_nodes": sorted(_suppressed_nodes),
        "suppressed_pairs": [sorted(pair) for pair in _suppressed_pairs],
    }


def _write_state(payload):
    """Atomic write -- aadha likha hua file kabhi nahi bachega."""
    temporary = STATE_PATH + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        os.replace(temporary, STATE_PATH)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass


def _state_file_mtime():
    try:
        return os.stat(STATE_PATH).st_mtime
    except OSError:
        return None


def _save_state():
    global _state_mtime
    with _lock:
        payload = _state_payload()
    _write_state(payload)
    _state_mtime = _state_file_mtime()   # apna hi write dobara load na ho


def _load_state():
    """Startup par purane edits wapas load karo. Corrupt file ko chup-chaap ignore."""
    global _state_mtime
    _state_mtime = _state_file_mtime()
    if not os.path.exists(STATE_PATH):
        return
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        return
    if not isinstance(saved, dict):
        return

    with _lock:
        _intake_batches.clear()
        for batch in saved.get("intake_batches") or []:
            if isinstance(batch, dict):
                _intake_batches.append({
                    "names": [x for x in (batch.get("names") or []) if _clean(x)],
                    "phones": [x for x in (batch.get("phones") or []) if _clean(x)],
                    "banks": [x for x in (batch.get("banks") or []) if _clean(x)],
                })
        _suppressed_nodes.clear()
        _suppressed_nodes.update(x for x in (saved.get("suppressed_nodes") or []) if _clean(x))
        _suppressed_pairs.clear()
        for pair in saved.get("suppressed_pairs") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                _suppressed_pairs.add(frozenset(_clean(x) for x in pair))


def reset_state():
    """Saare manual edits wipe -- graph wapas sirf source JSON jaisa."""
    with _lock:
        removed = len(_suppressed_nodes) + len(_suppressed_pairs)
        batches = len(_intake_batches)
        _intake_batches.clear()
        _suppressed_nodes.clear()
        _suppressed_pairs.clear()
        payload = _state_payload()
    _write_state(payload)
    graph(force=True)
    return {"cleared_uploads": batches, "cleared_removals": removed, "stats": stats()}


def state_info():
    return {
        "persisted": os.path.exists(STATE_PATH),
        "path": os.path.relpath(STATE_PATH, BASE_DIR),
        "uploads": len(_intake_batches),
        "removals": len(_suppressed_nodes) + len(_suppressed_pairs),
    }


_load_state()


# ------------------------------------------------------- PATH FINDING -------
# "Ye do log aapas mein kaise jude hain?" -- investigation ka sabse aam sawaal.
# Shortest evidence chain nikalta hai; barabar length ke paths mein se sabse
# strong evidence wala chunta hai.

def _full_adjacency(g, tower_min=DEFAULT_TOWER_MIN, include_devices=True):
    """Person + phone + account + location, sab ek hi traversable graph mein."""
    adj = defaultdict(list)

    for (a, b, kind), edge in g.pair_edges.items():
        detail = " | ".join(sorted(edge["labels"])[:2])
        adj[a].append((b, kind, edge["weight"], detail))
        adj[b].append((a, kind, edge["weight"], detail))

    for (person, place), edge in g.place_edges.items():
        if edge["weight"] >= tower_min:
            adj[person].append((place, "tower", edge["weight"], "%d pings" % edge["weight"]))
            adj[place].append((person, "tower", edge["weight"], "%d pings" % edge["weight"]))

    if include_devices:
        for phone, owner in g.phone_owner.items():
            adj[owner].append((phone, "owns_phone", 1, "Registered SIM"))
            adj[phone].append((owner, "owns_phone", 1, "Registered SIM"))
        for account, owner in g.account_owner.items():
            adj[owner].append((account, "owns_account", 1, "Bank account"))
            adj[account].append((owner, "owns_account", 1, "Bank account"))

    return adj


def shortest_path(start, end, include_devices=True, tower_min=DEFAULT_TOWER_MIN):
    """
    Do entities ke beech ka sabse chhota evidence chain.
    BFS se minimum hops, aur utne hi hops wale raaston mein se sabse strong.
    """
    g = graph()
    src, src_type = find_entity(start)
    dst, dst_type = find_entity(end)

    if not src:
        return {"found": False, "error": "Entity not found in graph: %s" % start}
    if not dst:
        return {"found": False, "error": "Entity not found in graph: %s" % end}
    if src == dst:
        return {"found": False, "error": "Both ends are the same entity."}

    adj = _full_adjacency(g, tower_min=tower_min, include_devices=include_devices)

    from collections import deque
    distance = {src: 0}
    strength = {src: 0.0}
    parent = {}
    queue = deque([src])

    while queue:
        current = queue.popleft()
        for neighbour, kind, weight, detail in adj.get(current, ()):
            step = distance[current] + 1
            if neighbour not in distance:
                distance[neighbour] = step
                strength[neighbour] = strength[current] + weight
                parent[neighbour] = (current, kind, weight, detail)
                queue.append(neighbour)
            elif distance[neighbour] == step and strength[current] + weight > strength[neighbour]:
                # Utne hi hops, lekin stronger evidence -> wahi raasta behtar hai
                strength[neighbour] = strength[current] + weight
                parent[neighbour] = (current, kind, weight, detail)

    if dst not in distance:
        return {"found": False, "error": "No evidence chain connects %s and %s." % (src, dst),
                "from": src, "to": dst}

    steps, node = [], dst
    while node != src:
        previous, kind, weight, detail = parent[node]
        steps.append({
            "from": previous,
            "to": node,
            "kind": kind,
            "label": EDGE_LABELS.get(kind, kind),
            "detail": detail,
            "weight": weight,
        })
        node = previous
    steps.reverse()

    node_types = {}
    for entry in [src, dst] + [s["to"] for s in steps]:
        _found, kind = find_entity(entry)
        node_types[entry] = kind

    return {
        "found": True,
        "from": src,
        "to": dst,
        "from_type": src_type,
        "to_type": dst_type,
        "hops": len(steps),
        "strength": round(strength[dst], 1),
        "nodes": [src] + [s["to"] for s in steps],
        "node_types": node_types,
        "steps": steps,
    }


def search_entities(term, limit=12):
    """Path finder ke input boxes ke liye simple autocomplete."""
    g = graph()
    needle = _clean(term).lower()
    if not needle:
        return []

    pools = [(sorted(g.persons), "person"), (sorted(g.phone_owner), "phone"),
             (sorted(g.account_owner), "bank"), (sorted(g.locations), "location")]
    starts, contains = [], []
    for pool, kind in pools:
        for value in pool:
            low = value.lower()
            if low.startswith(needle):
                starts.append({"id": value, "type": kind})
            elif needle in low:
                contains.append({"id": value, "type": kind})
    return (starts + contains)[:limit]


# ---------------------------------------------------------- TIMELINE --------
# Dataset mein har call ka timestamp aur har transfer ki date hai, lekin ab tak
# kahin dikhti nahi thi. Ye usko month-wise activity mein badalta hai --
# bade transfer se pehle call burst jaise pattern isi se dikhte hain.

def timeline(name=None, months=None):
    g = graph()

    target = None
    if name:
        target, _kind = find_entity(name)
        if not target or target not in g.persons:
            return None

    people = [target] if target else list(g.persons)
    buckets = defaultdict(lambda: {"calls": 0, "call_minutes": 0, "transfers": 0,
                                   "sent": 0.0, "received": 0.0})
    peaks = {"call_day": None, "largest_transfer": None}
    day_calls = defaultdict(int)
    biggest = None

    for person in people:
        rec = g.persons[person]

        for row in rec["call_log"]:
            # Global view mein har call sirf ek baar gine -- caller ki row se
            if not target and row["direction"] != "out":
                continue
            stamp = str(row.get("timestamp") or "")
            if len(stamp) < 7:
                continue
            buckets[stamp[:7]]["calls"] += 1
            buckets[stamp[:7]]["call_minutes"] += round(row["duration_sec"] / 60)
            day_calls[stamp[:10]] += 1

        for row in rec["txn_log"]:
            if not target and row["direction"] != "out":
                continue
            date = str(row.get("date") or "")
            if len(date) < 7:
                continue
            bucket = buckets[date[:7]]
            bucket["transfers"] += 1
            if row["direction"] == "out":
                bucket["sent"] += row["amount"]
            else:
                bucket["received"] += row["amount"]
            if biggest is None or row["amount"] > biggest["amount"]:
                biggest = {"amount": row["amount"], "date": date,
                           "direction": row["direction"], "person": person,
                           "counterparty": row["counterparty"]}

    if not buckets:
        return {"target": target, "points": [], "peaks": peaks, "totals": {}}

    ordered = sorted(buckets)
    if months:
        ordered = ordered[-int(months):]

    points = []
    for period in ordered:
        data = buckets[period]
        points.append({
            "period": period,
            "calls": data["calls"],
            "call_minutes": data["call_minutes"],
            "transfers": data["transfers"],
            "sent": round(data["sent"], 2),
            "received": round(data["received"], 2),
            "volume": round(data["sent"] + data["received"], 2),
        })

    if day_calls:
        busiest = max(day_calls.items(), key=lambda kv: kv[1])
        peaks["call_day"] = {"date": busiest[0], "calls": busiest[1]}
    peaks["largest_transfer"] = biggest

    return {
        "target": target,
        "points": points,
        "peaks": peaks,
        "totals": {
            "calls": sum(p["calls"] for p in points),
            "call_minutes": sum(p["call_minutes"] for p in points),
            "transfers": sum(p["transfers"] for p in points),
            "volume": round(sum(p["volume"] for p in points), 2),
            "span": "%s to %s" % (points[0]["period"], points[-1]["period"]) if points else "",
        },
    }
