import os
import tempfile
from pathlib import Path

import chess
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from lichess_import import configured_importer
from maia_game import GameSession, create_engine

MAIA_ELO = 800
PLAYER_ELO = 760
MAX_AUDIO_UPLOAD_BYTES = 25 * 1024 * 1024
AUDIO_SUFFIXES = {
    "audio/3gpp": ".3gp",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-wav": ".wav",
}


def create_whisper_model():
    import ctranslate2
    from faster_whisper import WhisperModel

    if ctranslate2.get_cuda_device_count() == 0:
        raise RuntimeError("No CUDA device is available to CTranslate2.")
    supported_compute_types = ctranslate2.get_supported_compute_types("cuda")
    compute_type = next(
        (
            candidate
            for candidate in ("float16", "int8_float16", "int8", "int8_float32", "float32")
            if candidate in supported_compute_types
        ),
        None,
    )
    if compute_type is None:
        raise RuntimeError("CTranslate2 found no supported CUDA compute type.")

    return WhisperModel(
        os.environ.get("WHISPER_MODEL", "small"),
        device="cuda",
        compute_type=compute_type,
    )


def configured_ssl_context():
    ssl_cert = os.environ.get("MAIA_SSL_CERT")
    ssl_key = os.environ.get("MAIA_SSL_KEY")

    if not ssl_cert and not ssl_key:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            cert_dir = Path(local_app_data) / "Maia-OTB" / "certs"
            default_cert = cert_dir / "server.pem"
            default_key = cert_dir / "server-key.pem"
            if default_cert.exists() or default_key.exists():
                ssl_cert = str(default_cert)
                ssl_key = str(default_key)

    if bool(ssl_cert) != bool(ssl_key):
        raise RuntimeError("Set both MAIA_SSL_CERT and MAIA_SSL_KEY for HTTPS.")
    if not ssl_cert:
        return None
    if not Path(ssl_cert).is_file() or not Path(ssl_key).is_file():
        raise FileNotFoundError("The configured HTTPS certificate or key was not found.")
    return ssl_cert, ssl_key


def create_app(engine, board=None, lichess_importer=None, transcriber=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_AUDIO_UPLOAD_BYTES
    game = GameSession(
        engine,
        player_color=chess.WHITE,
        player_name="You",
        player_elo=PLAYER_ELO,
        maia_elo=MAIA_ELO,
        board=board,
        started=False,
        lichess_importer=lichess_importer,
    )
    app.config["CHESS_GAME"] = game
    whisper_model = transcriber

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify(error="Recordings must be smaller than 25 MB."), 413

    def game_state(**extra):
        with game.lock:
            state = {
                "history": list(game.history),
                "started": game.started,
                "player_color": game.player_color_name if game.started else None,
                "maia_elo": game.maia_elo,
                "player_elo": game.player_elo,
                "maia_thinking": game.is_thinking,
                "game_over": game.is_game_over,
                "quit": game.quit_requested,
                "result": game.result if game.is_game_over else None,
                "saved_pgn": (
                    game.saved_pgn_path.name if game.saved_pgn_path is not None else None
                ),
            }
        state.update(extra)
        return jsonify(state)

    def play_maia_turn():
        turn = game.choose_maia_move()
        game.wait_for_maia_think(turn.think_seconds)
        return game.play_maia_move(turn)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/history")
    def get_history():
        with game.lock:
            return game_state()

    @app.get("/api/pgn")
    def get_pgn():
        with game.lock:
            if not game.started:
                return jsonify(error="No game has been started."), 409
            if not game.is_game_over:
                return jsonify(error="PGN is available when the game ends."), 409
            return jsonify(pgn=game.build_pgn(game.result))

    @app.post("/api/transcribe")
    def transcribe_audio():
        nonlocal whisper_model

        audio_file = request.files.get("audio")
        if audio_file is None:
            return jsonify(error="Upload a recording in the 'audio' field."), 400
        suffix = AUDIO_SUFFIXES.get(audio_file.mimetype)
        if suffix is None:
            return (
                jsonify(error="Use a WebM, Ogg, MP4, WAV, MP3, AAC, FLAC, or 3GPP recording."),
                415,
            )

        with tempfile.TemporaryDirectory(prefix="maia-whisper-") as directory:
            audio_path = Path(directory) / f"recording{suffix}"
            audio_file.save(audio_path)
            if audio_path.stat().st_size == 0:
                return jsonify(error="The recording is empty."), 400

            if whisper_model is None:
                try:
                    whisper_model = create_whisper_model()
                except Exception:
                    app.logger.exception("Failed to load local Whisper on the GPU.")
                    return (
                        jsonify(
                            error=(
                                "Local Whisper could not start on the GPU. "
                                "Check the CUDA/cuDNN setup and server logs."
                            )
                        ),
                        503,
                    )

            try:
                segments, _info = whisper_model.transcribe(str(audio_path), beam_size=5)
                transcript = " ".join(
                    segment.text.strip()
                    for segment in segments
                    if segment.text.strip()
                )
            except Exception:
                app.logger.exception("Local Whisper failed to transcribe the recording.")
                return (
                    jsonify(
                        error=(
                            "Whisper could not transcribe this recording. "
                            "Check its format, CUDA/cuDNN libraries, and server logs."
                        )
                    ),
                    500,
                )

        return jsonify(text=transcript)

    @app.post("/api/start")
    def start_game():
        payload = request.get_json(silent=True)
        color = payload.get("color") if isinstance(payload, dict) else None
        if isinstance(color, str):
            color = color.lower()
        if color not in ("white", "black"):
            return jsonify(error="Choose either white or black."), 400
        maia_elo = payload.get("maia_elo") if isinstance(payload, dict) else None
        player_elo = payload.get("player_elo") if isinstance(payload, dict) else None
        if maia_elo is None:
            maia_elo = game.maia_elo
        if player_elo is None:
            player_elo = game.player_elo
        try:
            started_now = game.start(
                chess.WHITE if color == "white" else chess.BLACK,
                maia_elo=maia_elo,
                player_elo=player_elo,
            )
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except Exception:
            app.logger.exception("Failed to configure Maia for the new game.")
            return jsonify(error="Could not configure the engine for this game."), 503

        messages = []
        if started_now and color == "black" and game.board.turn != game.player_color:
            try:
                san = play_maia_turn()
                messages = [{"role": "Engine", "text": san}]
            except Exception:
                game.cancel_start()
                app.logger.exception("Maia failed to make the opening move.")
                return (
                    jsonify(error="The engine failed to respond; check the server console."),
                    503,
                )
        return game_state(messages=messages)

    @app.post("/api/new-game")
    def new_game():
        try:
            with game.lock:
                if game.is_thinking:
                    raise ValueError(
                        "Maia is thinking; wait for her move or use 'go' first."
                    )
                saved_pgn = None
                if game.started and game.is_game_over:
                    saved_pgn = game.save_pgn_file()
                game.reset()
        except ValueError as error:
            return jsonify(error=str(error)), 409
        except Exception:
            app.logger.exception("Failed to save the previous game.")
            return jsonify(error="Could not save the previous game."), 500
        return game_state(
            saved_pgn=saved_pgn.name if saved_pgn is not None else None
        )

    @app.post("/api/command")
    @app.post("/api/move")
    def post_command():
        payload = request.get_json(silent=True)
        text = None
        if isinstance(payload, dict):
            text = payload.get("text", payload.get("move"))
        if not isinstance(text, str):
            return jsonify(error="Send a command or move as a string."), 400

        try:
            command = game.handle_player_command(text)
        except ValueError as error:
            return jsonify(error=str(error)), 400

        messages = []
        if command["action"] == "move":
            messages.append({"role": "You", "text": command["san"]})
            if not game.is_game_over:
                try:
                    messages.append({"role": "Engine", "text": play_maia_turn()})
                except Exception:
                    game.undo_plies(1)
                    app.logger.exception("Failed to process the chess move.")
                    return (
                        jsonify(error="The engine failed to respond; check the server console."),
                        503,
                    )
        elif command["action"] == "undo" and not game.is_game_over:
            if game.board.turn != game.player_color:
                try:
                    messages.append({"role": "Engine", "text": play_maia_turn()})
                except Exception:
                    app.logger.exception("Maia failed to resume after undo.")
                    return (
                        jsonify(error="The engine failed to respond; check the server console."),
                        503,
                    )
        saved_pgn = None
        if game.started and game.is_game_over:
            try:
                saved_pgn = game.save_pgn_file()
            except Exception:
                app.logger.exception("Failed to save the completed game PGN.")
                return (
                    jsonify(
                        error=(
                            "The game ended, but its PGN could not be saved. "
                            "Start a new game to retry saving."
                        )
                    ),
                    500,
                )
        return game_state(
            action=command["action"],
            message=command["message"],
            messages=messages,
            saved_pgn=saved_pgn.name if saved_pgn is not None else None,
        )

    return app


def main():
    print("Loading Maia model...", flush=True)
    engine = create_engine(MAIA_ELO, PLAYER_ELO)
    app = None
    importer = configured_importer()
    try:
        app = create_app(engine, lichess_importer=importer)
        host = os.environ.get("MAIA_HOST", "0.0.0.0")
        ssl_context = configured_ssl_context()
        if ssl_context:
            print("Starting local chess server with HTTPS on port 5000", flush=True)
            print("Open https://<PC-IP>:5000 on a device on the same Wi-Fi network.")
        else:
            print(f"Starting local chess server on {host}:5000", flush=True)
        if not ssl_context and host == "0.0.0.0":
            print("Open http://<PC-IP>:5000 on a device on the same Wi-Fi network.")
        elif not ssl_context:
            print("The server is bound to this PC only; use its configured HTTPS proxy.")
        app.run(
            host=host,
            port=5000,
            debug=False,
            use_reloader=False,
            ssl_context=ssl_context,
        )
    finally:
        try:
            engine.quit()
        finally:
            if importer is not None:
                importer.close()


if __name__ == "__main__":
    main()
