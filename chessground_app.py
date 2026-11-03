import logging
import threading
import webbrowser
from pathlib import Path

import chess
from flask import Flask, jsonify, render_template, request

from maia_game import GameSession, create_engine


MAIA_ELO = 800
PLAYER_ELO = 760
HOST = "127.0.0.1"
PORT = 5001


def create_app(engine, board=None):
    app = Flask(__name__)
    game = GameSession(
        engine,
        player_color=chess.WHITE,
        player_name="You",
        player_elo=PLAYER_ELO,
        maia_elo=MAIA_ELO,
        board=board,
        started=False,
    )
    app.config["CHESS_GAME"] = game
    app.config["CHESSGROUND_REQUEST_LOCK"] = threading.Lock()

    def state_data():
        with game.lock:
            board = game.board
            can_move = (
                game.started
                and not game.is_game_over
                and not game.is_thinking
                and not game.quit_requested
                and board.turn == game.player_color
            )
            destinations = {}
            promotions = {}
            if can_move:
                for move in board.legal_moves:
                    origin = chess.square_name(move.from_square)
                    destination = chess.square_name(move.to_square)
                    destinations.setdefault(origin, set()).add(destination)
                    if move.promotion:
                        key = origin + destination
                        promotions.setdefault(key, []).append(move.uci()[4:])

            last_move = (
                [
                    chess.square_name(board.peek().from_square),
                    chess.square_name(board.peek().to_square),
                ]
                if board.move_stack
                else None
            )
            king = board.king(board.turn) if board.is_check() else None
            return {
                "fen": board.board_fen(),
                "turn_color": "white" if board.turn == chess.WHITE else "black",
                "player_color": game.player_color_name if game.started else None,
                "started": game.started,
                "can_move": can_move,
                "maia_thinking": game.is_thinking,
                "game_over": game.is_game_over,
                "quit": game.quit_requested,
                "result": game.result if game.is_game_over else None,
                "check": game.board.is_check(),
                "check_square": chess.square_name(king) if king is not None else None,
                "last_move": last_move,
                "destinations": {
                    origin: sorted(targets)
                    for origin, targets in destinations.items()
                },
                "promotions": {
                    move: sorted(set(pieces))
                    for move, pieces in promotions.items()
                },
                "history": list(game.history),
                "ply": len(board.move_stack),
                "maia_elo": game.maia_elo,
                "player_elo": game.player_elo,
            }

    def state_response(status=200, error=None, **extra):
        response = {"state": state_data()}
        if error is not None:
            response["error"] = error
        response.update(extra)
        return jsonify(response), status

    def play_maia_turn():
        turn = game.choose_maia_move()
        game.wait_for_maia_think(turn.think_seconds)
        return game.play_maia_move(turn)

    def save_finished_game():
        if game.started and game.is_game_over:
            game.save_pgn_file()

    @app.get("/")
    def index():
        static_dir = Path(app.static_folder) / "chessground" / "assets"
        if not (static_dir / "app.js").is_file() or not (static_dir / "main.css").is_file():
            return (
                "Chessground frontend is not built. Run `npm ci` and `npm run build` "
                "from the frontend directory, then restart this app.",
                503,
                {"Content-Type": "text/plain; charset=utf-8"},
            )
        return render_template("chessground.html")

    @app.get("/api/state")
    def get_state():
        return jsonify(state_data())

    @app.post("/api/start")
    def start_game():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return state_response(400, "Send the game settings as a JSON object.")
        color = payload.get("color")
        if isinstance(color, str):
            color = color.lower()
        if color not in ("white", "black"):
            return state_response(400, "Choose either white or black.")

        maia_elo = payload.get("maia_elo", game.maia_elo)
        player_elo = payload.get("player_elo", game.player_elo)
        with app.config["CHESSGROUND_REQUEST_LOCK"]:
            try:
                started_now = game.start(
                    chess.WHITE if color == "white" else chess.BLACK,
                    maia_elo=maia_elo,
                    player_elo=player_elo,
                )
            except ValueError as error:
                return state_response(400, str(error))
            except Exception:
                app.logger.exception("Failed to configure Maia for Chessground.")
                return state_response(503, "Could not configure the engine.")

            if started_now and color == "black":
                try:
                    play_maia_turn()
                except Exception:
                    game.cancel_start()
                    app.logger.exception("Maia failed to make the opening move.")
                    return state_response(503, "The engine failed to make its opening move.")
        return state_response()

    @app.post("/api/move")
    def submit_move():
        payload = request.get_json(silent=True)
        move_text = payload.get("move") if isinstance(payload, dict) else None
        expected_ply = payload.get("expected_ply") if isinstance(payload, dict) else None
        if not isinstance(move_text, str) or not move_text:
            return state_response(400, "Send a UCI move.")
        if isinstance(expected_ply, bool) or not isinstance(expected_ply, int):
            return state_response(400, "Send the current ply count with the move.")

        with app.config["CHESSGROUND_REQUEST_LOCK"]:
            if expected_ply != len(game.board.move_stack):
                return state_response(409, "The position changed. The board was refreshed.")
            if not game.started:
                return state_response(409, "Start a game before moving.")
            if game.is_thinking:
                return state_response(409, "Maia is thinking.")

            try:
                game.apply_player_move(move_text)
            except ValueError as error:
                return state_response(400, str(error))
            try:
                if not game.is_game_over:
                    play_maia_turn()
                save_finished_game()
            except Exception:
                game.undo_plies(1)
                app.logger.exception("Maia failed to respond to a Chessground move.")
                return state_response(503, "The engine failed to respond.")
        return state_response()

    @app.post("/api/command")
    def command():
        payload = request.get_json(silent=True)
        text = payload.get("command") if isinstance(payload, dict) else None
        if text not in ("undo", "resign"):
            return state_response(400, "Supported commands are undo and resign.")

        with app.config["CHESSGROUND_REQUEST_LOCK"]:
            try:
                result = game.handle_player_command(text)
            except ValueError as error:
                return state_response(409, str(error))
            try:
                if result["action"] == "undo" and not game.is_game_over:
                    if game.board.turn != game.player_color:
                        play_maia_turn()
                save_finished_game()
            except Exception:
                app.logger.exception("Failed to complete a Chessground command.")
                return state_response(503, "The engine failed while processing the command.")
        return state_response(message=result["message"])

    @app.post("/api/new-game")
    def new_game():
        with app.config["CHESSGROUND_REQUEST_LOCK"]:
            try:
                if game.is_thinking:
                    raise ValueError("Maia is thinking; wait for her move to finish.")
                saved_pgn = game.save_pgn_file() if game.started else None
                game.reset()
            except ValueError as error:
                return state_response(409, str(error))
            except Exception:
                app.logger.exception("Failed to save the previous Chessground game.")
                return state_response(500, "Could not save the previous game.")
        return state_response(
            saved_pgn=saved_pgn.name if saved_pgn is not None else None
        )

    @app.get("/api/pgn")
    def get_pgn():
        with game.lock:
            if not game.started or not game.is_game_over:
                return jsonify(error="PGN is available when the game ends."), 409
            return jsonify(pgn=game.build_pgn(game.result))

    return app


def main():
    print("Loading Maia model...", flush=True)
    engine = create_engine(MAIA_ELO, PLAYER_ELO)
    app = create_app(engine)
    url = f"http://{HOST}:{PORT}/"
    print(f"Starting the Chessground app at {url}", flush=True)
    threading.Timer(1, webbrowser.open, args=(url,)).start()
    try:
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
    finally:
        try:
            engine.quit()
        finally:
            game = app.config["CHESS_GAME"]
            if game.started and game.saved_pgn_path is None:
                path = game.save_pgn_file()
                print(f"Saved unfinished game to {path}", flush=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
