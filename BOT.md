# Turning Maia into a Lichess bot

Research and implementation guide for this repository. The recommended first
version is to run the existing Maia UCI engine behind the
[lichess-bot](https://github.com/lichess-bot-devs/lichess-bot) bridge. That
avoids writing and maintaining a Lichess API client while reusing the engine
already in this project.

Research checked on 2026-11-03. Lichess API details and bridge configuration
can change; recheck the linked documentation during implementation.

## What this repository already provides

- [`maia3.uci`](./maia3/uci.py) implements a UCI engine and has a `maia3-79m`
  command-line entry point (as well as smaller model presets).
- UCI options `SelfElo`, `OppoElo`, `Temperature`, and `TopP` are supported.
- The engine accepts UCI positions and moves. Enable `--use-uci-history` if
  the bridge can pass engine startup arguments, so Maia gets the preceding
  game positions rather than only the current position.
- [`maia_game.py`](./maia_game.py) already starts the 79M engine with
  temperature `0.2`, top-p `0.9`, and UCI history enabled. It is local-game
  UI/session logic, not a Lichess integration.
- Dependencies already include `python-chess` and PyTorch. The model weights
  must be downloaded/cached before unattended play; first-run model loading
  can take too long for a game clock or engine startup timeout.

## Recommended implementation: `lichess-bot` + UCI

### 1. Prepare a dedicated Lichess account

Create a new account just for the bot. Do not convert a personal account or
one that has already played games: upgrading an account to a BOT account is
irreversible. Choose a username and profile that clearly identify it as an
automated Maia bot.

Read the [Lichess Bot API documentation](https://lichess.org/api#tag/Bot) and
its account-upgrade operation before proceeding. A BOT account is for API
play; do not use it to play manually.

### 2. Install the bridge separately

Keep the bridge in its own directory and virtual environment rather than
adding it as a dependency to this project. The bridge's current installation
guide requires Python 3.11 or later; this project's `pyproject.toml` supports
Python 3.10 and later. The two processes can use separate Python installations.

On Windows, follow the
[bridge installation guide](https://github.com/lichess-bot-devs/lichess-bot/wiki/How-to-Install):

1. Clone the bridge repository.
2. Create and install its Python 3.11+ virtual environment.
3. Copy `config.yml.default` to `config.yml`.

### 3. Install and warm up Maia

Install this project into its own virtual environment, then download the model
you intend to use before starting the bridge. For example, from this
repository's root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m maia3.cache --model maia3-79m
```

The 79M model may be too slow or memory-intensive on some machines. Start with
the 5M preset if necessary, then benchmark the chosen model on the actual
deployment hardware. Test its first move and subsequent moves against the
intended game clocks. The UCI implementation computes its move directly and
does not use the supplied clock values to search longer or shorter.

### 4. Point `lichess-bot` at the Maia UCI executable

Configure the bridge's `engine` section for UCI and point `engine.dir` and
`engine.name` at the installed Maia entry point, for example
`C:\dev\maia-3-otb-play\.venv\Scripts\maia3-79m.exe`. Set `working_dir` to the
project root if needed. Use the actual absolute paths on the machine running
the bot.

In the bridge config, set the UCI options Maia advertises. For an initial
configuration that resembles the local Maia game, use `Temperature: 0.2`,
`TopP: 0.9`, and fixed `SelfElo`/`OppoElo` values. Those Elo settings should
reflect the Maia and opponent strength you want the model to represent; this
bridge configuration is a fixed pair, not automatic per-opponent rating
selection.

Use the bridge's documented `engine_options` mechanism for UCI-engine startup
flags. If supported by the installed bridge version, pass
`--use-uci-history` (and optionally `--local-files-only` after warming the
cache). Verify the actual command-line argument format for boolean flags
against that bridge version rather than assuming YAML values map to flags.
Use the bridge's
[engine setup guide](https://github.com/lichess-bot-devs/lichess-bot/wiki/Setup-the-engine)
and [configuration guide](https://github.com/lichess-bot-devs/lichess-bot/wiki/Configure-lichess-bot).

### 5. Create and protect the API token

While signed in as the dedicated bot account, create a Lichess OAuth token
with the **Play games with the bot API** (`bot:play`) scope. The
[bridge token guide](https://github.com/lichess-bot-devs/lichess-bot/wiki/How-to-create-a-Lichess-OAuth-token)
links to the token creation page and explains how to set
`LICHESS_BOT_TOKEN`.

Keep the token out of source control, screenshots, logs, and shared config.
Prefer the environment-variable option documented by the bridge or an
equivalent secret store. If a token is exposed, revoke it and create another.

### 6. Verify identity, then upgrade the account

Use the token to check `/api/account` and confirm it identifies the intended
dedicated account. Only then follow the bridge's
[BOT-account upgrade instructions](https://github.com/lichess-bot-devs/lichess-bot/wiki/Upgrade-to-a-BOT-account)
(`python lichess-bot.py -u`). The upgrade is irreversible; do not automate it
as part of ordinary startup.

### 7. Start with a conservative challenge policy

In the bridge config, begin with one concurrent game, standard chess only,
and casual games. Restrict incoming challenges to time controls the engine
can handle reliably. Test measured model load and move latency before trying
fast controls. Review the bridge's controls for challenge acceptance,
concurrency, matchmaking, and game limits before enabling them.
Human challenges are supported; the bridge's current guide states a daily
limit for bot-vs-bot games (100) but not human games. Recheck that policy and
the current bridge configuration before relying on those limits.

Do not enable rated play by default. Maia is trained to model human moves,
not necessarily to maximize the chance of winning; the bridge's configuration
guide says rated play requires a comment if the engine does not try to win.
Decide deliberately whether rated games fit this bot, disclose the behavior,
and check current Lichess rules before enabling them.

### 8. Test privately before leaving it online

1. Run the bridge in the foreground with verbose logging.
2. Confirm it authenticates as the expected bot account and the UCI engine
   starts without downloading weights or failing to load the model.
3. Challenge it to a casual game from a test account; test both colors,
   opening moves, promotions, checkmate, resignation, and an opponent
   disconnect.
4. Confirm Maia only returns legal moves, finishes moves within the selected
   clock, and can handle a second game after the first has ended.
5. Inspect logs for the engine command and errors. Never print the OAuth
   token while debugging.

The bridge owns Lichess event streaming, move submission, challenge handling,
and reconnection. Keep it running as one supervised process on a stable host;
avoid multiple bridge instances using the same account. Add restart
monitoring and rotate/revoke credentials when needed. The current bridge docs
also describe daily bot-vs-bot game limits; recheck those and other limits
before enabling matchmaking.

### 9. Deploy and monitor

After testing, run the bridge as a persistent service (for example, Windows
Task Scheduler or a service wrapper) with the correct Python environment,
working directory, token, model cache, and logs. Verify it restarts after
machine reboots, can reach Lichess and the model cache, and has enough RAM or
GPU memory for the selected model. Keep the bridge and this project updated
deliberately, and re-run the smoke tests after upgrades.

## When to write a custom Lichess client instead

Only choose this if the bridge cannot meet a concrete requirement, such as
dynamic per-opponent Maia Elo or customized challenge policy. A custom client
must implement OAuth authentication, the account event stream, challenge
acceptance/rejection, per-game bot streams, board reconstruction, legal move
selection, move submission, resign/abort/draw handling, retries, and
reconnection without duplicate moves. Consult the current
[official API reference](https://lichess.org/api), especially the Bot API
endpoints for account upgrade, event streaming, game streaming, and move
submission. Respect API rate limits and do not build a tight polling loop.

For either approach, review this repository's [license](./LICENSE), the
network-use note in [`README.md`](./README.md), and the applicable Maia model
weight terms before redistributing or offering a hosted service.

## References

- [Lichess API reference — Bot endpoints](https://lichess.org/api#tag/Bot)
- [Lichess API reference — account upgrade](https://lichess.org/api#operation/botAccountUpgrade)
- [`lichess-bot` repository and overview](https://github.com/lichess-bot-devs/lichess-bot)
- [`lichess-bot` installation guide](https://github.com/lichess-bot-devs/lichess-bot/wiki/How-to-Install)
- [`lichess-bot` OAuth token guide](https://github.com/lichess-bot-devs/lichess-bot/wiki/How-to-create-a-Lichess-OAuth-token)
- [`lichess-bot` UCI engine setup](https://github.com/lichess-bot-devs/lichess-bot/wiki/Setup-the-engine)
- [`lichess-bot` configuration guide](https://github.com/lichess-bot-devs/lichess-bot/wiki/Configure-lichess-bot)
- [`lichess-bot` upgrade and run guides](https://github.com/lichess-bot-devs/lichess-bot/wiki/Upgrade-to-a-BOT-account),
  [running](https://github.com/lichess-bot-devs/lichess-bot/wiki/How-to-Run-lichess%E2%80%90bot)