/**
 * Data source registry — manages all flight search data sources.
 *
 * Each source conforms to:
 *   meta: { id, name, requiresChrome, provides }
 *   search(params) → Flight[]
 *   isAvailable() → boolean
 *
 * Normalized Flight format:
 *   {
 *     flight_number, airline, origin_iata, destination_iata,
 *     scheduled_departure, scheduled_arrival, price, stops,
 *     duration_minutes, cabin_class, aircraft_type,
 *     jump_url, segments[], layover_cities[],
 *     // OTA-specific additions:
 *     depAirportCode, arrAirportCode, prices[], booking_url, search_url,
 *     ctripSegments
 *   }
 */

import { searchFliggy } from '../fliggy.js';
import * as ctripModule from './ctrip.js';
import * as qunarModule from './qunar.js';
import * as tongchengModule from './tongcheng.js';

// ---- Fliggy source ----
export const fliggy = {
  meta: {
    id: 'fliggy',
    name: '飞猪',
    requiresChrome: false,
    provides: ['price', 'booking_url'],
  },

  async search(params) {
    const cabinMap = {
      'ECONOMY': 'economy', 'PREMIUM_ECONOMY': 'premium',
      'BUSINESS': 'business', 'FIRST': 'first',
    };
    return searchFliggy({
      origin: params.origin,
      destination: params.destination,
      date: params.date,
      cabin: params.cabin ? (cabinMap[params.cabin] || params.cabin) : 'economy',
      stops: params.stops !== undefined ? params.stops : 'any',
      limit: params.limit,
      timeout: 15,
    });
  },

  async isAvailable() {
    try { return true; } catch { return false; }
  },
};

// ---- Ctrip source ----
export const ctrip = {
  meta: ctripModule.meta,
  search: (params) => ctripModule.search(params),
  isAvailable: () => ctripModule.isAvailable(),
};

// ---- Qunar source ----
export const qunar = {
  meta: qunarModule.meta,
  search: (params) => qunarModule.search(params),
  isAvailable: () => qunarModule.isAvailable(),
};

// ---- Tongcheng source ----
export const tongcheng = {
  meta: tongchengModule.meta,
  search: (params) => tongchengModule.search(params),
  isAvailable: () => tongchengModule.isAvailable(),
};

// ---- Source management ----
const ALL_SOURCES = { fliggy, ctrip, qunar, tongcheng };

export function getAllSources() {
  return Object.values(ALL_SOURCES);
}

export function getSource(id) {
  return ALL_SOURCES[id] || null;
}

/**
 * Search across all enabled sources in parallel.
 * @param {Object} params - normalized search params { origin, destination, date, cabin, stops, limit }
 * @returns {{ flights: Array, meta: Object }}
 */
export async function searchAll(params) {
  const sources = getAvailableSources();
  const startTimes = {};

  const results = await Promise.allSettled(
    sources.map(async (src) => {
      const start = Date.now();
      startTimes[src.meta.id] = start;
      const flights = await src.search(params);
      const elapsed = Date.now() - start;
      return { source: src.meta.id, name: src.meta.name, flights, elapsedMs: elapsed };
    })
  );

  const allFlights = [];
  const sourcesSearched = [];
  const sourcesSucceeded = [];
  const sourcesFailed = [];
  const timings = {};

  for (const r of results) {
    if (r.status === 'fulfilled') {
      const { source, name, flights, elapsedMs } = r.value;
      sourcesSearched.push(source);
      timings[source] = { name, elapsedMs, count: flights.length, status: 'ok' };
      if (flights.length > 0) sourcesSucceeded.push(source);
      allFlights.push(...flights.map(f => ({ ...f, _source: source })));
    } else {
      const errMsg = r.reason?.message || 'Unknown error';
      console.error(`Source search failed:`, errMsg);
      sourcesFailed.push({ id: 'unknown', error: errMsg });
    }
  }

  return {
    allFlights,
    meta: {
      sources_searched: sourcesSearched,
      sources_succeeded: sourcesSucceeded,
      sources_failed: sourcesFailed,
      timings,
    },
  };
}

export function getAvailableSources() {
  return getAllSources();
}
