import { Chessground } from '@lichess-org/chessground';
import '@lichess-org/chessground/assets/chessground.base.css';
import '@lichess-org/chessground/assets/chessground.brown.css';
import '@lichess-org/chessground/assets/chessground.cburnett.css';
import './style.css';

const boardElement = document.querySelector('#board');
const setupForm = document.querySelector('#setup');
const colorInput = document.querySelector('#player-color');
const maiaEloInput = document.querySelector('#maia-elo');
const playerEloInput = document.querySelector('#player-elo');
const timeControlInput = document.querySelector('#time-control');
const statusElement = document.querySelector('#status');
const historyElement = document.querySelector('#move-history');
const clockElements = {
  white: document.querySelector('#white-clock'),
  black: document.querySelector('#black-clock'),
};
const promotionDialog = document.querySelector('#promotion-dialog');
const newGameButton = document.querySelector('#new-game');
const undoButton = document.querySelector('#undo');
const resignButton = document.querySelector('#resign');
const pgnButton = document.querySelector('#download-pgn');

let currentState;
let requestPending = false;
let promotionMove;
let clockState;

const ground = Chessground(boardElement, {
  fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR',
  orientation: 'white',
  autoCastle: true,
  viewOnly: false,
  movable: { dests: new Map() },
});

function destinationsToMap(destinations = {}) {
  return new Map(
    Object.entries(destinations).map(([origin, targets]) => [origin, targets]),
  );
}

function renderState(state) {
  currentState = state;
  maiaEloInput.value = state.maia_elo;
  playerEloInput.value = state.player_elo;
  if (state.time_control) timeControlInput.value = state.time_control;
  renderClocks(state.clock, state.started);
  const canMove = Boolean(state.can_move) && !requestPending;
  ground.set({
    fen: state.fen,
    orientation: state.player_color || 'white',
    turnColor: state.turn_color,
    lastMove: state.last_move || undefined,
    check: state.check,
    movable: {
      color: canMove ? state.player_color : undefined,
      dests: destinationsToMap(canMove ? state.destinations : {}),
      events: { after: onBoardMove },
    },
  });

  setupForm.hidden = state.started;
  newGameButton.hidden = !state.started;
  newGameButton.disabled = requestPending || state.maia_thinking;
  undoButton.disabled =
    !state.started || state.game_over || requestPending || state.maia_thinking;
  resignButton.disabled =
    !state.started || state.game_over || requestPending || state.maia_thinking;
  pgnButton.hidden = !state.game_over;

  historyElement.replaceChildren();
  for (const [index, item] of state.history.entries()) {
    const row = document.createElement('li');
    row.textContent = `${Math.floor(index / 2) + 1}${index % 2 === 0 ? '.' : '...'} ${item.text}`;
    historyElement.append(row);
  }

  if (state.game_over) {
    const reason = state.termination === 'timeout' ? 'Time out. ' : '';
    statusElement.textContent = `${reason}Game over${state.result ? ` (${state.result})` : ''}.`;
  } else if (!state.started) {
    statusElement.textContent = 'Choose a side and start a game.';
  } else if (state.maia_thinking || requestPending) {
    statusElement.textContent = 'Maia is thinking…';
  } else {
    statusElement.textContent = state.can_move ? 'Your move.' : 'Waiting for Maia.';
  }
}

function getDisplayedClockTimes() {
  if (!clockState?.started) return { white: 0, black: 0 };
  const remaining = { ...clockState.remainingMs };
  if (clockState.runningColor) {
    remaining[clockState.runningColor] = Math.max(
      0,
      remaining[clockState.runningColor] -
        (performance.now() - clockState.sampledAt),
    );
  }
  return remaining;
}

function formatClock(milliseconds) {
  if (milliseconds < 10_000) {
    const tenths = Math.ceil(milliseconds / 100);
    return `${Math.floor(tenths / 10)}.${tenths % 10}`;
  }
  const seconds = Math.ceil(milliseconds / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function updateClockDisplay() {
  const remaining = getDisplayedClockTimes();
  for (const color of ['white', 'black']) {
    const element = clockElements[color];
    const timeElement = element.querySelector('time');
    const milliseconds = remaining[color];
    timeElement.textContent = clockState?.started
      ? formatClock(milliseconds)
      : '--:--';
    if (clockState?.started) {
      timeElement.dateTime = `PT${(milliseconds / 1000).toFixed(3)}S`;
    } else {
      timeElement.removeAttribute('datetime');
    }
    element.classList.toggle(
      'active',
      Boolean(clockState?.started) && clockState.runningColor === color,
    );
    element.classList.toggle(
      'expiring',
      clockState?.runningColor === color && milliseconds < 10_000,
    );
  }
}

function renderClocks(clock, started) {
  clockState = {
    remainingMs: clock.remaining_ms,
    runningColor: started ? clock.running_color : null,
    incrementSeconds: clock.increment_seconds,
    sampledAt: performance.now(),
    started,
  };
  updateClockDisplay();
}

function optimisticallyStartMaiaClock() {
  if (!clockState?.started) return;
  const remaining = getDisplayedClockTimes();
  const playerColor = currentState.player_color;
  remaining[playerColor] += clockState.incrementSeconds * 1000;
  clockState = {
    ...clockState,
    remainingMs: remaining,
    runningColor: playerColor === 'white' ? 'black' : 'white',
    sampledAt: performance.now(),
  };
  updateClockDisplay();
}

async function readJson(response) {
  const data = await response.json();
  if (!response.ok) {
    if (data.state) renderState(data.state);
    throw new Error(data.error || 'The request failed.');
  }
  return data;
}

async function refreshState() {
  const response = await fetch('/api/state');
  renderState(await readJson(response));
}

function setPending(pending) {
  requestPending = pending;
  if (!currentState) return;
  if (!pending) {
    renderState(currentState);
    return;
  }

  ground.set({
    movable: {
      color: undefined,
      dests: new Map(),
      events: { after: onBoardMove },
    },
  });
  newGameButton.disabled = true;
  undoButton.disabled = true;
  resignButton.disabled = true;
  statusElement.textContent = 'Maia is thinking…';
}

async function sendMove(move) {
  const expectedPly = currentState.ply;
  setPending(true);
  optimisticallyStartMaiaClock();
  try {
    const response = await fetch('/api/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ move, expected_ply: expectedPly }),
    });
    const data = await readJson(response);
    renderState(data.state);
  } catch (error) {
    statusElement.textContent = error.message;
    await refreshState();
  } finally {
    setPending(false);
  }
}

function onBoardMove(origin, destination) {
  if (!currentState?.can_move || requestPending) {
    void refreshState();
    return;
  }

  const moveKey = `${origin}${destination}`;
  const promotions = currentState.promotions[moveKey];
  if (promotions?.length) {
    promotionMove = { moveKey, promotions };
    for (const button of promotionDialog.querySelectorAll('[data-piece]')) {
      button.hidden = !promotions.includes(button.dataset.piece);
    }
    promotionDialog.showModal();
    return;
  }
  void sendMove(moveKey);
}

setupForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitButton = setupForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  statusElement.textContent = 'Starting game…';
  try {
    const response = await fetch('/api/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        color: colorInput.value,
        maia_elo: Number(maiaEloInput.value),
        player_elo: Number(playerEloInput.value),
        time_control: timeControlInput.value,
      }),
    });
    const data = await readJson(response);
    renderState(data.state);
  } catch (error) {
    statusElement.textContent = error.message;
    await refreshState();
  } finally {
    submitButton.disabled = false;
  }
});

promotionDialog.addEventListener('click', (event) => {
  const piece = event.target.closest('[data-piece]')?.dataset.piece;
  if (!piece || !promotionMove?.promotions.includes(piece)) return;
  const move = `${promotionMove.moveKey}${piece}`;
  promotionMove = undefined;
  promotionDialog.close();
  void sendMove(move);
});

promotionDialog.addEventListener('cancel', () => {
  promotionMove = undefined;
  void refreshState();
});

async function sendCommand(command) {
  setPending(true);
  try {
    const response = await fetch('/api/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command }),
    });
    const data = await readJson(response);
    renderState(data.state);
    if (data.message) statusElement.textContent = data.message;
  } catch (error) {
    statusElement.textContent = error.message;
    await refreshState();
  } finally {
    setPending(false);
  }
}

newGameButton.addEventListener('click', async () => {
  setPending(true);
  try {
    const response = await fetch('/api/new-game', { method: 'POST' });
    const data = await readJson(response);
    renderState(data.state);
  } catch (error) {
    statusElement.textContent = error.message;
  } finally {
    setPending(false);
  }
});

undoButton.addEventListener('click', () => void sendCommand('undo'));
resignButton.addEventListener('click', () => {
  if (window.confirm('Resign this game?')) void sendCommand('resign');
});

pgnButton.addEventListener('click', async () => {
  try {
    const response = await fetch('/api/pgn');
    const data = await readJson(response);
    const link = document.createElement('a');
    const url = URL.createObjectURL(
      new Blob([data.pgn], { type: 'application/x-chess-pgn' }),
    );
    link.href = url;
    link.download = 'maia-game.pgn';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

refreshState().catch((error) => {
  statusElement.textContent = error.message;
});

setInterval(updateClockDisplay, 100);
