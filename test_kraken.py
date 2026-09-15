"""
KRAKEN TEST SUITE
=================
Sirf stdlib unittest -- koi extra dependency install nahi karni padti.

Chalane ke liye:
    ./venv/bin/python -m unittest test_kraken -v
    ./venv/bin/python test_kraken.py

Ye tests un cheezon ko pakadte hain jo demo se pehle chup-chaap toot sakti hain:
NER noise filter, graph counts, community detection ka determinism, path finding,
manual edits ka round-trip, aur API contract jo templates use karte hain.
"""

import json
import os
import unittest

import kraken_data as kd


# ---------------------------------------------------------------------------
# Tests operator ke asli ledger ko chhedte nahi hain: state file ko backup
# karke test ke baad wapas restore kar dete hain.
# ---------------------------------------------------------------------------
_SAVED_STATE = None


def setUpModule():
    global _SAVED_STATE
    if os.path.exists(kd.STATE_PATH):
        with open(kd.STATE_PATH, encoding="utf-8") as handle:
            _SAVED_STATE = handle.read()


def tearDownModule():
    if _SAVED_STATE is None:
        if os.path.exists(kd.STATE_PATH):
            os.remove(kd.STATE_PATH)
    else:
        with open(kd.STATE_PATH, "w", encoding="utf-8") as handle:
            handle.write(_SAVED_STATE)
    kd._load_state()
    kd.graph(force=True)


class NoiseFilterTests(unittest.TestCase):
    """NER ka kachra person list mein nahi ghusna chahiye."""

    def test_phone_number_is_not_a_person(self):
        self.assertIsNone(kd.normalise_person("+918947382419"))

    def test_date_is_not_a_person(self):
        self.assertIsNone(kd.normalise_person("06/09/2024"))

    def test_account_number_is_not_a_person(self):
        self.assertIsNone(kd.normalise_person("XXXX1488"))
        self.assertIsNone(kd.normalise_person("account XXXX5554"))

    def test_address_with_pincode_is_not_a_person(self):
        graph = kd.graph()
        self.assertNotIn("Jaipur 312984", graph.persons)
        self.assertNotIn("Meerut-612147", graph.persons)

    def test_role_prefix_is_stripped(self):
        self.assertEqual(kd.normalise_person("Complainant Timothy Dutt"), "Timothy Dutt")
        self.assertEqual(kd.normalise_person("accused Kritika Buch"), "Kritika Buch")

    def test_document_artifacts_are_not_people(self):
        # spaCy naye data par "FIR No" ko PERSON tag kar deta hai aur wo
        # influence ranking mein #1 aa jata tha.
        for junk in ("FIR No", "FIR No. 1004/2025", "Police Station", "IPC 420",
                     "Rs 50000", "Section 66D", "Case No", "Dated 12/03/2025"):
            self.assertIsNone(kd.normalise_person(junk), junk)

    def test_real_name_survives(self):
        self.assertEqual(kd.normalise_person("  Kritika  Buch "), "Kritika Buch")

    def test_no_person_name_contains_digits(self):
        offenders = [n for n in kd.graph().persons if any(c.isdigit() for c in n)]
        self.assertEqual(offenders, [], "person names must never contain digits")


class GraphBuildTests(unittest.TestCase):
    """Graph asli dataset se banta hai, hardcoded demo data se nahi."""

    @classmethod
    def setUpClass(cls):
        cls.stats = kd.stats()
        cls.graph = kd.graph()

    def test_dataset_is_the_source(self):
        self.assertTrue(self.stats["source"], "no source file recorded")
        self.assertIn("extracted_entities.json", self.stats["source"][0])

    def test_graph_is_not_empty(self):
        self.assertGreater(self.stats["persons"], 30)
        self.assertGreater(self.stats["confirmed_links"], 300)

    def test_every_edge_connects_known_people(self):
        for (a, b, _kind) in self.graph.pair_edges:
            self.assertIn(a, self.graph.persons)
            self.assertIn(b, self.graph.persons)

    def test_no_self_links(self):
        for (a, b, _kind) in self.graph.pair_edges:
            self.assertNotEqual(a, b)

    def test_adjacency_is_symmetric(self):
        for person, neighbours in self.graph.adjacency.items():
            for other, weight in neighbours.items():
                self.assertEqual(self.graph.adjacency[other][person], weight)

    def test_payload_has_no_orphan_edges(self):
        payload = kd.graph_payload()
        ids = {n["id"] for n in payload["nodes"]}
        for edge in payload["edges"]:
            self.assertIn(edge["source"], ids)
            self.assertIn(edge["target"], ids)

    def test_node_types_are_renderable(self):
        # network.html sirf ye 4 types samajhta hai
        allowed = {"person", "location", "phone", "bank"}
        for node in kd.graph_payload()["nodes"]:
            self.assertIn(node["type"], allowed)

    def test_stats_match_the_payload(self):
        payload = kd.graph_payload()
        intel = [e for e in payload["edges"]
                 if e["kind"] in ("co_accused", "call", "money", "intake", "tower")]
        self.assertEqual(self.stats["confirmed_links"], len(intel),
                         "dashboard counter must equal the links actually drawn")


class PredictionTests(unittest.TestCase):

    def test_predictions_reference_real_people(self):
        graph = kd.graph()
        for prediction in kd.predictions(15):
            self.assertIn(prediction["source"], graph.persons)
            self.assertIn(prediction["target"], graph.persons)

    def test_confidence_is_a_percentage(self):
        for prediction in kd.predictions(15):
            self.assertGreaterEqual(prediction["confidence"], 0)
            self.assertLessEqual(prediction["confidence"], 99)

    def test_predictions_are_not_already_confirmed(self):
        graph = kd.graph()
        for prediction in kd.predictions(15):
            pair = {prediction["source"], prediction["target"]}
            for (a, b, _kind) in graph.pair_edges:
                self.assertNotEqual({a, b}, pair,
                                    "a confirmed link must not be offered as a prediction")

    def test_reasoning_is_populated(self):
        for prediction in kd.predictions(15):
            self.assertTrue(prediction["reasoning"].strip())


class CommunityTests(unittest.TestCase):
    """Syndicates demo ke beech reshuffle nahi hone chahiye."""

    def test_detection_is_deterministic(self):
        runs = []
        for _ in range(3):
            result = kd.syndicates()
            runs.append(tuple(sorted(
                tuple(sorted(m["name"] for m in s["members"])) for s in result["syndicates"])))
        self.assertEqual(len(set(runs)), 1, "Louvain output changed between runs")

    def test_every_member_has_a_role(self):
        for syndicate in kd.syndicates()["syndicates"]:
            for member in syndicate["members"]:
                self.assertTrue(member["role"])
                self.assertIsInstance(member["level"], int)

    def test_each_syndicate_has_exactly_one_boss(self):
        for syndicate in kd.syndicates()["syndicates"]:
            bosses = [m for m in syndicate["members"] if m["level"] == 0]
            self.assertEqual(len(bosses), 1)
            self.assertEqual(bosses[0]["name"], syndicate["boss"])

    def test_members_are_disjoint(self):
        seen = set()
        for syndicate in kd.syndicates()["syndicates"]:
            names = {m["name"] for m in syndicate["members"]}
            self.assertFalse(names & seen, "a person appears in two syndicates")
            seen |= names


class DossierTests(unittest.TestCase):

    def test_dossiers_cover_every_person(self):
        self.assertEqual(len(kd.dossiers()), len(kd.graph().persons))

    def test_scores_are_in_range(self):
        for dossier in kd.dossiers():
            self.assertGreaterEqual(dossier["score"], 60)
            self.assertLessEqual(dossier["score"], 99)

    def test_contact_details_are_real_not_generated(self):
        graph = kd.graph()
        for dossier in kd.dossiers():
            for phone in dossier["phones"]:
                self.assertIn(phone, graph.phone_owner)
            for account in dossier["accounts"]:
                self.assertIn(account, graph.account_owner)

    def test_entity_record_matches_the_graph(self):
        name = kd.dossiers()[0]["name"]
        entity = kd.entity(name)
        self.assertIsNotNone(entity)
        self.assertEqual(len(entity["associates"]), len(kd.graph().adjacency[name]))
        for associate in entity["associates"]:
            self.assertTrue(associate["reason"].strip())

    def test_unknown_entity_returns_none(self):
        self.assertIsNone(kd.entity("Nobody At All"))


class PathFindingTests(unittest.TestCase):

    def test_path_between_two_known_people(self):
        people = [d["name"] for d in kd.dossiers()[:2]]
        result = kd.shortest_path(people[0], people[1])
        self.assertTrue(result["found"])
        self.assertGreaterEqual(result["hops"], 1)
        self.assertEqual(result["nodes"][0], people[0])
        self.assertEqual(result["nodes"][-1], people[1])

    def test_every_step_is_a_real_edge(self):
        people = [d["name"] for d in kd.dossiers()[:2]]
        result = kd.shortest_path(people[0], people[1])
        graph = kd.graph()
        for step in result["steps"]:
            pair = {step["from"], step["to"]}
            real = (
                any({a, b} == pair for (a, b, _k) in graph.pair_edges)
                or any({p, place} == pair for (p, place) in graph.place_edges)
                or any({owner, phone} == pair for phone, owner in graph.phone_owner.items())
                or any({owner, acct} == pair for acct, owner in graph.account_owner.items())
            )
            self.assertTrue(real, "path invented an edge: %s" % step)

    def test_path_chain_is_continuous(self):
        people = [d["name"] for d in kd.dossiers()[:2]]
        result = kd.shortest_path(people[0], people[1])
        for first, second in zip(result["steps"], result["steps"][1:]):
            self.assertEqual(first["to"], second["from"])

    def test_missing_entity_is_reported(self):
        result = kd.shortest_path("Nobody At All", kd.dossiers()[0]["name"])
        self.assertFalse(result["found"])
        self.assertIn("not found", result["error"].lower())

    def test_same_entity_is_rejected(self):
        name = kd.dossiers()[0]["name"]
        self.assertFalse(kd.shortest_path(name, name)["found"])

    def test_search_finds_a_known_person(self):
        name = kd.dossiers()[0]["name"]
        hits = [h["id"] for h in kd.search_entities(name[:4])]
        self.assertIn(name, hits)


class TimelineTests(unittest.TestCase):

    def test_global_timeline_is_ordered(self):
        points = kd.timeline()["points"]
        self.assertTrue(points)
        self.assertEqual([p["period"] for p in points],
                         sorted(p["period"] for p in points))

    def test_totals_match_the_points(self):
        data = kd.timeline()
        self.assertEqual(data["totals"]["calls"], sum(p["calls"] for p in data["points"]))
        self.assertEqual(data["totals"]["transfers"], sum(p["transfers"] for p in data["points"]))

    def test_global_volume_matches_stats(self):
        self.assertAlmostEqual(kd.timeline()["totals"]["volume"],
                               kd.stats()["money_volume"], places=0)

    def test_per_person_timeline(self):
        name = kd.dossiers()[0]["name"]
        data = kd.timeline(name)
        self.assertEqual(data["target"], name)

    def test_unknown_person_returns_none(self):
        self.assertIsNone(kd.timeline("Nobody At All"))


class ManualEditTests(unittest.TestCase):
    """Add / remove / restore ka round-trip baseline par wapas aana chahiye."""

    def setUp(self):
        self.baseline = kd.stats()

    def tearDown(self):
        kd.reset_state()

    def test_add_then_undo(self):
        kd.add_intake(["Test Subject Alpha"], ["+919999900001"], ["XXXX9001"])
        after = kd.stats()
        self.assertEqual(after["persons"], self.baseline["persons"] + 1)
        self.assertEqual(after["phones"], self.baseline["phones"] + 1)
        kd.undo_last_intake()
        self.assertEqual(kd.stats()["persons"], self.baseline["persons"])

    def test_remove_then_restore(self):
        name = kd.dossiers()[0]["name"]
        removed = kd.remove_entity(name)
        self.assertIsNotNone(removed)
        self.assertGreater(removed["links_dropped"], 0)
        self.assertNotIn(name, kd.graph().persons)
        kd.restore_entity(name)
        self.assertIn(name, kd.graph().persons)
        self.assertEqual(kd.stats()["confirmed_links"], self.baseline["confirmed_links"])

    def test_removing_a_person_drops_their_edges(self):
        name = kd.dossiers()[0]["name"]
        kd.remove_entity(name)
        for (a, b, _kind) in kd.graph().pair_edges:
            self.assertNotEqual(a, name)
            self.assertNotEqual(b, name)

    def test_empty_intake_is_rejected(self):
        # Khaali payload par kuch merge nahi hona chahiye
        self.assertIsNone(kd.add_intake([], [], []))
        self.assertIsNone(kd.add_intake(["  "], [""], []))
        self.assertEqual(kd.stats()["persons"], self.baseline["persons"])

    def test_duplicate_intake_adds_nothing_new(self):
        existing = kd.dossiers()[0]["name"]
        before = kd.stats()
        kd.add_intake([existing], [], [])
        after = kd.stats()
        self.assertEqual(after["persons"], before["persons"],
                         "re-adding a known person must not create a duplicate")

    def test_remove_unknown_entity_returns_none(self):
        self.assertIsNone(kd.remove_entity("Nobody At All"))

    def test_reset_returns_to_source(self):
        kd.add_intake(["Test Subject Beta"], [], [])
        kd.remove_entity(kd.dossiers()[0]["name"])
        kd.reset_state()
        self.assertEqual(kd.stats()["persons"], self.baseline["persons"])
        self.assertEqual(kd.stats()["confirmed_links"], self.baseline["confirmed_links"])

    def test_state_file_is_written(self):
        kd.add_intake(["Test Subject Gamma"], [], [])
        self.assertTrue(os.path.exists(kd.STATE_PATH))
        with open(kd.STATE_PATH, encoding="utf-8") as handle:
            saved = json.load(handle)
        names = [n for batch in saved["intake_batches"] for n in batch["names"]]
        self.assertIn("Test Subject Gamma", names)


class ApiContractTests(unittest.TestCase):
    """Templates in fields par depend karte hain -- rename hua to test fail hoga."""

    def test_graph_payload_shape(self):
        payload = kd.graph_payload()
        for node in payload["nodes"][:5]:
            self.assertEqual(set(node), {"id", "type"})
        for edge in payload["edges"][:5]:
            self.assertLessEqual({"source", "target", "type", "kind", "weight", "label"}, set(edge))

    def test_target_fields_used_by_templates(self):
        needed = {"id", "name", "score", "color", "badge", "phones", "accounts"}
        self.assertLessEqual(needed, set(kd.targets(1)[0]))

    def test_prediction_fields_used_by_templates(self):
        needed = {"source", "target", "confidence", "reasoning"}
        self.assertLessEqual(needed, set(kd.predictions(1)[0]))

    def test_syndicate_fields_used_by_templates(self):
        needed = {"id", "name", "boss", "size", "members", "cohesion",
                  "dominant_trail", "territory", "turnover", "tier"}
        self.assertLessEqual(needed, set(kd.syndicates()["syndicates"][0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
