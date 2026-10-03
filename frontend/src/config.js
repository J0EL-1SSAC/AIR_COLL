export const UI = {
  labelZoomFallback: 11,
  predictionTickZoomFallback: 11,
  oldestTrailOpacityFallback: 0.18,
  newestTrailOpacityFallback: 0.82,
  trailWidth: 2.2,
  predictionWidth: 2.8,
  predictionHaloWidth: 7,
  pairWidth: 2.3,
  tickRadius: 4,
  mapFocusPadding: 56,
  mapFocusMaxZoom: 13,
  updateAgeTickMs: 1000,
  panes: {trails:410,pairs:420,predictionHalo:430,predictions:440,uncertainty:435,airports:610},
  tileUrls: {
    dark: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    light: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  },
  tileAttribution: {
    dark: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    light: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  },
};

export const ALTITUDE_BANDS = {
  ground: 'var(--band-ground)',
  low: 'var(--band-low)',
  medium: 'var(--band-medium)',
  high: 'var(--band-high)',
};

export const DISTANCE_FORMAT = { metersPerNm: 1852, metersPerFoot: 0.3048, metersPerSecondToKnots: 1.943844492 };
