from io import BytesIO
import tempfile
import unittest
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch

import chess

from maia_game import GameSession
from web_app import configured_ssl_context, create_app


class FakeEngine:
    def __init__(self, move="e5", error=None, moves=None):
        self.move = move
        self.error = error
        self.moves = list(moves or [])
        self.configured = []

    def configure(self, options):
        self.configured.append(options)

    def play(self, board, limit, info=None):
        if self.error:
            raise self.error
        move = self.moves.pop(0) if self.moves else self.move
        return SimpleNamespace(
            move=board.parse_san(move),
            info={"string": "policy_entropy 0.5"},
        )


class FakeWhisper:
    def __init__(self):
        self.audio_path = None
        self.beam_size = None

    def transcribe(self, audio_path, beam_size):
        self.audio_path = Path(audio_path)
        self.beam_size = beam_size
        return iter(
            [
                SimpleNamespace(text=" knight "),
                SimpleNamespace(text=" f3 "),
            ]
        ), None


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(FakeEngine()).test_client()
        self.app.post("/api/start", json={"color": "white"})

    def test_move_returns_and_records_both_moves(self):
        with patch.object(GameSession, "wait_for_maia_think") as wait:
            response = self.app.post("/api/move", json={"move": "e4"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["messages"],
            [
                {"role": "You", "text": "e4"},
                {"role": "Engine", "text": "e5"},
            ],
        )
        self.assertEqual(response.json["history"], response.json["messages"])
        wait.assert_called_once()
        self.assertAlmostEqual(wait.call_args.args[0], 3.0, delta=0.1)

    def test_move_accepts_uci_notation(self):
        with patch.object(GameSession, "wait_for_maia_think"):
            response = self.app.post("/api/move", json={"move": "e2e4"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["messages"][0]["text"], "e4")

    def test_illegal_move_does_not_change_position_or_history(self):
        response = self.app.post("/api/move", json={"move": "not a move"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.app.get("/api/history").json["history"], [])
        game = self.app.application.config["CHESS_GAME"]
        self.assertEqual(len(game.board.move_stack), 0)

    def test_engine_failure_rolls_back_player_move(self):
        client = create_app(FakeEngine(error=RuntimeError("engine failed"))).test_client()
        client.post("/api/start", json={"color": "white"})

        with patch.object(GameSession, "wait_for_maia_think"):
            response = client.post("/api/move", json={"move": "e4"})

        self.assertEqual(response.status_code, 503)
        game = client.application.config["CHESS_GAME"]
        self.assertEqual(len(game.board.move_stack), 0)
        self.assertEqual(game.history, [])

    def test_shared_undo_removes_moves_from_board_and_history(self):
        session = GameSession(FakeEngine())
        session.apply_player_move("e4")
        turn = session.choose_maia_move()
        session.play_maia_move(turn)

        self.assertEqual(session.undo_last_turn(), 2)
        self.assertEqual(len(session.board.move_stack), 0)
        self.assertEqual(session.history, [])

    def test_rejects_missing_move(self):
        response = self.app.post("/api/move", json={})

        self.assertEqual(response.status_code, 400)

    def test_transcribe_returns_local_whisper_text(self):
        transcriber = FakeWhisper()
        client = create_app(FakeEngine(), transcriber=transcriber).test_client()

        response = client.post(
            "/api/transcribe",
            data={"audio": (BytesIO(b"local test audio"), "recording.webm", "audio/webm")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["text"], "knight f3")
        self.assertEqual(transcriber.beam_size, 5)
        self.assertFalse(transcriber.audio_path.exists())

    def test_transcribe_rejects_missing_or_unsupported_audio(self):
        client = create_app(FakeEngine(), transcriber=FakeWhisper()).test_client()

        missing = client.post("/api/transcribe", data={})
        unsupported = client.post(
            "/api/transcribe",
            data={"audio": (BytesIO(b"not audio"), "recording.txt", "text/plain")},
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(unsupported.status_code, 415)

    def test_transcribe_reports_gpu_initialization_failure(self):
        client = create_app(FakeEngine()).test_client()
        with patch("web_app.create_whisper_model", side_effect=RuntimeError("CUDA missing")):
            response = client.post(
                "/api/transcribe",
                data={"audio": (BytesIO(b"local test audio"), "recording.webm", "audio/webm")},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("GPU", response.json["error"])

    def test_https_certificate_is_auto_detected_for_local_app_data(self):
        with tempfile.TemporaryDirectory() as directory:
            cert_dir = Path(directory) / "Maia-OTB" / "certs"
            cert_dir.mkdir(parents=True)
            cert_path = cert_dir / "server.pem"
            key_path = cert_dir / "server-key.pem"
            cert_path.write_text("test certificate", encoding="utf-8")
            key_path.write_text("test key", encoding="utf-8")

            with patch.dict("os.environ", {"LOCALAPPDATA": directory}, clear=True):
                self.assertEqual(
                    configured_ssl_context(),
                    (str(cert_path), str(key_path)),
                )

    def test_selecting_black_plays_maias_opening_move(self):
        client = create_app(FakeEngine(move="e4")).test_client()

        with patch.object(GameSession, "wait_for_maia_think"):
            response = client.post("/api/start", json={"color": "black"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["player_color"], "black")
        self.assertEqual(response.json["history"], [{"role": "Engine", "text": "e4"}])
        game = client.application.config["CHESS_GAME"]
        self.assertEqual(game.board.turn, chess.BLACK)

    def test_black_undo_replays_maias_opening_move(self):
        client = create_app(FakeEngine(move="e4")).test_client()

        with patch.object(GameSession, "wait_for_maia_think"):
            client.post("/api/start", json={"color": "black"})
            response = client.post("/api/command", json={"text": "undo"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["action"], "undo")
        self.assertEqual(response.json["history"], [{"role": "Engine", "text": "e4"}])
        game = client.application.config["CHESS_GAME"]
        self.assertEqual(game.board.turn, chess.BLACK)

    def test_web_can_set_elos_and_configure_engine(self):
        engine = FakeEngine()
        client = create_app(engine).test_client()

        response = client.post(
            "/api/start",
            json={"color": "white", "maia_elo": 1500, "player_elo": 1300},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["maia_elo"], 1500)
        self.assertEqual(response.json["player_elo"], 1300)
        self.assertEqual(
            engine.configured[-1], {"SelfElo": 1500, "OppoElo": 1300}
        )

    def test_invalid_elo_is_rejected(self):
        client = create_app(FakeEngine()).test_client()

        response = client.post(
            "/api/start",
            json={"color": "white", "maia_elo": 6000, "player_elo": 760},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(client.application.config["CHESS_GAME"].started)

    def test_shared_commands_work_over_http(self):
        help_response = self.app.post("/api/command", json={"text": "help"})
        self.assertEqual(help_response.json["action"], "help")
        self.assertIn("undo", help_response.json["message"])

        with patch.object(GameSession, "wait_for_maia_think"):
            self.app.post("/api/command", json={"text": "e4"})
        undo_response = self.app.post("/api/command", json={"text": "undo"})

        self.assertEqual(undo_response.json["action"], "undo")
        self.assertEqual(undo_response.json["message"], "Undid 2 half-move(s).")
        self.assertEqual(undo_response.json["history"], [])

    def test_help_command_is_available_while_maia_is_thinking(self):
        game = self.app.application.config["CHESS_GAME"]
        game.begin_maia_turn()

        response = self.app.post("/api/command", json={"text": "help"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["action"], "help")
        game.finish_maia_think()

    def test_resign_alias_and_quit_commands_work_over_http(self):
        resign_client = create_app(FakeEngine(move="e4")).test_client()
        with patch.object(GameSession, "wait_for_maia_think"):
            resign_client.post("/api/start", json={"color": "black"})
        in_progress = resign_client.get("/api/pgn")
        self.assertEqual(in_progress.status_code, 409)

        with patch("maia_game.save_pgn", return_value=Path("resigned-game.pgn")) as save_pgn:
            response = resign_client.post("/api/command", json={"text": "concede"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["result"], "1-0")
        self.assertTrue(response.json["game_over"])
        self.assertEqual(response.json["saved_pgn"], "resigned-game.pgn")
        save_pgn.assert_called_once()
        self.assertIn('[Result "1-0"]', save_pgn.call_args.args[0])
        game = resign_client.application.config["CHESS_GAME"]
        pgn_response = resign_client.get("/api/pgn")
        self.assertEqual(pgn_response.status_code, 200)
        self.assertIn('[Result "1-0"]', pgn_response.json["pgn"])
        self.assertEqual(pgn_response.json["pgn"], game.build_pgn(game.result))

        with patch("maia_game.save_pgn") as save_pgn:
            new_game_response = resign_client.post("/api/new-game")

        self.assertEqual(new_game_response.status_code, 200)
        save_pgn.assert_not_called()
        self.assertEqual(new_game_response.json["saved_pgn"], "resigned-game.pgn")

        quit_response = self.app.post("/api/command", json={"text": "exit"})
        self.assertEqual(quit_response.json["action"], "quit")
        self.assertTrue(quit_response.json["quit"])

    def test_new_game_discards_unfinished_game_and_resets_to_color_picker(self):
        with patch.object(GameSession, "wait_for_maia_think"):
            self.app.post("/api/command", json={"text": "e4"})

        with patch("maia_game.save_pgn", return_value=Path("maia-game-test.pgn")) as save_pgn:
            response = self.app.post("/api/new-game")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json["started"])
        self.assertEqual(response.json["history"], [])
        self.assertIsNone(response.json["saved_pgn"])
        self.assertEqual(len(self.app.application.config["CHESS_GAME"].board.move_stack), 0)
        save_pgn.assert_not_called()

    def test_in_progress_game_cannot_be_saved_as_a_pgn(self):
        game = self.app.application.config["CHESS_GAME"]
        with patch("maia_game.save_pgn") as save_pgn:
            with self.assertRaisesRegex(ValueError, "Only completed games"):
                game.save_pgn_file()

        save_pgn.assert_not_called()

    def test_completed_game_is_saved_immediately_and_only_once(self):
        client = create_app(
            FakeEngine(moves=["e5", "Qh4#"])
        ).test_client()
        client.post("/api/start", json={"color": "white"})

        with patch.object(GameSession, "wait_for_maia_think"), patch(
            "maia_game.save_pgn", return_value=Path("completed-game.pgn")
        ) as save_pgn:
            client.post("/api/command", json={"text": "f3"})
            response = client.post("/api/command", json={"text": "g4"})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["result"], "0-1")
            self.assertEqual(response.json["saved_pgn"], "completed-game.pgn")
            save_pgn.assert_called_once()

            saved_pgn = save_pgn.call_args.args[0]
            self.assertIn('[Result "0-1"]', saved_pgn)
            new_game_response = client.post("/api/new-game")

        self.assertEqual(new_game_response.status_code, 200)
        save_pgn.assert_called_once()

    def test_new_game_is_rejected_while_maia_is_thinking(self):
        client = create_app(FakeEngine()).test_client()
        client.post("/api/start", json={"color": "white"})
        waiting = Event()
        move_response = {}
        wait_for_think = GameSession.wait_for_maia_think

        def observe_wait(session, seconds):
            waiting.set()
            return wait_for_think(session, seconds)

        def submit_move():
            with client.application.test_client() as move_client:
                move_response["response"] = move_client.post(
                    "/api/command", json={"text": "e4"}
                )

        with patch.object(GameSession, "wait_for_maia_think", observe_wait):
            worker = Thread(target=submit_move)
            worker.start()
            self.assertTrue(waiting.wait(2))
            response = client.post("/api/new-game")
            client.post("/api/command", json={"text": "go"})
            worker.join(2)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(worker.is_alive())
        self.assertEqual(move_response["response"].status_code, 200)

    def test_go_command_interrupts_web_think_delay(self):
        client = create_app(FakeEngine()).test_client()
        client.post("/api/start", json={"color": "white"})
        waiting = Event()
        move_response = {}
        wait_for_think = GameSession.wait_for_maia_think

        def observe_wait(session, seconds):
            waiting.set()
            return wait_for_think(session, seconds)

        def submit_move():
            with client.application.test_client() as move_client:
                move_response["response"] = move_client.post(
                    "/api/command", json={"text": "e4"}
                )

        with patch.object(GameSession, "wait_for_maia_think", observe_wait):
            worker = Thread(target=submit_move)
            worker.start()
            self.assertTrue(waiting.wait(2))
            go_response = client.post("/api/command", json={"text": "go"})
            worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(go_response.json["action"], "go")
        self.assertEqual(move_response["response"].json["history"][-1]["text"], "e5")

    def test_homepage_and_empty_history_load(self):
        page = self.app.get("/")
        history = self.app.get("/api/history")

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Maia OTB Chess", page.data)
        self.assertIn(b'id="go-button"', page.data)
        self.assertIn(b'input.disabled = gameEnded', page.data)
        self.assertIn(b'button.disabled = waiting || gameEnded', page.data)
        self.assertIn(b'input.value = "";', page.data)
        self.assertIn(b'goButton.hidden = !waiting || gameEnded', page.data)
        self.assertIn(b'setWaitingForEngine(true);', page.data)
        self.assertIn(b'id="help-button"', page.data)
        self.assertIn(b'id="maia-elo"', page.data)
        self.assertIn(b'id="player-elo"', page.data)
        self.assertIn(b'id="copy-pgn-button"', page.data)
        self.assertIn(b'id="pgn-fallback"', page.data)
        self.assertIn(b'id="record-button"', page.data)
        self.assertIn(b'id="stop-recording-button"', page.data)
        self.assertIn(b'fetch("/api/transcribe"', page.data)
        self.assertIn(b"Review the transcript, edit it if needed, then select Send.", page.data)
        self.assertIn(b'function focusMoveInput()', page.data)
        self.assertIn(b'input.focus({ preventScroll: true })', page.data)
        self.assertIn(b'function scrollHistoryToBottom()', page.data)
        self.assertIn(b'historyElement.lastElementChild?.scrollIntoView({ block: "end" })', page.data)
        self.assertIn(b'button.addEventListener("pointerdown"', page.data)
        self.assertIn(b'event.preventDefault();', page.data)
        self.assertIn(b'input.disabled = gameEnded', page.data)
        self.assertIn(b'if (waitingForEngine || gameEnded) return;', page.data)
        self.assertIn(b'enterkeyhint="send"', page.data)
        self.assertIn(b'focusMoveInput();', page.data)
        self.assertEqual(history.json["history"], [])


if __name__ == "__main__":
    unittest.main()
