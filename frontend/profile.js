import { format, isValid, parseISO } from 'date-fns';

for (const timeElement of document.querySelectorAll('time[data-local-time]')) {
  const date = parseISO(timeElement.dateTime);
  if (isValid(date)) {
    timeElement.textContent = format(date, 'PP p');
  }
}

const copyStatus = document.querySelector('#pgn-copy-status');
const pgnFallback = document.querySelector('#pgn-copy-fallback');

document.addEventListener('click', async (event) => {
  const button = event.target.closest('button.copy-match-pgn');
  if (!button) return;

  const pgn = button.dataset.pgn;
  try {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(pgn);
      } catch {
        copyWithFallback(pgn);
      }
    } else {
      copyWithFallback(pgn);
    }
    pgnFallback.hidden = true;
    copyStatus.textContent = 'PGN copied to clipboard.';
  } catch {
    pgnFallback.value = pgn;
    pgnFallback.hidden = false;
    pgnFallback.focus();
    pgnFallback.select();
    copyStatus.textContent = 'Clipboard access failed. The PGN is selected below; copy it manually.';
  }
});

function copyWithFallback(text) {
  pgnFallback.value = text;
  pgnFallback.hidden = false;
  pgnFallback.focus();
  pgnFallback.select();
  if (!document.execCommand || !document.execCommand('copy')) {
    throw new Error('Clipboard copy failed.');
  }
}
