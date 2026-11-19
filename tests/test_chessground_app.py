import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chess

from chessground_app import TIME_CONTROLS, create_app
from maia_game import GameSession


class FakeEngine:
    def __init__(self, moves=None, error=None):
        self.moves = list(moves or [])
        self.error = error
        self.configured = []

    def configure(self, options):
        self.configured.append(options)

    def play(self, board, limit, info=None):
        if self.error:
            raise self.error
        move = self.moves.pop(0) if self.moves else "e5"
        return SimpleNamespace(
            move=board.parse_san(move),
            info={"string": "policy_entropy 0.5"},
        )


class ChessgroundAppTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.player_data_path = Path(self.temp_dir.name) / "ladder.json"
        self.client = create_app(
            self.engine,
            player_data_path=self.player_data_path,
        ).test_client()
        self.random_patches = (
            patch("chessground_app.random.choice", return_value=chess.WHITE),
            patch("chessground_app.random.randint", return_value=820),
            patch("chessground_app.random.uniform", return_value=0.7),
        )
        for random_patch in self.random_patches:
            random_patch.start()

    def tearDown(self):
        for random_patch in reversed(self.random_patches):
            random_patch.stop()

    def wait_for_maia(self, client=None):
        client = client or self.client
        for _ in range(500):
            state = client.get("/api/state").json
            if not state["maia_thinking"]:
                return state
            threading.Event().wait(0.01)
        self.fail("Maia did not finish thinking")

    def test_initial_state_includes_position_and_no_legal_player_moves(self):
        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["fen"],
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
        )
        self.assertFalse(response.json["can_move"])
        self.assertEqual(response.json["destinations"], {})

    def profile_data(self, response):
        body = response.get_data(as_text=True)
        bootstrap = body.split("window.__PROFILE_DATA__ = ", 1)[1]
        return json.JSONDecoder().raw_decode(bootstrap)[0]

    def test_game_page_loads_react_entrypoint(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="root"', response.data)
        self.assertIn(b"assets/app.js", response.data)

    def test_available_clocks_are_grouped_into_blitz_and_rapid(self):
        self.assertEqual(
            TIME_CONTROLS,
            {
                "blitz": {"3+2": (180, 2), "5+5": (300, 5)},
                "rapid": {"15+10": (900, 10), "20+10": (1200, 10)},
            },
        )

    def test_profile_bootstraps_empty_rating_chart_and_match_history(self):
        response = self.client.get("/profile")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Player profile", response.data)
        self.assertIn(b"assets/profile.js", response.data)
        data = self.profile_data(response)
        self.assertEqual(data["category"], "blitz")
        self.assertEqual(data["playerElo"], 800)
        self.assertEqual(data["matches"], [])
        self.assertEqual([point["rating"] for point in data["ratingPoints"]], [800])

    def test_profile_bootstraps_rating_progress_and_newest_match_first(self):
        ladder = self.client.application.config["CHESSGROUND_LADDER"]
        ladder.record_match(
            {
                "match_id": "older",
                "finished_at": "2026-11-03T12:00:00Z",
                "result": "1-0",
                "player_color": "white",
                "rating_category": "blitz",
                "maia_elo": 800,
                "temperature": 0.7,
                "time_control": "5+5",
                "plies": 20,
                "pgn_file": "older.pgn",
            }
        )
        ladder.record_match(
            {
                "match_id": "newer",
                "finished_at": "2026-11-04T12:00:00Z",
                "result": "1-0",
                "player_color": "black",
                "rating_category": "blitz",
                "opponent_name": "TacticalLlama4821",
                "maia_elo": 900,
                "temperature": 0.8,
                "time_control": "20+10",
                "plies": 30,
                "pgn_file": "newer.pgn",
                "pgn": "1. e4 e5 1-0",
            }
        )
        ladder.record_match(
            {
                "match_id": "draw",
                "finished_at": "2026-11-05T12:00:00Z",
                "result": "1/2-1/2",
                "player_color": "white",
                "rating_category": "blitz",
                "opponent_name": "DrawOpponent123",
                "maia_elo": 800,
                "temperature": 0.7,
                "time_control": "3+2",
                "plies": 40,
                "pgn_file": "draw.pgn",
            }
        )

        response = self.client.get("/profile")

        self.assertEqual(response.status_code, 200)
        data = self.profile_data(response)
        self.assertEqual([match["match_id"] for match in data["matches"]], [
            "draw", "newer", "older",
        ])
        newer = data["matches"][1]
        self.assertEqual(newer["opponent_name"], "TacticalLlama4821")
        self.assertEqual(newer["maia_elo"], 900)
        self.assertEqual(newer["pgn"], "1. e4 e5 1-0")
        self.assertEqual(newer["finished_at"], "2026-11-04T12:00:00Z")
        self.assertEqual([point["rating"] for point in data["ratingPoints"]], [
            800, 820, 805, 805,
        ])
        white = data["colorStats"]["white"]
        black = data["colorStats"]["black"]
        self.assertEqual((white["wins"], white["draws"], white["losses"]), (1, 1, 0))
        self.assertEqual((white["win_percentage"], white["score_percentage"]), (50, 75))
        self.assertEqual((black["wins"], black["draws"], black["losses"]), (0, 0, 1))
        self.assertEqual((black["win_percentage"], black["score_percentage"]), (0, 0))
        rapid_response = self.client.get("/profile?category=rapid")
        self.assertEqual(rapid_response.status_code, 200)
        rapid_data = self.profile_data(rapid_response)
        self.assertEqual(rapid_data["category"], "rapid")
        self.assertEqual(rapid_data["matches"], [])

    def test_profile_reads_legacy_matches_without_piece_color(self):
        ladder = self.client.application.config["CHESSGROUND_LADDER"]
        ladder.record_match(
            {
                "match_id": "legacy",
                "finished_at": "2026-11-04T12:00:00Z",
                "result": "1-0",
                "player_color": "white",
                "rating_category": "blitz",
                "maia_elo": 800,
                "temperature": 0.7,
                "time_control": "5+5",
                "plies": 20,
                "pgn_file": "legacy.pgn",
            }
        )
        saved_data = json.loads(self.player_data_path.read_text(encoding="utf-8"))
        del saved_data["matches"]["blitz"][0]["player_color"]
        self.player_data_path.write_text(
            json.dumps(saved_data, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.player_data_path.parent / "legacy.pgn").write_text(
            '[Event "Legacy match"]\n\n1. e4 e5 1-0\n',
            encoding="utf-8",
        )
        original_save = self.player_data_path.read_bytes()

        legacy_client = create_app(
            FakeEngine(),
            player_data_path=self.player_data_path,
        ).test_client()
        response = legacy_client.get("/profile")

        self.assertEqual(response.status_code, 200)
        data = self.profile_data(response)
        self.assertNotIn("player_color", data["matches"][0])
        self.assertEqual(data["matches"][0]["pgn"], '[Event "Legacy match"]\n\n1. e4 e5 1-0')
        self.assertEqual(data["colorStats"]["white"]["games"], 0)
        self.assertEqual(data["colorStats"]["black"]["games"], 0)
        self.assertEqual(self.player_data_path.read_bytes(), original_save)

    def test_delete_match_recalculates_rating_and_removes_local_game_files(self):
        ladder = self.client.application.config["CHESSGROUND_LADDER"]
        first_pgn = self.player_data_path.parent / "first.pgn"
        second_pgn = self.player_data_path.parent / "second.pgn"
        first_pgn.write_text("1. e4 e5 1-0\n", encoding="utf-8")
        second_pgn.write_text("1. d4 d5 0-1\n", encoding="utf-8")
        first_sidecar = first_pgn.with_suffix(".pgn.lichess.json")
        first_sidecar.write_text('{"status":"failed"}\n', encoding="utf-8")
        ladder.record_match(
            {
                "match_id": "first",
                "result": "1-0",
                "player_color": "white",
                "rating_category": "blitz",
                "maia_elo": 800,
                "pgn_file": first_pgn.name,
                "pgn": "1. e4 e5 1-0",
            }
        )
        ladder.record_match(
            {
                "match_id": "second",
                "result": "0-1",
                "player_color": "white",
                "rating_category": "blitz",
                "maia_elo": 800,
                "pgn_file": second_pgn.name,
                "pgn": "1. d4 d5 0-1",
            }
        )
        self.client.post("/api/find-match", json={"time_control": "3+2"})
        blocked = self.client.delete("/api/matches/blitz/first")
        self.assertEqual(blocked.status_code, 409)
        self.client.post("/api/new-game")

        response = self.client.delete("/api/matches/blitz/first")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["rating"], 780)
        self.assertFalse(first_pgn.exists())
        self.assertFalse(first_sidecar.exists())
        self.assertTrue(second_pgn.exists())
        self.assertEqual(ladder.rating_for("blitz"), 780)
        self.assertEqual(
            ladder.matches_for("blitz")[0]["player_elo_before"],
            800,
        )

        data = self.profile_data(self.client.get("/profile"))
        self.assertEqual(data["playerElo"], 780)
        self.assertEqual([match["match_id"] for match in data["matches"]], ["second"])
        self.assertEqual([point["rating"] for point in data["ratingPoints"]], [800, 780])
        self.assertEqual(data["colorStats"]["white"]["losses"], 1)
        self.assertEqual(self.client.delete("/api/matches/blitz/first").status_code, 404)
        self.assertEqual(self.client.delete("/api/matches/invalid/second").status_code, 400)

    def test_start_exposes_legal_destinations(self):
        response = self.client.post(
            "/api/find-match", json={"time_control": "5+5"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["state"]["can_move"])
        self.assertIn("e4", response.json["state"]["destinations"]["e2"])
        self.assertEqual(response.json["state"]["time_control"], "5+5")
        self.assertEqual(response.json["state"]["rating_category"], "blitz")
        self.assertEqual(response.json["state"]["player_elo"], 800)
        self.assertEqual(response.json["state"]["maia_elo"], 820)
        self.assertNotIn("engine_maia_elo", response.json["state"])
        self.assertEqual(response.json["state"]["maia_temperature"], 0.7)
        self.assertRegex(
            response.json["state"]["maia_name"],
            r"^[A-Z][A-Za-z]+[A-Z][A-Za-z]+\d{4}$",
        )
        self.assertEqual(
            self.engine.configured[-1],
            {"SelfElo": 620, "OppoElo": 800, "Temperature": "0.7"},
        )
        self.assertEqual(
            response.json["state"]["clock"]["remaining_ms"],
            {"white": 300_000, "black": 300_000},
        )

    def test_start_from_fen_uses_side_to_move_and_unrated_time_control(self):
        self.engine.moves = ["e4"]
        with patch.object(GameSession, "wait_for_maia_think"):
            response = self.client.post(
                "/api/start-from-fen",
                json={
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1",
                    "time_control": "20+10",
                },
            )

        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
        self.assertEqual(state["player_color"], "black")
        self.assertEqual(state["time_control"], "20+10")
        self.assertEqual(state["rating_category"], "rapid")
        self.assertTrue(state["unrated_mode"])
        self.assertEqual(state["history"], [])
        self.assertTrue(state["can_move"])
        self.assertEqual(state["clock"]["running_color"], "black")
        self.assertEqual(state["clock"]["base_seconds"], 1_200)
        self.assertLessEqual(state["clock"]["remaining_ms"]["white"], 1_200_000)
        self.assertLessEqual(state["clock"]["remaining_ms"]["black"], 1_200_000)

    def test_start_from_fen_rejects_invalid_or_finished_positions(self):
        invalid_fen = self.client.post(
            "/api/start-from-fen",
            json={"fen": "not a FEN", "time_control": "3+2"},
        )
        finished_position = self.client.post(
            "/api/start-from-fen",
            json={
                "fen": "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1",
                "time_control": "3+2",
            },
        )

        self.assertEqual(invalid_fen.status_code, 400)
        self.assertIn("FEN", invalid_fen.json["error"])
        self.assertEqual(finished_position.status_code, 400)
        self.assertIn("game in progress", finished_position.json["error"])

    def test_find_match_randomizes_color_and_maia_settings_on_the_server(self):
        self.engine.moves = ["e4"]
        with (
            patch("chessground_app.random.choice", return_value=chess.BLACK) as color_picker,
            patch("chessground_app.random.randint", return_value=900) as rating_picker,
            patch("chessground_app.random.uniform", return_value=0.9) as temperature_picker,
            patch.object(GameSession, "wait_for_maia_think"),
        ):
            response = self.client.post(
                "/api/find-match",
                json={
                    "maia_elo": 0,
                    "player_elo": 5000,
                    "temperature": 5,
                    "color": "random",
                    "time_control": "3+2",
                },
            )

        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
        self.assertEqual(state["player_color"], "black")
        self.assertEqual(state["player_elo"], 800)
        self.assertEqual(state["maia_elo"], 900)
        self.assertEqual(state["maia_temperature"], 0.9)
        self.assertRegex(state["maia_name"], r"^[A-Z][A-Za-z]+[A-Z][A-Za-z]+\d{4}$")
        color_picker.assert_called_once_with((chess.WHITE, chess.BLACK))
        rating_picker.assert_called_once_with(800, 900)
        temperature_picker.assert_called_once_with(0.5, 0.9)
        self.assertEqual(
            self.engine.configured[-1],
            {"SelfElo": 700, "OppoElo": 800, "Temperature": "0.9"},
        )

    def test_find_match_uses_selected_piece_color(self):
        self.engine.moves = ["d4"]
        for requested_color, expected_color in (("white", "white"), ("black", "black")):
            with self.subTest(color=requested_color):
                response = self.client.post(
                    "/api/find-match",
                    json={"color": requested_color, "time_control": "3+2"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["state"]["player_color"], expected_color)
                reset_response = self.client.post("/api/new-game")
                self.assertEqual(reset_response.status_code, 200)

    def test_find_match_rejects_unsupported_piece_color(self):
        response = self.client.post(
            "/api/find-match",
            json={"color": "green", "time_control": "3+2"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("piece color", response.json["error"])

    def test_find_match_configures_both_elos_for_a_persisted_lower_rating(self):
        self.player_data_path.write_text(
            json.dumps(
                {
                    "ratings": {"blitz": 600, "rapid": 600},
                    "matches": {"blitz": [], "rapid": []},
                }
            ),
            encoding="utf-8",
        )
        client = create_app(
            self.engine,
            player_data_path=self.player_data_path,
        ).test_client()

        with patch("chessground_app.random.randint", return_value=800) as rating_picker:
            response = client.post(
                "/api/find-match",
                json={"time_control": "3+2"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"]["player_elo"], 600)
        self.assertEqual(response.json["state"]["maia_elo"], 800)
        self.assertEqual(
            self.engine.configured[-1],
            {"SelfElo": 600, "OppoElo": 600, "Temperature": "0.7"},
        )
        rating_picker.assert_called_once_with(800, 800)

    def test_finished_match_updates_and_persists_player_rating_once(self):
        with patch("maia_game.save_pgn", return_value=Path("match.pgn")) as save_pgn:
            self.client.post("/api/find-match", json={"time_control": "3+2"})
            response = self.client.post("/api/command", json={"command": "resign"})
            refreshed = self.client.get("/api/state")

        save_pgn.assert_called_once()
        self.assertIn('[Result "0-1"]', save_pgn.call_args.args[0])
        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
        self.assertEqual(state["player_elo"], 781)
        self.assertEqual(state["rating_change"], -19)
        self.assertEqual(state["rating_outcome"], "loss")
        self.assertEqual(state["termination"], "resignation")
        self.assertEqual(refreshed.json["player_elo"], 781)
        ladder = self.client.application.config["CHESSGROUND_LADDER"]
        self.assertEqual(len(ladder.matches_for("blitz")), 1)
        self.assertEqual(ladder.rating_for("rapid"), 800)

        stored = json.loads(self.player_data_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["ratings"], {"blitz": 781, "rapid": 800})
        self.assertEqual(stored["matches"]["blitz"][0]["outcome"], "loss")
        self.assertEqual(
            stored["matches"]["blitz"][0]["maia_elo"],
            state["maia_elo"],
        )
        self.assertNotIn("engine_maia_elo", stored["matches"]["blitz"][0])
        self.assertEqual(
            stored["matches"]["blitz"][0]["opponent_name"],
            state["maia_name"],
        )
        self.assertEqual(stored["matches"]["blitz"][0]["k_factor"], 40)
        self.assertIn('[Result "0-1"]', stored["matches"]["blitz"][0]["pgn"])
        self.assertIn("1.", stored["matches"]["blitz"][0]["pgn"])

        restarted_engine = FakeEngine()
        restarted = create_app(
            restarted_engine, player_data_path=self.player_data_path
        ).test_client()
        self.assertEqual(restarted.get("/api/state").json["player_elo"], 781)
        with (
            patch("chessground_app.random.choice", return_value=chess.WHITE),
            patch("chessground_app.random.randint", return_value=820) as rating_picker,
            patch("chessground_app.random.uniform", return_value=0.7),
        ):
            next_match = restarted.post(
                "/api/find-match", json={"time_control": "3+2"}
            )
        self.assertEqual(next_match.json["state"]["player_elo"], 781)
        rating_picker.assert_called_once_with(800, 881)
        self.assertEqual(
            restarted_engine.configured[-1],
            {"SelfElo": 620, "OppoElo": 781, "Temperature": "0.7"},
        )

    def test_unrated_game_uses_category_rating_and_saves_without_rating_change(self):
        ladder = self.client.application.config["CHESSGROUND_LADDER"]
        ladder.record_match(
            {
                "match_id": "rapid-win",
                "result": "1-0",
                "player_color": "white",
                "rating_category": "rapid",
                "maia_elo": 800,
            }
        )

        response = self.client.post(
            "/api/find-match",
            json={
                "color": "white",
                "time_control": "20+10",
                "unrated_mode": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"]["player_elo"], 820)
        self.assertTrue(response.json["state"]["unrated_mode"])
        self.assertEqual(
            self.engine.configured[-1],
            {"SelfElo": 620, "OppoElo": 820, "Temperature": "0.7"},
        )

        finished = self.client.post("/api/command", json={"command": "resign"})
        state = finished.json["state"]
        self.assertEqual(state["rating_change"], None)
        self.assertEqual(state["player_elo"], 820)
        self.assertEqual(len(ladder.matches_for("rapid")), 2)
        unrated_match = ladder.matches_for("rapid")[-1]
        self.assertFalse(unrated_match["rated"])
        self.assertEqual(unrated_match["rating_change"], 0)
        self.assertEqual(ladder.rating_for("rapid"), 820)

        profile = self.profile_data(self.client.get("/profile?category=rapid"))
        self.assertEqual(profile["playerElo"], 820)
        self.assertEqual(profile["colorStats"]["white"]["games"], 2)
        self.assertEqual(
            [point["rating"] for point in profile["ratingPoints"]],
            [800, 820],
        )

    def test_find_match_rejects_non_boolean_unrated_mode(self):
        response = self.client.post(
            "/api/find-match",
            json={"time_control": "3+2", "unrated_mode": "yes"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("unrated mode", response.json["error"])

    def test_start_supports_rapid_time_control_and_rapid_rating(self):
        ladder = self.client.application.config["CHESSGROUND_LADDER"]
        ladder.record_match(
            {
                "match_id": "blitz-win",
                "result": "1-0",
                "player_color": "white",
                "rating_category": "blitz",
                "maia_elo": 800,
            }
        )
        response = self.client.post(
            "/api/start",
            json={"color": "white", "time_control": "20+10"},
        )

        self.assertEqual(response.status_code, 200)
        clock = response.json["state"]["clock"]
        self.assertEqual(response.json["state"]["time_control"], "20+10")
        self.assertEqual(response.json["state"]["rating_category"], "rapid")
        self.assertEqual(response.json["state"]["player_elo"], 800)
        self.assertEqual(clock["base_seconds"], 1200)
        self.assertEqual(clock["increment_seconds"], 10)
        self.assertEqual(
            clock["remaining_ms"],
            {"white": 1_200_000, "black": 1_200_000},
        )

    def test_unsupported_time_controls_are_rejected(self):
        for payload in (
            {"time_control": "custom"},
            {"time_control": "bullet"},
            {"time_control": "5+0"},
            {"time_control": None},
            {"rating_category": "blitz"},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/api/find-match", json=payload)
                self.assertEqual(response.status_code, 400)

    def test_explicit_clock_parameters_are_no_longer_accepted(self):
        response = self.client.post(
            "/api/find-match",
            json={
                "time_control": "custom",
                "base_time_minutes": 7,
                "increment_seconds": 4,
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_clock_increment_is_added_after_each_player_completes_a_move(self):
        now = [100.0]
        with (
            patch("chessground_app.time.monotonic", side_effect=lambda: now[0]),
            patch.object(GameSession, "wait_for_maia_think"),
        ):
            self.client.post(
                "/api/start",
                json={"color": "white", "time_control": "5+5"},
            )
            now[0] = 102
            response = self.client.post(
                "/api/move",
                json={"move": "e2e4", "expected_ply": 0},
            )
            state = self.wait_for_maia()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            state["clock"]["remaining_ms"],
            {"white": 303_000, "black": 305_000},
        )
        self.assertEqual(state["clock"]["running_color"], "white")

    def test_completed_pgn_includes_elapsed_time_for_each_move(self):
        now = [100.0]

        def think_for_three_seconds(_session, _seconds):
            now[0] += 3

        with (
            patch("chessground_app.time.monotonic", side_effect=lambda: now[0]),
            patch.object(GameSession, "wait_for_maia_think", think_for_three_seconds),
            patch(
                "maia_game.save_pgn",
                return_value=Path("timed-game.pgn"),
            ) as save_pgn,
        ):
            self.client.post("/api/find-match", json={"time_control": "5+5"})
            now[0] = 102
            self.client.post(
                "/api/move",
                json={"move": "e2e4", "expected_ply": 0},
            )
            self.wait_for_maia()
            now[0] = 104
            response = self.client.post("/api/command", json={"command": "resign"})

        self.assertEqual(response.status_code, 200)
        pgn = save_pgn.call_args.args[0]
        self.assertIn("e4 { [%emt 0:00:02.00] }", pgn)
        self.assertIn("e5 { [%emt 0:00:03.00] }", pgn)

    def test_player_timeout_rejects_move_and_sets_the_result(self):
        now = [0.0]
        with (
            patch.dict(TIME_CONTROLS["blitz"], {"1+0": (1, 0)}),
            patch("chessground_app.time.monotonic", side_effect=lambda: now[0]),
            patch.object(GameSession, "save_pgn_file", return_value=Path("timeout.pgn")),
        ):
            self.client.post(
                "/api/start",
                json={"color": "white", "time_control": "1+0"},
            )
            now[0] = 2
            response = self.client.post(
                "/api/move",
                json={"move": "e2e4", "expected_ply": 0},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["state"]["ply"], 0)
        self.assertTrue(response.json["state"]["game_over"])
        self.assertEqual(response.json["state"]["result"], "0-1")
        self.assertEqual(response.json["state"]["termination"], "timeout")

    def test_state_poll_ends_game_when_player_runs_out_of_time(self):
        now = [0.0]
        with (
            patch.dict(TIME_CONTROLS["blitz"], {"1+0": (1, 0)}),
            patch("chessground_app.time.monotonic", side_effect=lambda: now[0]),
            patch.object(
                GameSession,
                "save_pgn_file",
                return_value=Path("timeout.pgn"),
            ),
        ):
            self.client.post(
                "/api/start",
                json={"color": "white", "time_control": "1+0"},
            )
            now[0] = 2
            response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["game_over"])
        self.assertEqual(response.json["result"], "0-1")
        self.assertEqual(response.json["termination"], "timeout")
        self.assertIsNone(response.json["clock"]["running_color"])

    def test_maia_timeout_stops_before_applying_her_move(self):
        now = [0.0]

        def think_then_expire(_self, _seconds):
            now[0] = 2

        with (
            patch.dict(TIME_CONTROLS["blitz"], {"1+0": (1, 0)}),
            patch("chessground_app.time.monotonic", side_effect=lambda: now[0]),
            patch.object(GameSession, "wait_for_maia_think", think_then_expire),
            patch.object(GameSession, "save_pgn_file", return_value=Path("timeout.pgn")),
        ):
            self.client.post(
                "/api/start",
                json={"color": "white", "time_control": "1+0"},
            )
            response = self.client.post(
                "/api/move",
                json={"move": "e2e4", "expected_ply": 0},
            )
            state = self.wait_for_maia()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(state["ply"], 1)
        self.assertTrue(state["game_over"])
        self.assertEqual(state["result"], "1-0")
        self.assertEqual(state["termination"], "timeout")
        self.assertEqual(state["clock"]["running_color"], None)

    def test_timeout_is_a_draw_when_the_winner_has_insufficient_material(self):
        game = GameSession(FakeEngine(), board=chess.Board("8/8/8/8/8/8/4k3/6K1 w - - 0 1"))

        game.finish_on_time(chess.WHITE)

        self.assertTrue(game.is_game_over)
        self.assertEqual(game.result, "1/2-1/2")

    def test_threefold_repetition_auto_claims_a_draw(self):
        board = chess.Board()
        for move in ("Nf3", "Nf6", "Ng1", "Ng8") * 2:
            board.push_san(move)
        client = create_app(
            self.engine,
            board=board,
            player_data_path=self.player_data_path,
        ).test_client()

        with patch.object(
            GameSession,
            "save_pgn_file",
            return_value=Path("repetition.pgn"),
        ):
            response = client.post(
                "/api/find-match",
                json={"time_control": "3+2"},
            )

        state = response.json["state"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(state["game_over"])
        self.assertEqual(state["result"], "1/2-1/2")
        self.assertEqual(state["termination"], "threefold_repetition")
        self.assertEqual(state["ply"], 8)

    def test_fifty_move_rule_auto_claims_a_draw(self):
        board = chess.Board("7k/8/8/8/8/8/6K1/R7 w - - 99 1")
        client = create_app(
            self.engine,
            board=board,
            player_data_path=self.player_data_path,
        ).test_client()

        with patch.object(
            GameSession,
            "save_pgn_file",
            return_value=Path("fifty-moves.pgn"),
        ):
            client.post("/api/find-match", json={"time_control": "3+2"})
            response = client.post(
                "/api/move",
                json={"move": "a1a2", "expected_ply": 0},
            )

        state = response.json["state"]
        self.assertEqual(response.status_code, 200)
        self.assertTrue(state["game_over"])
        self.assertEqual(state["result"], "1/2-1/2")
        self.assertEqual(state["termination"], "fifty_moves")
        self.assertEqual(state["ply"], 1)

    def test_move_acknowledges_player_then_returns_maias_position(self):
        self.client.post("/api/start", json={"color": "white"})
        response = self.client.post(
            "/api/move",
            json={"move": "e2e4", "expected_ply": 0},
        )

        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
        self.assertEqual(state["ply"], 1)
        self.assertEqual(state["history"], [{"role": "You", "text": "e4"}])
        self.assertTrue(state["maia_thinking"])
        self.assertFalse(state["can_move"])
        self.assertEqual(state["clock"]["running_color"], "black")
        state = self.wait_for_maia()
        self.assertEqual(state["ply"], 2)
        self.assertEqual(len(state["positions"]), 3)
        self.assertEqual(state["positions"][0]["turn_color"], "white")
        self.assertEqual(state["positions"][0]["last_move"], None)
        self.assertEqual(state["positions"][1]["turn_color"], "black")
        self.assertEqual(state["positions"][1]["last_move"], ["e2", "e4"])
        self.assertEqual(state["positions"][2]["turn_color"], "white")
        self.assertEqual(state["positions"][2]["last_move"], ["e7", "e5"])
        self.assertEqual(state["positions"][2]["fen"], state["fen"])
        self.assertEqual(
            state["history"],
            [
                {"role": "You", "text": "e4"},
                {"role": "Engine", "text": "e5"},
            ],
        )
        self.assertTrue(state["can_move"])
        self.assertEqual(state["last_move"], ["e7", "e5"])

    def test_move_and_state_return_while_engine_inference_is_blocked(self):
        entered = threading.Event()
        release = threading.Event()
        move_done = threading.Event()
        state_done = threading.Event()
        results = {}

        class BlockingEngine(FakeEngine):
            def play(self, board, limit, info=None):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Engine test was not released")
                return super().play(board, limit, info)

        app = create_app(BlockingEngine(), player_data_path=self.player_data_path)
        client = app.test_client()
        client.post("/api/start", json={"color": "white"})

        def submit():
            with app.test_client() as move_client:
                results["move"] = move_client.post(
                    "/api/move", json={"move": "e2e4", "expected_ply": 0}
                )
                move_done.set()

        def read_state():
            with app.test_client() as state_client:
                results["state"] = state_client.get("/api/state")
                state_done.set()

        move_thread = threading.Thread(target=submit, daemon=True)
        state_thread = threading.Thread(target=read_state, daemon=True)
        move_thread.start()
        try:
            self.assertTrue(entered.wait(2), "Engine inference did not start")
            self.assertTrue(move_done.wait(2), "Move acknowledgement waited for inference")
            state_thread.start()
            self.assertTrue(state_done.wait(2), "State request waited for inference")
            self.assertEqual(results["move"].status_code, 200)
            self.assertEqual(results["move"].json["state"]["ply"], 1)
            self.assertEqual(results["state"].json["history"], [{"role": "You", "text": "e4"}])
            self.assertTrue(results["state"].json["maia_thinking"])
        finally:
            release.set()
            move_thread.join(2)
            if state_thread.ident is not None:
                state_thread.join(2)
        self.assertEqual(self.wait_for_maia(client)["ply"], 2)

    def test_maia_timeout_during_inference_does_not_apply_a_late_move(self):
        release = threading.Event()
        entered = threading.Event()
        now = [0.0]

        class BlockingEngine(FakeEngine):
            def play(self, board, limit, info=None):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Engine test was not released")
                return super().play(board, limit, info)

        client = create_app(
            BlockingEngine(), player_data_path=self.player_data_path
        ).test_client()
        with (
            patch.dict(TIME_CONTROLS["blitz"], {"1+0": (1, 0)}),
            patch("chessground_app.time.monotonic", side_effect=lambda: now[0]),
            patch.object(GameSession, "save_pgn_file", return_value=Path("timeout.pgn")),
        ):
            client.post("/api/start", json={"color": "white", "time_control": "1+0"})
            client.post("/api/move", json={"move": "e2e4", "expected_ply": 0})
            try:
                self.assertTrue(entered.wait(2))
                now[0] = 2
                state = client.get("/api/state").json
                self.assertTrue(state["game_over"])
                self.assertEqual(state["termination"], "timeout")
                self.assertEqual(state["ply"], 1)
            finally:
                release.set()
            state = self.wait_for_maia(client)

        self.assertEqual(state["ply"], 1)
        self.assertEqual(state["result"], "1-0")
        self.assertEqual(state["history"], [{"role": "You", "text": "e4"}])

    def test_positions_track_captured_pieces_for_each_player(self):
        self.engine.moves = ["d5", "Qxd5"]
        self.client.post("/api/start", json={"color": "white"})
        self.client.post("/api/move", json={"move": "e2e4", "expected_ply": 0})
        self.wait_for_maia()
        response = self.client.post(
            "/api/move",
            json={"move": "e4d5", "expected_ply": 2},
        )

        self.assertEqual(response.status_code, 200)
        state = self.wait_for_maia()
        self.assertEqual(state["positions"][0]["captured_pieces"], {
            "white": [],
            "black": [],
        })
        self.assertEqual(state["positions"][3]["captured_pieces"]["white"], ["p"])
        self.assertEqual(state["positions"][4]["captured_pieces"]["black"], ["P"])
        self.assertEqual(state["captured_pieces"], state["positions"][-1]["captured_pieces"])

    def test_positions_track_en_passant_captures(self):
        self.engine.moves = ["a6", "d5", "Nf6"]
        self.client.post("/api/start", json={"color": "white"})
        self.client.post("/api/move", json={"move": "e2e4", "expected_ply": 0})
        self.wait_for_maia()
        self.client.post("/api/move", json={"move": "e4e5", "expected_ply": 2})
        self.wait_for_maia()
        response = self.client.post(
            "/api/move",
            json={"move": "e5d6", "expected_ply": 4},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["state"]["captured_pieces"]["white"],
            ["p"],
        )
        self.wait_for_maia()

    def test_stale_move_is_rejected_without_changing_position(self):
        self.client.post("/api/start", json={"color": "white"})
        first = self.client.post(
            "/api/move",
            json={"move": "e2e4", "expected_ply": 0},
        )
        self.wait_for_maia()
        second = self.client.post(
            "/api/move",
            json={"move": "d2d4", "expected_ply": 0},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json["state"]["ply"], 2)
        self.assertEqual(len(second.json["state"]["history"]), 2)

    def test_move_from_an_old_match_is_rejected_at_the_same_ply(self):
        first = self.client.post("/api/start", json={"color": "white"}).json["state"]
        self.client.post("/api/new-game")
        second = self.client.post("/api/start", json={"color": "white"}).json["state"]

        response = self.client.post("/api/move", json={
            "move": "e2e4", "expected_ply": 0, "match_id": first["match_id"],
        })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["state"]["match_id"], second["match_id"])
        self.assertEqual(response.json["state"]["ply"], 0)

    def test_engine_failure_preserves_the_players_move_and_can_retry(self):
        engine = FakeEngine(error=RuntimeError("engine failed"))
        client = create_app(
            engine,
            player_data_path=self.player_data_path,
        ).test_client()
        client.post("/api/start", json={"color": "white"})

        response = client.post(
            "/api/move",
            json={"move": "e2e4", "expected_ply": 0},
        )

        self.assertEqual(response.status_code, 200)
        state = self.wait_for_maia(client)
        self.assertEqual(state["ply"], 1)
        self.assertEqual(state["history"], [{"role": "You", "text": "e4"}])
        self.assertIn("Retry", state["maia_error"])
        self.assertIsNone(state["clock"]["running_color"])
        engine.error = None
        retry = client.post("/api/retry-maia")
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(retry.json["state"]["maia_thinking"])
        state = self.wait_for_maia(client)
        self.assertIsNone(state["maia_error"])
        self.assertEqual(state["ply"], 2)
        self.assertEqual(state["history"][-1]["text"], "e5")

    def test_engine_failure_pauses_clock_until_retry(self):
        now = [0.0]

        class FailOnceEngine(FakeEngine):
            def play(self, board, limit, info=None):
                if now[0] == 0:
                    now[0] = 2
                    raise RuntimeError("engine failed")
                return super().play(board, limit, info)

        def think_for_three_seconds(_session, _seconds):
            now[0] += 3

        client = create_app(
            FailOnceEngine(), player_data_path=self.player_data_path
        ).test_client()
        with (
            patch("chessground_app.time.monotonic", side_effect=lambda: now[0]),
            patch.object(GameSession, "wait_for_maia_think", think_for_three_seconds),
        ):
            client.post("/api/start", json={"color": "white"})
            client.post("/api/move", json={"move": "e2e4", "expected_ply": 0})
            state = self.wait_for_maia(client)
            self.assertEqual(state["clock"]["remaining_ms"]["black"], 178_000)
            now[0] = 22
            paused = client.get("/api/state").json
            self.assertEqual(paused["clock"]["remaining_ms"]["black"], 178_000)
            client.post("/api/retry-maia")
            state = self.wait_for_maia(client)

        self.assertEqual(state["clock"]["remaining_ms"]["black"], 177_000)
        self.assertEqual(client.application.config["CHESS_GAME"].move_times[1], 5)

    def test_undo_after_engine_failure_restores_player_turn(self):
        client = create_app(
            FakeEngine(error=RuntimeError("engine failed")),
            player_data_path=self.player_data_path,
        ).test_client()
        client.post("/api/start", json={"color": "white"})
        client.post("/api/move", json={"move": "e2e4", "expected_ply": 0})
        self.wait_for_maia(client)

        response = client.post("/api/command", json={"command": "undo"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"]["ply"], 0)
        self.assertIsNone(response.json["state"]["maia_error"])
        self.assertEqual(response.json["state"]["clock"]["running_color"], "white")
        self.assertTrue(response.json["state"]["can_move"])

    def test_illegal_move_does_not_change_position(self):
        self.client.post("/api/start", json={"color": "white"})
        response = self.client.post(
            "/api/move",
            json={"move": "e2e5", "expected_ply": 0},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["state"]["ply"], 0)
        self.assertTrue(response.json["state"]["can_move"])

    def test_black_start_plays_maias_opening_move(self):
        with (
            patch("chessground_app.random.choice", return_value=chess.BLACK),
            patch.object(GameSession, "wait_for_maia_think"),
        ):
            client = create_app(
                FakeEngine(moves=["e4"]),
                player_data_path=self.player_data_path,
            ).test_client()
            response = client.post(
                "/api/find-match", json={"time_control": "3+2"}
            )

        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
        self.assertEqual(state["player_color"], "black")
        self.assertEqual(state["ply"], 1)
        self.assertTrue(state["can_move"])
        self.assertEqual(state["history"], [{"role": "Engine", "text": "e4"}])

    def test_blitz_makes_maia_think_faster_than_rapid(self):
        think_times = {}
        for time_control in ("3+2", "15+10"):
            client = create_app(
                FakeEngine(moves=["e4"]),
                player_data_path=self.player_data_path,
            ).test_client()
            with (
                patch("chessground_app.random.choice", return_value=chess.BLACK),
                patch("maia_game.time.perf_counter", side_effect=(0, 0)),
                patch.object(GameSession, "wait_for_maia_think") as wait,
            ):
                response = client.post(
                    "/api/find-match", json={"time_control": time_control}
                )

            self.assertEqual(response.status_code, 200)
            think_times[time_control] = wait.call_args.args[0]

        self.assertAlmostEqual(think_times["3+2"], 0.6)
        self.assertAlmostEqual(think_times["15+10"], 3.0)

    def test_play_instantly_skips_maias_move_delay(self):
        self.client.post("/api/start", json={"color": "white"})
        response = self.client.post("/api/play-instantly", json={"enabled": True})
        with patch.object(GameSession, "wait_for_maia_think") as wait:
            move_response = self.client.post(
                "/api/move",
                json={"move": "e2e4", "expected_ply": 0},
            )
            self.wait_for_maia()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["play_instantly"])
        self.assertTrue(move_response.json["state"]["play_instantly"])
        wait.assert_called_once_with(0.0)

    def test_play_instantly_must_be_a_boolean(self):
        response = self.client.post(
            "/api/play-instantly",
            json={"enabled": "true"},
        )

        self.assertEqual(response.status_code, 400)

    def test_promotion_choices_are_returned_for_promotion_moves(self):
        board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")
        client = create_app(
            FakeEngine(),
            board=board,
            player_data_path=self.player_data_path,
        ).test_client()
        client.post("/api/start", json={"color": "white"})

        state = client.get("/api/state").json

        self.assertEqual(state["promotions"]["a7a8"], ["b", "n", "q", "r"])
        self.assertIn("a8", state["destinations"]["a7"])

    def test_state_identifies_legal_moves_that_give_check(self):
        board = chess.Board("k7/8/8/8/8/8/8/R6K w - - 0 1")
        client = create_app(
            FakeEngine(),
            board=board,
            player_data_path=self.player_data_path,
        ).test_client()
        client.post("/api/start", json={"color": "white"})

        state = client.get("/api/state").json

        self.assertIn("a1a7", state["checking_moves"])
        self.assertNotIn("a1b1", state["checking_moves"])

    def test_checking_promotion_choices_are_identified_individually(self):
        board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")
        client = create_app(
            FakeEngine(),
            board=board,
            player_data_path=self.player_data_path,
        ).test_client()
        client.post("/api/start", json={"color": "white"})

        state = client.get("/api/state").json

        self.assertIn("a7a8q", state["checking_moves"])
        self.assertIn("a7a8r", state["checking_moves"])
        self.assertNotIn("a7a8b", state["checking_moves"])
        self.assertNotIn("a7a8n", state["checking_moves"])

    def test_resign_finishes_game_and_allows_pgn_download(self):
        self.client.post("/api/start", json={"color": "white"})
        with patch.object(GameSession, "save_pgn_file", return_value=Path("resigned.pgn")):
            response = self.client.post(
                "/api/command",
                json={"command": "resign"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["state"]["game_over"])
        pgn = self.client.get("/api/pgn")
        self.assertEqual(pgn.status_code, 200)
        self.assertIn('[Result "0-1"]', pgn.json["pgn"])

    def test_undo_and_new_game_restore_the_initial_state(self):
        self.client.post("/api/start", json={"color": "white"})
        self.client.post("/api/move", json={"move": "e2e4", "expected_ply": 0})
        self.wait_for_maia()

        undo = self.client.post("/api/command", json={"command": "undo"})

        self.assertEqual(undo.status_code, 200)
        self.assertEqual(undo.json["state"]["ply"], 0)
        self.assertTrue(undo.json["state"]["can_move"])
        self.assertEqual(
            self.client.application.config["CHESS_GAME"].move_times,
            [],
        )

        with patch.object(GameSession, "save_pgn_file", return_value=Path("game.pgn")) as save_pgn:
            new_game = self.client.post("/api/new-game")

        self.assertEqual(new_game.status_code, 200)
        self.assertFalse(new_game.json["state"]["started"])
        self.assertEqual(new_game.json["state"]["ply"], 0)
        self.assertIsNone(new_game.json["saved_pgn"])
        save_pgn.assert_not_called()

    def test_missing_expected_ply_is_rejected(self):
        self.client.post("/api/start", json={"color": "white"})
        response = self.client.post("/api/move", json={"move": "e2e4"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["state"]["ply"], 0)


if __name__ == "__main__":
    unittest.main()
