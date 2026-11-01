import os
import select
import sys
import time

import chess

from maia_game import (
    HELP_TEXT,
    GameSession,
    create_engine,
    format_move_list,
    save_pgn,
)

try:
    import winsound
except ImportError:
    winsound = None


def play_move_sound():
    if winsound is not None:
        winsound.MessageBeep()
    else:
        print("\a", end="", flush=True)


def print_help():
    print("\n" + HELP_TEXT)


def wait_for_go(seconds, session):
    deadline = time.monotonic() + seconds
    typed = []

    if os.name == "nt":
        import msvcrt

        while time.monotonic() < deadline:
            if not msvcrt.kbhit():
                time.sleep(max(0.0, min(0.05, deadline - time.monotonic())))
                continue

            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                msvcrt.getwch()
            elif char in ("\r", "\n"):
                print()
                command = "".join(typed).strip().lower()
                typed.clear()
                if command == "go":
                    result = session.handle_player_command(command)
                    print(result["message"], flush=True)
                    return
                if command == "help":
                    print_help()
                    continue
                if command:
                    print("Type 'help' to list commands.", flush=True)
            elif char == "\b":
                if typed:
                    typed.pop()
                    print("\b \b", end="", flush=True)
            elif char.isprintable():
                typed.append(char)
                print(char, end="", flush=True)
    else:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            readable, _, _ = select.select(
                [sys.stdin], [], [], max(0.0, min(0.05, remaining))
            )
            if not readable:
                continue

            command = sys.stdin.readline()
            if not command:
                time.sleep(max(0, deadline - time.monotonic()))
                return
            if command.strip().lower() == "go":
                result = session.handle_player_command("go")
                print(result["message"], flush=True)
                return
            if command.strip().lower() == "help":
                print_help()
                continue
            if command.strip():
                print("Type 'help' to list commands.", flush=True)


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


def main():
    maia_elo = prompt_elo("Maia", 800)
    player_elo = prompt_elo("Your", 760)
    player_color = prompt_color()
    session = None
    final_result = None

    try:
        print("\nLoading Maia model...", flush=True)
        engine = create_engine(maia_elo, player_elo)
        session = GameSession(
            engine,
            player_color=player_color,
            player_name="Devonty",
            player_elo=player_elo,
            maia_elo=maia_elo,
        )
        print("Maia is ready.", flush=True)

        while not session.is_game_over and not session.quit_requested:
            print()

            if session.board.turn == session.player_color:
                print("Moves:", format_move_list(session.board))
                text = input(
                    f"\nYour move (move {session.board.fullmove_number}; "
                    "type help for commands): "
                ).strip()
                try:
                    command = session.handle_player_command(text)
                except ValueError:
                    print("I couldn't parse that as a legal move. Try again.")
                    continue

                if command["action"] == "help":
                    print_help()
                elif command["action"] == "undo":
                    print(command["message"])
                elif command["action"] == "resign":
                    final_result = session.final_result
                    print(f"\n{command['message']}")
            else:
                turn = session.choose_maia_move()
                print(
                    f"\nMaia is thinking (uncertainty {turn.uncertainty:.2f}); "
                    "type 'go' and press Enter to play now...",
                    flush=True,
                )
                wait_for_go(turn.think_seconds, session)
                print("\nMaia plays:", session.play_maia_move(turn))
                play_move_sound()

        print("\nFinal position:")
        if session.board.is_game_over():
            final_result = session.board.result()
        if final_result:
            print("Game result:", final_result)
    finally:
        if session is not None:
            try:
                session.engine.quit()
            finally:
                pgn_result = (
                    final_result
                    or session.final_result
                    or (session.board.result() if session.is_game_over else "*")
                )
                pgn_text = session.build_pgn(pgn_result)
                pgn_path = save_pgn(pgn_text)
                print(f"\nSaved PGN to: {pgn_path}")
                print("\nGame PGN:\n" + pgn_text)


if __name__ == "__main__":
    main()
