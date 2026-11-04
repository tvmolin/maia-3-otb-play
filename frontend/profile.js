import { format, isValid, parseISO } from 'date-fns';

for (const timeElement of document.querySelectorAll('time[data-local-time]')) {
  const date = parseISO(timeElement.dateTime);
  if (isValid(date)) {
    timeElement.textContent = format(date, 'PP p');
  }
}
