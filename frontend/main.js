import { Chessground } from '@lichess-org/chessground';
import '@lichess-org/chessground/assets/chessground.base.css';
import '@lichess-org/chessground/assets/chessground.brown.css';
import '@lichess-org/chessground/assets/chessground.cburnett.css';
import './style.css';

const boardElement = document.querySelector('#board');
const setupForm = document.querySelector('#setup');
const timeControlInput = document.querySelector('#time-control');
const statusElement = document.querySelector('#status');
const historyElement = document.querySelector('#move-history');
const moveFirstButton = document.querySelector('#move-first');
const movePreviousButton = document.querySelector('#move-previous');
const moveNextButton = document.querySelector('#move-next');
const moveLiveButton = document.querySelector('#move-live');
const opponentDisplay = document.querySelector('#opponent-display');
const opponentName = document.querySelector('#opponent-name');
const opponentRating = document.querySelector('#opponent-rating');
const playerDisplay = document.querySelector('#player-display');
const playerName = document.querySelector('#player-name');
const playerRating = document.querySelector('#player-rating');
const clockElements = {
  white: document.querySelector('#white-clock'),
  black: document.querySelector('#black-clock'),
};
const promotionDialog = document.querySelector('#promotion-dialog');
const clocksPanel = document.querySelector('#clocks-panel');
const actionsPanel = document.querySelector('#actions-panel');
const movesPanel = document.querySelector('#moves-panel');
const resultSummary = document.querySelector('#result-summary');
const resultIcon = document.querySelector('#result-icon');
const resultCategory = document.querySelector('#result-category');
const resultTitle = document.querySelector('#result-title');
const resultDetail = document.querySelector('#result-detail');
const resultRating = document.querySelector('#result-rating');
const newGameButton = document.querySelector('#new-game');
const undoButton = document.querySelector('#undo');
const resignButton = document.querySelector('#resign');
const copyPgnButton = document.querySelector('#copy-pgn');
const pgnFallback = document.querySelector('#pgn-fallback');

let currentState;
let reviewPly = null;
let requestPending = false;
let timeoutRefreshPending = false;
let lastTimeoutRefreshAt = -Infinity;
let promotionMove;
let clockState;
let audioContext;

const ground = Chessground(boardElement, {
  fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR',
  orientation: 'white',
  autoCastle: true,
  viewOnly: false,
  premovable: { enabled: true },
  movable: { free: false, dests: new Map() },
});

function fitBoardToDevicePixels() {
  const availableWidth = boardElement.parentElement.getBoundingClientRect().width;
  const boardSize =
    (Math.floor((availableWidth * window.devicePixelRatio) / 8) * 8) /
    window.devicePixelRatio;
  if (boardSize > 0) {
    const roundingMargin = 0.02;
    boardElement.style.width = `${boardSize + roundingMargin}px`;
    boardElement.style.height = `${boardSize + roundingMargin}px`;
  }
}

fitBoardToDevicePixels();
if ('ResizeObserver' in window) {
  new ResizeObserver(fitBoardToDevicePixels).observe(boardElement.parentElement);
} else {
  window.addEventListener('resize', fitBoardToDevicePixels);
}

function destinationsToMap(destinations = {}) {
  return new Map(
    Object.entries(destinations).map(([origin, targets]) => [origin, targets]),
  );
}

function getPlayerOutcome(state) {
  if (state.rating_outcome) return state.rating_outcome;
  if (!state.game_over || !state.result) return null;
  if (state.result === '1/2-1/2') return 'draw';
  const whiteWon = state.result === '1-0';
  const playerWon = (state.player_color === 'white') === whiteWon;
  return playerWon ? 'win' : 'loss';
}

function getResultDetail(state, outcome) {
  if (state.termination === 'timeout') {
    if (outcome === 'draw') return 'Time expired, but neither side had enough material to win.';
    return outcome === 'win' ? 'Maia ran out of time.' : 'Your clock ran out.';
  }
  if (state.termination === 'resignation') {
    return outcome === 'loss' ? 'You resigned. There is always another game.' : 'The game ended by resignation.';
  }
  if (state.termination === 'checkmate') {
    return outcome === 'win' ? 'Checkmate! A brilliant finish.' : 'Checkmate. Maia found the winning line.';
  }
  if (state.termination === 'stalemate') return 'No legal moves remain, and the game is drawn.';
  if (state.termination === 'threefold_repetition') {
    return 'The same position occurred three times, so the game is drawn.';
  }
  if (state.termination === 'fifty_moves') {
    return 'Fifty moves passed without a pawn move or capture, so the game is drawn.';
  }
  if (outcome === 'draw') return 'A hard-fought game ends level.';
  return 'The game is complete.';
}

function renderState(state) {
  const previousState = currentState;
  const boardLayoutChanged =
    previousState && previousState.started !== state.started;
  currentState = state;
  if (
    !state.started ||
    (previousState &&
      (state.ply < previousState.ply ||
        state.history.length < previousState.history.length))
  ) {
    reviewPly = null;
  } else if (reviewPly !== null && reviewPly > state.ply) {
    reviewPly = state.ply;
  }
  if (previousState) {
    const newEngineMoves = state.history.slice(previousState.history.length);
    for (const move of newEngineMoves) {
      if (move.role === 'Engine') playMoveSound(move.text.includes('x'));
    }
    if (
      previousState.started &&
      state.started &&
      state.ply > previousState.ply &&
      state.positions
        ?.slice(previousState.ply + 1, state.ply + 1)
        .some((position) => position.check)
    ) {
      playCheckSound();
    }
  }
  if (state.started && state.time_control) {
    timeControlInput.value = state.time_control;
  }
  renderClocks(state.clock, state.started);
  const activePly = reviewPly ?? state.ply;
  const reviewing = activePly !== state.ply;
  const position = state.positions?.[activePly] || {
    fen: state.fen,
    turn_color: state.turn_color,
    last_move: state.last_move,
    check: state.check,
    check_square: state.check_square,
  };
  const canMove = Boolean(state.can_move) && !requestPending && !reviewing;
  opponentDisplay.hidden = !state.started;
  playerDisplay.hidden = !state.started;
  opponentName.textContent = state.maia_name || 'Maia';
  opponentRating.textContent = state.started ? `(${state.maia_elo})` : '';
  playerName.textContent = state.player_name || 'You';
  playerRating.textContent = state.started ? `(${state.player_elo})` : '';
  const playerOutcome = getPlayerOutcome(state);
  if (previousState && !previousState.started && state.started) {
    playNewMatchSound();
  } else if (
    previousState?.started &&
    !previousState.game_over &&
    state.game_over
  ) {
    playMatchEndSound(playerOutcome);
  }
  playerRating.classList.toggle(
    'rating-winner',
    state.game_over && playerOutcome === 'win',
  );
  playerRating.classList.toggle(
    'rating-loser',
    state.game_over && playerOutcome === 'loss',
  );
  opponentRating.classList.toggle(
    'rating-winner',
    state.game_over && playerOutcome === 'loss',
  );
  opponentRating.classList.toggle(
    'rating-loser',
    state.game_over && playerOutcome === 'win',
  );
  ground.set({
    fen: position.fen,
    orientation: state.player_color || 'white',
    turnColor: position.turn_color,
    lastMove: position.last_move || undefined,
    check: position.check,
    movable: {
      color: canMove ? state.player_color : undefined,
      dests: destinationsToMap(canMove ? state.destinations : {}),
      events: { after: onBoardMove },
    },
    premovable: {
      enabled: state.started && !state.game_over && !reviewing,
    },
  });

  setupForm.hidden = state.started;
  clocksPanel.hidden = !state.started;
  actionsPanel.hidden = !state.started;
  movesPanel.hidden = !state.started;
  newGameButton.hidden = !state.started;
  newGameButton.disabled = requestPending || state.maia_thinking;
  undoButton.disabled =
    !state.started ||
    state.game_over ||
    requestPending ||
    state.maia_thinking ||
    reviewing;
  resignButton.disabled =
    !state.started || state.game_over || requestPending || state.maia_thinking;
  copyPgnButton.hidden = !state.game_over;
  newGameButton.textContent = state.game_over ? 'Play again' : 'End match';
  resultSummary.hidden = !state.game_over;
  if (state.game_over) {
    resultSummary.dataset.outcome = playerOutcome || 'draw';
    resultIcon.textContent =
      playerOutcome === 'win' ? '♔' : playerOutcome === 'loss' ? '♟' : '½';
    resultCategory.textContent = state.rating_category || '';
    resultTitle.textContent =
      playerOutcome === 'win'
        ? 'Victory!'
        : playerOutcome === 'loss'
          ? 'Defeat'
          : 'Draw';
    resultDetail.textContent = getResultDetail(state, playerOutcome);
    resultRating.textContent =
      state.rating_change === null
        ? `Rating ${state.player_elo}`
        : `${state.rating_change > 0 ? '+' : ''}${state.rating_change} rating · ${state.player_elo}`;
  }
  if (!state.game_over) pgnFallback.hidden = true;
  if (boardLayoutChanged) {
    fitBoardToDevicePixels();
    ground.redrawAll();
  }

  historyElement.replaceChildren();
  for (let index = 0; index < state.history.length; index += 2) {
    const row = document.createElement('li');
    row.className = 'move-pair';
    const moveNumber = document.createElement('span');
    moveNumber.className = 'move-number';
    moveNumber.textContent = `${Math.floor(index / 2) + 1}.`;
    historyElement.append(row);
    row.append(moveNumber);
    for (let offset = 0; offset < 2; offset += 1) {
      const move = state.history[index + offset];
      if (!move) {
        row.append(document.createElement('span'));
        continue;
      }
      const moveButton = document.createElement('button');
      moveButton.type = 'button';
      moveButton.className = 'move-san';
      moveButton.textContent = move.text;
      moveButton.dataset.ply = String(index + offset + 1);
      moveButton.setAttribute(
        'aria-label',
        `Review move ${Math.floor(index / 2) + 1}${offset ? '... ' : '. '}${move.text}`,
      );
      if (activePly === index + offset + 1) {
        moveButton.setAttribute('aria-current', 'true');
      }
      row.append(moveButton);
    }
  }
  const currentPly = reviewPly ?? state.ply;
  moveFirstButton.disabled = !state.started || currentPly === 0;
  movePreviousButton.disabled = !state.started || currentPly === 0;
  moveNextButton.disabled = !state.started || currentPly >= state.ply;
  moveLiveButton.disabled = !state.started || currentPly >= state.ply;

  if (reviewing) {
    statusElement.textContent = `Reviewing move ${activePly} of ${state.ply}.`;
  } else if (state.game_over) {
    statusElement.textContent = `Final score: ${state.result || '—'}`;
  } else if (!state.started) {
    statusElement.textContent = 'Choose a time control and find a match.';
  } else if (state.maia_thinking || requestPending) {
    statusElement.textContent = `${state.maia_name || 'Maia'} is thinking…`;
  } else {
    const matchSummary =
      `You (${state.player_elo}) are ${state.player_color} vs ${state.maia_name || 'Maia'} (${state.maia_elo}). `;
    statusElement.textContent = state.can_move
      ? `${matchSummary}Your move.`
      : `${matchSummary}Waiting for Maia.`;
  }
}

function selectReviewPly(ply) {
  if (!currentState) return;
  reviewPly = ply >= currentState.ply ? null : Math.max(0, ply);
  renderState(currentState);
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

function playCue(notes) {
  void unlockMoveSounds().then(() => {
    if (!audioContext || audioContext.state !== 'running') return;
    const now = audioContext.currentTime;
    for (const { frequency, offset, duration, volume, type } of notes) {
      const oscillator = audioContext.createOscillator();
      const envelope = audioContext.createGain();
      const start = now + offset;
      oscillator.type = type;
      oscillator.frequency.setValueAtTime(frequency, start);
      envelope.gain.setValueAtTime(0.0001, start);
      envelope.gain.exponentialRampToValueAtTime(volume, start + 0.025);
      envelope.gain.exponentialRampToValueAtTime(0.0001, start + duration);
      oscillator.connect(envelope);
      envelope.connect(audioContext.destination);
      oscillator.start(start);
      oscillator.stop(start + duration + 0.01);
    }
  });
}

function playNewMatchSound() {
  playCue([
    { frequency: 392, offset: 0, duration: 0.32, volume: 0.08, type: 'sine' },
    { frequency: 523.25, offset: 0.11, duration: 0.38, volume: 0.075, type: 'sine' },
    { frequency: 659.25, offset: 0.23, duration: 0.52, volume: 0.07, type: 'triangle' },
  ]);
}

function playCheckSound() {
  playCue([
    { frequency: 880, offset: 0, duration: 0.12, volume: 0.075, type: 'triangle' },
    { frequency: 1174.66, offset: 0.085, duration: 0.19, volume: 0.06, type: 'sine' },
    { frequency: 783.99, offset: 0.22, duration: 0.17, volume: 0.045, type: 'triangle' },
  ]);
}

function playMatchEndSound(outcome) {
  const notes =
    outcome === 'win'
      ? [
          { frequency: 523.25, offset: 0, duration: 0.3, volume: 0.07, type: 'sine' },
          { frequency: 659.25, offset: 0.14, duration: 0.36, volume: 0.07, type: 'sine' },
          { frequency: 783.99, offset: 0.28, duration: 0.45, volume: 0.07, type: 'triangle' },
          { frequency: 1046.5, offset: 0.44, duration: 0.75, volume: 0.055, type: 'sine' },
        ]
      : outcome === 'loss'
        ? [
            { frequency: 392, offset: 0, duration: 0.3, volume: 0.07, type: 'triangle' },
            { frequency: 329.63, offset: 0.2, duration: 0.35, volume: 0.065, type: 'sine' },
            { frequency: 261.63, offset: 0.42, duration: 0.62, volume: 0.055, type: 'sine' },
          ]
        : [
            { frequency: 440, offset: 0, duration: 0.38, volume: 0.06, type: 'sine' },
            { frequency: 554.37, offset: 0.18, duration: 0.48, volume: 0.055, type: 'triangle' },
            { frequency: 440, offset: 0.42, duration: 0.58, volume: 0.045, type: 'sine' },
          ];
  playCue(notes);
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

  if (
    clockState?.started &&
    clockState.runningColor &&
    remaining[clockState.runningColor] <= 0 &&
    performance.now() - lastTimeoutRefreshAt >= 1000 &&
    !timeoutRefreshPending
  ) {
    lastTimeoutRefreshAt = performance.now();
    timeoutRefreshPending = true;
    void refreshState()
      .catch((error) => {
        statusElement.textContent = error.message;
      })
      .finally(() => {
        timeoutRefreshPending = false;
      });
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
    ? `${currentState.maia_name || 'Maia'} is thinking… You can queue a premove.`
    : `${currentState.maia_name || 'Maia'} is thinking…`;
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
  await unlockMoveSounds();
  const submitButton = setupForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  statusElement.textContent = 'Finding a match…';
  try {
    const response = await fetch('/api/find-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
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
  const { moveKey } = promotionMove;
  const move = `${moveKey}${piece}`;
  const capture = promotionMove.capture;
  promotionMove = undefined;
  promotionDialog.close();
  const role = { q: 'queen', r: 'rook', b: 'bishop', n: 'knight' }[piece];
  ground.setPieces(
    new Map([
      [moveKey.slice(0, 2), undefined],
      [moveKey.slice(2), { role, color: currentState.player_color }],
    ]),
  );
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
moveFirstButton.addEventListener('click', () => selectReviewPly(0));
movePreviousButton.addEventListener('click', () => {
  selectReviewPly((reviewPly ?? currentState.ply) - 1);
});
moveNextButton.addEventListener('click', () => {
  selectReviewPly((reviewPly ?? currentState.ply) + 1);
});
moveLiveButton.addEventListener('click', () => selectReviewPly(currentState.ply));
historyElement.addEventListener('click', (event) => {
  const moveButton = event.target.closest('[data-ply]');
  if (moveButton) selectReviewPly(Number(moveButton.dataset.ply));
});
resignButton.addEventListener('click', () => {
  if (window.confirm('Resign this game?')) void sendCommand('resign');
});

copyPgnButton.addEventListener('click', async () => {
  try {
    const response = await fetch('/api/pgn');
    const data = await readJson(response);
    let copied = false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(data.pgn);
        copied = true;
      } catch {
        copied = false;
      }
    }

    if (!copied) {
      pgnFallback.value = data.pgn;
      pgnFallback.hidden = false;
      pgnFallback.focus();
      pgnFallback.select();
      if (document.execCommand) {
        try {
          copied = document.execCommand('copy');
        } catch {
          copied = false;
        }
      }
    }

    if (copied) {
      pgnFallback.hidden = true;
      statusElement.textContent = 'PGN copied to clipboard.';
    } else {
      statusElement.textContent =
        'Clipboard access is unavailable. The PGN is selected below; copy it manually.';
    }
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

refreshState().catch((error) => {
  statusElement.textContent = error.message;
});

setInterval(updateClockDisplay, 100);
