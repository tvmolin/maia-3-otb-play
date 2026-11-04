import json
import tempfile
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

    def test_initial_state_includes_position_and_no_legal_player_moves(self):
        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["fen"],
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
        )
        self.assertFalse(response.json["can_move"])
        self.assertEqual(response.json["destinations"], {})

    def test_matchmaking_form_only_exposes_time_control_and_find_match(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Find match", response.data)
        self.assertIn(b'id="time-control"', response.data)
        self.assertIn(b'<optgroup label="Blitz">', response.data)
        self.assertIn(b'<optgroup label="Rapid">', response.data)
        for control in (b"3+2", b"5+5", b"15+10", b"20+10"):
            self.assertIn(control, response.data)
        self.assertNotIn(b"Custom", response.data)
        self.assertNotIn(b'id="player-color"', response.data)
        self.assertNotIn(b'id="maia-elo"', response.data)
        self.assertNotIn(b'id="player-elo"', response.data)
        self.assertIn(b'id="opponent-display"', response.data)
        self.assertIn(b'id="player-display"', response.data)
        self.assertIn(b'id="result-summary"', response.data)
        self.assertIn(b'id="result-title"', response.data)
        self.assertIn(b'id="result-rating"', response.data)
        self.assertIn(b'id="clocks-panel"', response.data)
        self.assertIn(b'id="actions-panel" class="panel" hidden', response.data)
        self.assertIn(b'id="moves-panel" class="panel" hidden', response.data)
        self.assertIn(b'id="move-previous"', response.data)
        self.assertIn(b'id="move-next"', response.data)
        self.assertIn(b'id="copy-pgn"', response.data)
        self.assertIn(b">Copy PGN</button>", response.data)
        self.assertIn(b'id="pgn-fallback"', response.data)
        self.assertNotIn(b"Download PGN", response.data)
        self.assertLess(
            response.data.index(b'<ol id="move-history"'),
            response.data.index(b'id="copy-pgn"'),
        )

    def test_available_clocks_are_grouped_into_blitz_and_rapid(self):
        self.assertEqual(
            TIME_CONTROLS,
            {
                "blitz": {"3+2": (180, 2), "5+5": (300, 5)},
                "rapid": {"15+10": (900, 10), "20+10": (1200, 10)},
            },
        )

    def test_profile_shows_empty_rating_chart_and_match_history(self):
        response = self.client.get("/profile")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Player profile", response.data)
        self.assertIn(b"Blitz rating progress", response.data)
        self.assertIn(b"Blitz rating", response.data)
        self.assertIn(b"Blitz", response.data)
        self.assertIn(b"Rapid", response.data)
        self.assertIn(b">800</strong>", response.data)
        self.assertIn(b"No matches yet", response.data)
        self.assertIn(b"Back to game", response.data)
        self.assertIn(b"assets/profile.js", response.data)

    def test_profile_shows_rating_progress_and_newest_match_first(self):
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
            }
        )

        response = self.client.get("/profile")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"class=\"rating-chart-line\"", response.data)
        self.assertIn(b"TacticalLlama4821 (900)", response.data)
        self.assertIn(b'data-local-time datetime="2026-11-04T12:00:00Z"', response.data)
        self.assertIn(b"Maia (800)", response.data)
        self.assertIn(b"5+5", response.data)
        self.assertLess(
            response.data.index(b"TacticalLlama4821 (900)"),
            response.data.index(b"Maia (800)"),
        )
        rapid_response = self.client.get("/profile?category=rapid")
        self.assertEqual(rapid_response.status_code, 200)
        self.assertIn(b"Rapid rating progress", rapid_response.data)
        self.assertIn(b"No matches yet", rapid_response.data)
        self.assertNotIn(b"TacticalLlama4821", rapid_response.data)

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
        self.assertEqual(response.json["state"]["maia_temperature"], 0.7)
        self.assertRegex(
            response.json["state"]["maia_name"],
            r"^[A-Z][A-Za-z]+[A-Z][A-Za-z]+\d{4}$",
        )
        self.assertEqual(
            self.engine.configured[-1],
            {"SelfElo": 820, "OppoElo": 800, "Temperature": "0.7"},
        )
        self.assertEqual(
            response.json["state"]["clock"]["remaining_ms"],
            {"white": 300_000, "black": 300_000},
        )

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
                    "color": "white",
                    "maia_elo": 0,
                    "player_elo": 5000,
                    "temperature": 5,
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
        rating_picker.assert_called_once_with(700, 900)
        temperature_picker.assert_called_once_with(0.5, 0.9)
        self.assertEqual(
            self.engine.configured[-1],
            {"SelfElo": 900, "OppoElo": 800, "Temperature": "0.9"},
        )

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

        with patch("chessground_app.random.randint", return_value=600):
            response = client.post(
                "/api/find-match",
                json={"time_control": "3+2"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"]["player_elo"], 600)
        self.assertEqual(response.json["state"]["maia_elo"], 600)
        self.assertEqual(
            self.engine.configured[-1],
            {"SelfElo": 600, "OppoElo": 600, "Temperature": "0.7"},
        )

    def test_finished_match_updates_and_persists_player_rating_once(self):
        with patch.object(
            GameSession,
            "save_pgn_file",
            return_value=Path("match.pgn"),
        ):
            self.client.post("/api/find-match", json={"time_control": "3+2"})
            response = self.client.post("/api/command", json={"command": "resign"})
            refreshed = self.client.get("/api/state")

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
            stored["matches"]["blitz"][0]["opponent_name"],
            state["maia_name"],
        )
        self.assertEqual(stored["matches"]["blitz"][0]["k_factor"], 40)

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
        rating_picker.assert_called_once_with(681, 881)
        self.assertEqual(
            restarted_engine.configured[-1],
            {"SelfElo": 820, "OppoElo": 781, "Temperature": "0.7"},
        )

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

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["state"]["clock"]["remaining_ms"],
            {"white": 303_000, "black": 305_000},
        )
        self.assertEqual(response.json["state"]["clock"]["running_color"], "white")

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

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"]["ply"], 1)
        self.assertTrue(response.json["state"]["game_over"])
        self.assertEqual(response.json["state"]["result"], "1-0")
        self.assertEqual(response.json["state"]["termination"], "timeout")
        self.assertEqual(response.json["state"]["clock"]["running_color"], None)

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

    def test_move_returns_authoritative_position_after_maia_responds(self):
        self.client.post("/api/start", json={"color": "white"})
        response = self.client.post(
            "/api/move",
            json={"move": "e2e4", "expected_ply": 0},
        )

        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
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

    def test_stale_move_is_rejected_without_changing_position(self):
        self.client.post("/api/start", json={"color": "white"})
        first = self.client.post(
            "/api/move",
            json={"move": "e2e4", "expected_ply": 0},
        )
        second = self.client.post(
            "/api/move",
            json={"move": "d2d4", "expected_ply": 0},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json["state"]["ply"], 2)
        self.assertEqual(len(second.json["state"]["history"]), 2)

    def test_engine_failure_rolls_back_the_players_move(self):
        client = create_app(
            FakeEngine(error=RuntimeError("engine failed")),
            player_data_path=self.player_data_path,
        ).test_client()
        client.post("/api/start", json={"color": "white"})

        response = client.post(
            "/api/move",
            json={"move": "e2e4", "expected_ply": 0},
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["state"]["ply"], 0)
        self.assertEqual(response.json["state"]["history"], [])

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

        undo = self.client.post("/api/command", json={"command": "undo"})

        self.assertEqual(undo.status_code, 200)
        self.assertEqual(undo.json["state"]["ply"], 0)
        self.assertTrue(undo.json["state"]["can_move"])

        with patch.object(GameSession, "save_pgn_file", return_value=Path("game.pgn")):
            new_game = self.client.post("/api/new-game")

        self.assertEqual(new_game.status_code, 200)
        self.assertFalse(new_game.json["state"]["started"])
        self.assertEqual(new_game.json["state"]["ply"], 0)

    def test_missing_expected_ply_is_rejected(self):
        self.client.post("/api/start", json={"color": "white"})
        response = self.client.post("/api/move", json={"move": "e2e4"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["state"]["ply"], 0)


if __name__ == "__main__":
    unittest.main()
