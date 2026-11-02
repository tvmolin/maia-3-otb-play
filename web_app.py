import chess
from flask import Flask, jsonify, render_template, request

from maia_game import GameSession, create_engine, save_pgn

MAIA_ELO = 800
PLAYER_ELO = 760


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
                if game.started:
                    saved_pgn = save_pgn(game.build_pgn(game.result))
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
        return game_state(
            action=command["action"],
            message=command["message"],
            messages=messages,
        )

    return app


def main():
    print("Loading Maia model...", flush=True)
    engine = create_engine(MAIA_ELO, PLAYER_ELO)
    app = None
    try:
        app = create_app(engine)
        print("Starting local chess server at http://0.0.0.0:5000", flush=True)
        print("Open http://<PC-IP>:5000 on a device on the same Wi-Fi network.")
        app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
    finally:
        try:
            engine.quit()
        finally:
            if app is not None:
                game = app.config["CHESS_GAME"]
                if game.started:
                    pgn_text = game.build_pgn(game.result)
                    pgn_path = save_pgn(pgn_text)
                    print(f"\nSaved PGN to: {pgn_path}")
                    print("\nGame PGN:\n" + pgn_text)


if __name__ == "__main__":
    main()
