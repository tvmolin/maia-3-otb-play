import json
from io import BytesIO
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from urllib.error import URLError
from unittest.mock import patch

import chess

from maia_game import GameSession
from web_app import (
    configured_ssl_context,
    create_app,
    request_ollama_interpretation,
    spoken_chess_move,
)


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
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.player_data_path = Path(self.storage.name) / "ladder.json"
        self.app = create_app(
            FakeEngine(),
            player_data_path=self.player_data_path,
        ).test_client()
        self.app.post("/api/start", json={"color": "white"})

    def wait_for_maia(self, client=None, timeout=3):
        client = client or self.app
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = client.get("/api/history")
            self.assertEqual(response.status_code, 200)
            if not response.json["maia_thinking"]:
                return response.json
            Event().wait(0.01)
        self.fail("Maia did not finish within the test timeout.")

    def test_move_is_returned_before_maias_reply(self):
        waiting = Event()
        continue_thinking = Event()

        def pause_think(_session, _seconds):
            waiting.set()
            continue_thinking.wait(2)

        with patch.object(GameSession, "wait_for_maia_think", pause_think):
            response = self.app.post("/api/move", json={"move": "e4"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["messages"], [{"role": "You", "text": "e4"}])
            self.assertEqual(response.json["history"], response.json["messages"])
            self.assertTrue(response.json["maia_thinking"])
            self.assertTrue(waiting.wait(2))
            continue_thinking.set()

        state = self.wait_for_maia()
        self.assertEqual(
            state["history"],
            [
                {"role": "You", "text": "e4"},
                {"role": "Engine", "text": "e5"},
            ],
        )
        history = self.app.get("/api/history?after=1")
        self.assertEqual(history.json["speech_text"], "pawn to e five.")

    def test_spoken_move_describes_castling_and_checkmate(self):
        castle_board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
        self.assertEqual(
            spoken_chess_move(castle_board, castle_board.parse_san("O-O")),
            "Castle kingside.",
        )
        self.assertEqual(
            spoken_chess_move(castle_board, castle_board.parse_san("O-O-O")),
            "Castle queenside.",
        )

        mate_board = chess.Board()
        for move in ("f3", "e5", "g4"):
            mate_board.push_san(move)
        self.assertEqual(
            spoken_chess_move(mate_board, mate_board.parse_san("Qh4#")),
            "queen to h four, checkmate.",
        )

    def test_spoken_capture_includes_piece_and_destination(self):
        board = chess.Board("7k/8/2n5/4P3/8/8/7P/K7 b - - 0 1")
        self.assertEqual(
            spoken_chess_move(board, board.parse_san("Nxe5")),
            "knight takes e five.",
        )

    def test_speech_endpoint_returns_audio_from_local_synthesizer(self):
        spoken = []

        def synthesize(text):
            spoken.append(text)
            return b"local wav data"

        client = create_app(
            FakeEngine(),
            speech_synthesizer=synthesize,
        ).test_client()
        response = client.post(
            "/api/speech",
            json={"text": "Knight to f three."},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "audio/wav")
        self.assertEqual(response.data, b"local wav data")
        self.assertEqual(spoken, ["Knight to f three."])

    def test_speech_endpoint_reports_missing_local_voice(self):
        client = create_app(FakeEngine()).test_client()
        with patch(
            "web_app.create_piper_synthesizer",
            side_effect=FileNotFoundError("voice missing"),
        ):
            response = client.post("/api/speech", json={"text": "Pawn to e four."})

        self.assertEqual(response.status_code, 503)
        self.assertIn("download its voice model", response.json["error"])

    def test_move_accepts_uci_notation(self):
        with patch.object(GameSession, "wait_for_maia_think"):
            response = self.app.post("/api/move", json={"move": "e2e4"})
            self.wait_for_maia()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["messages"][0]["text"], "e4")

    def test_move_accepts_typed_san_knight_notation(self):
        with patch.object(GameSession, "wait_for_maia_think"):
            response = self.app.post("/api/command", json={"text": "Nf3"})
            self.wait_for_maia()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["messages"], [{"role": "You", "text": "Nf3"}])

    def test_invalid_san_reports_san_error_instead_of_uci_error(self):
        response = self.app.post("/api/move", json={"move": "Nf4"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("illegal san", response.json["error"])
        self.assertNotIn("uci string", response.json["error"])

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
            state = self.wait_for_maia(client)

        self.assertEqual(response.status_code, 200)
        game = client.application.config["CHESS_GAME"]
        self.assertEqual(len(game.board.move_stack), 0)
        self.assertEqual(game.history, [])
        self.assertIn("your move was undone", state["maia_error"])

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

    def test_voice_command_interprets_and_uses_existing_engine_flow(self):
        transcriber = FakeWhisper()
        client = create_app(FakeEngine(), transcriber=transcriber).test_client()
        client.post("/api/start", json={"color": "white"})

        with patch.object(GameSession, "wait_for_maia_think") as wait, patch(
            "web_app.request_ollama_interpretation",
            return_value={"status": "move", "uci": "g1f3"},
        ) as interpret:
            response = client.post(
                "/api/voice-command",
                data={
                    "audio": (
                        BytesIO(b"local test audio"),
                        "recording.webm",
                        "audio/webm",
                    )
                },
                content_type="multipart/form-data",
            )
            state = self.wait_for_maia(client)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["transcript"], "knight f3")
        self.assertEqual(response.json["interpreted_uci"], "g1f3")
        self.assertEqual(response.json["messages"], [{"role": "You", "text": "Nf3"}])
        interpret.assert_called_once()
        self.assertEqual(interpret.call_args.args[0], "knight f3")
        self.assertEqual(state["history"][-1], {"role": "Engine", "text": "e5"})
        self.assertEqual(client.get("/api/history?after=1").json["speech_text"], "pawn to e five.")
        wait.assert_called_once()

    def test_voice_command_rejects_ambiguous_or_illegal_interpretations(self):
        for interpretation in (
            {"status": "clarify", "question": "Which knight?"},
            {"status": "move", "uci": "e2e5"},
        ):
            with self.subTest(interpretation=interpretation):
                client = create_app(
                    FakeEngine(),
                    transcriber=FakeWhisper(),
                ).test_client()
                client.post("/api/start", json={"color": "white"})

                with patch(
                    "web_app.request_ollama_interpretation",
                    return_value=interpretation,
                ):
                    response = client.post(
                        "/api/voice-command",
                        data={
                            "audio": (
                                BytesIO(b"local test audio"),
                                "recording.webm",
                                "audio/webm",
                            )
                        },
                    )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    client.application.config["CHESS_GAME"].history,
                    [],
                )
                self.assertEqual(
                    len(client.application.config["CHESS_GAME"].board.move_stack),
                    0,
                )

    def test_voice_command_reports_ollama_unavailable_without_playing(self):
        client = create_app(
            FakeEngine(),
            transcriber=FakeWhisper(),
        ).test_client()
        client.post("/api/start", json={"color": "white"})

        with patch(
            "web_app.request_ollama_interpretation",
            side_effect=URLError("Ollama is not running"),
        ):
            response = client.post(
                "/api/voice-command",
                data={
                    "audio": (
                        BytesIO(b"local test audio"),
                        "recording.webm",
                        "audio/webm",
                    )
                },
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Start Ollama", response.json["error"])
        self.assertEqual(client.application.config["CHESS_GAME"].history, [])

    def test_ollama_request_is_local_and_contains_legal_candidates(self):
        model_response = {
            "response": json.dumps({"status": "move", "uci": "e2e4"})
        }
        with patch(
            "web_app.urlopen",
            return_value=BytesIO(json.dumps(model_response).encode("utf-8")),
        ) as open_url, patch.dict("os.environ", {"OLLAMA_MODEL": "qwen2.5:7b"}):
            interpretation = request_ollama_interpretation(
                "pawn to e4",
                chess.Board(),
            )

        self.assertEqual(interpretation, {"status": "move", "uci": "e2e4"})
        ollama_request = open_url.call_args.args[0]
        self.assertEqual(
            ollama_request.full_url,
            "http://127.0.0.1:11434/api/generate",
        )
        request_body = json.loads(ollama_request.data)
        prompt = json.loads(request_body["prompt"])
        self.assertEqual(request_body["model"], "qwen2.5:7b")
        self.assertTrue(request_body["format"]["additionalProperties"] is False)
        self.assertIn(
            {"uci": "e2e4", "san": "e4"},
            prompt["legal_moves"],
        )
        self.assertIn("untrusted data", request_body["system"])

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
        self.assertEqual(response.json["speech_text"], "pawn to e four.")
        game = client.application.config["CHESS_GAME"]
        self.assertEqual(game.board.turn, chess.BLACK)

    def test_random_color_can_choose_black_and_maia_makes_opening_move(self):
        client = create_app(
            FakeEngine(move="e4"),
            player_data_path=self.player_data_path,
        ).test_client()

        with patch("web_app.random.choice", return_value=chess.BLACK), patch.object(
            GameSession, "wait_for_maia_think"
        ):
            response = client.post("/api/start", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["player_color"], "black")
        self.assertEqual(response.json["history"], [{"role": "Engine", "text": "e4"}])

    def test_black_undo_replays_maias_opening_move(self):
        client = create_app(FakeEngine(move="e4")).test_client()

        with patch.object(GameSession, "wait_for_maia_think"):
            client.post("/api/start", json={"color": "black"})
            response = client.post("/api/command", json={"text": "undo"})
            state = self.wait_for_maia(client)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["action"], "undo")
        self.assertEqual(state["history"], [{"role": "Engine", "text": "e4"}])
        self.assertEqual(client.get("/api/history?after=0").json["speech_text"], "pawn to e four.")
        game = client.application.config["CHESS_GAME"]
        self.assertEqual(game.board.turn, chess.BLACK)

    def test_ladder_assigns_player_and_compensated_maia_ratings(self):
        engine = FakeEngine()
        client = create_app(
            engine,
            player_data_path=self.player_data_path,
        ).test_client()

        with patch("web_app.random.randint", return_value=1250) as rating_picker, patch(
            "web_app.random.uniform", return_value=0.6
        ):
            response = client.post("/api/start", json={"color": "white"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["maia_elo"], 1250)
        self.assertEqual(response.json["player_elo"], 1200)
        self.assertEqual(response.json["rating"], 1200)
        self.assertEqual(
            engine.configured[-1],
            {"SelfElo": 1050, "OppoElo": 1200, "Temperature": "0.6"},
        )
        rating_picker.assert_called_once_with(1100, 1300)

    def test_client_cannot_override_ladder_ratings(self):
        client = create_app(
            FakeEngine(),
            player_data_path=self.player_data_path,
        ).test_client()

        response = client.post(
            "/api/start",
            json={"color": "white", "maia_elo": 6000, "player_elo": 760},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("assigned by the ladder", response.json["error"])
        self.assertFalse(client.application.config["CHESS_GAME"].started)

    def test_profile_import_and_delete_use_the_saved_game_and_recalculate_rating(self):
        class FakeImporter:
            def __init__(self):
                self.enqueued = None

            def enqueue(self, path, retry=False, fallback_pgn=None):
                self.enqueued = (path, retry, fallback_pgn)
                return True

            def remove_local_game_files(self, path):
                path.unlink(missing_ok=True)
                path.with_suffix(".pgn.lichess.json").unlink(missing_ok=True)

        importer = FakeImporter()
        client = create_app(
            FakeEngine(),
            lichess_importer=importer,
            player_data_path=self.player_data_path,
        ).test_client()
        pgn_path = self.player_data_path.parent / "saved-game.pgn"
        pgn = '[Event "OTB Maia game"]\n[Result "1-0"]\n\n1. e4 1-0\n'
        pgn_path.write_text(pgn, encoding="utf-8")
        ladder = client.application.config["OTB_LADDER"]
        ladder.record_match({
            "match_id": "match-1",
            "rating_category": "rapid",
            "result": "1-0",
            "player_color": "white",
            "maia_elo": 1200,
            "opponent_name": "Maia",
            "time_control": "OTB",
            "pgn_file": pgn_path.name,
            "pgn": pgn,
        })

        profile = client.get("/profile")
        self.assertEqual(profile.status_code, 200)
        self.assertIn(b'"singleCategory": true', profile.data)
        self.assertIn(b'"ratingLabel": "Rapid"', profile.data)
        self.assertIn(b"saved-game.pgn", profile.data)

        imported = client.post("/api/lichess/import/match-1")
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json["status"], "pending")
        self.assertEqual(importer.enqueued, (pgn_path, True, pgn))

        deleted = client.delete("/api/matches/rapid/match-1")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json["rating"], 1200)
        self.assertFalse(pgn_path.exists())
        self.assertEqual(ladder.matches_for("rapid"), [])

    def test_shared_commands_work_over_http(self):
        help_response = self.app.post("/api/command", json={"text": "help"})
        self.assertEqual(help_response.json["action"], "help")
        self.assertIn("undo", help_response.json["message"])

        with patch.object(GameSession, "wait_for_maia_think"):
            self.app.post("/api/command", json={"text": "e4"})
            self.wait_for_maia()
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
        resign_client = create_app(
            FakeEngine(move="e4"),
            player_data_path=self.player_data_path,
        ).test_client()
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
            self.wait_for_maia()

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
            FakeEngine(moves=["e5", "Qh4#"]),
            player_data_path=self.player_data_path,
        ).test_client()
        with patch("web_app.random.randint", return_value=1200), patch(
            "web_app.random.uniform", return_value=0.8
        ):
            client.post("/api/start", json={"color": "white"})

        with patch("web_app.random.randint", return_value=1200), patch(
            "web_app.random.uniform", return_value=0.8
        ), patch.object(GameSession, "wait_for_maia_think"), patch(
            "maia_game.save_pgn", return_value=Path("completed-game.pgn")
        ) as save_pgn:
            client.post("/api/command", json={"text": "f3"})
            self.wait_for_maia(client)
            response = client.post("/api/command", json={"text": "g4"})
            state = self.wait_for_maia(client)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(state["result"], "0-1")
            self.assertEqual(state["saved_pgn"], "completed-game.pgn")
            self.assertEqual(state["rating_change"], -20)
            self.assertEqual(state["rating"], 1180)
            save_pgn.assert_called_once()

            saved_pgn = save_pgn.call_args.args[0]
            self.assertIn('[Result "0-1"]', saved_pgn)
            ladder = client.application.config["OTB_LADDER"]
            self.assertEqual(len(ladder.matches_for("rapid")), 1)
            self.assertEqual(ladder.rating_for("rapid"), 1180)
            profile = client.get("/profile")
            self.assertEqual(profile.status_code, 200)
            self.assertIn(b"Rapid profile", profile.data)
            self.assertIn(b"completed-game.pgn", profile.data)
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
            state = self.wait_for_maia(client)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(worker.is_alive())
        self.assertEqual(move_response["response"].status_code, 200)
        self.assertEqual(state["history"][-1], {"role": "Engine", "text": "e5"})

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
            state = self.wait_for_maia(client)

        self.assertFalse(worker.is_alive())
        self.assertEqual(go_response.json["action"], "go")
        self.assertEqual(move_response["response"].json["history"], [{"role": "You", "text": "e4"}])
        self.assertEqual(state["history"][-1]["text"], "e5")

    def test_homepage_and_empty_history_load(self):
        page = self.app.get("/")
        history = self.app.get("/api/history")

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Maia OTB Chess", page.data)
        self.assertIn(b'id="profile-link"', page.data)
        self.assertIn(b"Rapid rating", page.data)
        self.assertIn(b'<option value="random" selected>Random</option>', page.data)
        self.assertIn(b"radial-gradient(ellipse at 18% 0%", page.data)
        self.assertIn(b"#setup[hidden] { display: none; }", page.data)
        self.assertIn(b"#voice-controls:not([hidden])", page.data)
        self.assertIn(b"position: fixed;", page.data)
        self.assertIn(b"white-space: nowrap;", page.data)
        self.assertIn(b'id="go-button"', page.data)
        self.assertIn(b'input.disabled = gameEnded', page.data)
        self.assertIn(b'button.disabled = waiting || gameEnded', page.data)
        self.assertIn(b'input.value = "";', page.data)
        self.assertIn(b'goButton.hidden = !waiting || gameEnded', page.data)
        self.assertIn(b'setWaitingForEngine(true);', page.data)
        self.assertIn(b'id="help-button"', page.data)
        self.assertNotIn(b'id="maia-elo"', page.data)
        self.assertNotIn(b'id="player-elo"', page.data)
        self.assertIn(b'id="copy-pgn-button"', page.data)
        self.assertIn(b'id="pgn-fallback"', page.data)
        self.assertIn(b'id="record-button"', page.data)
        self.assertIn(b'id="stop-recording-button"', page.data)
        self.assertIn(b'id="speak-maia-moves"', page.data)
        self.assertIn(b"Speak move", page.data)
        self.assertIn(b"Send move", page.data)
        self.assertIn(b'fetch("/api/voice-command"', page.data)
        self.assertIn(b'fetch("/api/speech"', page.data)
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
