import logging
import random
import threading
import time
import uuid
import webbrowser
from pathlib import Path

import chess
from flask import Flask, jsonify, render_template, request

from ladder import DEFAULT_PLAYER_ELO, RATING_CATEGORIES, LadderStore
from maia_game import GameSession, create_engine


MAIA_ELO = 800
PLAYER_ELO = DEFAULT_PLAYER_ELO
MATCH_ELO_OFFSET = 100
MAIA_ENGINE_ELO_OFFSET = 200
MIN_MAIA_ENGINE_ELO = 600
MAX_MAIA_ENGINE_ELO = 5000 - MAIA_ENGINE_ELO_OFFSET
MATCH_TEMPERATURE_RANGE = (0.5, 0.9)
BLITZ_THINK_TIME_FACTOR = 0.2
HOST = "127.0.0.1"
PORT = 5001
TIME_CONTROLS = {
    "blitz": {
        "3+2": (180, 2),
        "5+5": (300, 5),
    },
    "rapid": {
        "15+10": (900, 10),
        "20+10": (1200, 10),
    },
}


class ChessClock:
    def __init__(self):
        self.base_seconds = 0
        self.increment_seconds = 0
        self.remaining = {chess.WHITE: 0.0, chess.BLACK: 0.0}
        self.running_color = None
        self.started_at = None

    def start(self, base_seconds, increment_seconds, color, now):
        self.base_seconds = base_seconds
        self.increment_seconds = increment_seconds
        self.remaining = {chess.WHITE: float(base_seconds), chess.BLACK: float(base_seconds)}
        self.running_color = color
        self.started_at = now

    def stop(self):
        self.running_color = None
        self.started_at = None

    def settle(self, now):
        if self.running_color is None:
            return None
        color = self.running_color
        self.remaining[color] -= max(0.0, now - self.started_at)
        if self.remaining[color] <= 0:
            self.remaining[color] = 0.0
            self.stop()
            return color
        self.started_at = now
        return None

    def complete_move(self, mover, next_color, now):
        if self.running_color != mover:
            raise RuntimeError("The chess clock is not running for the player who moved.")
        self.remaining[mover] += self.increment_seconds
        self.running_color = next_color
        self.started_at = now if next_color is not None else None

    def save_turn(self):
        return self.remaining.copy()

    def restore_turn(self, remaining, color, now):
        self.remaining = remaining.copy()
        self.running_color = color
        self.started_at = now

    def data(self, now):
        remaining = self.remaining.copy()
        if self.running_color is not None:
            elapsed = max(0.0, now - self.started_at)
            remaining[self.running_color] = max(
                0.0, remaining[self.running_color] - elapsed
            )
        return {
            "base_seconds": self.base_seconds,
            "remaining_ms": {
                "white": round(remaining[chess.WHITE] * 1000),
                "black": round(remaining[chess.BLACK] * 1000),
            },
            "running_color": (
                "white" if self.running_color == chess.WHITE
                else "black" if self.running_color == chess.BLACK
                else None
            ),
            "increment_seconds": self.increment_seconds,
        }


def create_app(engine, board=None, player_data_path=None):
    app = Flask(__name__)
    ladder = LadderStore(
        player_data_path
        or Path(__file__).resolve().parent / "games" / "ladder-ratings.json"
    )
    game = GameSession(
        engine,
        player_color=chess.WHITE,
        player_name="Devonty",
        player_elo=ladder.rating_for("blitz"),
        maia_elo=MAIA_ELO,
        board=board,
        started=False,
    )
    app.config["CHESS_GAME"] = game
    app.config["CHESSGROUND_LADDER"] = ladder
    app.config["CHESSGROUND_REQUEST_LOCK"] = threading.RLock()
    clock = ChessClock()
    app.config["CHESSGROUND_CLOCK"] = clock
    clock_history = []

    def save_finished_game():
        if not game.started or not game.is_game_over:
            return
        pgn_path = game.saved_pgn_path
        if pgn_path is None:
            pgn_path = game.save_pgn_file()
        if app.config.get("CHESSGROUND_RATING_RECORD") is None:
            match = {
                "match_id": app.config["CHESSGROUND_MATCH_ID"],
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "result": game.result,
                "player_color": game.player_color_name,
                "opponent_name": game.maia_name,
                "rating_category": app.config["CHESSGROUND_RATING_CATEGORY"],
                "maia_elo": game.maia_elo,
                "temperature": game.temperature,
                "time_control": app.config["CHESSGROUND_TIME_CONTROL"],
                "plies": len(game.board.move_stack),
                "pgn_file": pgn_path.name,
            }
            record = ladder.record_match(match)
            game.player_elo = record["player_elo_after"]
            app.config["CHESSGROUND_RATING_RECORD"] = record

    def sync_clock(now=None):
        loser = clock.settle(time.monotonic() if now is None else now)
        if loser is not None:
            game.finish_on_time(loser)
            app.config["CHESSGROUND_TIMEOUT_LOSER"] = (
                "white" if loser == chess.WHITE else "black"
            )
            save_finished_game()
        return loser

    def state_data():
        with app.config["CHESSGROUND_REQUEST_LOCK"], game.lock:
            now = time.monotonic()
            sync_clock(now)
            if game.is_game_over:
                save_finished_game()
            board = game.board
            board_outcome = game.outcome
            termination = None
            if app.config.get("CHESSGROUND_TIMEOUT_LOSER"):
                termination = "timeout"
            elif game.final_result is not None:
                termination = "resignation"
            elif board_outcome:
                termination = board_outcome.termination.name.lower()
            can_move = (
                game.started
                and not game.is_game_over
                and not game.is_thinking
                and not game.quit_requested
                and board.turn == game.player_color
            )
            destinations = {}
            promotions = {}
            checking_moves = []
            if can_move:
                for move in board.legal_moves:
                    origin = chess.square_name(move.from_square)
                    destination = chess.square_name(move.to_square)
                    destinations.setdefault(origin, set()).add(destination)
                    if move.promotion:
                        key = origin + destination
                        promotions.setdefault(key, []).append(move.uci()[4:])
                    resulting_board = board.copy(stack=False)
                    resulting_board.push(move)
                    if resulting_board.is_check():
                        checking_moves.append(move.uci())

            last_move = (
                [
                    chess.square_name(board.peek().from_square),
                    chess.square_name(board.peek().to_square),
                ]
                if board.move_stack
                else None
            )
            king = board.king(board.turn) if board.is_check() else None
            position_board = board.root()
            captured_pieces = {
                "white": [],
                "black": [],
            }
            positions = [
                {
                    "fen": position_board.board_fen(),
                    "captured_pieces": {
                        color: list(pieces)
                        for color, pieces in captured_pieces.items()
                    },
                    "turn_color": (
                        "white" if position_board.turn == chess.WHITE else "black"
                    ),
                    "last_move": None,
                    "check": position_board.is_check(),
                    "check_square": (
                        chess.square_name(position_board.king(position_board.turn))
                        if position_board.is_check()
                        else None
                    ),
                }
            ]
            for move in board.move_stack:
                if position_board.is_capture(move):
                    captured_piece = position_board.piece_at(move.to_square)
                    if position_board.is_en_passant(move):
                        captured_piece = chess.Piece(chess.PAWN, not position_board.turn)
                    if captured_piece is not None:
                        capturer = "white" if position_board.turn == chess.WHITE else "black"
                        captured_pieces[capturer].append(captured_piece.symbol())
                position_board.push(move)
                checked_king = position_board.king(position_board.turn)
                positions.append(
                    {
                        "fen": position_board.board_fen(),
                        "captured_pieces": {
                            color: list(pieces)
                            for color, pieces in captured_pieces.items()
                        },
                        "turn_color": (
                            "white" if position_board.turn == chess.WHITE else "black"
                        ),
                        "last_move": [
                            chess.square_name(move.from_square),
                            chess.square_name(move.to_square),
                        ],
                        "check": position_board.is_check(),
                        "check_square": (
                            chess.square_name(checked_king)
                            if checked_king is not None
                            else None
                        ),
                    }
                )
            return {
                "fen": board.board_fen(),
                "turn_color": "white" if board.turn == chess.WHITE else "black",
                "player_color": game.player_color_name if game.started else None,
                "player_name": game.player_name,
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
                "checking_moves": checking_moves,
                "history": list(game.history),
                "ply": len(board.move_stack),
                "positions": positions,
                "captured_pieces": positions[-1]["captured_pieces"],
                "maia_elo": game.maia_elo,
                "maia_name": game.maia_name,
                "player_elo": game.player_elo if game.started else ladder.rating_for(
                    app.config.get("CHESSGROUND_RATING_CATEGORY", "blitz")
                ),
                "rating_category": app.config.get(
                    "CHESSGROUND_RATING_CATEGORY", "blitz"
                ),
                "player_ratings": dict(ladder.ratings),
                "rating_change": (
                    app.config["CHESSGROUND_RATING_RECORD"]["rating_change"]
                    if app.config.get("CHESSGROUND_RATING_RECORD")
                    else None
                ),
                "rating_outcome": (
                    app.config["CHESSGROUND_RATING_RECORD"]["outcome"]
                    if app.config.get("CHESSGROUND_RATING_RECORD")
                    else None
                ),
                "maia_temperature": game.temperature if game.started else None,
                "time_control": app.config.get("CHESSGROUND_TIME_CONTROL"),
                "play_instantly": app.config.get(
                    "CHESSGROUND_PLAY_INSTANTLY", False
                ),
                "termination": termination,
                "clock": clock.data(now),
            }

    def state_response(status=200, error=None, **extra):
        response = {"state": state_data()}
        if error is not None:
            response["error"] = error
        response.update(extra)
        return jsonify(response), status

    def play_maia_turn():
        turn = game.choose_maia_move()
        think_seconds = (
            0.0
            if app.config.get("CHESSGROUND_PLAY_INSTANTLY")
            else turn.think_seconds
        )
        if (
            think_seconds > 0
            and app.config.get("CHESSGROUND_RATING_CATEGORY") == "blitz"
        ):
            think_seconds *= BLITZ_THINK_TIME_FACTOR
        game.wait_for_maia_think(think_seconds)
        now = time.monotonic()
        loser = sync_clock(now)
        if loser is not None:
            game.finish_maia_think()
            return None
        san = game.play_maia_move(turn)
        next_color = None if game.is_game_over else game.player_color
        clock.complete_move(not game.player_color, next_color, now)
        return san

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

    @app.get("/profile")
    def profile():
        category = request.args.get("category", "blitz")
        if category not in RATING_CATEGORIES:
            return "Choose blitz or rapid.", 404
        with app.config["CHESSGROUND_REQUEST_LOCK"]:
            matches = list(ladder.matches_for(category))
            player_elo = ladder.rating_for(category)

        color_stats = {
            color: {"games": 0, "wins": 0, "draws": 0, "losses": 0, "score": 0}
            for color in ("white", "black")
        }
        for match in matches:
            color = match.get("player_color")
            if color not in color_stats:
                continue
            outcome = match.get("outcome")
            if outcome not in ("win", "draw", "loss"):
                result = match.get("result")
                if result == "1/2-1/2":
                    outcome = "draw"
                elif result in ("1-0", "0-1"):
                    player_won = (result == "1-0") == (color == "white")
                    outcome = "win" if player_won else "loss"
                else:
                    continue
            stats = color_stats[color]
            stats["games"] += 1
            stats[{"win": "wins", "draw": "draws", "loss": "losses"}[outcome]] += 1
            stats["score"] += {"win": 1, "draw": 0.5, "loss": 0}[outcome]
        for stats in color_stats.values():
            games = stats["games"]
            stats["win_percentage"] = (
                round(stats["wins"] * 100 / games, 1) if games else None
            )
            stats["score_percentage"] = (
                round(stats["score"] * 100 / games, 1) if games else None
            )

        ratings = [matches[0]["player_elo_before"]] if matches else [player_elo]
        ratings.extend(match["player_elo_after"] for match in matches)
        low = min(ratings)
        high = max(ratings)
        padding = max(20, (high - low) * 0.15)
        chart_min = max(0, low - padding)
        chart_max = min(5000, high + padding)
        if chart_min == chart_max:
            chart_min = max(0, chart_min - 20)
            chart_max = min(5000, chart_max + 20)

        left, right, top, bottom = 64, 776, 24, 224
        point_count = len(ratings)
        rating_points = [
            {
                "x": round(
                    left
                    if point_count == 1
                    else left + (right - left) * index / (point_count - 1)
                ),
                "y": round(
                    bottom
                    - (rating - chart_min) * (bottom - top) / (chart_max - chart_min)
                ),
                "rating": rating,
            }
            for index, rating in enumerate(ratings)
        ]
        rating_ticks = [
            {
                "y": round(bottom - fraction * (bottom - top)),
                "rating": round(chart_min + fraction * (chart_max - chart_min)),
            }
            for fraction in (0, 0.5, 1)
        ]
        return render_template(
            "profile.html",
            category=category,
            player_elo=player_elo,
            matches=reversed(matches),
            color_stats=color_stats,
            rating_points=rating_points,
            rating_ticks=rating_ticks,
            chart_min=chart_min,
            chart_max=chart_max,
        )

    @app.get("/api/state")
    def get_state():
        return jsonify(state_data())

    @app.post("/api/start")
    @app.post("/api/find-match")
    def find_match():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return state_response(400, "Send the game settings as a JSON object.")

        if any(
            field in payload
            for field in ("rating_category", "base_time_minutes", "increment_seconds")
        ):
            return state_response(
                400, "Choose a specific Blitz or Rapid time control."
            )
        time_control = payload.get("time_control", "3+2")
        if not isinstance(time_control, str):
            return state_response(400, "Choose a supported time control.")
        category = next(
            (
                category
                for category, controls in TIME_CONTROLS.items()
                if time_control in controls
            ),
            None,
        )
        if category is None:
            return state_response(400, "Choose a supported time control.")
        time_control_values = TIME_CONTROLS[category][time_control]
        with app.config["CHESSGROUND_REQUEST_LOCK"]:
            if game.started:
                return state_response(409, "Finish the current match before finding another.")
            color = random.choice((chess.WHITE, chess.BLACK))
            player_elo = ladder.rating_for(category)
            min_opponent_elo = max(
                MIN_MAIA_ENGINE_ELO + MAIA_ENGINE_ELO_OFFSET,
                player_elo - MATCH_ELO_OFFSET,
            )
            max_opponent_elo = min(
                MAX_MAIA_ENGINE_ELO + MAIA_ENGINE_ELO_OFFSET,
                player_elo + MATCH_ELO_OFFSET,
            )
            max_opponent_elo = max(min_opponent_elo, max_opponent_elo)
            maia_elo = random.randint(min_opponent_elo, max_opponent_elo)
            engine_maia_elo = maia_elo - MAIA_ENGINE_ELO_OFFSET
            temperature = random.uniform(*MATCH_TEMPERATURE_RANGE)
            opponent_name = ladder.generate_opponent_name()
            try:
                started_now = game.start(
                    color,
                    maia_elo=maia_elo,
                    engine_maia_elo=engine_maia_elo,
                    player_elo=player_elo,
                    temperature=temperature,
                )
            except ValueError as error:
                return state_response(400, str(error))
            except Exception:
                app.logger.exception("Failed to configure Maia for Chessground.")
                return state_response(503, "Could not configure the engine.")

            if started_now:
                game.maia_name = opponent_name
                base_seconds, increment_seconds = time_control_values
                app.config["CHESSGROUND_RATING_CATEGORY"] = category
                app.config["CHESSGROUND_TIME_CONTROL"] = time_control
                app.config["CHESSGROUND_PLAY_INSTANTLY"] = False
                app.config["CHESSGROUND_TEMPERATURE"] = temperature
                app.config["CHESSGROUND_MATCH_ID"] = str(uuid.uuid4())
                app.config.pop("CHESSGROUND_RATING_RECORD", None)
                app.config.pop("CHESSGROUND_TIMEOUT_LOSER", None)
                clock_history.clear()
                clock.start(
                    base_seconds,
                    increment_seconds,
                    chess.WHITE,
                    time.monotonic(),
                )
            if started_now and color == chess.BLACK:
                try:
                    play_maia_turn()
                except Exception:
                    game.cancel_start()
                    clock.stop()
                    clock_history.clear()
                    app.config.pop("CHESSGROUND_TIME_CONTROL", None)
                    app.config.pop("CHESSGROUND_PLAY_INSTANTLY", None)
                    app.config.pop("CHESSGROUND_RATING_CATEGORY", None)
                    app.config.pop("CHESSGROUND_TEMPERATURE", None)
                    app.config.pop("CHESSGROUND_MATCH_ID", None)
                    app.config.pop("CHESSGROUND_RATING_RECORD", None)
                    app.config.pop("CHESSGROUND_TIMEOUT_LOSER", None)
                    app.logger.exception("Maia failed to make the opening move.")
                    return state_response(503, "The engine failed to make its opening move.")
            save_finished_game()
        return state_response()

    @app.post("/api/play-instantly")
    def set_play_instantly():
        payload = request.get_json(silent=True)
        enabled = payload.get("enabled") if isinstance(payload, dict) else None
        if not isinstance(enabled, bool):
            return jsonify({"error": "Choose whether Maia should play instantly."}), 400
        if not game.started:
            return jsonify({"error": "Start a match before changing Maia move timing."}), 409
        app.config["CHESSGROUND_PLAY_INSTANTLY"] = enabled
        if enabled:
            game.skip_maia_think()
        return jsonify({"play_instantly": enabled})

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
            loser = sync_clock()
            if loser is not None:
                return state_response(409, "Your clock ran out.")
            if expected_ply != len(game.board.move_stack):
                return state_response(409, "The position changed. The board was refreshed.")
            if not game.started:
                return state_response(409, "Start a game before moving.")
            if game.is_thinking:
                return state_response(409, "Maia is thinking.")

            now = time.monotonic()
            if clock.running_color != game.player_color:
                return state_response(409, "It is not your turn.")
            clock_checkpoint = clock.save_turn()
            try:
                game.apply_player_move(move_text)
            except ValueError as error:
                return state_response(400, str(error))
            clock.complete_move(
                game.player_color,
                None if game.is_game_over else not game.player_color,
                now,
            )
            clock_history.append(clock_checkpoint)
            try:
                if not game.is_game_over:
                    play_maia_turn()
                save_finished_game()
            except Exception:
                game.undo_plies(1)
                clock_history.pop()
                clock.restore_turn(clock_checkpoint, game.player_color, time.monotonic())
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
            loser = sync_clock()
            if loser is not None:
                return state_response(409, "Your clock ran out.")
            if text == "resign" and clock.running_color != game.player_color:
                return state_response(409, "It is not your turn.")
            try:
                result = game.handle_player_command(text)
            except ValueError as error:
                return state_response(409, str(error))
            try:
                if result["action"] == "undo" and not game.is_game_over:
                    if clock_history:
                        clock.restore_turn(
                            clock_history.pop(), game.player_color, time.monotonic()
                        )
                    if game.board.turn != game.player_color:
                        play_maia_turn()
                elif result["action"] == "resign":
                    clock.stop()
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
                clock.stop()
                clock_history.clear()
                app.config.pop("CHESSGROUND_TIME_CONTROL", None)
                app.config.pop("CHESSGROUND_PLAY_INSTANTLY", None)
                app.config.pop("CHESSGROUND_RATING_CATEGORY", None)
                app.config.pop("CHESSGROUND_TEMPERATURE", None)
                app.config.pop("CHESSGROUND_MATCH_ID", None)
                app.config.pop("CHESSGROUND_RATING_RECORD", None)
                app.config.pop("CHESSGROUND_TIMEOUT_LOSER", None)
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
    engine = create_engine(
        MAIA_ELO - MAIA_ENGINE_ELO_OFFSET,
        PLAYER_ELO,
    )
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
