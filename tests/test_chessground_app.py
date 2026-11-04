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
        self.client = create_app(self.engine).test_client()

    def test_initial_state_includes_position_and_no_legal_player_moves(self):
        response = self.client.get("/api/state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["fen"],
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
        )
        self.assertFalse(response.json["can_move"])
        self.assertEqual(response.json["destinations"], {})

    def test_start_exposes_legal_destinations(self):
        response = self.client.post("/api/start", json={"color": "white"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["state"]["can_move"])
        self.assertIn("e4", response.json["state"]["destinations"]["e2"])
        self.assertEqual(response.json["state"]["time_control"], "5+0")
        self.assertEqual(
            response.json["state"]["clock"]["remaining_ms"],
            {"white": 300_000, "black": 300_000},
        )

    def test_start_supports_twenty_plus_ten_clock(self):
        response = self.client.post(
            "/api/start",
            json={"color": "white", "time_control": "20+10"},
        )

        self.assertEqual(response.status_code, 200)
        clock = response.json["state"]["clock"]
        self.assertEqual(response.json["state"]["time_control"], "20+10")
        self.assertEqual(clock["base_seconds"], 1200)
        self.assertEqual(clock["increment_seconds"], 10)
        self.assertEqual(
            clock["remaining_ms"],
            {"white": 1_200_000, "black": 1_200_000},
        )

    def test_custom_clock_uses_requested_base_time_and_increment(self):
        response = self.client.post(
            "/api/start",
            json={
                "color": "white",
                "time_control": "custom",
                "base_time_minutes": 7,
                "increment_seconds": 4,
            },
        )

        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
        self.assertEqual(state["time_control"], "custom")
        self.assertEqual(state["clock"]["base_seconds"], 420)
        self.assertEqual(state["clock"]["increment_seconds"], 4)
        self.assertEqual(
            state["clock"]["remaining_ms"],
            {"white": 420_000, "black": 420_000},
        )

    def test_custom_clock_rejects_out_of_range_values(self):
        for minutes, increment in ((0, 0), (181, 0), (5, -1), (5, 61), (True, 0)):
            with self.subTest(minutes=minutes, increment=increment):
                client = create_app(FakeEngine()).test_client()
                response = client.post(
                    "/api/start",
                    json={
                        "color": "white",
                        "time_control": "custom",
                        "base_time_minutes": minutes,
                        "increment_seconds": increment,
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
                json={"color": "white", "time_control": "5+3"},
            )
            now[0] = 102
            response = self.client.post(
                "/api/move",
                json={"move": "e2e4", "expected_ply": 0},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["state"]["clock"]["remaining_ms"],
            {"white": 301_000, "black": 303_000},
        )
        self.assertEqual(response.json["state"]["clock"]["running_color"], "white")

    def test_player_timeout_rejects_move_and_sets_the_result(self):
        now = [0.0]
        with (
            patch.dict(TIME_CONTROLS, {"1+0": (1, 0)}),
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

    def test_maia_timeout_stops_before_applying_her_move(self):
        now = [0.0]

        def think_then_expire(_self, _seconds):
            now[0] = 2

        with (
            patch.dict(TIME_CONTROLS, {"1+0": (1, 0)}),
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

    def test_move_returns_authoritative_position_after_maia_responds(self):
        self.client.post("/api/start", json={"color": "white"})
        response = self.client.post(
            "/api/move",
            json={"move": "e2e4", "expected_ply": 0},
        )

        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
        self.assertEqual(state["ply"], 2)
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
        client = create_app(FakeEngine(error=RuntimeError("engine failed"))).test_client()
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
        client = create_app(FakeEngine(moves=["e4"])).test_client()
        response = client.post("/api/start", json={"color": "black"})

        self.assertEqual(response.status_code, 200)
        state = response.json["state"]
        self.assertEqual(state["player_color"], "black")
        self.assertEqual(state["ply"], 1)
        self.assertTrue(state["can_move"])
        self.assertEqual(state["history"], [{"role": "Engine", "text": "e4"}])

    def test_promotion_choices_are_returned_for_promotion_moves(self):
        board = chess.Board("7k/P7/8/8/8/8/8/7K w - - 0 1")
        client = create_app(FakeEngine(), board=board).test_client()
        client.post("/api/start", json={"color": "white"})

        state = client.get("/api/state").json

        self.assertEqual(state["promotions"]["a7a8"], ["b", "n", "q", "r"])
        self.assertIn("a8", state["destinations"]["a7"])

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
