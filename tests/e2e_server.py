"""Deterministic Chessground app server for the browser (Playwright) tests.

Uses a scripted fake engine and temporary storage, so it never loads a model or
touches the real ratings and games folders. POST /__e2e__/reset starts a fresh
app for each test.
"""

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import chess
from werkzeug.serving import run_simple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chessground_app  # noqa: E402
import lichess_import  # noqa: E402
import maia_game  # noqa: E402

DEFAULT_THINK_SECONDS = 0.3
DEFAULT_CLOCK = (180, 2)


class ScriptedEngine:
    """Plays the scripted SAN moves, then the first legal move in UCI order."""

    def __init__(self, moves=(), fail=False):
        self.moves = list(moves)
        self.fail = fail

    def configure(self, options):
        pass

    def play(self, board, limit, info=None):
        if self.fail:
            raise RuntimeError("scripted engine failure")
        if self.moves:
            move = board.parse_san(self.moves.pop(0))
        else:
            move = min(board.legal_moves, key=lambda legal: legal.uci())
        return SimpleNamespace(move=move, info={"string": "policy_entropy 0.5"})


class Server:
    def __init__(self):
        self.storage = Path(tempfile.mkdtemp(prefix="maia-e2e-"))
        self.think_seconds = DEFAULT_THINK_SECONDS
        self.generation = 0
        self.app = None
        self.reset({})

    def reset(self, options):
        self.generation += 1
        self.think_seconds = options.get("think_seconds", DEFAULT_THINK_SECONDS)
        chessground_app.TIME_CONTROLS["blitz"]["3+2"] = (
            tuple(options["clock"]) if "clock" in options else DEFAULT_CLOCK
        )
        board = chess.Board(options["fen"]) if "fen" in options else None
        engine = ScriptedEngine(options.get("engine_moves", ()), options.get("engine_fails", False))
        failures = [options.get("lichess_fail", False)]

        def import_game(token, pgn):
            time.sleep(options.get("lichess_delay", 0))
            if failures.pop() if failures else False:
                raise RuntimeError("HTTP 429")
            return "https://lichess.org/abcd1234/analysis"

        lichess_import.import_game = import_game
        importer = (
            lichess_import.LichessImporter("test-token", self.storage)
            if options.get("lichess") else None
        )
        self.app = chessground_app.create_app(
            engine,
            board=board,
            player_data_path=self.storage / f"ladder-{self.generation}.json",
            lichess_importer=importer,
        )
        if options.get("legacy_match"):
            self.app.config["CHESSGROUND_LADDER"].record_match({
                "match_id": "legacy-match",
                "finished_at": "2026-01-01T12:00:00Z",
                "result": "1-0",
                "player_color": "white",
                "rating_category": "blitz",
                "maia_elo": 800,
                "time_control": "3+2",
                "pgn_file": f"legacy-{self.generation}.pgn",
                "pgn": "1. e4 e5 1-0",
            })

    def __call__(self, environ, start_response):
        if environ["PATH_INFO"] == "/__e2e__/reset" and environ["REQUEST_METHOD"] == "POST":
            length = int(environ.get("CONTENT_LENGTH") or 0)
            body = environ["wsgi.input"].read(length)
            self.reset(json.loads(body) if body else {})
            start_response("204 No Content", [])
            return [b""]
        return self.app(environ, start_response)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5055
    server = Server()

    def save_pgn(pgn_text):
        path = server.storage / f"game-{server.generation}-{threading.get_ident()}.pgn"
        path.write_text(pgn_text + "\n", encoding="utf-8")
        return path

    def wait_for_maia_think(session, seconds):
        session._think_skip.wait(server.think_seconds if seconds > 0 else 0)

    maia_game.save_pgn = save_pgn
    maia_game.GameSession.wait_for_maia_think = wait_for_maia_think
    print(f"E2E server at http://127.0.0.1:{port}/", flush=True)
    run_simple("127.0.0.1", port, server, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
