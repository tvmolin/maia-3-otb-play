import json
import tempfile
import unittest
from pathlib import Path

from ladder import K_FACTOR, LadderStore


class LadderStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "ladder.json"
        self.store = LadderStore(self.path)

    def match(
        self,
        result,
        maia_elo=800,
        player_color="white",
        match_id="match-1",
        rating_category="blitz",
    ):
        return {
            "match_id": match_id,
            "result": result,
            "maia_elo": maia_elo,
            "player_color": player_color,
            "rating_category": rating_category,
        }

    def test_equal_rating_win_and_loss_change_rating_by_twenty(self):
        win = self.store.record_match(self.match("1-0"))
        self.assertEqual(win["rating_change"], 20)
        self.assertEqual(win["player_elo_after"], 820)

        loss_store = LadderStore(Path(self.temp_dir.name) / "loss-ladder.json")
        loss = loss_store.record_match(self.match("0-1", match_id="match-2"))
        self.assertEqual(loss["rating_change"], -20)
        self.assertEqual(loss["player_elo_after"], 780)
        self.assertEqual(loss["k_factor"], K_FACTOR)

    def test_draw_changes_rating_by_expected_score(self):
        record = self.store.record_match(self.match("1/2-1/2", maia_elo=1000))

        self.assertEqual(record["score"], 0.5)
        self.assertEqual(record["outcome"], "draw")
        self.assertEqual(record["rating_change"], 10)

    def test_color_is_accounted_for_in_result(self):
        record = self.store.record_match(
            self.match("1-0", player_color="black")
        )

        self.assertEqual(record["outcome"], "loss")
        self.assertEqual(record["rating_change"], -20)

    def test_match_is_persisted_and_duplicate_recording_is_idempotent(self):
        match = self.match("1-0")
        record = self.store.record_match(match)

        self.assertEqual(self.store.record_match(match), record)
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["ratings"], {"blitz": 820, "rapid": 800})
        self.assertEqual(len(persisted["matches"]["blitz"]), 1)

        reloaded = LadderStore(self.path)
        self.assertEqual(reloaded.rating_for("blitz"), 820)
        self.assertEqual(reloaded.rating_for("rapid"), 800)
        self.assertEqual(reloaded.matches_for("blitz"), [record])

    def test_blitz_and_rapid_ratings_are_independent(self):
        self.store.record_match(self.match("1-0"))
        rapid = self.store.record_match(
            self.match("1-0", rating_category="rapid", match_id="rapid-1")
        )

        self.assertEqual(self.store.rating_for("blitz"), 820)
        self.assertEqual(self.store.rating_for("rapid"), 820)
        self.assertEqual(rapid["rating_category"], "rapid")
        self.assertEqual(len(self.store.matches_for("blitz")), 1)
        self.assertEqual(len(self.store.matches_for("rapid")), 1)

    def test_generated_usernames_are_playful_and_unique_to_match_history(self):
        first = self.store.generate_opponent_name()
        self.store.matches["rapid"].append({"opponent_name": first})
        second = self.store.generate_opponent_name()

        self.assertRegex(first, r"^[A-Z][A-Za-z]+[A-Z][A-Za-z]+\d{4}$")
        self.assertNotEqual(first, second)
        self.assertRegex(second, r"^[A-Z][A-Za-z]+[A-Z][A-Za-z]+\d{4}$")


if __name__ == "__main__":
    unittest.main()
