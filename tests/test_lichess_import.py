import io
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs

import chess

from chessground_app import create_app
from lichess_import import LichessImporter, configured_importer, import_game, read_status
from maia_game import GameSession


class FakeEngine:
    def configure(self, options):
        pass


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.pgn = self.directory / "game.pgn"
        self.pgn.write_text("1. e4 e5 1-0\n", encoding="utf-8")

    def test_api_request_encodes_pgn_and_validates_response(self):
        class Response(io.BytesIO):
            pass

        with patch("lichess_import.build_opener") as opener:
            opener.return_value.open.return_value = Response(
                b'{"id":"abcd1234","url":"https://lichess.org/abcd1234"}'
            )
            url = import_game("test-token", "1. e4 e5 1-0")
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://lichess.org/api/import")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(parse_qs(request.data.decode()), {"pgn": ["1. e4 e5 1-0"]})
            self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
            self.assertTrue(request.get_header("User-agent"))
            self.assertEqual(url, "https://lichess.org/abcd1234/analysis")

            opener.return_value.open.return_value = Response(b'{"id":"../../bad"}')
            with self.assertRaisesRegex(RuntimeError, "invalid game ID"):
                import_game("test-token", "1. e4")

    def test_http_failure_does_not_expose_token(self):
        error = HTTPError("https://lichess.org/api/import", 429, "limited", {}, None)
        with patch("lichess_import.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaisesRegex(RuntimeError, "HTTP 429") as raised:
                import_game("test-token", "1. e4")
        self.assertNotIn("test-token", str(raised.exception))

    def test_completed_game_is_queued_once_and_link_survives_restart(self):
        importer = LichessImporter("test-token", self.directory)
        with patch("lichess_import.import_game", return_value="https://lichess.org/abcd1234/analysis") as upload:
            session = GameSession(FakeEngine(), lichess_importer=importer)
            session.final_result = "1-0"
            with patch("maia_game.save_pgn", return_value=self.pgn):
                session.save_pgn_file()
                session.save_pgn_file()
            importer.close()
        upload.assert_called_once()
        self.assertEqual(read_status(self.pgn), {
            "status": "imported", "url": "https://lichess.org/abcd1234/analysis"
        })
        restarted = LichessImporter("test-token", self.directory)
        self.assertFalse(restarted.enqueue(self.pgn))
        restarted.close()

    def test_failed_import_retry_and_interrupted_upload(self):
        importer = LichessImporter("test-token", self.directory)
        with patch("lichess_import.import_game", side_effect=RuntimeError("HTTP 429")):
            self.assertTrue(importer.enqueue(self.pgn))
            importer.close()
        self.assertEqual(read_status(self.pgn)["status"], "failed")

        importer = LichessImporter("test-token", self.directory)
        with patch("lichess_import.import_game", return_value="https://lichess.org/abcd1234/analysis"):
            self.assertTrue(importer.enqueue(self.pgn, retry=True))
            importer.close()
        self.assertEqual(read_status(self.pgn)["status"], "imported")

        self.pgn.with_suffix(".pgn.lichess.json").write_text(
            '{"status":"uploading"}', encoding="utf-8"
        )
        importer = LichessImporter("test-token", self.directory)
        importer.close()
        self.assertIn("check Lichess", importer.status(self.pgn)["error"])

    def test_pending_import_resumes_after_restart(self):
        self.pgn.with_suffix(".pgn.lichess.json").write_text(
            '{"status":"pending"}', encoding="utf-8"
        )
        with patch("lichess_import.import_game", return_value="https://lichess.org/abcd1234/analysis") as upload:
            importer = LichessImporter("test-token", self.directory)
            importer.close()
        upload.assert_called_once()
        self.assertEqual(read_status(self.pgn)["status"], "imported")

    def test_second_app_does_not_duplicate_an_inflight_import(self):
        started = Event()
        finish = Event()

        def upload(token, pgn):
            started.set()
            self.assertTrue(finish.wait(5))
            return "https://lichess.org/abcd1234/analysis"

        first = LichessImporter("test-token", self.directory)
        with patch("lichess_import.import_game", side_effect=upload) as mock_upload:
            self.assertTrue(first.enqueue(self.pgn))
            self.assertTrue(started.wait(5))
            second = LichessImporter("test-token", self.directory)
            finish.set()
            first.close()
            second.close()
        mock_upload.assert_called_once()
        self.assertEqual(read_status(self.pgn)["status"], "imported")

    def test_token_uses_environment_over_dotenv(self):
        with patch.dict("os.environ", {"LICHESS_API_TOKEN": "env-token"}):
            with patch("lichess_import.dotenv_values") as dotenv:
                importer = configured_importer(self.directory)
                self.assertEqual(importer.token, "env-token")
                importer.close()
                dotenv.assert_not_called()
        with patch.dict("os.environ", {}, clear=True), patch(
            "lichess_import.dotenv_values", return_value={"LICHESS_API_TOKEN": "file-token"}
        ):
            importer = configured_importer(self.directory)
            self.assertEqual(importer.token, "file-token")
            importer.close()

    def test_profile_link_and_retry_use_saved_match(self):
        importer = LichessImporter("test-token", self.directory)
        self.addCleanup(importer.close)
        client = create_app(
            FakeEngine(), player_data_path=self.directory / "ladder.json",
            lichess_importer=importer,
        ).test_client()
        with patch("maia_game.save_pgn", return_value=self.pgn), patch(
            "lichess_import.import_game", side_effect=RuntimeError("HTTP 429")
        ):
            client.post("/api/start", json={"color": "white"})
            response = client.post("/api/command", json={"command": "resign"})
            self.assertEqual(response.status_code, 200)
            importer.executor.shutdown(wait=True)
        self.assertEqual(read_status(self.pgn)["status"], "failed")
        self.assertEqual(len(client.application.config["CHESSGROUND_LADDER"].matches_for("blitz")), 1)

        # A new service represents the next app run after a failed upload.
        importer = LichessImporter("test-token", self.directory)
        self.addCleanup(importer.close)
        client = create_app(
            FakeEngine(), player_data_path=self.directory / "ladder.json",
            lichess_importer=importer,
        ).test_client()
        match = client.application.config["CHESSGROUND_LADDER"].matches_for("blitz")[0]
        self.assertIn(b"HTTP 429", client.get("/profile").data)
        with patch("lichess_import.import_game", return_value="https://lichess.org/abcd1234/analysis"):
            self.assertEqual(client.post(f"/api/lichess/retry/{match['match_id']}").status_code, 200)
            importer.executor.shutdown(wait=True)
        response = client.get("/profile")
        self.assertIn(b"https://lichess.org/abcd1234/analysis", response.data)
        self.assertEqual(client.post(f"/api/lichess/retry/{match['match_id']}").status_code, 409)
        self.assertEqual(client.post("/api/lichess/retry/unknown").status_code, 404)

    def test_imports_old_matches_from_saved_pgn_and_restores_missing_file(self):
        importer = LichessImporter("test-token", self.directory)
        self.addCleanup(importer.close)
        client = create_app(
            FakeEngine(), player_data_path=self.directory / "ladder.json",
            lichess_importer=importer,
        ).test_client()
        ladder = client.application.config["CHESSGROUND_LADDER"]
        common = {
            "result": "1-0",
            "player_color": "white",
            "rating_category": "blitz",
            "maia_elo": 800,
            "pgn": "1. d4 d5 1-0",
            "finished_at": "2026-01-01T12:00:00Z",
            "time_control": "3+2",
        }
        ladder.record_match({**common, "match_id": "missing-file", "pgn_file": "missing.pgn"})
        ladder.record_match({**common, "match_id": "missing-name"})
        ladder.record_match({
            **common, "match_id": "no-pgn", "pgn_file": "no-pgn.pgn", "pgn": None
        })

        with patch("lichess_import.import_game", return_value="https://lichess.org/abcd1234/analysis") as upload:
            self.assertEqual(client.post("/api/lichess/import/missing-file").status_code, 200)
            self.assertEqual(client.post("/api/lichess/import/missing-name").status_code, 200)
            self.assertEqual(client.post("/api/lichess/import/no-pgn").status_code, 409)
            importer.close()
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(upload.call_args.args[1], "1. d4 d5 1-0\n")
        restored = self.directory / "missing.pgn"
        self.assertEqual(restored.read_text(encoding="utf-8"), "1. d4 d5 1-0\n")
        self.assertEqual(read_status(restored)["status"], "imported")
        generated = list(self.directory.glob("lichess-match-*.pgn"))
        self.assertEqual(len(generated), 1)
        self.assertEqual(read_status(generated[0])["status"], "imported")
        self.assertEqual(client.post("/api/lichess/import/missing-file").status_code, 409)
        self.assertEqual(client.get("/profile").data.count(b"https://lichess.org/abcd1234/analysis"), 2)

    def test_profile_status_endpoint_reports_saved_import_state(self):
        client = create_app(
            FakeEngine(), player_data_path=self.directory / "ladder.json"
        ).test_client()
        ladder = client.application.config["CHESSGROUND_LADDER"]
        ladder.record_match({
            "match_id": "match-1", "result": "1-0", "player_color": "white",
            "rating_category": "blitz", "maia_elo": 800, "pgn_file": self.pgn.name,
        })
        sidecar = self.pgn.with_suffix(".pgn.lichess.json")
        sidecar.write_text('{"status":"pending"}', encoding="utf-8")
        self.assertEqual(client.get("/api/lichess/status?category=blitz").json, {
            "matches": [{"match_id": "match-1", "lichess": {"status": "pending"}}]
        })
        sidecar.write_text(
            '{"status":"imported","url":"https://lichess.org/abcd1234/analysis"}',
            encoding="utf-8",
        )
        self.assertEqual(
            client.get("/api/lichess/status?category=blitz").json["matches"][0]["lichess"]["url"],
            "https://lichess.org/abcd1234/analysis",
        )
        self.assertEqual(client.get("/api/lichess/status?category=rapid").json, {"matches": []})
        self.assertEqual(client.get("/api/lichess/status?category=blitz&match_id=other").json, {"matches": []})
        self.assertEqual(client.get("/api/lichess/status?category=invalid").status_code, 400)

    def test_unfinished_games_are_not_queued(self):
        importer = LichessImporter("test-token", self.directory)
        self.addCleanup(importer.close)
        session = GameSession(FakeEngine(), board=chess.Board(), lichess_importer=importer)
        with self.assertRaises(ValueError):
            session.save_pgn_file()
        self.assertIsNone(read_status(self.pgn))


if __name__ == "__main__":
    unittest.main()
