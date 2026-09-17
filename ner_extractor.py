import re
import csv
import json
import spacy

nlp = spacy.load("en_core_web_sm")

PHONE_PATTERN = re.compile(r"\+91\d{10}")
ACCOUNT_PATTERN = re.compile(r"XXXX\d{4}")
FIR_NUMBER_PATTERN = re.compile(r"FIR No\.\s*\d+/\d{4}")
RELATIONSHIP_KEYWORDS = ["co-accused", "along with", "in association with", "accomplice", "known associate of"]

# "residing at <address>, <relationship phrase>" -- is clause ke andar jo kuch
# hai wo pata hai, insaan nahi. spaCy address ke tukdon ko PERSON tag kar deta
# tha ("Berhampore", "Kara"), aur phir neeche wala contact-regex un tukdon par
# asli accused ka phone/account chipka deta tha.
ADDRESS_SPAN_RE = re.compile(
    r"residing at\s+(.*?),\s*(?:%s)\b" % "|".join(re.escape(k) for k in RELATIONSHIP_KEYWORDS),
    re.I | re.S,
)


def _address_spans(text_block):
    return [m.span(1) for m in ADDRESS_SPAN_RE.finditer(text_block)]


def _inside(span, spans):
    start, end = span
    return any(s <= start and end <= e for s, e in spans)


def extract_from_fir_text(text_block):
    doc = nlp(text_block)
    addr_spans = _address_spans(text_block)

    persons, locations = [], []
    for ent in doc.ents:
        span = (ent.start_char, ent.end_char)
        if ent.label_ == "PERSON":
            # Pata ke andar ka naam jagah hai, aadmi nahi.
            (locations if _inside(span, addr_spans) else persons).append(ent.text)
        elif ent.label_ in ("GPE", "LOC", "FAC"):
            locations.append(ent.text)
    phones = PHONE_PATTERN.findall(text_block)
    accounts = ACCOUNT_PATTERN.findall(text_block)
    fir_no = FIR_NUMBER_PATTERN.findall(text_block)

    relationship = None
    ownership_edges = [] 

    for keyword in RELATIONSHIP_KEYWORDS:
        if keyword in text_block and len(persons) >= 2:
            relationship = {
                "person_a": persons[0], "person_b": persons[1],
                "type": keyword, "source": fir_no[0] if fir_no else "unknown_fir",
            }
            break

    for person in persons:
        phone_match = re.search(re.escape(person) + r".*?contact\s*(\+91\d{10})", text_block)
        if phone_match:
            ownership_edges.append({"type": "OWNS_PHONE", "person": person, "phone": phone_match.group(1)})
        acc_match = re.search(re.escape(person) + r".*?account\s*(XXXX\d{4})", text_block)
        if acc_match:
            ownership_edges.append({"type": "OWNS_ACCOUNT", "person": person, "account": acc_match.group(1)})

    return {
        "fir_number": fir_no[0] if fir_no else None,
        "persons": list(set(persons)), "locations": list(set(locations)),
        "phones": list(set(phones)), "accounts": list(set(accounts)),
        "relationship": relationship, "ownership_edges": ownership_edges
    }

def process_firs(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()
    fir_blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
    return [extract_from_fir_text(block) for block in fir_blocks]

def process_cdr(filepath):
    edges = []
    with open(filepath, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            edges.append({
                "type": "call", 
                "from": row["caller_number"], 
                "to": row["receiver_number"],
                "timestamp": row["timestamp"],
                "duration_sec": row["duration_sec"],
                "tower_location": row["tower_location"]
            })
    return edges

def process_transactions(filepath):
    edges = []
    with open(filepath, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            edges.append({
                "type": "money_transfer", 
                "from": row["sender_account"], 
                "from_name": row["sender_name"],
                "to": row["receiver_account"], 
                "to_name": row["receiver_name"],
                "amount": row["amount"],
                "date": row["date"]
            })
    return edges

def build_graph_payload():
    fir_data = process_firs("sample_data/firs.txt")
    call_edges = process_cdr("sample_data/cdr.csv")
    txn_edges = process_transactions("sample_data/transactions.csv")

    person_nodes, location_nodes = set(), set()
    fir_relationships, ownership_links = [], []

    for record in fir_data:
        person_nodes.update(record["persons"])
        location_nodes.update(record["locations"])
        if record["relationship"] and record["relationship"]["person_b"]:
            fir_relationships.append(record["relationship"])
        if record.get("ownership_edges"):
            ownership_links.extend(record["ownership_edges"])

    return {
        "nodes": {"persons": sorted(person_nodes), "locations": sorted(location_nodes)},
        "edges": {
            "co_accused_links": fir_relationships, "calls": call_edges,
            "money_transfers": txn_edges, "ownership_links": ownership_links
        }
    }

if __name__ == "__main__":
    payload = build_graph_payload()
    with open("sample_data/extracted_entities.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print("Extraction complete -> sample_data/extracted_entities.json")