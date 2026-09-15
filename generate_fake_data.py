"""
Synthetic case data generator for Kraken.

Writes firs.txt, cdr.csv and transactions.csv into sample_data/.
Needs faker:  ./venv/bin/pip install -r requirements-pipeline.txt

IMPORTANT: this only regenerates the RAW sources. sample_data/extracted_entities.json
(which the dashboard actually reads) is produced by ner_extractor.py, so run that
afterwards or the app will keep serving entities that no longer match these files.

    ./venv/bin/python generate_fake_data.py --force
    ./venv/bin/python ner_extractor.py
"""

import argparse
import csv
import os
import random
import sys

from faker import Faker

SEED = 42

# Dono RNG seed karna zaroori hai. Pehle sirf random.seed(42) tha, isliye phone
# aur account number to fixed rehte the lekin Faker ke naam har run par badal
# jate the -- yaani wahi number kisi aur insaan ka ban jata tha.
fake = Faker("en_IN")
Faker.seed(SEED)
random.seed(SEED)

OUT_DIR = "sample_data"
OUTPUT_FILES = ("firs.txt", "cdr.csv", "transactions.csv")
os.makedirs(OUT_DIR, exist_ok=True)

NUM_PERSONS = 40
NUM_FIRS = 60
NUM_CDR_RECORDS = 300
NUM_TXN_RECORDS = 150

RELATIONSHIP_PHRASES = ["co-accused", "along with", "in association with", "accomplice", "known associate of"]
CRIME_TYPES = ["financial fraud", "cyber fraud", "extortion", "cheating and forgery", "criminal conspiracy", "money laundering"]

def make_person_pool(n):
    people = []
    for _ in range(n):
        people.append({
            "name": fake.name(),
            "phone": "+91" + str(random.randint(7000000000, 9999999999)),
            "account": "XXXX" + str(random.randint(1000, 9999)),
            "address": fake.address().replace("\n", ", "),
        })
    return people

def generate_firs(people):
    firs = []
    for i in range(NUM_FIRS):
        complainant, accused, co_accused = random.sample(people, 3)
        date = fake.date_between(start_date="-2y", end_date="today").strftime("%d/%m/%Y")
        crime = random.choice(CRIME_TYPES)
        rel_phrase = random.choice(RELATIONSHIP_PHRASES)
        amount = random.randint(50000, 2000000)

        fir_text = (
            f"FIR No. {1000+i}/2025 dated {date}. "
            f"Complainant {complainant['name']} (contact {complainant['phone']}) reported "
            f"an incident of {crime}. As per investigation, accused {accused['name']}, "
            f"residing at {accused['address']}, {rel_phrase} {co_accused['name']} "
            f"(contact {co_accused['phone']}), committed the offence involving a sum of "
            f"Rs. {amount} transferred to account {accused['account']}."
        )
        firs.append(fir_text)

    with open(os.path.join(OUT_DIR, "firs.txt"), "w", encoding="utf-8") as f:
        f.write("\n\n".join(firs))

def generate_cdr(people):
    rows = []
    towers = [fake.city() for _ in range(10)]
    for _ in range(NUM_CDR_RECORDS):
        caller, receiver = random.sample(people, 2)
        timestamp = fake.date_time_between(start_date="-6M", end_date="now")
        rows.append({
            "caller_number": caller["phone"], "receiver_number": receiver["phone"],
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": random.randint(10, 900), "tower_location": random.choice(towers),
        })

    with open(os.path.join(OUT_DIR, "cdr.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

def generate_transactions(people):
    rows = []
    for _ in range(NUM_TXN_RECORDS):
        sender, receiver = random.sample(people, 2)
        date = fake.date_between(start_date="-1y", end_date="today")
        rows.append({
            "sender_account": sender["account"], "sender_name": sender["name"],
            "receiver_account": receiver["account"], "receiver_name": receiver["name"],
            "amount": random.randint(1000, 500000), "date": date.strftime("%Y-%m-%d"),
            "ifsc": fake.swift8(),
        })

    with open(os.path.join(OUT_DIR, "transactions.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

def main():
    parser = argparse.ArgumentParser(description="Generate synthetic case data for Kraken.")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing files in sample_data/")
    parser.add_argument("--seed", type=int, default=SEED,
                        help="random seed (default: %d)" % SEED)
    args = parser.parse_args()

    Faker.seed(args.seed)
    random.seed(args.seed)

    # Committed dataset ko chup-chaap overwrite karna sabse bada khatra tha
    existing = [f for f in OUTPUT_FILES if os.path.exists(os.path.join(OUT_DIR, f))]
    if existing and not args.force:
        sys.exit(
            "Refusing to overwrite existing data in %s/:\n"
            "    %s\n"
            "Re-run with --force if you really want to replace it.\n"
            "Note: the dashboard reads sample_data/extracted_entities.json, which is\n"
            "produced by ner_extractor.py -- regenerate it afterwards or the app will\n"
            "serve entities that no longer match these files."
            % (OUT_DIR, "\n    ".join(existing))
        )

    people_pool = make_person_pool(NUM_PERSONS)
    generate_firs(people_pool)
    generate_cdr(people_pool)
    generate_transactions(people_pool)

    print("Generated in %s/ (seed %d):" % (OUT_DIR, args.seed))
    print("  firs.txt          %d FIR reports" % NUM_FIRS)
    print("  cdr.csv           %d call records" % NUM_CDR_RECORDS)
    print("  transactions.csv  %d transfers" % NUM_TXN_RECORDS)
    print("  person pool       %d people" % NUM_PERSONS)
    print()
    print("Next: ./venv/bin/python ner_extractor.py")
    print("      (rebuilds sample_data/extracted_entities.json, which the app reads)")


if __name__ == "__main__":
    main()