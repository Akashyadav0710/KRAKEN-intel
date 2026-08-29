import random
import csv
import os
from datetime import datetime, timedelta
from faker import Faker

fake = Faker("en_IN")  
random.seed(42)

OUT_DIR = "sample_data"
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

if __name__ == "__main__":
    people_pool = make_person_pool(NUM_PERSONS)
    generate_firs(people_pool)
    generate_cdr(people_pool)
    generate_transactions(people_pool)