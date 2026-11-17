import { expect, test } from '@playwright/test';
import {
  expectPieceAt,
  findMatch,
  movePiece,
  moveButton,
  resetServer,
  resign,
  startScenario,
} from './helpers.js';

const status = (page) => page.getByRole('status');

test.describe('starting a match', () => {
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

  test('resyncs the board when the engine fails to respond', async ({ page, request }) => {
    await startScenario(page, request, { engine_fails: true });
    await page.route('**/api/move', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.continue();
    });

    await movePiece(page, 'e2', 'e4');
    await expect(page.getByLabel('Black clock')).toHaveClass(/active/);

    await expectPieceAt(page, 'e2', 'P');
    await expectPieceAt(page, 'e4', null);
    await expect(page.getByRole('button', { name: /^Review move/ })).toHaveCount(0);
    await expect(status(page)).toContainText('Your move.');
    await expect(page.getByLabel('White clock')).toHaveClass(/active/);
    await expect(page.getByLabel('Black clock')).not.toHaveClass(/active/);
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

    await page.getByRole('link', { name: 'Rapid' }).click();
    await expect(page.getByRole('heading', { name: 'Rapid rating progress' })).toBeVisible();
    await expect(page.getByText('No matches yet.')).toBeVisible();

    await page.goto('/');
    await expect(page.getByRole('button', { name: 'Resign' })).toBeVisible();
  });
});
