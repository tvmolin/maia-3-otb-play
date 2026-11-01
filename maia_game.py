import math
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, RLock

import chess
import chess.engine
import chess.pgn


MIN_THINK_SECONDS = 10.0
MAX_THINK_SECONDS = 30.0
OPENING_FULL_MOVES = 5
OPENING_MIN_THINK_SECONDS = 1.0
OPENING_MAX_THINK_SECONDS = 5.0
HELP_TEXT = (
    "Commands:\n"
    "  help          Show this command list\n"
    "  undo          Take back the last turn\n"
    "  resign        Resign the game (alias: concede)\n"
    "  q             Quit the game (aliases: quit, exit)\n"
    "  go            Skip Maia's remaining think delay (during her turn)\n"
    "\nYou can enter moves in SAN (e.g. e4) or UCI (e.g. e2e4) notation."
)


@dataclass(frozen=True)
class MaiaTurn:
    move: chess.Move
    san: str
    uncertainty: float
    think_seconds: float


def create_engine(maia_elo, player_elo):
    engine = chess.engine.SimpleEngine.popen_uci(
        [
            sys.executable,
            "-m",
            "maia3.uci",
            "--model",
            "maia3-79m",
            "--elo",
            str(maia_elo),
            "--temperature",
            "0.2",
            "--top-p",
            "0.9",
            "--use-uci-history",
            "--seed",
            str(secrets.randbits(32)),
            "--local-files-only",
        ],
        cwd=Path(__file__).resolve().parent,
    )
    try:
        engine.configure({"SelfElo": maia_elo, "OppoElo": player_elo})
        engine.ping()
    except Exception:
        engine.quit()
        raise
    return engine


def format_move_list(board):
    replay = board.root()
    moves = []
    for move in board.move_stack:
        if replay.turn == chess.WHITE:
            moves.append(f"{replay.fullmove_number}.")
        moves.append(replay.san(move))
        replay.push(move)
    return " ".join(moves) if moves else "(no moves yet)"


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


class GameSession:
    def __init__(
        self,
        engine,
        player_color=chess.WHITE,
        player_name="Devonty",
        player_elo=760,
        maia_elo=800,
        board=None,
        started=True,
    ):
        self.engine = engine
        self.board = board if board is not None else chess.Board()
        self.player_color = player_color
        self.player_name = player_name
        self.player_elo = player_elo
        self.maia_elo = maia_elo
        self.maia_name = f"Maia {maia_elo}"
        self.history = []
        self.lock = RLock()
        self.started = started
        self.quit_requested = False
        self.final_result = None
        self._think_skip = Event()
        self._think_state_lock = Lock()
        self._maia_turn_active = False

    @property
    def is_game_over(self):
        return self.board.is_game_over() or self.final_result is not None

    @property
    def is_thinking(self):
        with self._think_state_lock:
            return self._maia_turn_active

    @property
    def player_color_name(self):
        return "white" if self.player_color == chess.WHITE else "black"

    def start(self, player_color):
        with self.lock:
            if self.started:
                if self.player_color == player_color:
                    return False
                raise ValueError("This game has already started.")
            if player_color not in (chess.WHITE, chess.BLACK):
                raise ValueError("Choose White or Black.")
            self.player_color = player_color
            self.started = True
            return True

    def cancel_start(self):
        with self.lock:
            if self.history or self.board.move_stack:
                raise RuntimeError("Cannot cancel a game after moves have started.")
            self.started = False
            self.player_color = chess.WHITE

    def reset(self):
        with self.lock:
            with self._think_state_lock:
                if self._maia_turn_active:
                    raise ValueError(
                        "Maia is thinking; wait for her move or use 'go' first."
                    )
                self._think_skip.clear()
                self.board = chess.Board()
                self.history.clear()
                self.player_color = chess.WHITE
                self.started = False
                self.quit_requested = False
                self.final_result = None

    def handle_player_command(self, text):
        command = text.strip()
        normalized = command.lower()
        if normalized == "help":
            return {"action": "help", "message": HELP_TEXT}
        if normalized == "go":
            with self._think_state_lock:
                if self._maia_turn_active:
                    self._think_skip.set()
                    return {"action": "go", "message": "Skipping the remaining think time."}
            raise ValueError("Maia is not thinking.")

        with self._think_state_lock:
            if self._maia_turn_active:
                raise ValueError("Maia is thinking; use 'go' to play her move now.")

        with self.lock:
            if not self.started:
                raise ValueError("Choose a color and start the game first.")
            if normalized in ("q", "quit", "exit"):
                self.quit_requested = True
                return {"action": "quit", "message": "Game quit."}
            if normalized in ("resign", "concede"):
                self.final_result = "0-1" if self.player_color == chess.WHITE else "1-0"
                return {
                    "action": "resign",
                    "message": f"You resigned. Maia wins ({self.final_result}).",
                }
            if normalized == "undo":
                removed = self.undo_last_turn()
                if removed == 0:
                    return {"action": "undo", "message": "There are no moves to undo."}
                return {
                    "action": "undo",
                    "message": f"Undid {removed} half-move(s).",
                }
            san = self.apply_player_move(command)
            return {"action": "move", "message": san, "san": san}

    def begin_maia_turn(self):
        with self._think_state_lock:
            if self._maia_turn_active:
                raise ValueError("Maia is already thinking.")
            self._think_skip.clear()
            self._maia_turn_active = True

    def apply_player_move(self, text):
        with self.lock:
            if not self.started:
                raise ValueError("Choose a color and start the game first.")
            if self.is_game_over:
                raise ValueError("This game is over.")
            if self.quit_requested:
                raise ValueError("This game has been quit.")
            if self.board.turn != self.player_color:
                raise ValueError("It is Maia's turn.")

            try:
                move = self.board.parse_san(text)
            except ValueError:
                move = self.board.parse_uci(text)

            san = self.board.san(move)
            self.board.push(move)
            self.history.append({"role": "You", "text": san})
            return san

    def choose_maia_move(self):
        with self.lock:
            if not self.started:
                raise ValueError("Choose a color and start the game first.")
            if self.is_game_over:
                raise ValueError("This game is over.")
            if self.board.turn == self.player_color:
                raise ValueError("It is your turn.")

            self.begin_maia_turn()
            move_started = time.perf_counter()
            try:
                result = self.engine.play(
                    self.board,
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

                opening = self.board.fullmove_number <= OPENING_FULL_MOVES
                min_think = OPENING_MIN_THINK_SECONDS if opening else MIN_THINK_SECONDS
                max_think = MAX_THINK_SECONDS
                if opening:
                    max_think = OPENING_MAX_THINK_SECONDS
                target_seconds = min_think + uncertainty * (max_think - min_think)
                think_seconds = max(0.0, target_seconds - inference_seconds)

                move = result.move
                if move is None or not self.board.is_legal(move):
                    raise RuntimeError("Maia returned an invalid move.")
                return MaiaTurn(
                    move=move,
                    san=self.board.san(move),
                    uncertainty=uncertainty,
                    think_seconds=think_seconds,
                )
            except Exception:
                self.finish_maia_think()
                raise

    def wait_for_maia_think(self, seconds):
        self._think_skip.wait(seconds)

    def finish_maia_think(self):
        with self._think_state_lock:
            self._maia_turn_active = False
            self._think_skip.clear()

    def play_maia_move(self, turn):
        with self.lock:
            try:
                if self.is_game_over:
                    raise ValueError("This game is over.")
                if self.board.turn == self.player_color:
                    raise ValueError("It is your turn.")
                if not self.board.is_legal(turn.move):
                    raise ValueError("Maia's move is no longer legal.")

                self.board.push(turn.move)
                self.history.append({"role": "Engine", "text": turn.san})
                return turn.san
            finally:
                self.finish_maia_think()

    def undo_plies(self, count):
        with self.lock:
            removed = min(count, len(self.board.move_stack))
            for _ in range(removed):
                self.board.pop()
                if self.history:
                    self.history.pop()
            return removed

    def undo_last_turn(self):
        with self.lock:
            if not self.board.move_stack:
                return 0
            plies = min(2, len(self.board.move_stack))
            if self.board.turn != self.player_color:
                plies = 1
            return self.undo_plies(plies)

    def build_pgn(self, result):
        return build_pgn(
            self.board,
            self.player_color,
            self.player_name,
            self.player_elo,
            self.maia_name,
            self.maia_elo,
            result,
        )
