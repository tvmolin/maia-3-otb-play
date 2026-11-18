import React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ActionIcon,
  Anchor,
  Button,
  Checkbox,
  Group,
  MantineProvider,
  Modal,
  Paper,
  Select,
  Stack,
  Text,
  ThemeIcon,
  Title,
} from '@mantine/core';
import {
  IconChess,
  IconChevronLeft,
  IconChevronRight,
  IconChevronsLeft,
  IconClock,
  IconCopy,
  IconFlag,
  IconRefresh,
  IconSettings,
} from '@tabler/icons-react';
import { Chessground } from '@lichess-org/chessground';
import '@mantine/core/styles.css';
import '@lichess-org/chessground/assets/chessground.base.css';
import '@lichess-org/chessground/assets/chessground.brown.css';
import '@lichess-org/chessground/assets/chessground.cburnett.css';
import './style.css';
import defaultBoard from './board-texture.svg';
import woodBoard from './boards/wood.jpg';
import marbleBoard from './boards/marble.jpg';
import blueMarbleBoard from './boards/blue-marble.jpg';
import mosaicBoard from './boards/mosaic.png';

const initialFen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR';
const boardThemes = {
  classic: { label: 'Classic', image: `url("${defaultBoard}")` },
  wood: { label: 'Wood', image: `url("${woodBoard}")` },
  marble: { label: 'Marble', image: `url("${marbleBoard}")` },
  'blue-marble': { label: 'Blue marble', image: `url("${blueMarbleBoard}")` },
  mosaic: { label: 'Mosaic', image: `url("${mosaicBoard}")`, size: '25% 25%' },
  green: { label: 'Green', light: '#eeeed2', dark: '#769656' },
  blue: { label: 'Blue', light: '#dee3e6', dark: '#8ca2ad' },
  brown: { label: 'Brown', light: '#f0d9b5', dark: '#b58863' },
};
const pieceSets = {
  tatiana: 'Tatiana',
  fantasy: 'Fantasy',
  spatial: 'Spatial',
  celtic: 'Celtic',
  maestro: 'Maestro',
};
const pieceImages = import.meta.glob('./pieces/*/*.svg', { eager: true, query: '?url', import: 'default' });
const pieceRoles = { pawn: 'P', knight: 'N', bishop: 'B', rook: 'R', queen: 'Q', king: 'K' };
const appearanceKey = 'chessground-appearance';

function readAppearance() {
  try {
    const saved = JSON.parse(localStorage.getItem(appearanceKey) || '{}');
    return {
      board: Object.hasOwn(boardThemes, saved?.board) ? saved.board : 'classic',
      pieces: Object.hasOwn(pieceSets, saved?.pieces) ? saved.pieces : 'tatiana',
    };
  } catch (error) {
    console.warn('Could not read board appearance settings:', error);
    return { board: 'classic', pieces: 'tatiana' };
  }
}

function appearanceStyle(appearance) {
  const board = boardThemes[appearance.board];
  const style = board.image ? { '--board-image': board.image, '--board-size': board.size || '100% 100%' } : {
    '--board-image': `conic-gradient(${board.dark} 25%, ${board.light} 0 50%, ${board.dark} 0 75%, ${board.light} 0)`,
    '--board-size': '25% 25%',
  };
  for (const [color, prefix] of [['white', 'w'], ['black', 'b']]) {
    for (const [role, letter] of Object.entries(pieceRoles)) {
      style[`--piece-${color}-${role}`] = `url("${pieceImages[`./pieces/${appearance.pieces}/${prefix}${letter}.svg`]}")`;
    }
  }
  return style;
}

const materialValues = { p: 1, n: 3, b: 3, r: 5, q: 9 };
const pieceSymbols = {
  p: '♟',
  n: '♞',
  b: '♝',
  r: '♜',
  q: '♛',
  P: '♙',
  N: '♘',
  B: '♗',
  R: '♖',
  Q: '♕',
};
const pieceNames = { p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen' };

function destinationsToMap(destinations = {}) {
  return new Map(Object.entries(destinations).map(([origin, targets]) => [origin, targets]));
}

function getPlayerOutcome(state) {
  if (state.rating_outcome) return state.rating_outcome;
  if (!state.game_over || !state.result) return null;
  if (state.result === '1/2-1/2') return 'draw';
  const whiteWon = state.result === '1-0';
  return (state.player_color === 'white') === whiteWon ? 'win' : 'loss';
}

function getResultDetail(state, outcome) {
  if (state.termination === 'timeout') {
    if (outcome === 'draw') return 'Time expired, but neither side had enough material to win.';
    return outcome === 'win' ? 'Your opponent ran out of time.' : 'Your clock ran out.';
  }
  if (state.termination === 'resignation') {
    return outcome === 'loss' ? 'You resigned. There is always another game.' : 'The game ended by resignation.';
  }
  if (state.termination === 'checkmate') return outcome === 'win' ? 'Checkmate! A brilliant finish.' : 'Checkmate.';
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

function getMaterialDifference(fen) {
  return fen.split(' ')[0].split('').reduce((difference, piece) => {
    const value = materialValues[piece.toLowerCase()] || 0;
    return difference + (piece === piece.toUpperCase() ? value : -value);
  }, 0);
}

function formatClock(milliseconds) {
  if (milliseconds < 10_000) {
    const tenths = Math.ceil(Math.max(0, milliseconds) / 100);
    return `${Math.floor(tenths / 10)}.${tenths % 10}`;
  }
  const seconds = Math.ceil(Math.max(0, milliseconds) / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

const soundSources = {
  move: '/static/chessground/sounds/Move.mp3',
  capture: '/static/chessground/sounds/Capture.mp3',
  check: '/static/chessground/sounds/Check.mp3',
  checkmate: '/static/chessground/sounds/Checkmate.mp3',
  confirmation: '/static/chessground/sounds/Confirmation.mp3',
  victory: '/static/chessground/sounds/Victory.mp3',
  defeat: '/static/chessground/sounds/Defeat.mp3',
  draw: '/static/chessground/sounds/Draw.mp3',
};

function playSound(name) {
  const audio = new Audio(soundSources[name]);
  audio.volume = 0.8;
  audio.play().catch(() => {
    // Browsers can reject playback until the user interacts with the page.
  });
}

function playMoveSound(move) {
  const notation = move?.text || '';
  if (notation.includes('#')) playSound('checkmate');
  else if (notation.includes('+')) playSound('check');
  else playSound(notation.includes('x') ? 'capture' : 'move');
}

function useChessSounds(state) {
  const previousStateRef = useRef(null);
  useEffect(() => {
    const previousState = previousStateRef.current;
    previousStateRef.current = state;
    if (!state || !previousState) return;

    if (state.history?.length > previousState.history?.length) {
      const newMoves = state.history.slice(previousState.history.length);
      for (const [index, move] of newMoves.entries()) {
        const position = state.positions?.[previousState.history.length + index + 1];
        if (position?.check) {
          if (move.role === 'Engine' || move.text?.includes('#')) {
            playSound(move.text?.includes('#') ? 'checkmate' : 'check');
          }
        } else if (move.role === 'Engine') {
          playMoveSound(move);
        }
      }
    }

    if (!previousState.started && state.started) playSound('confirmation');

    if (!previousState.game_over && state.game_over) {
      const outcome = getPlayerOutcome(state);
      playSound(outcome === 'win' ? 'victory' : outcome === 'loss' ? 'defeat' : 'draw');
    }
  }, [state]);
}

function isCaptureMove(state, moveKey) {
  const destination = moveKey.slice(2, 4);
  const [board, , , enPassant] = state.fen.split(' ');
  const rows = board.split('/');
  let file = destination.charCodeAt(0) - 97;
  const rank = Number(destination[1]);
  let piece = null;
  for (const character of rows[8 - rank]) {
    if (/\d/.test(character)) {
      file -= Number(character);
    } else if (file-- === 0) {
      piece = character;
      break;
    }
  }
  if (piece) return true;
  return moveKey[0] !== moveKey[2] && enPassant === destination;
}

function useDisplayedClock(clock, started, onExpired, movePending) {
  const [now, setNow] = useState(() => performance.now());
  const expiredSampleRef = useRef(null);
  const clockKey = clock
    ? `${clock.remaining_ms.white}:${clock.remaining_ms.black}:${clock.running_color}:${started}`
    : 'stopped';
  const sample = useMemo(() => ({
    remaining: clock?.remaining_ms || null,
    sampledAt: performance.now(),
  }), [clockKey]);
  useEffect(() => {
    if (!started || !clock?.running_color) return undefined;
    const timer = window.setInterval(() => setNow(performance.now()), 100);
    return () => window.clearInterval(timer);
  }, [clock, started]);
  const getRemaining = (at) => {
    const remaining = !started || !clock
      ? { white: 0, black: 0 }
      : { ...sample.remaining };
    if (started && clock?.running_color) {
      remaining[clock.running_color] = Math.max(
        0,
        remaining[clock.running_color] - Math.max(0, at - sample.sampledAt),
      );
    }
    return remaining;
  };
  const remaining = getRemaining(now);
  useEffect(() => {
    const runningColor = clock?.running_color;
    if (!started || movePending || !runningColor || remaining[runningColor] > 0) return;
    const expirationKey = `${clockKey}:${runningColor}`;
    if (expiredSampleRef.current === expirationKey) return;
    expiredSampleRef.current = expirationKey;
    void onExpired();
  }, [clockKey, clock, now, onExpired, remaining, started, movePending]);
  return { remaining, getRemaining };
}

function CapturedPieces({ pieces = [] }) {
  const counts = new Map();
  for (const piece of pieces) {
    const type = piece.toLowerCase();
    if (pieceSymbols[piece]) counts.set(type, (counts.get(type) || 0) + 1);
  }
  const description = [...counts].map(([type, count]) => (
    `${count} ${pieceNames[type]}${count === 1 ? '' : 's'}`
  ));
  return (
    <span
      className="captured-pieces"
      aria-label={description.length ? `Captured: ${description.join(', ')}` : 'No pieces captured'}
    >
      {pieces.map((piece, index) => (
        pieceSymbols[piece] ? (
          <span
            className="captured-piece"
            data-color={piece === piece.toLowerCase() ? 'black' : 'white'}
            aria-hidden="true"
            key={`${piece}-${index}`}
          >
            {pieceSymbols[piece]}
          </span>
        ) : null
      ))}
    </span>
  );
}

function PlayerBar({ state, color, pieces, advantage, label, clock, clockTimes }) {
  const isPlayer = label === 'YOU';
  const milliseconds = clockTimes?.[color];
  return (
    <div className="board-player">
      <span className={`player-avatar ${isPlayer ? 'player-avatar-you' : 'opponent-avatar'}`} aria-hidden="true">
        {isPlayer ? 'Y' : 'M'}
      </span>
      <span className="board-player-name">{isPlayer ? state.player_name || 'You' : state.maia_name || 'Maia'}</span>
      <span className="board-player-rating">
        ({isPlayer ? state.player_elo : state.maia_elo})
      </span>
      <CapturedPieces pieces={pieces} />
      {advantage > 0 ? (
        <span className="material-advantage" aria-label={`${advantage} point material advantage`}>
          +{advantage}
        </span>
      ) : (
        <span className="material-advantage" aria-label="No material advantage" hidden />
      )}
      <div className="player-clock">
        <Clock color={color} clock={clock} milliseconds={milliseconds} />
      </div>
    </div>
  );
}

function ChessBoard({ state, reviewPly, requestPending, pendingMove, onMove, appearance }) {
  const elementRef = useRef(null);
  const groundRef = useRef(null);
  const moveRef = useRef(onMove);
  const previousPendingMoveRef = useRef(null);
  const fitBoardRef = useRef(() => {});
  moveRef.current = onMove;

  useEffect(() => {
    if (!elementRef.current) return undefined;
    groundRef.current = Chessground(elementRef.current, {
      fen: initialFen,
      orientation: 'white',
      autoCastle: true,
      viewOnly: false,
      premovable: { enabled: true },
      movable: {
        free: false,
        dests: new Map(),
        events: { after: (...args) => moveRef.current(...args) },
      },
    });
    const fitBoard = () => {
      const board = elementRef.current;
      const column = board?.closest('.board-column');
      const layout = column?.parentElement;
      if (!board || !column || !layout) return;
      const sidebar = layout.querySelector(':scope > .sidebar');
      const layoutRect = layout.getBoundingClientRect();
      const sidebarRect = sidebar?.getBoundingClientRect();
      const layoutGap = Number.parseFloat(getComputedStyle(layout).columnGap) || 0;
      const sidebarBeside = Boolean(
        sidebarRect && sidebarRect.width > 0 && sidebarRect.left > layoutRect.left + 160,
      );
      const availableWidth = sidebarBeside
        ? sidebarRect.left - layoutRect.left - layoutGap
        : layoutRect.width;
      const bars = [...column.querySelectorAll('.board-player')];
      const rowGap = Number.parseFloat(getComputedStyle(column).rowGap) || 0;
      const bottomPadding = Number.parseFloat(getComputedStyle(document.body).paddingBottom) || 0;
      const availableHeight = window.innerHeight
        - column.getBoundingClientRect().top
        - bottomPadding
        - bars.reduce((sum, bar) => sum + bar.getBoundingClientRect().height, 0)
        - rowGap * bars.length;
      const available = Math.min(availableWidth, availableHeight);
      const pixelRatio = window.devicePixelRatio || 1;
      const size = (Math.floor((available * pixelRatio) / 8) * 8) / pixelRatio;
      if (size > 0) {
        column.style.width = `${size}px`;
        board.style.width = `${size}px`;
        board.style.height = `${size}px`;
        groundRef.current?.redrawAll();
      }
    };
    fitBoardRef.current = fitBoard;
    fitBoard();
    const layoutElement = elementRef.current?.closest('.game-layout');
    const observer = 'ResizeObserver' in window
      ? new ResizeObserver(fitBoard)
      : null;
    if (observer && layoutElement) observer.observe(layoutElement);
    window.addEventListener('resize', fitBoard);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', fitBoard);
      groundRef.current?.destroy();
      groundRef.current = null;
    };
  }, []);

  useEffect(() => {
    fitBoardRef.current();
  }, [state?.started]);

  useEffect(() => {
    if (!groundRef.current || !state) return;
    const activePly = reviewPly ?? state.ply;
    const position = state.positions?.[activePly] || {
      fen: state.fen,
      captured_pieces: state.captured_pieces,
      turn_color: state.turn_color,
      last_move: state.last_move,
      check: state.check,
      check_square: state.check_square,
    };
    const reviewing = activePly !== state.ply;
    const canMove = Boolean(state.can_move) && !reviewing;
    if (requestPending && pendingMove?.basePly === state.ply) {
      previousPendingMoveRef.current = pendingMove;
      groundRef.current.set({
        turnColor: state.player_color === 'white' ? 'black' : 'white',
        check: pendingMove.givesCheck
          ? (state.player_color === 'white' ? 'black' : 'white')
          : false,
        movable: {
          color: state.player_color,
          dests: new Map(),
          events: { after: (...args) => moveRef.current(...args) },
        },
        premovable: { enabled: true },
      });
      return;
    }
    const completedMove = previousPendingMoveRef.current;
    groundRef.current.set({
      fen: position.fen,
      orientation: state.player_color || 'white',
      turnColor: position.turn_color,
      lastMove: position.last_move || undefined,
      check: position.check ? position.turn_color : false,
      movable: {
        color: canMove ? state.player_color : undefined,
        dests: destinationsToMap(canMove ? state.destinations : {}),
        events: { after: (...args) => moveRef.current(...args) },
      },
      premovable: { enabled: state.started && !state.game_over && !reviewing },
    });
    if (completedMove && !pendingMove) {
      if (state.ply > completedMove.basePly) groundRef.current.playPremove();
      else if (!requestPending) groundRef.current.cancelPremove();
    }
    previousPendingMoveRef.current = pendingMove;
  }, [state, reviewPly, requestPending, pendingMove]);

  return (
    <div className="board-slot">
      <div ref={elementRef} className="cg-wrap" style={appearanceStyle(appearance)} />
    </div>
  );
}

function Clock({ color, clock, milliseconds }) {
  const active = clock?.running_color === color;
  return (
    <div aria-label={`${color[0].toUpperCase() + color.slice(1)} clock`} className={`clock-display clock-display-${color}${active ? ' active' : ''}${active && milliseconds < 10_000 ? ' expiring' : ''}`}>
      <time dateTime={clock ? `PT${(milliseconds / 1000).toFixed(3)}S` : undefined}>
        {clock ? formatClock(milliseconds) : '--:--'}
      </time>
    </div>
  );
}

function MoveHistory({ state, reviewPly, onSelect }) {
  if (!state?.history?.length) return null;
  return (
    <ol id="move-history" aria-label="Move history">
      {Array.from({ length: Math.ceil(state.history.length / 2) }, (_, rowIndex) => (
        <li className="move-pair" key={rowIndex}>
          <span className="move-number">{rowIndex + 1}.</span>
          {[0, 1].map((offset) => {
            const index = rowIndex * 2 + offset;
            const move = state.history[index];
            if (!move) return <span key={offset} />;
            const ply = index + 1;
            return (
              <button
                type="button"
                className="move-san"
                aria-label={`Review move ${rowIndex + 1}${offset ? '... ' : '. '}${move.text}`}
                aria-current={(reviewPly ?? state.ply) === ply ? 'true' : undefined}
                onClick={() => onSelect(ply)}
                key={offset}
              >
                {move.text}
              </button>
            );
          })}
        </li>
      ))}
    </ol>
  );
}

function optimisticUndoState(state) {
  const pliesToUndo = state.turn_color === state.player_color ? 2 : 1;
  const ply = Math.max(0, state.ply - pliesToUndo);
  const position = state.positions?.[ply];
  if (!position) return state;
  return {
    ...state,
    ...position,
    can_move: false,
    maia_thinking: true,
    game_over: false,
    result: null,
    termination: null,
    rating_change: null,
    rating_outcome: null,
    ply,
    history: state.history.slice(0, ply),
    positions: state.positions.slice(0, ply + 1),
  };
}

function optimisticResignationState(state) {
  return {
    ...state,
    can_move: false,
    maia_thinking: false,
    game_over: true,
    result: state.player_color === 'white' ? '0-1' : '1-0',
    termination: 'resignation',
    rating_change: null,
    rating_outcome: 'loss',
    clock: { ...state.clock, running_color: null },
  };
}

function optimisticNewGameState(state) {
  return {
    ...state,
    fen: initialFen,
    turn_color: 'white',
    player_color: null,
    started: false,
    can_move: false,
    maia_thinking: false,
    game_over: false,
    quit: false,
    result: null,
    termination: null,
    rating_change: null,
    rating_outcome: null,
    history: [],
    ply: 0,
    positions: [],
    captured_pieces: { white: [], black: [] },
    destinations: {},
    promotions: {},
    checking_moves: [],
  };
}

function App() {
  const [state, setState] = useState(null);
  const [appearance, setAppearance] = useState(readAppearance);
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const [appearanceError, setAppearanceError] = useState('');
  const stateRef = useRef(null);
  const [reviewPly, setReviewPly] = useState(null);
  const [requestPending, setRequestPending] = useState(false);
  const [statusOverride, setStatusOverride] = useState('');
  const [timeControl, setTimeControl] = useState('3+2');
  const [playerColor, setPlayerColor] = useState('random');
  const [promotionMove, setPromotionMove] = useState(null);
  const [pendingMove, setPendingMove] = useState(null);
  const requestPendingRef = useRef(false);
  useChessSounds(state);

  const changeAppearance = (field, value) => {
    const next = { ...appearance, [field]: value };
    setAppearance(next);
    try {
      localStorage.setItem(appearanceKey, JSON.stringify(next));
      setAppearanceError('');
    } catch (error) {
      console.warn('Could not save board appearance settings:', error);
      setAppearanceError('Appearance changed, but could not be saved in this browser.');
    }
  };

  const updateState = useCallback((nextState) => {
    stateRef.current = nextState;
    setState(nextState);
    if (nextState.time_control) setTimeControl(nextState.time_control);
    setStatusOverride('');
  }, []);

  const readJson = useCallback(async (response) => {
    const data = await response.json();
    if (!response.ok) {
      if (data.state) updateState(data.state);
      throw new Error(data.error || 'The request failed.');
    }
    return data;
  }, [updateState]);

  const refreshState = useCallback(async () => {
    updateState(await readJson(await fetch('/api/state')));
  }, [readJson, updateState]);

  const clock = state?.started ? pendingMove?.clock || state.clock : null;
  const { remaining: clockTimes, getRemaining } = useDisplayedClock(
    clock, Boolean(state?.started), refreshState, Boolean(pendingMove),
  );

  useEffect(() => {
    refreshState().catch((error) => setStatusOverride(error.message));
  }, [refreshState]);

  const activePly = reviewPly ?? state?.ply ?? 0;
  const reviewing = Boolean(state && activePly !== state.ply);
  const position = state?.positions?.[activePly] || state;
  const captured = position?.captured_pieces || state?.captured_pieces || { white: [], black: [] };
  const playerColorValue = state?.player_color || 'white';
  const opponentColor = playerColorValue === 'white' ? 'black' : 'white';
  const difference = position ? getMaterialDifference(position.fen) : 0;
  const playerAdvantage = Math.max(0, playerColorValue === 'white' ? difference : -difference);
  const opponentAdvantage = Math.max(0, -(
    playerColorValue === 'white' ? difference : -difference
  ));
  const outcome = state ? getPlayerOutcome(state) : null;

  const setPending = useCallback((pending) => {
    requestPendingRef.current = pending;
    setRequestPending(pending);
  }, []);

  const sendMove = useCallback(async (move) => {
    const current = stateRef.current;
    if (!current) return;
    setPending(true);
    try {
      const response = await fetch('/api/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ move, expected_ply: current.ply }),
      });
      const data = await readJson(response);
      updateState(data.state);
      setPendingMove(null);
    } catch (error) {
      setStatusOverride(error.message);
      await refreshState().catch((refreshError) => setStatusOverride(refreshError.message));
      setPendingMove(null);
    } finally {
      setPending(false);
    }
  }, [readJson, refreshState, setPending, updateState]);

  const startPendingMove = (current, moveKey) => {
    const remaining = getRemaining(performance.now());
    remaining[current.player_color] += current.clock.increment_seconds * 1000;
    const givesCheck = current.checking_moves?.includes(moveKey);
    setPendingMove({
      basePly: current.ply,
      givesCheck,
      clock: {
        ...current.clock,
        remaining_ms: remaining,
        running_color: current.player_color === 'white' ? 'black' : 'white',
      },
    });
  };

  const onBoardMove = useCallback((origin, destination) => {
    const current = stateRef.current;
    if (!current || !current.can_move || requestPendingRef.current) {
      void refreshState();
      return;
    }
    const moveKey = `${origin}${destination}`;
    const promotions = current.promotions[moveKey];
    if (promotions?.length) {
      setPromotionMove({ moveKey, promotions });
      return;
    }
    const givesCheck = current.checking_moves?.includes(moveKey);
    playSound(givesCheck ? 'check' : (isCaptureMove(current, moveKey) ? 'capture' : 'move'));
    startPendingMove(current, moveKey);
    void sendMove(moveKey);
  }, [refreshState, sendMove, startPendingMove]);

  const sendCommand = useCallback(async (command) => {
    const current = stateRef.current;
    if (current && command === 'undo') {
      updateState(optimisticUndoState(current));
      setStatusOverride('Undoing…');
    } else if (current && command === 'resign') {
      updateState(optimisticResignationState(current));
      setStatusOverride('Resigning…');
    }
    setPending(true);
    try {
      const response = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command }),
      });
      const data = await readJson(response);
      updateState(data.state);
    } catch (error) {
      setStatusOverride(error.message);
      await refreshState().catch((refreshError) => setStatusOverride(refreshError.message));
    } finally {
      setPending(false);
    }
  }, [readJson, refreshState, setPending, updateState]);

  const findMatch = async (event) => {
    event.preventDefault();
    setPending(true);
    setStatusOverride('Finding a match…');
    try {
      const response = await fetch('/api/find-match', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ time_control: timeControl, color: playerColor }),
      });
      updateState((await readJson(response)).state);
    } catch (error) {
      setStatusOverride(error.message);
      await refreshState().catch((refreshError) => setStatusOverride(refreshError.message));
    } finally {
      setPending(false);
    }
  };

  const newGame = async () => {
    if (stateRef.current) {
      updateState(optimisticNewGameState(stateRef.current));
      setStatusOverride('Returning to matchmaking…');
    }
    setPending(true);
    try {
      updateState((await readJson(await fetch('/api/new-game', { method: 'POST' }))).state);
      setReviewPly(null);
    } catch (error) {
      setStatusOverride(error.message);
    } finally {
      setPending(false);
    }
  };

  const toggleInstant = async (event) => {
    const enabled = event.currentTarget.checked;
    updateState({ ...stateRef.current, play_instantly: enabled });
    try {
      const response = await fetch('/api/play-instantly', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not update Maia move timing.');
      updateState({ ...stateRef.current, play_instantly: data.play_instantly });
    } catch (error) {
      setStatusOverride(error.message);
      await refreshState().catch((refreshError) => setStatusOverride(refreshError.message));
    }
  };

  const copyPgn = async () => {
    try {
      const data = await readJson(await fetch('/api/pgn'));
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
        setStatusOverride('Clipboard is unavailable. PGN: ' + data.pgn);
      } else {
        setStatusOverride('PGN copied to clipboard.');
      }
    } catch (error) {
      setStatusOverride(error.message);
    }
  };

  const statusText = useMemo(() => {
    if (statusOverride) return statusOverride;
    if (reviewing) return `Reviewing move ${activePly} of ${state?.ply}.`;
    if (state?.game_over) return `Final score: ${state.result || '—'}`;
    if (!state?.started) return 'Choose a time control and find a match.';
    if (state.maia_thinking || requestPending) return `${state.maia_name || 'Maia'} is thinking…`;
    const summary = `You (${state.player_elo}) are ${state.player_color} vs ${state.maia_name || 'Maia'} (${state.maia_elo}). `;
    return state.can_move ? `${summary}Your move.` : `${summary}Waiting for Maia.`;
  }, [activePly, requestPending, reviewing, state, statusOverride]);

  const selectReviewPly = (ply) => {
    if (!state) return;
    setReviewPly(ply >= state.ply ? null : Math.max(0, ply));
  };

  const canGoBack = Boolean(state?.started && activePly > 0);
  const canGoForward = Boolean(state?.started && activePly < (state?.ply || 0));
  const canGoLive = Boolean(state?.started && activePly < (state?.ply || 0));

  return (
    <MantineProvider defaultColorScheme="dark" theme={{
      primaryColor: 'lime',
      colors: { lime: ['#f2ffe1', '#e7ffc0', '#d3f58b', '#b6e36a', '#9ac954', '#7da53e', '#5f7c2d', '#40551e', '#293815', '#151e0a'] },
      fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
    }}>
      <main>
        <div className="app-container">
          <aside className="app-sidebar">
            <Anchor className="brand-lockup" href="/" aria-label="Home">
              <span className="brand-mark" aria-hidden="true">♞</span>
              <span>
                <span className="brand-name">PERSONAL CHESS LADDER</span>
                <span className="brand-tagline">Your next great game starts here</span>
              </span>
            </Anchor>
            <Anchor className="header-link" href="/profile" title="Profile">
              <IconClock size={16} aria-hidden="true" /> Profile
            </Anchor>
            <ActionIcon className="header-link" aria-label="Board settings" title="Board settings" onClick={() => setAppearanceOpen(true)}>
              <IconSettings size={19} aria-hidden="true" />
            </ActionIcon>
          </aside>
          <div className="game-layout">
          <section className="board-column" aria-label="Chess board">
            {state?.started && (
              <PlayerBar state={state} color={opponentColor} pieces={captured[opponentColor]} advantage={opponentAdvantage} label="OPPONENT" clock={clock} clockTimes={clockTimes} />
            )}
            <ChessBoard
              state={state}
              reviewPly={reviewPly}
              requestPending={requestPending}
              pendingMove={pendingMove}
              onMove={onBoardMove}
              appearance={appearance}
            />
            {state?.started && (
              <PlayerBar state={state} color={playerColorValue} pieces={captured[playerColorValue]} advantage={playerAdvantage} label="YOU" clock={clock} clockTimes={clockTimes} />
            )}
          </section>
          <aside className="sidebar">
            {!state?.started && (
              <Paper component="form" id="setup" className="panel" onSubmit={findMatch}>
                <div className="setup-heading">
                  <span className="eyebrow">READY WHEN YOU ARE</span>
                  <Title order={2}>Find your next game</Title>
                  <Text>Pick a time control and take your place at the board.</Text>
                </div>
                <Select
                  label="Time control"
                  value={timeControl}
                  onChange={setTimeControl}
                  data={['3+2', '5+5', '15+10', '20+10']}
                  allowDeselect={false}
                  disabled={requestPending}
                />
                <Select
                  label="Piece color"
                  value={playerColor}
                  onChange={setPlayerColor}
                  data={[
                    { value: 'random', label: 'Random' },
                    { value: 'white', label: 'White' },
                    { value: 'black', label: 'Black' },
                  ]}
                  allowDeselect={false}
                  disabled={requestPending}
                />
                <Button type="submit" leftSection={<IconChess size={17} />} loading={requestPending}>
                  Find match
                </Button>
              </Paper>
            )}
            {state?.started && (
              <>
                <Paper className="panel">
                  {state.game_over && (
                    <div className="result-summary" data-outcome={outcome || 'draw'} aria-live="polite">
                      <div className="result-icon" aria-hidden="true">{outcome === 'win' ? '♔' : outcome === 'loss' ? '♟' : '½'}</div>
                      <div className="result-copy">
                        <span className="result-category">{state.rating_category || ''}</span>
                        <Title order={2}>{outcome === 'win' ? 'Victory!' : outcome === 'loss' ? 'Defeat' : 'Draw'}</Title>
                        <Text>{getResultDetail(state, outcome)}</Text>
                      </div>
                      <div className="result-rating">
                        {state.rating_change === null ? `Rating ${state.player_elo}` : `${state.rating_change > 0 ? '+' : ''}${state.rating_change} rating · ${state.player_elo}`}
                      </div>
                    </div>
                  )}
                  <div id="status" role="status" aria-live="polite">{statusText}</div>
                  {!state.game_over && (
                    <div className="game-checkbox-wrapper">
                      <Checkbox
                        label="Play instantly"
                        checked={Boolean(state.play_instantly)}
                        onChange={toggleInstant}
                      />
                    </div>
                  )}
                  <Group className="actions">
                    <Button onClick={newGame} disabled={requestPending || state.maia_thinking}>
                      {state.game_over ? 'Play again' : 'End match'}
                    </Button>
                    <Button variant="light" onClick={() => sendCommand('undo')} disabled={state.game_over || requestPending || state.maia_thinking || reviewing} leftSection={<IconRefresh size={16} />}>
                      Undo
                    </Button>
                    <Button className="resign-button" color="red" variant="light" onClick={() => window.confirm('Resign this game?') && sendCommand('resign')} disabled={state.game_over || requestPending || state.maia_thinking}>
                      <span className="button-icon" aria-hidden="true"><IconFlag size={16} /></span>
                      Resign
                    </Button>
                  </Group>
                </Paper>
                <Paper className="panel" id="moves-panel">
                  <Title order={2}>Moves</Title>
                  <nav className="move-navigation" aria-label="Navigate moves">
                    <ActionIcon aria-label="Go to starting position" onClick={() => selectReviewPly(0)} disabled={!canGoBack}><IconChevronsLeft size={18} /></ActionIcon>
                    <ActionIcon aria-label="Previous move" onClick={() => selectReviewPly(activePly - 1)} disabled={!canGoBack}><IconChevronLeft size={18} /></ActionIcon>
                    <ActionIcon aria-label="Next move" onClick={() => selectReviewPly(activePly + 1)} disabled={!canGoForward}><IconChevronRight size={18} /></ActionIcon>
                    <Button id="move-live" aria-label="Return to current position" onClick={() => selectReviewPly(state.ply)} disabled={!canGoLive}>Live</Button>
                  </nav>
                  <MoveHistory state={state} reviewPly={reviewPly} onSelect={selectReviewPly} />
                  {state.game_over && (
                    <Button id="copy-pgn" onClick={copyPgn} leftSection={<IconCopy size={16} />}>Copy PGN</Button>
                  )}
                </Paper>
              </>
            )}
          </aside>
        </div>
        <Modal opened={appearanceOpen} onClose={() => setAppearanceOpen(false)} title="Board settings" centered>
          <Stack>
            <Text size="sm" c="dimmed">Changes are applied to the board immediately and saved in this browser.</Text>
            {appearanceError && <Text role="alert" size="sm" c="red">{appearanceError}</Text>}
            <Select
              label="Board"
              value={appearance.board}
              onChange={(value) => changeAppearance('board', value)}
              data={Object.entries(boardThemes).map(([value, theme]) => ({ value, label: theme.label }))}
              allowDeselect={false}
            />
            <Select
              label="Piece set"
              value={appearance.pieces}
              onChange={(value) => changeAppearance('pieces', value)}
              data={Object.entries(pieceSets).map(([value, label]) => ({ value, label }))}
              allowDeselect={false}
            />
            {appearance.pieces === 'maestro' && (
              <Text size="xs" c="dimmed">
                Maestro by sadsnake1, via <Anchor href="https://github.com/lichess-org/lila/tree/master/public/piece/maestro" target="_blank" rel="noreferrer">Lichess</Anchor>.
                {' '}<Anchor href="https://creativecommons.org/licenses/by-nc-sa/4.0/" target="_blank" rel="noreferrer">CC BY-NC-SA 4.0</Anchor> (noncommercial).
              </Text>
            )}
          </Stack>
        </Modal>
        <Modal
          opened={Boolean(promotionMove)}
          onClose={() => {
            setPromotionMove(null);
            void refreshState();
          }}
          title="Promote pawn"
          centered
        >
          <Group className="promotion-options">
            {[
              ['q', 'queen', '♕'],
              ['r', 'rook', '♖'],
              ['b', 'bishop', '♗'],
              ['n', 'knight', '♘'],
            ].filter(([piece]) => promotionMove?.promotions.includes(piece)).map(([piece, name, symbol]) => (
              <Button
                key={piece}
                aria-label={`Promote to ${name}`}
                onClick={() => {
                  const move = `${promotionMove.moveKey}${piece}`;
                  setPromotionMove(null);
                  const givesCheck = state.checking_moves?.includes(move);
                  playSound(givesCheck ? 'check' : (isCaptureMove(state, move) ? 'capture' : 'move'));
                  startPendingMove(state, move);
                  void sendMove(move);
                }}
              >
                {symbol}
              </Button>
            ))}
          </Group>
        </Modal>
        </div>
      </main>
    </MantineProvider>
  );
}

createRoot(document.getElementById('root')).render(<App />);
