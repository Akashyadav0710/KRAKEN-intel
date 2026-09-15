import json
import os
import sys

from neo4j import GraphDatabase

# Credentials environment se aate hain -- repo mein koi password commit nahi hota.
#   export NEO4J_URI="bolt://localhost:7687"
#   export NEO4J_USER="neo4j"
#   export NEO4J_PASSWORD="<your password>"
NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")

class GraphLoader:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def clear_database(self):
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def create_person(self, tx, name):
        tx.run("MERGE (p:Person {name: $name})", name=name)

    def create_location(self, tx, name):
        tx.run("MERGE (l:Location {name: $name})", name=name)

    def create_co_accused_link(self, tx, person_a, person_b, source):
        tx.run(
            """
            MERGE (a:Person {name: $person_a})
            MERGE (b:Person {name: $person_b})
            MERGE (a)-[r:CO_ACCUSED {source: $source}]->(b)
            """,
            person_a=person_a, person_b=person_b, source=source,
        )

    def create_call_edge(self, tx, from_phone, to_phone, timestamp, duration, tower):
        tx.run(
            """
            MERGE (a:PhoneNumber {number: $from_phone})
            MERGE (b:PhoneNumber {number: $to_phone})
            MERGE (a)-[r:CALLED {timestamp: $timestamp, duration_sec: $duration, tower: $tower}]->(b)
            """,
            from_phone=from_phone, to_phone=to_phone,
            timestamp=timestamp, duration=duration, tower=tower,
        )

    def create_transfer_edge(self, tx, from_acc, from_name, to_acc, to_name, amount, date):
        tx.run(
            """
            MERGE (a:Account {number: $from_acc, owner: $from_name})
            MERGE (b:Account {number: $to_acc, owner: $to_name})
            MERGE (a)-[r:TRANSFERRED_TO {amount: $amount, date: $date}]->(b)
            """,
            from_acc=from_acc, from_name=from_name,
            to_acc=to_acc, to_name=to_name,
            amount=amount, date=date,
        )

    # NAYA FUNCTION: Jo Phone aur Account ko real insaan se jodega
    def create_ownership_edge(self, tx, edge):
        if edge["type"] == "OWNS_PHONE":
            tx.run(
                """
                MERGE (p:Person {name: $person})
                MERGE (ph:PhoneNumber {number: $phone})
                MERGE (p)-[:OWNS_PHONE]->(ph)
                """,
                person=edge["person"], phone=edge["phone"]
            )
        elif edge["type"] == "OWNS_ACCOUNT":
            tx.run(
                """
                MERGE (p:Person {name: $person})
                MERGE (acc:Account {number: $account})
                MERGE (p)-[:OWNS_ACCOUNT]->(acc)
                """,
                person=edge["person"], account=edge["account"]
            )

    def load_all(self, payload):
        with self.driver.session() as session:
            for name in payload["nodes"]["persons"]:
                session.execute_write(self.create_person, name)

            for name in payload["nodes"]["locations"]:
                session.execute_write(self.create_location, name)

            for link in payload["edges"]["co_accused_links"]:
                session.execute_write(self.create_co_accused_link, link["person_a"], link["person_b"], link["source"])

            for call in payload["edges"]["calls"]:
                session.execute_write(
                    self.create_call_edge, call["from"], call["to"], call["timestamp"],
                    call["duration_sec"], call["tower_location"],
                )

            for txn in payload["edges"]["money_transfers"]:
                session.execute_write(
                    self.create_transfer_edge, txn["from"], txn["from_name"], txn["to"], txn["to_name"],
                    txn["amount"], txn["date"],
                )

            # NAYA LOGIC: Ownership links ko graph mein push karna
            if "ownership_links" in payload["edges"]:
                for link in payload["edges"]["ownership_links"]:
                    session.execute_write(self.create_ownership_edge, link)

if __name__ == "__main__":
    if not NEO4J_PASSWORD:
        sys.exit("NEO4J_PASSWORD is not set. Export it before running this loader:\n"
                 '    export NEO4J_PASSWORD="<your password>"')

    with open("sample_data/extracted_entities.json", "r", encoding="utf-8") as f:
        payload = json.load(f)

    loader = GraphLoader(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    print("Clearing old data...")
    loader.clear_database()
    print("Loading nodes and edges into Neo4j...")
    loader.load_all(payload)
    loader.close()

    print("\nDone! Open http://localhost:7474 and run:")
    print("  MATCH (n) RETURN n LIMIT 200")
    print("to see your criminal network graph.")