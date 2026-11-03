# Chessground integration plan

Research and implementation guide for adding a graphical computer-play
interface for Maia. This is separate from the existing OTB web interface:
leave `web_app.py`, its routes, and `templates/index.html` unchanged.

Research checked on 2026-11-03. Recheck upstream Chessground API and license
details before implementation; the project evolves.

## Current status

A first local Chessground app is implemented in `chessground_app.py`, with its
frontend in `frontend/` and `templates/chessground.html`. It reuses
`GameSession`, serves only on loopback, and does not change the OTB app. See
the [README launch instructions](./README.md#play-maia-on-a-chessground-board).
It includes server-enforced 3+2, 5+0, 5+3, and 10+0 presets plus custom
base-time/increment controls (1–180 minutes, 0–60 seconds); Maia's clock runs
while the engine computes and waits, and undo restores the clock before the
taken-back turn.

The backend and production frontend build have automated checks. The real
79M model still needs to be exercised from the UI on the intended computer,
including promotion and castling. The implementation uses the user's stated
assumption that this is a private, single-user app and will not be
redistributed.

## Recommendation

Build a separate local web app that opens in the computer's browser and uses
Chessground for the board. Chessground is a JavaScript/TypeScript browser
component, not a native desktop application or a UCI GUI. A local Flask
process can serve the interface and own the existing Maia engine. This gives
the desired graphical computer experience without changing the existing web
game used for OTB.

If a native `.exe` window is a firm requirement, that is a later packaging
step (for example, an Electron wrapper around the local web UI). It is not
needed to get a Chessground-based board working.

## What exists already

- [`maia3.uci`](./maia3/uci.py) is the existing UCI engine. No UCI protocol
  rewrite is needed for a board UI.
- [`maia_game.py`](./maia_game.py) contains the shared `GameSession` for game
  setup, player move validation, Maia move selection, undo, game results, and
  PGN creation. It already wraps the Maia UCI process through `python-chess`.
- [`web_app.py`](./web_app.py) exposes a Flask API around `GameSession`, but
  its current page is a text-entry interface. Its API response does not
  currently provide board FEN, legal destinations, check, or last-move data.
- [`templates/index.html`](./templates/index.html) is the OTB-facing
  interface. Do not modify it for this integration.
- The project has Python dependencies and packaging in `pyproject.toml`, but
  no existing JavaScript package/build setup.

## Proposed architecture

1. A separate Flask entry point (for example, `chessground_app.py`) starts the
   Maia engine and owns one `GameSession`.
2. A new HTML template and a small bundled frontend provide the computer
   interface. Keep their source and built assets distinct from the OTB
   template.
3. Chessground renders the position, orientation, legal move destinations,
   highlights, and user move input.
4. The Flask backend remains authoritative for all chess rules and game state.
   Use `python-chess` to calculate legal moves, validate submitted moves,
   detect check/game over, and create PGNs. Do not trust a browser-submitted
   move just because Chessground displayed it as legal.
5. The frontend submits a move, waits for the server to finish Maia's
   response, then renders the server's canonical position. It should resync
   from the server after errors rather than trying to repair a possibly
   out-of-date board locally.

Use the existing `GameSession` instead of duplicating engine or move logic.
Keep this first integration single-user and single-game: one engine/session
must not process overlapping move requests. Add request locking or an
in-flight guard so double-clicks or repeated submissions cannot make moves
against stale state.

## Work to implement

### 1. Add a separate frontend build

- Add a small frontend package (Vite with JavaScript or TypeScript is
  sufficient) that installs a pinned `@lichess-org/chessground` package.
- Import Chessground's base CSS and one included board/piece theme. Chessground
  does not render correctly with JavaScript alone; the `.cg-wrap` also needs a
  sized container and its stylesheet. The Cburnett theme embeds SVG piece
  artwork in CSS, avoiding a separate piece-image download.
- Build the frontend to static files served by the new Flask app. Check in
  source and the dependency lockfile; normally do not check in generated
  `node_modules`. Decide whether built output is generated during install or
  committed based on how this repository will be launched.
- Pin the Chessground version and keep required license/attribution notices
  with the bundled dependency.

No separate JavaScript chess-rules library is required if the server provides
legal destinations. That is preferable for this single-player app because
`python-chess` already validates the move and remains the one source of truth.

### 2. Add the separate local app

- Add a new Flask entry point and Chessground-specific template/static build.
- Use `create_engine()` and a `GameSession`; allow choosing side and ratings,
  matching capabilities of the existing game where practical.
- Reuse shared session methods for move application, Maia turns, undo, resign,
  new game, and PGN. Do not fork the UCI/model handling.
- Bind to loopback (`127.0.0.1`) by default and open the local URL in the
  desktop browser. Do not expose a development server to the network.
- Ensure shutdown quits the engine cleanly and saves an unfinished PGN using
  the same policy as the existing app.

### 3. Define a board-oriented API contract

Create endpoints for the new app rather than changing the OTB app's endpoints.
The exact paths can follow existing conventions, but the responses should
support:

- **Current state**: full game state needed by the UI, including piece
  placement FEN, side to move, human side/orientation, game started/thinking/
  over flags, result, check state, last move, and legal moves for the human.
- **Start/new game**: configure side and ratings, reset the shared session,
  and immediately return canonical state. If Maia has the first move when the
  user chooses Black, return state only after that opening move is complete.
- **Submit a move**: accept a UCI move (`e2e4`, or with promotion suffix such
  as `a7a8q`), check it against the current session, make the player's move,
  then make Maia's response if the game is still in progress. Return the
  resulting canonical state, SAN move list/status as useful, and game result.
- **Commands**: support undo, resign, and new game; either separate command
  routes or one well-defined command endpoint.
- **Recoverable state**: provide a way to fetch current state after a failed
  request or browser refresh.

For destinations, serialize legal moves as a map from origin square to
destination squares (for example, `a2 -> [a3, a4]`). Chessground's
`movable.dests` configuration can use this to show allowed moves. The server
still validates every submitted UCI move.

Chessground's `fen`/`getFen()` concerns piece placement, not complete chess
state. Keep the actual `chess.Board` on the server; separately provide the
side to move, last move, check state, and legal destinations needed to
configure the view.

### 4. Wire Chessground's interactions

- Create the board with the server's position, user orientation, side to move,
  last move, check state, and legal destinations.
- Set the movable color to the human side only while a game is active, it is
  the player's turn, and no move/engine request is in flight. Disable moves
  while Maia is thinking or the game has ended.
- In the move callback, submit the origin/destination as UCI. On success,
  update the board from returned server state. On failure, fetch and render
  canonical state and show the error.
- Chessground does not implement chess rules or choose a promotion piece.
  When a pawn reaches the last rank, show a promotion chooser (queen, rook,
  bishop, knight), then submit the corresponding UCI promotion move. Do not
  silently default without offering the other legal choices.
- Handle castling as the legal UCI move returned/accepted by `python-chess`;
  configure Chessground's castling interaction consistently (king-to-rook
  versus king-to-destination). Test both sides and both castling directions.
- After Maia's move, refresh legal destinations, turn, check, last move, and
  game-over state from the backend. Disable further input as appropriate.
- Keep move history, status, undo/resign/new-game controls, and PGN download
  accessible alongside the board.

### 5. License scope

Chessground is GPL-3.0-or-later. Its upstream README explicitly says that a
website using Chessground must be distributed under GPL terms and that source
must be released to users. This repository's `LICENSE` is AGPL-3.0. Before
sharing the app, review the applicable GPLv3/AGPLv3 compatibility and notice
requirements for the combined work. Preserve upstream notices and include the
corresponding license if the app is distributed. For the current
implementation, the user has stated that it will remain private and only they
will use it. This records the chosen scope, not a legal determination.

## Follow-up work

1. Run the app with the cached 79M model and play a complete game as both
   White and Black on the target machine.
2. Manually verify promotion, both castling directions, en passant, and
   checkmate/game-over presentation with the graphical board.
3. If desired, add browser-driven interaction tests and model selection
   controls; the current version intentionally keeps one local game and the
   existing 79M engine configuration.
4. Consider a native desktop wrapper only if opening the separate local app
   in a browser is not sufficient.

## Acceptance checks

- The existing OTB web app and its routes/page still work unchanged.
- The new app starts Maia once, opens the Chessground board locally, and lets
  the player move with mouse drag and click-click interaction.
- Legal highlights and orientation match the server state for either side.
- Illegal, stale, or out-of-turn moves are rejected and the displayed board
  resynchronizes to the backend.
- Promotion offers all four legal pieces; castling, en passant, and captures
  update correctly.
- While Maia is thinking, the board cannot submit additional player moves;
  after Maia responds, the user's side becomes movable again.
- The selected time control applies increments, flags either side on time, and
  stops the clocks when the game ends.
- Check, checkmate, stalemate/draw, resignation, undo, new game, browser
  refresh, and PGN export behave consistently with `GameSession`.
- Closing the local app shuts down the engine; it does not expose a listener
  to the LAN by default.
- Chessground license notices and source distribution obligations are
  documented for the chosen way of sharing/deploying the app.

## References

- [Chessground repository and upstream usage/license notes](https://github.com/lichess-org/chessground)
- [Chessground configuration types](https://github.com/lichess-org/chessground/blob/master/src/config.ts)
- [Chessground API](https://github.com/lichess-org/chessground/blob/master/src/api.ts)
- [Chessground base CSS](https://github.com/lichess-org/chessground/blob/master/assets/chessground.base.css)
- [Chessground Cburnett piece theme](https://github.com/lichess-org/chessground/blob/master/assets/chessground.cburnett.css)
- [Chessground examples and stylesheet notes](https://github.com/lichess-org/chessground-examples)
- [This repository's Maia game/session code](./maia_game.py)
- [This repository's current web app](./web_app.py) and [OTB interface](./templates/index.html)
- [This repository's license](./LICENSE)