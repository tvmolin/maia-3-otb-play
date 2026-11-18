import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import {
  expectPieceAt,
  chooseOption,
  findMatch,
  movePiece,
  moveButton,
  resetServer,
  resign,
  startScenario,
} from './helpers.js';

const status = (page) => page.getByRole('status');

test.describe('board appearance', () => {
  test('changes every board and piece set, persists choices, and keeps the game playable', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e5'] });
    const board = page.locator('cg-board');
    const image = (locator) => locator.evaluate((element) => getComputedStyle(element).backgroundImage);
    const defaultBoard = await image(board);
    const defaultPiece = await image(page.locator('piece.white.knight').first());

    await page.getByRole('button', { name: 'Board settings' }).click();
    const dialog = page.getByRole('dialog', { name: 'Board settings' });
    for (const name of ['Wood', 'Marble', 'Blue marble', 'Green', 'Blue', 'Brown']) {
      await chooseOption(page, 'Board', name);
      expect(await image(board)).not.toBe(defaultBoard);
    }
    for (const name of ['Fantasy', 'Spatial', 'Celtic', 'Maestro']) {
      await chooseOption(page, 'Piece set', name);
      expect(await image(page.locator('piece.white.knight').first())).not.toBe(defaultPiece);
      const loaded = await page.locator('.cg-wrap').evaluate(async (wrap) => {
        const style = getComputedStyle(wrap);
        const urls = ['white', 'black'].flatMap((color) =>
          ['pawn', 'knight', 'bishop', 'rook', 'queen', 'king'].map((role) =>
            style.getPropertyValue(`--piece-${color}-${role}`).trim().slice(5, -2)));
        return Promise.all(urls.map(async (url) => {
          const response = await fetch(url);
          return response.ok ? response.text() : '';
        }));
      });
      expect(loaded.every((svg) => svg.includes('<svg'))).toBe(true);
      if (name === 'Maestro') {
        const originals = await Promise.all(['w', 'b'].flatMap((color) =>
          ['P', 'N', 'B', 'R', 'Q', 'K'].map((role) =>
            readFile(new URL(`../pieces/maestro/${color}${role}.svg`, import.meta.url), 'utf8'))));
        const matches = await page.evaluate(async ({ actual, expected }) => {
          const render = async (svg) => {
            const image = new Image();
            image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
            await image.decode();
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 100;
            const context = canvas.getContext('2d');
            context.drawImage(image, 0, 0, 100, 100);
            return context.getImageData(0, 0, 100, 100).data;
          };
          return Promise.all(actual.map(async (svg, index) => {
            const rendered = await render(svg);
            const original = await render(expected[index]);
            return rendered.every((value, pixel) => value === original[pixel]);
          }));
        }, { actual: loaded, expected: originals });
        expect(matches).toEqual(Array(12).fill(true));
      }
    }
    await chooseOption(page, 'Board', 'Wood');
    const selectedBoard = await image(board);
    const selectedPiece = await image(page.locator('piece.white.knight').first());
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await page.reload();
    await expectPieceAt(page, 'e4', 'P');
    expect(await image(board)).toBe(selectedBoard);
    expect(await image(page.locator('piece.white.knight').first())).toBe(selectedPiece);
    await page.getByRole('button', { name: 'Board settings' }).click();
    await expect(page.getByRole('textbox', { name: 'Board', exact: true })).toHaveValue('Wood');
    await expect(page.getByRole('textbox', { name: 'Piece set' })).toHaveValue('Maestro');
    await expect(dialog.getByText('Maestro by sadsnake1', { exact: false })).toBeVisible();
  });

  test('falls back to the defaults for invalid saved choices', async ({ page, request }) => {
    await resetServer(request);
    await page.addInitScript(() => {
      localStorage.setItem('chessground-appearance', JSON.stringify({ board: 'invalid', pieces: 'invalid' }));
    });
    await page.goto('/');
    await page.getByRole('button', { name: 'Board settings' }).click();
    await expect(page.getByRole('textbox', { name: 'Board', exact: true })).toHaveValue('Classic');
    await expect(page.getByRole('textbox', { name: 'Piece set' })).toHaveValue('Tatiana');
  });

  test('keeps board settings accessible on a narrow screen', async ({ page, request }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    await resetServer(request);
    await page.goto('/');
    const settings = page.getByRole('button', { name: 'Board settings' });
    const box = await settings.boundingBox();
    expect(box.x + box.width).toBeLessThanOrEqual(320);
    await settings.click();
    await chooseOption(page, 'Board', 'Green');
    await expect(page.locator('cg-board')).toHaveCSS('background-size', '25% 25%');
  });

  test('tiles the mosaic texture across the board and restores it after reload', async ({ page, request }) => {
    await resetServer(request);
    await page.goto('/');
    await page.getByRole('button', { name: 'Board settings' }).click();
    await chooseOption(page, 'Board', 'Mosaic');
    await page.keyboard.press('Escape');
    await page.reload();
    const board = page.locator('cg-board');
    await expect(board).toHaveCSS('background-size', '25% 25%');
    await page.addStyleTag({ content: 'cg-board piece, cg-board square, .cg-wrap coords { visibility: hidden !important; }' });
    const screenshot = await board.screenshot();
    const brightness = await page.evaluate(async (data) => {
      const image = new Image();
      image.src = `data:image/png;base64,${data}`;
      await image.decode();
      const canvas = document.createElement('canvas');
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext('2d');
      context.drawImage(image, 0, 0);
      return Array.from({ length: 64 }, (_, index) => {
        const x = Math.floor(((index % 8) + 0.5) * image.width / 8);
        const y = Math.floor((Math.floor(index / 8) + 0.5) * image.height / 8);
        const pixel = context.getImageData(x, y, 1, 1).data;
        return (pixel[0] + pixel[1] + pixel[2]) / 3;
      });
    }, screenshot.toString('base64'));
    for (const [index, value] of brightness.entries()) {
      if ((index % 8 + Math.floor(index / 8)) % 2 === 0) expect(value).toBeGreaterThan(130);
      else expect(value).toBeLessThan(110);
    }
    await page.getByRole('button', { name: 'Board settings' }).click();
    await expect(page.getByRole('textbox', { name: 'Board', exact: true })).toHaveValue('Mosaic');
    await chooseOption(page, 'Board', 'Wood');
    await expect(board).toHaveCSS('background-size', '100% 100%');
  });

  test('solid-color boards have a light square at the bottom right', async ({ page, request }) => {
    await resetServer(request);
    await page.goto('/');
    await page.addStyleTag({ content: 'cg-board piece, cg-board square, .cg-wrap coords { visibility: hidden !important; }' });
    for (const [name, light, dark] of [
      ['Green', [238, 238, 210], [118, 150, 86]],
      ['Blue', [222, 227, 230], [140, 162, 173]],
      ['Brown', [240, 217, 181], [181, 136, 99]],
    ]) {
      await page.getByRole('button', { name: 'Board settings' }).click();
      await chooseOption(page, 'Board', name);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog', { name: 'Board settings' })).toBeHidden();
      const screenshot = await page.locator('cg-board').screenshot();
      const colors = await page.evaluate(async (data) => {
        const image = new Image();
        image.src = `data:image/png;base64,${data}`;
        await image.decode();
        const canvas = document.createElement('canvas');
        canvas.width = image.width;
        canvas.height = image.height;
        const context = canvas.getContext('2d');
        context.drawImage(image, 0, 0);
        return [[1, 1], [15, 1], [1, 15], [15, 15]].map(([x, y]) =>
          Array.from(context.getImageData(Math.floor(x * image.width / 16), Math.floor(y * image.height / 16), 1, 1).data).slice(0, 3));
      }, screenshot.toString('base64'));
      expect(colors, name).toEqual([light, dark, dark, light]);
    }
  });
});

test.describe('starting a match', () => {
  test('offers the supported matchmaking settings', async ({ page, request }) => {
    await resetServer(request);
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Find match' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Resign' })).toBeHidden();
    await expect(page.getByRole('textbox', { name: 'Piece color' })).toHaveValue('Random');
    for (const control of ['3+2', '5+5', '15+10', '20+10']) {
      await chooseOption(page, 'Time control', control);
      await expect(page.getByRole('textbox', { name: 'Time control' })).toHaveValue(control);
    }
    for (const color of ['White', 'Black', 'Random']) {
      await chooseOption(page, 'Piece color', color);
      await expect(page.getByRole('textbox', { name: 'Piece color' })).toHaveValue(color);
    }
  });

  test('plays moves as white against Maia and shows the game panels', async ({ page, request }) => {
    await resetServer(request, { engine_moves: ['e5', 'Nc6'] });
    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Find match' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Resign' })).toBeHidden();

    await findMatch(page, { color: 'white' });
    await expect(status(page)).toContainText('Your move.');
    await expect(page.getByLabel('White clock')).toBeVisible();
    await expect(page.getByLabel('Black clock')).toBeVisible();
    await expectPieceAt(page, 'e2', 'P');

    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await expectPieceAt(page, 'e4', 'P');
    await expectPieceAt(page, 'e5', 'p');
    await expectPieceAt(page, 'e2', null);

    await movePiece(page, 'g1', 'f3');
    await expect(moveButton(page, '2... Nc6')).toBeVisible();
    await expect(moveButton(page, '2. Nf3')).toBeVisible();
    await expect(status(page)).toContainText('Your move.');
  });

  test('flips the board and waits for Maia to open when playing black', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e4', 'Nf3'] }, { color: 'black' });

    await expect(page.locator('.cg-wrap')).toHaveClass(/orientation-black/);
    await expect(moveButton(page, '1. e4')).toBeVisible();
    await expectPieceAt(page, 'e4', 'P');
    await expect(status(page)).toContainText('Your move.');

    await movePiece(page, 'e7', 'e5');
    await expect(moveButton(page, '2. Nf3')).toBeVisible();
    await expectPieceAt(page, 'e5', 'p');
    await expectPieceAt(page, 'f3', 'N');
  });

  test('Maia replies immediately when "Play instantly" is on', async ({ page, request }) => {
    await startScenario(page, request, { think_seconds: 5, engine_moves: ['e5'] });
    await page.getByLabel('Play instantly').check();

    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... e5')).toBeVisible({ timeout: 2000 });
  });

  test('keeps a moved piece visible while the server responds', async ({ page, request }) => {
    const sounds = [];
    page.on('request', (requestEvent) => {
      if (requestEvent.url().includes('/sounds/')) sounds.push(requestEvent.url());
    });
    await startScenario(page, request, { think_seconds: 5, engine_moves: ['e5'] });
    await page.route('**/api/move', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await route.continue();
    });

    await movePiece(page, 'e2', 'e4');
    await expectPieceAt(page, 'e4', 'P');
    await expectPieceAt(page, 'e2', null);
    await expect.poll(() => sounds.some((url) => url.endsWith('/Move.mp3'))).toBeTruthy();
  });

  test('keeps board and clock responsive until a move request completes', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e5'] });
    let releaseMove;
    const gate = new Promise((resolve) => { releaseMove = resolve; });
    await page.route('**/api/move', async (route) => {
      await gate;
      await route.continue();
    }, { times: 1 });
    const moveRequest = page.waitForRequest('**/api/move');

    try {
      await movePiece(page, 'e2', 'e4');
      await moveRequest;
      await expectPieceAt(page, 'e4', 'P');
      await expectPieceAt(page, 'e2', null);
      await expect(page.getByLabel('Black clock')).toHaveClass(/active/);
      await expect(page.getByLabel('White clock')).not.toHaveClass(/active/);
      await expect(status(page)).toContainText('thinking');
    } finally {
      releaseMove();
    }

    await expect(moveButton(page, '1. e4')).toBeVisible();
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await expectPieceAt(page, 'e5', 'p');
    await expect(page.getByLabel('White clock')).toHaveClass(/active/);
    await expect(status(page)).toContainText('Your move.');
  });

  for (const color of ['white', 'black']) {
    test(`switches clocks while Maia thinks when playing ${color}`, async ({ page, request }) => {
      await startScenario(
        page,
        request,
        { think_seconds: 4, engine_moves: color === 'white' ? ['e5'] : ['e4', 'Nf3'] },
        { color },
      );
      const opponent = color === 'white' ? 'black' : 'white';
      const playerClock = page.getByLabel(`${color === 'white' ? 'White' : 'Black'} clock`);
      const opponentClock = page.getByLabel(`${opponent === 'white' ? 'White' : 'Black'} clock`);
      const seconds = async (clock) => {
        const duration = await clock.locator('time').getAttribute('datetime');
        return Number(duration.slice(2, -1));
      };

      const beforeMove = await seconds(playerClock);
      await movePiece(page, color === 'white' ? 'e2' : 'e7', color === 'white' ? 'e4' : 'e5');
      await expect(opponentClock).toHaveClass(/active/);
      await expect(playerClock).not.toHaveClass(/active/);
      const playerStart = await seconds(playerClock);
      expect(playerStart - beforeMove).toBeGreaterThan(1.3);
      const opponentStart = await seconds(opponentClock);
      await expect.poll(async () => opponentStart - await seconds(opponentClock)).toBeGreaterThan(1);
      expect(Math.abs(await seconds(playerClock) - playerStart)).toBeLessThan(0.15);
      await expect(playerClock).toHaveClass(/active/, { timeout: 7000 });
      await expect(opponentClock).not.toHaveClass(/active/);
    });
  }

  test('queues and plays a premove while Maia responds', async ({ page, request }) => {
    await startScenario(page, request, {
      think_seconds: 1,
      engine_moves: ['e5', 'Nc6'],
    });
    await page.route('**/api/move', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.continue();
    });

    await movePiece(page, 'e2', 'e4');
    await movePiece(page, 'g1', 'f3');

    await expectPieceAt(page, 'e4', 'P');
    await expectPieceAt(page, 'e5', 'p');
    await expectPieceAt(page, 'f3', 'N');
    await expect(page.getByRole('button', { name: 'Review move 2. Nf3' })).toBeVisible();
  });

  test('queues a premove after the player move has been acknowledged', async ({ page, request }) => {
    await startScenario(page, request, {
      block_engine: true,
      engine_moves: ['e5', 'Nc6'],
    });
    try {
      await movePiece(page, 'e2', 'e4');
      await expect(moveButton(page, '1. e4')).toBeVisible();
      await movePiece(page, 'g1', 'f3');
    } finally {
      await request.post('/__e2e__/release-engine');
    }
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await expect(moveButton(page, '2. Nf3')).toBeVisible();
    await expectPieceAt(page, 'f3', 'N');
  });

  test('shows an accepted Black move before Maia responds', async ({ page, request }) => {
    await startScenario(page, request, {
      block_engine: true,
      block_engine_after: 1,
      engine_moves: ['e4', 'Nf3'],
    }, { color: 'black' });
    try {
      await movePiece(page, 'e7', 'e5');
      await expect(moveButton(page, '1... e5')).toBeVisible();
      await expectPieceAt(page, 'e5', 'p');
      await expect(page.getByLabel('White clock')).toHaveClass(/active/);
      await expect(moveButton(page, '2. Nf3')).toHaveCount(0);
    } finally {
      await request.post('/__e2e__/release-engine');
    }
    await expect(moveButton(page, '2. Nf3')).toBeVisible();
  });
});

test.describe('moves and board feedback', () => {
  test('offers promotion choices, can cancel, then promotes', async ({ page, request }) => {
    const sounds = [];
    page.on('request', (requestEvent) => {
      if (requestEvent.url().includes('/sounds/')) sounds.push(requestEvent.url());
    });
    await startScenario(page, request, {
      fen: '7k/P7/8/8/8/8/8/7K w - - 0 1',
      engine_moves: ['Kg7'],
    });
    const dialog = page.getByRole('dialog');

    await movePiece(page, 'a7', 'a8');
    await expect(dialog).toBeVisible();
    for (const piece of ['queen', 'rook', 'bishop', 'knight']) {
      await expect(dialog.getByRole('button', { name: `Promote to ${piece}` })).toBeVisible();
    }
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expectPieceAt(page, 'a7', 'P');
    await expect(moveButton(page, '1. a8=Q+')).toHaveCount(0);

    await movePiece(page, 'a7', 'a8');
    await dialog.getByRole('button', { name: 'Promote to queen' }).click();
    await expect(moveButton(page, '1. a8=Q+')).toBeVisible();
    await expect(moveButton(page, '1... Kg7')).toBeVisible();
    await expectPieceAt(page, 'a8', 'Q');
    await expectPieceAt(page, 'a7', null);
    await moveButton(page, '1. a8=Q+').click();
    await expect(page.locator('cg-board square.check')).toHaveCount(1);
    await expect.poll(() => sounds.some((url) => url.endsWith('/Check.mp3'))).toBeTruthy();
  });

  test('tracks captured pieces and material advantage', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['d5', 'Nf6'] });
    await expect(page.getByLabel('No pieces captured')).toHaveCount(2);

    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... d5')).toBeVisible();
    await movePiece(page, 'e4', 'd5');
    await expect(moveButton(page, '2... Nf6')).toBeVisible();

    await expect(page.getByLabel('Captured: 1 pawn')).toHaveCount(1);
    await expect(page.getByLabel('No pieces captured')).toHaveCount(1);
    await expect(page.getByLabel('1 point material advantage')).toBeVisible();
  });

  test('shows the accepted move and capture while engine inference is blocked, including after reload', async ({ page, request }) => {
    const sounds = [];
    page.on('request', (event) => {
      if (event.url().includes('/sounds/')) sounds.push(event.url());
    });
    await startScenario(page, request, {
      fen: '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
      engine_moves: ['Kf7'],
      block_engine: true,
    });

    try {
      await movePiece(page, 'e5', 'd6');
      await expect(moveButton(page, '1. exd6')).toBeVisible({ timeout: 3000 });
      await expectPieceAt(page, 'd6', 'P');
      await expectPieceAt(page, 'd5', null);
      await expect(page.getByLabel('Captured: 1 pawn')).toHaveCount(1);
      await expect(page.getByLabel('1 point material advantage')).toBeVisible();
      await expect.poll(() => sounds.some((url) => url.endsWith('/Capture.mp3'))).toBeTruthy();
      await expect(page.getByLabel('Black clock')).toHaveClass(/active/);
      await expect(status(page)).toContainText('thinking');
      await expect(moveButton(page, '1... Kf7')).toHaveCount(0);

      await page.getByRole('button', { name: 'Go to starting position' }).click();
      await expectPieceAt(page, 'd5', 'p');
      await page.getByRole('button', { name: 'Return to current position' }).click();
      await expectPieceAt(page, 'd6', 'P');

      await page.reload();
      await expect(moveButton(page, '1. exd6')).toBeVisible();
      await expect(page.getByLabel('Captured: 1 pawn')).toHaveCount(1);
      await expect(status(page)).toContainText('thinking');
    } finally {
      await request.post('/__e2e__/release-engine');
    }

    await expect(moveButton(page, '1... Kf7')).toBeVisible();
    await expect(status(page)).toContainText('Your move.');
  });

  test('shows the chosen promotion before Maia responds', async ({ page, request }) => {
    await startScenario(page, request, {
      fen: '7k/P7/8/8/8/8/8/7K w - - 0 1',
      engine_moves: ['Kg7'],
      block_engine: true,
    });
    try {
      await movePiece(page, 'a7', 'a8');
      await page.getByRole('button', { name: 'Promote to queen' }).click();
      await expect(moveButton(page, '1. a8=Q+')).toBeVisible();
      await expectPieceAt(page, 'a8', 'Q');
      await expect(page.locator('cg-board square.check')).toHaveCount(1);
      await expect(moveButton(page, '1... Kg7')).toHaveCount(0);
    } finally {
      await request.post('/__e2e__/release-engine');
    }
    await expect(moveButton(page, '1... Kg7')).toBeVisible();
  });

  test('shows en passant captures correctly when reviewing positions', async ({ page, request }) => {
    await startScenario(page, request, {
      fen: '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1',
      engine_moves: ['Kf7'],
    });

    await movePiece(page, 'e5', 'd6');
    await expect(moveButton(page, '1. exd6')).toBeVisible();
    await expect(moveButton(page, '1... Kf7')).toBeVisible();
    await expectPieceAt(page, 'd6', 'P');
    await expectPieceAt(page, 'd5', null);
    await expect(page.getByLabel('Captured: 1 pawn')).toHaveCount(1);
    await expect(page.getByLabel('1 point material advantage')).toBeVisible();

    await page.getByRole('button', { name: 'Go to starting position' }).click();
    await expectPieceAt(page, 'e5', 'P');
    await expectPieceAt(page, 'd5', 'p');
    await expect(page.getByLabel('No pieces captured')).toHaveCount(2);
    await page.getByRole('button', { name: 'Return to current position' }).click();
    await expectPieceAt(page, 'd6', 'P');
    await expect(page.getByLabel('Captured: 1 pawn')).toHaveCount(1);
  });

  test('moves can be undone', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e5'] });
    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... e5')).toBeVisible();

    await page.getByRole('button', { name: 'Undo' }).click();

    await expect(page.getByRole('button', { name: /^Review move/ })).toHaveCount(0);
    await expectPieceAt(page, 'e2', 'P');
    await expectPieceAt(page, 'e4', null);
    await expect(status(page)).toContainText('Your move.');
  });

  test('preserves the accepted move and retries after engine failure', async ({ page, request }) => {
    await startScenario(page, request, { engine_fails_once: true, engine_moves: ['e5'] });

    await movePiece(page, 'e2', 'e4');
    await expect(status(page)).toContainText('Maia could not respond');
    await expectPieceAt(page, 'e2', null);
    await expectPieceAt(page, 'e4', 'P');
    await expect(moveButton(page, '1. e4')).toBeVisible();
    await expect(page.getByLabel('White clock')).not.toHaveClass(/active/);
    await expect(page.getByLabel('Black clock')).not.toHaveClass(/active/);
    await page.getByRole('button', { name: 'Retry Maia' }).click();
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await expect(status(page)).toContainText('Your move.');
    await expect(page.getByRole('button', { name: /^Review move/ })).toHaveCount(2);
  });

  test('restores a rejected move and allows a retry', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e5'] });
    const initialState = await (await request.get('/api/state')).json();
    await page.route('**/api/move', (route) => route.fulfill({
      status: 409,
      json: { error: 'The position changed.', state: initialState },
    }), { times: 1 });

    await movePiece(page, 'e2', 'e4');
    await expectPieceAt(page, 'e2', 'P');
    await expectPieceAt(page, 'e4', null);
    await expect(page.getByRole('button', { name: /^Review move/ })).toHaveCount(0);
    await expect(page.getByLabel('White clock')).toHaveClass(/active/);
    await expect(status(page)).toContainText('The position changed.');

    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1. e4')).toBeVisible();
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await expect(page.getByRole('button', { name: /^Review move/ })).toHaveCount(2);
  });

  test('recovers the accepted move when its acknowledgement is lost', async ({ page, request }) => {
    await startScenario(page, request, { block_engine: true, engine_moves: ['e5'] });
    await page.route('**/api/move', async (route) => {
      await route.fetch();
      await route.abort('failed');
    }, { times: 1 });
    try {
      await movePiece(page, 'e2', 'e4');
      await expect(moveButton(page, '1. e4')).toBeVisible();
      await expectPieceAt(page, 'e4', 'P');
      await expect(moveButton(page, '1... e5')).toHaveCount(0);
    } finally {
      await request.post('/__e2e__/release-engine');
    }
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await expect(page.getByRole('button', { name: /^Review move/ })).toHaveCount(2);
  });
});

test.describe('reviewing and reloading', () => {
  test('steps through past positions without changing the live game', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e5'] });
    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... e5')).toBeVisible();

    await page.getByRole('button', { name: 'Previous move' }).click();
    await expect(status(page)).toHaveText('Reviewing move 1 of 2.');
    await expectPieceAt(page, 'e4', 'P');
    await expectPieceAt(page, 'e5', null);
    await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Next move' })).toBeEnabled();

    await page.getByRole('button', { name: 'Go to starting position' }).click();
    await expect(status(page)).toHaveText('Reviewing move 0 of 2.');
    await expectPieceAt(page, 'e2', 'P');
    await expectPieceAt(page, 'e4', null);

    await moveButton(page, '1. e4').click();
    await expect(status(page)).toHaveText('Reviewing move 1 of 2.');

    await page.getByRole('button', { name: 'Return to current position' }).click();
    await expect(status(page)).toContainText('Your move.');
    await expectPieceAt(page, 'e5', 'p');
    await expect(page.getByRole('button', { name: 'Undo' })).toBeEnabled();
  });

  test('restores the game after a page reload', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e5'] });
    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... e5')).toBeVisible();

    await page.reload();

    await expect(page.getByRole('button', { name: 'Find match' })).toBeHidden();
    await expect(moveButton(page, '1. e4')).toBeVisible();
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await expectPieceAt(page, 'e4', 'P');
    await expectPieceAt(page, 'e5', 'p');
    await expect(status(page)).toContainText('Your move.');
  });
});

test.describe('ending a game', () => {
  test('shows a win with rating change after checkmate', async ({ page, request }) => {
    await startScenario(page, request, { fen: '6k1/5ppp/8/8/8/8/8/R6K w - - 0 1' });

    await movePiece(page, 'a1', 'a8');

    await expect(page.getByRole('heading', { name: 'Victory!' })).toBeVisible();
    await expect(page.getByText('Checkmate! A brilliant finish.')).toBeVisible();
    await expect(page.getByText(/\+\d+ rating/)).toBeVisible();
    await expect(status(page)).toHaveText('Final score: 1-0');
    await expect(page.getByRole('button', { name: 'Copy PGN' })).toBeVisible();
  });

  test('resigning shows a defeat and "Play again" returns to matchmaking', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e5'] });
    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... e5')).toBeVisible();

    await resign(page);

    await expect(page.getByRole('heading', { name: 'Defeat' })).toBeVisible();
    await expect(page.getByText('You resigned.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Copy PGN' })).toBeVisible();

    await page.getByRole('button', { name: 'Play again' }).click();
    await expect(page.getByRole('button', { name: 'Find match' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Resign' })).toBeHidden();
  });

  test('losing on time ends the game', async ({ page, request }) => {
    await startScenario(page, request, { clock: [2, 0] });

    await expect(page.getByRole('heading', { name: 'Defeat' })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Your clock ran out.')).toBeVisible();
  });

  test('Maia running out of time while thinking ends the game', async ({ page, request }) => {
    await startScenario(page, request, { clock: [2, 0], think_seconds: 4, engine_moves: ['e5'] });
    await movePiece(page, 'e2', 'e4');
    await expect(page.getByLabel('Black clock')).toHaveClass(/active/);
    await expect(page.getByRole('heading', { name: 'Victory!' })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('Your opponent ran out of time.')).toBeVisible();
    await expect(page.getByLabel('Black clock')).not.toHaveClass(/active/);
  });
});

test.describe('profile', () => {
  test('renders legacy matches without a player color', async ({ page, request }) => {
    await resetServer(request, { legacy_match: true, legacy_without_color: true });
    await page.goto('/profile');

    const match = page.locator('table:not(.color-stats) tbody tr');
    await expect(match).toContainText('Unknown');
    await expect(match.getByRole('button', { name: 'Copy PGN' })).toBeVisible();
    await expect(page.locator('.color-stats tbody tr').first()).toContainText('0-0-0');
    await expect(page.locator('.color-stats tbody tr').last()).toContainText('0-0-0');
  });

  test('provides a visible way back to the game', async ({ page, request }) => {
    await resetServer(request);
    await page.goto('/profile');

    await expect(page.getByRole('link', { name: 'Back to game' }))
      .toBeVisible();
  });

  test('imports an older match from the profile', async ({ page, request }) => {
    await resetServer(request, { lichess: true, legacy_match: true, lichess_delay: 1.5 });
    await page.goto('/profile');
    await page.evaluate(() => { window.profileMarker = 'unchanged'; });
    const button = page.getByRole('button', { name: 'Import to Lichess' });
    await expect(button).toBeEnabled();
    await button.click();
    await expect(page.getByText('Importing…')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Lichess analysis' }))
      .toHaveAttribute('href', 'https://lichess.org/abcd1234/analysis');
    expect(await page.evaluate(() => window.profileMarker)).toBe('unchanged');
    await expect(page.getByRole('button', { name: 'Import to Lichess' })).toHaveCount(0);
  });

  test('recovers from a temporary status check failure without reloading', async ({ page, request }) => {
    await resetServer(request, { lichess: true, legacy_match: true, lichess_delay: 2.5 });
    let checks = 0;
    await page.route('**/api/lichess/status?*', async (route) => {
      checks += 1;
      if (checks === 1) await route.fulfill({ status: 503, body: 'Unavailable' });
      else await route.continue();
    });
    await page.goto('/profile');
    await page.evaluate(() => { window.profileMarker = 'unchanged'; });
    await page.getByRole('button', { name: 'Import to Lichess' }).click();
    await expect(page.getByRole('status')).toContainText('Could not check Lichess import status');
    await expect(page.getByRole('link', { name: 'Lichess analysis' })).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole('status')).not.toContainText('Could not check Lichess import status');
    expect(await page.evaluate(() => window.profileMarker)).toBe('unchanged');
  });

  test('links a finished match to its imported Lichess analysis', async ({ page, request }) => {
    await startScenario(page, request, { lichess: true, lichess_delay: 1.5 });
    await resign(page);
    await page.goto('/profile');

    await page.evaluate(() => { window.profileMarker = 'unchanged'; });
    await expect(page.getByText('Importing…')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Lichess analysis' }))
      .toHaveAttribute('href', 'https://lichess.org/abcd1234/analysis');
    expect(await page.evaluate(() => window.profileMarker)).toBe('unchanged');
  });

  test('shows an import failure and allows retrying', async ({ page, request }) => {
    await startScenario(page, request, { lichess: true, lichess_fail: true, lichess_delay: 1.5 });
    await resign(page);
    await page.goto('/profile');
    await page.evaluate(() => { window.profileMarker = 'unchanged'; });
    await expect(page.getByText('Importing…')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Retry import' })).toBeVisible();
    await expect(page.getByText('Import failed: HTTP 429')).toBeVisible();
    await page.getByRole('button', { name: 'Retry import' }).click();
    await expect(page.getByText('Importing…')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Lichess analysis' })).toBeVisible();
    expect(await page.evaluate(() => window.profileMarker)).toBe('unchanged');
  });

  test('lists finished matches per rating category', async ({ page, request }) => {
    await startScenario(page, request, { engine_moves: ['e5'] });
    await movePiece(page, 'e2', 'e4');
    await expect(moveButton(page, '1... e5')).toBeVisible();
    await resign(page);
    await expect(page.getByRole('heading', { name: 'Defeat' })).toBeVisible();

    await page.goto('/profile');

    await expect(page.locator('h2', { hasText: 'Blitz rating progress' })).toBeVisible();
    const match = page.locator('table:not(.color-stats) tbody tr').filter({ hasText: '3+2' });
    await expect(match).toContainText('Loss');
    await expect(match).toContainText('White');
    await expect(match.getByRole('button', { name: 'Copy PGN' })).toBeVisible();
    await expect(match.getByRole('button', { name: 'Import to Lichess' })).toBeDisabled();

    await page.getByRole('link', { name: 'Rapid' }).click();
    await expect(page.getByRole('heading', { name: 'Rapid rating progress' })).toBeVisible();
    await expect(page.getByText('No matches yet.')).toBeVisible();

    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Resign' })).toBeVisible();
  });
});
