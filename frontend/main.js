import { Chessground } from '@lichess-org/chessground';
import '@lichess-org/chessground/assets/chessground.base.css';
import '@lichess-org/chessground/assets/chessground.brown.css';
import '@lichess-org/chessground/assets/chessground.cburnett.css';
import './style.css';

const boardElement = document.querySelector('#board');
const setupForm = document.querySelector('#setup');
const timeControlInput = document.querySelector('#time-control');
const customTimeControl = document.querySelector('#custom-time-control');
const baseTimeMinutesInput = document.querySelector('#base-time-minutes');
const incrementSecondsInput = document.querySelector('#increment-seconds');
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
let audioContext;

const ground = Chessground(boardElement, {
  fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR',
  orientation: 'white',
  autoCastle: true,
  viewOnly: false,
  premovable: { enabled: true },
  movable: { dests: new Map() },
});

function destinationsToMap(destinations = {}) {
  return new Map(
    Object.entries(destinations).map(([origin, targets]) => [origin, targets]),
  );
}

function renderState(state) {
  const previousState = currentState;
  currentState = state;
  if (previousState) {
    const newEngineMoves = state.history.slice(previousState.history.length);
    for (const move of newEngineMoves) {
      if (move.role === 'Engine') playMoveSound(move.text.includes('x'));
    }
  }
  if (state.time_control) {
    timeControlInput.value = state.time_control;
    customTimeControl.hidden = state.time_control !== 'custom';
  }
  if (state.clock.base_seconds) {
    baseTimeMinutesInput.value = state.clock.base_seconds / 60;
    incrementSecondsInput.value = state.clock.increment_seconds;
  }
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
    premovable: { enabled: state.started && !state.game_over },
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
    statusElement.textContent = 'Choose a time control and find a match.';
  } else if (state.maia_thinking || requestPending) {
    statusElement.textContent = 'Maia is thinking…';
  } else {
    const matchSummary =
      `You (${state.player_elo}) are ${state.player_color} vs Maia ${state.maia_elo}. `;
    statusElement.textContent = state.can_move
      ? `${matchSummary}Your move.`
      : `${matchSummary}Waiting for Maia.`;
  }
}

function playMoveSound(capture = false) {
  void unlockMoveSounds().then(() => {
    if (!audioContext || audioContext.state !== 'running') return;
    const now = audioContext.currentTime;
    const impacts = capture ? [0, 0.095] : [0];
    for (const delay of impacts) {
      const oscillator = audioContext.createOscillator();
      const volume = audioContext.createGain();
      const start = now + delay;
      oscillator.type = 'triangle';
      oscillator.frequency.setValueAtTime(capture ? 410 : 520, start);
      oscillator.frequency.exponentialRampToValueAtTime(
        capture ? 155 : 230,
        start + 0.055,
      );
      volume.gain.setValueAtTime(0.0001, start);
      volume.gain.exponentialRampToValueAtTime(capture ? 0.16 : 0.12, start + 0.006);
      volume.gain.exponentialRampToValueAtTime(0.0001, start + 0.075);
      oscillator.connect(volume);
      volume.connect(audioContext.destination);
      oscillator.start(start);
      oscillator.stop(start + 0.08);
    }
  });
}

function unlockMoveSounds() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return Promise.resolve();
  try {
    audioContext ||= new AudioContextClass();
  } catch (error) {
    console.warn('Could not initialize chess move sounds.', error);
    return Promise.resolve();
  }
  if (audioContext.state === 'suspended') {
    return audioContext.resume().catch((error) => {
      console.warn('Could not play chess move sound.', error);
    });
  }
  return Promise.resolve();
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

function setPending(pending, allowPremove = false) {
  requestPending = pending;
  if (!currentState) return;
  if (!pending) {
    renderState(currentState);
    return;
  }

  ground.set({
    ...(allowPremove
      ? {
          turnColor: currentState.player_color === 'white' ? 'black' : 'white',
        }
      : {}),
    movable: {
      color: allowPremove ? currentState.player_color : undefined,
      dests: new Map(),
      events: { after: onBoardMove },
    },
  });
  newGameButton.disabled = true;
  undoButton.disabled = true;
  resignButton.disabled = true;
  statusElement.textContent = allowPremove
    ? 'Maia is thinking… You can queue a premove.'
    : 'Maia is thinking…';
}

async function sendMove(move, capture = false) {
  const expectedPly = currentState.ply;
  let moveAccepted = false;
  setPending(true, true);
  playMoveSound(capture);
  optimisticallyStartMaiaClock();
  try {
    const response = await fetch('/api/move', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ move, expected_ply: expectedPly }),
    });
    const data = await readJson(response);
    renderState(data.state);
    moveAccepted = true;
  } catch (error) {
    statusElement.textContent = error.message;
    ground.cancelPremove();
    await refreshState();
  } finally {
    setPending(false);
    if (moveAccepted && currentState?.can_move) ground.playPremove();
    else ground.cancelPremove();
  }
}

function onBoardMove(origin, destination, metadata) {
  if (!currentState?.can_move || requestPending) {
    void refreshState();
    return;
  }

  const capture = Boolean(metadata.captured);
  const moveKey = `${origin}${destination}`;
  const promotions = currentState.promotions[moveKey];
  if (promotions?.length) {
    promotionMove = { moveKey, promotions, capture };
    for (const button of promotionDialog.querySelectorAll('[data-piece]')) {
      button.hidden = !promotions.includes(button.dataset.piece);
    }
    promotionDialog.showModal();
    return;
  }
  void sendMove(moveKey, capture);
}

setupForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  unlockMoveSounds();
  const submitButton = setupForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  statusElement.textContent = 'Finding a match…';
  try {
    const response = await fetch('/api/find-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        time_control: timeControlInput.value,
        base_time_minutes: Number(baseTimeMinutesInput.value),
        increment_seconds: Number(incrementSecondsInput.value),
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

timeControlInput.addEventListener('change', () => {
  customTimeControl.hidden = timeControlInput.value !== 'custom';
});

promotionDialog.addEventListener('click', (event) => {
  const piece = event.target.closest('[data-piece]')?.dataset.piece;
  if (!piece || !promotionMove?.promotions.includes(piece)) return;
  const move = `${promotionMove.moveKey}${piece}`;
  const capture = promotionMove.capture;
  promotionMove = undefined;
  promotionDialog.close();
  void sendMove(move, capture);
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
