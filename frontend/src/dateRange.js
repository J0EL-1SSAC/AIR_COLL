export function localDateTimeInput(seconds, timeZone='Asia/Kolkata') {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(seconds * 1000));
  const value = Object.fromEntries(parts.map(part=>[part.type,part.value]));
  return `${value.year}-${value.month}-${value.day}T${value.hour}:${value.minute}`;
}

export function dateTimeLocalToEpoch(value, timeZone='Asia/Kolkata') {
  return value ? Date.parse(`${value}:00${timeZone==='UTC'?'Z':'+05:30'}`) / 1000 : null;
}

export function validateDateRange(from, to, nowSeconds, earliestSeconds = null, timeZone='Asia/Kolkata') {
  const start = dateTimeLocalToEpoch(from,timeZone);
  const end = dateTimeLocalToEpoch(to,timeZone);
  if (start != null && Number.isNaN(start)) return 'From date is invalid.';
  if (end != null && Number.isNaN(end)) return 'To date is invalid.';
  if (start != null && end != null && start > end) return 'From time must be at or before To time.';
  if (start != null && start > nowSeconds) return 'From time cannot be in the future.';
  if (end != null && end > nowSeconds) return 'To time cannot be in the future.';
  if (start != null && earliestSeconds != null && start < earliestSeconds) return 'From time is earlier than recorded data.';
  return null;
}
