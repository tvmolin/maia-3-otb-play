import math
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

import chess
import chess.engine
import chess.pgn

try:
    import winsound
except ImportError:
    winsound = None


MIN_THINK_SECONDS = 10.0
MAX_THINK_SECONDS = 30.0
OPENING_FULL_MOVES = 5
OPENING_MIN_THINK_SECONDS = 1.0
OPENING_MAX_THINK_SECONDS = 5.0


def format_move_list(board):
    replay = board.root()
    moves = []
    for move in board.move_stack:
        if replay.turn == chess.WHITE:
            moves.append(f"{replay.fullmove_number}.")
        moves.append(replay.san(move))
        replay.push(move)
    return " ".join(moves) if moves else "(no moves yet)"


def undo_last_turn(board):
    plies_to_undo = min(2, len(board.move_stack))
    for _ in range(plies_to_undo):
        board.pop()
    return plies_to_undo


def build_pgn(board, player_color, player_name, player_elo, maia_name, maia_elo, result):
    game = chess.pgn.Game.from_board(board)
    game.headers["Event"] = "OTB Maia game"
    game.headers["Date"] = datetime.now().strftime("%Y.%m.%d")
    game.headers["White"] = player_name if player_color == chess.WHITE else maia_name
    game.headers["Black"] = maia_name if player_color == chess.WHITE else player_name
    game.headers["WhiteElo"] = str(player_elo if player_color == chess.WHITE else maia_elo)
    game.headers["BlackElo"] = str(maia_elo if player_color == chess.WHITE else player_elo)
    game.headers["Result"] = result
    exporter = chess.pgn.StringExporter(headers=True, variations=False, comments=False)
    return game.accept(exporter)


def save_pgn(pgn_text):
    games_dir = Path(__file__).resolve().parent / "games"
    games_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = games_dir / f"maia-game-{timestamp}.pgn"
    suffix = 1
    while path.exists():
        path = games_dir / f"maia-game-{timestamp}-{suffix}.pgn"
        suffix += 1
    path.write_text(pgn_text + "\n", encoding="utf-8")
    return path


def play_move_sound():
    if winsound is not None:
        winsound.MessageBeep()
    else:
        print("\a", end="", flush=True)


def prompt_elo(label, default):
    while True:
        value = input(f"{label} Elo [{default}]: ").strip()
        if not value:
            return default
        try:
            elo = int(value)
        except ValueError:
            print("Enter a whole number between 0 and 5000.")
            continue
        if 0 <= elo <= 5000:
            return elo
        print("Enter a whole number between 0 and 5000.")


def prompt_color():
    while True:
        value = input("Play as White or Black? [White]: ").strip().lower()
        if not value or value in ("w", "white"):
            return chess.WHITE
        if value in ("b", "black"):
            return chess.BLACK
        print("Enter White or Black.")


MAIA_ELO = prompt_elo("Maia", 600)
YOUR_ELO = prompt_elo("Your", 700)
PLAYER_COLOR = prompt_color()
PLAYER_NAME = "Devonty"
MAIA_NAME = f"Maia {MAIA_ELO}"

board = chess.Board()
engine = chess.engine.SimpleEngine.popen_uci([
    sys.executable,
    "-m", "maia3.uci",
    "--model", "maia3-79m",
    "--elo", str(MAIA_ELO),
    "--temperature", "0.8",
    "--use-uci-history",
    "--seed", str(secrets.randbits(32)),
    "--local-files-only",
], cwd=Path(__file__).resolve().parent)
final_result = None

try:
    print("\nLoading Maia model...", flush=True)
    engine.configure({"SelfElo": MAIA_ELO, "OppoElo": YOUR_ELO})
    engine.ping()
    print("Maia is ready.", flush=True)

    while not board.is_game_over():
        print()

        if board.turn == PLAYER_COLOR:
            print("Moves:", format_move_list(board))
            text = input(
                f"\nYour move (move {board.fullmove_number}): "
            ).strip()
            if text.lower() in ("q", "quit", "exit"):
                break
            if text.lower() == "undo":
                plies_to_undo = undo_last_turn(board)
                if plies_to_undo == 0:
                    print("There are no moves to undo.")
                    continue
                print(f"Undid {plies_to_undo} half-move(s).")
                continue
            if text.lower() in ("resign", "concede"):
                final_result = "0-1" if PLAYER_COLOR == chess.WHITE else "1-0"
                print(f"\nYou resigned. Maia wins ({final_result}).")
                break
            try:
                try:
                    move = board.parse_san(text)
                except ValueError:
                    move = board.parse_uci(text)
                board.push(move)
            except ValueError:
                print("I couldn't parse that as a legal move. Try again.")
        else:
            move_started = time.perf_counter()
            result = engine.play(
                board,
                chess.engine.Limit(nodes=1),
                info=chess.engine.INFO_ALL,
            )
            inference_seconds = time.perf_counter() - move_started
            info = result.info.get("string", "").split()
            if len(info) != 2 or info[0] != "policy_entropy":
                raise RuntimeError("Maia did not report move-policy uncertainty.")
            uncertainty = float(info[1])
            if not math.isfinite(uncertainty):
                raise RuntimeError(f"Invalid Maia policy uncertainty: {uncertainty}")
            uncertainty = min(1.0, max(0.0, uncertainty))
            opening = board.fullmove_number <= OPENING_FULL_MOVES
            min_think = OPENING_MIN_THINK_SECONDS if opening else MIN_THINK_SECONDS
            max_think = OPENING_MAX_THINK_SECONDS if opening else MAX_THINK_SECONDS
            target_seconds = min_think + uncertainty * (max_think - min_think)
            think_seconds = max(0.0, target_seconds - inference_seconds)
            total_seconds = inference_seconds + think_seconds
            print(
                f"\nMaia is thinking (uncertainty {uncertainty:.2f})...",
                flush=True,
            )
            time.sleep(think_seconds)
            move = result.move
            print("\nMaia plays:", board.san(move))
            play_move_sound()
            board.push(move)

    print("\nFinal position:")
    if board.is_game_over():
        final_result = board.result()
        print("Game result:", final_result)
finally:
    try:
        engine.quit()
    finally:
        pgn_text = build_pgn(
            board,
            PLAYER_COLOR,
            PLAYER_NAME,
            YOUR_ELO,
            MAIA_NAME,
            MAIA_ELO,
            final_result or (board.result() if board.is_game_over() else "*"),
        )
        pgn_path = save_pgn(pgn_text)
        print(f"\nSaved PGN to: {pgn_path}")
        print("\nGame PGN:\n" + pgn_text)
