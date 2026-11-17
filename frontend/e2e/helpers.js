import { expect } from '@playwright/test';

// Everything that touches UI widgets that may be swapped for a component
// library (dropdowns, confirm dialogs) lives here, so specs stay stable.

export async function resetServer(request, options = {}) {
  const response = await request.post('/__e2e__/reset', { data: options });
  expect(response.ok()).toBeTruthy();
}

export async function chooseOption(page, label, value) {
  const combobox = page.getByRole('combobox', { name: label });
  const control = (await combobox.count())
    ? combobox.first()
    : page.getByRole('textbox', { name: label }).first();
  if (await control.evaluate((element) => element instanceof HTMLSelectElement)) {
    await control.selectOption(value);
    return;
  }
  if (await control.inputValue() === value) return;
  await control.click();
  const option = page
    .locator('[data-combobox-option]:visible')
    .filter({ hasText: new RegExp(`^${value}$`, 'i') })
    .first();
  await option.click();
}

export async function findMatch(page, { color = 'white', timeControl = '3+2' } = {}) {
  await chooseOption(page, 'Time control', timeControl);
  await chooseOption(page, 'Piece color', color);
  await page.getByRole('button', { name: 'Find match' }).click();
  await expect(page.getByRole('button', { name: 'Resign' })).toBeVisible();
}

export async function startScenario(page, request, scenario = {}, matchOptions = {}) {
  await resetServer(request, scenario);
  await page.goto('/');
  await findMatch(page, matchOptions);
}

export async function resign(page) {
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Resign' }).click();
}

async function boardGeometry(page) {
  const wrap = page.locator('.cg-wrap');
  const box = await wrap.boundingBox();
  const flipped = await wrap.evaluate((element) => element.classList.contains('orientation-black'));
  return { box, flipped, size: box.width / 8 };
}

export async function clickSquare(page, square) {
  const { box, flipped, size } = await boardGeometry(page);
  const file = square.charCodeAt(0) - 97;
  const rank = Number(square[1]) - 1;
  const column = flipped ? 7 - file : file;
  const row = flipped ? rank : 7 - rank;
  await page.mouse.click(box.x + (column + 0.5) * size, box.y + (row + 0.5) * size);
}

export async function movePiece(page, from, to) {
  await clickSquare(page, from);
  await clickSquare(page, to);
}

// Maps squares to FEN piece letters by reading where Chessground draws pieces.
export async function readBoard(page) {
  return page.locator('.cg-wrap').evaluate((wrap) => {
    const board = wrap.querySelector('cg-board').getBoundingClientRect();
    const flipped = wrap.classList.contains('orientation-black');
    const size = board.width / 8;
    const letters = { pawn: 'p', knight: 'n', bishop: 'b', rook: 'r', queen: 'q', king: 'k' };
    const pieces = {};
    for (const piece of wrap.querySelectorAll('cg-board piece')) {
      const [color, role] = piece.className.split(' ');
      if (!letters[role] || piece.classList.contains('ghost')) continue;
      const rect = piece.getBoundingClientRect();
      const column = Math.floor((rect.x + rect.width / 2 - board.x) / size);
      const row = Math.floor((rect.y + rect.height / 2 - board.y) / size);
      const file = flipped ? 7 - column : column;
      const rank = flipped ? row + 1 : 8 - row;
      const letter = letters[role];
      pieces[`${String.fromCharCode(97 + file)}${rank}`] =
        color === 'white' ? letter.toUpperCase() : letter;
    }
    return pieces;
  });
}

// `piece` is a FEN letter, or null for an empty square.
export async function expectPieceAt(page, square, piece) {
  await expect
    .poll(async () => (await readBoard(page))[square] ?? null, {
      message: `piece on ${square}`,
    })
    .toBe(piece);
}

export function moveButton(page, name) {
  return page.getByRole('button', { name: `Review move ${name}` });
}
