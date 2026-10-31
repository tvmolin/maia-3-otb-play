cd C:\dev\maia3

@'
import chess
import chess.engine

board = chess.Board()
engine = chess.engine.SimpleEngine.popen_uci([
    r".\.venv\Scripts\maia3-79m.exe",
    "--elo", "1200",
])

try:
    print("You are White. Enter moves like e4 or e2e4; type q to quit.")
    while not board.is_game_over():
        print()
        print(board)

        if board.turn == chess.WHITE:
            text = input("\nYour move: ").strip()
            if text.lower() in ("q", "quit", "exit"):
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
            move = engine.play(board, chess.engine.Limit(nodes=1)).move
            print("\nMaia plays:", board.san(move))
            board.push(move)

    print("\nFinal position:")
    print(board)
    if board.is_game_over():
        print("Game result:", board.result())
finally:we 
    engine.quit()
'@ | Set-Content -Path .\play_maia.py -Encoding utf8

.\.venv\Scripts\python.exe .\play_maia.py