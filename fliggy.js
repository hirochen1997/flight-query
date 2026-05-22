/**
 * Fliggy MCP API client — Node.js implementation.
 *
 * Calls flyai.open.fliggy.com/mcp with HMAC-SHA256 signing + AES-256-GCM
 * context encryption.  Replaces the Python FlyClaw subprocess so the server
 * can run on platforms without Python (e.g. Netlify Functions).
 *
 * Protocol reverse-engineered from @fly-ai/flyai-cli v1.0.6 (MIT-licensed).
 */

import crypto from 'crypto';
import zlib from 'zlib';
import os from 'os';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = (() => {
  try { return path.dirname(fileURLToPath(import.meta.url)); }
  catch { return process.cwd(); }
})();

// ---- Constants (from flyai-cli v1.0.6) ----
const MCP_URL = 'https://flyai.open.fliggy.com/mcp';
const DEFAULT_API_KEY = 'sk-faRn8Kp2QzXvLm9YtA4EjHcWbS7oUdG5iF3xNqV6rZ';
const DEFAULT_SIGN_SECRET = 'XSbdYnucPARDc9knhD8+X6hxdD1Nh6ZGI6Hadg25kBw=';
const X_TTID = 'ai2c(sk.clawhub)';
const SIGN_VER = '7';

const CABIN_CN_MAP = {
  economy: '经济舱',
  premium: '超级经济舱',
  business: '商务舱',
  first: '头等舱',
};

// ---- Helpers ----

let _deviceIdCache = null;
function getDeviceId() {
  if (_deviceIdCache) return _deviceIdCache;

  // Try persistent storage locations; fall back to in-memory (e.g. on Netlify)
  const candidates = [
    path.join(__dirname, 'cache', '.device_id'),
    path.join('/tmp', 'flyclaw_device_id'),
  ];

  for (const idPath of candidates) {
    try {
      const did = fs.readFileSync(idPath, 'utf-8').trim();
      if (did.length === 64) { _deviceIdCache = did; return did; }
    } catch { /* not found */ }
  }

  const did = crypto.createHash('sha256').update(crypto.randomUUID()).digest('hex');
  // Try to persist; ignore errors (read-only fs, etc.)
  for (const idPath of candidates) {
    try {
      fs.mkdirSync(path.dirname(idPath), { recursive: true });
      fs.writeFileSync(idPath, did);
      break;
    } catch { /* read-only filesystem */ }
  }
  _deviceIdCache = did;
  return did;
}

function sha256Hex(s) {
  return crypto.createHash('sha256').update(s, 'utf-8').digest('hex');
}

function makeSignature(method, pathname, timestampMs, nonce, body, auth, signSecret) {
  const bodyHash = sha256Hex(body);
  const authStr = (auth || '').trim();
  const authNorm = authStr.startsWith('Bearer ') ? authStr : `Bearer ${authStr}`;
  const authHash = sha256Hex(authNorm);
  const signString = `${method}\n${pathname}\n${timestampMs}\n${nonce}\n${bodyHash}\n${authHash}`;
  return crypto.createHmac('sha256', signSecret)
    .update(signString, 'utf-8')
    .digest('base64url')
    .replace(/=+$/, '');
}

function buildContext(signSecret) {
  const cpus = os.cpus().length;
  const totalMemGB = os.totalmem() / (1024 ** 3);
  let memTier = 8;
  for (const t of [2, 4, 8, 16, 20]) { if (totalMemGB <= t * 1.2) { memTier = t; break; } }
  const platform = os.platform();  // 'darwin' | 'linux'
  const arch = os.machine ? os.machine() : os.arch();
  const osReleaseMajor = os.release().split('.')[0];
  const nodeVersion = 'v22.22.0';
  const lang = (process.env.LC_ALL || process.env.LANG || 'en').split(/[._]/)[0];

  const ctx = {
    machine: {
      platform, arch, cpus, memoryTierGB: memTier,
      osType: platform, nodeVersion, osReleaseMajor,
    },
    fingerprint: {
      language: lang,
      platform,
      userAgent: `flyai-cli/1.0.6 (Node.js ${nodeVersion}; ${platform} ${arch})`,
      hardwareConcurrency: cpus,
      deviceMemory: Math.min(8, Math.max(2, memTier)),
      clientSurface: 'cli',
      timezoneOffset: -new Date().getTimezoneOffset(),
      deviceId: getDeviceId(),
    },
  };

  const json = JSON.stringify(ctx);
  const compressed = zlib.gzipSync(Buffer.from(json, 'utf-8'));
  const secret = (signSecret || '').trim();
  if (!secret) return Buffer.from(compressed).toString('base64');

  // AES-256-GCM encrypt
  const key = crypto.createHash('sha256').update(secret, 'utf-8').digest();
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv, { authTagLength: 16 });
  const encrypted = Buffer.concat([cipher.update(compressed), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([Buffer.from([0x01]), iv, encrypted, tag]).toString('base64');
}

// ---- Public API ----

/**
 * Search flights via Fliggy MCP.
 *
 * @param {object} params
 * @param {string} params.origin - IATA code or Chinese city name
 * @param {string} params.destination
 * @param {string} params.date - YYYY-MM-DD
 * @param {string} [params.cabin] - economy/premium/business/first
 * @param {number|string} [params.stops] - 0/1/2/'any'
 * @param {number} [params.limit]
 * @param {number} [params.timeout] - seconds, default 10
 * @returns {Promise<Array>}
 */
export async function searchFliggy({
  origin, destination, date,
  cabin = 'economy', stops = 'any', limit,
  timeout = 10,
  apiKey, signSecret,
}) {
  const key = apiKey || DEFAULT_API_KEY;
  const secret = signSecret || DEFAULT_SIGN_SECRET;

  const toolArgs = { origin, destination, depDate: date };
  if (limit) toolArgs.limit = limit;

  const body = JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/call',
    params: { name: 'search_flight', arguments: toolArgs },
  });

  const pathname = '/mcp';
  const timestampMs = String(Date.now());
  const nonce = crypto.randomBytes(16).toString('hex');
  const auth = `Bearer ${key}`;
  const sig = makeSignature('POST', pathname, timestampMs, nonce, body, auth, secret);

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json, text/event-stream',
    'Authorization': auth,
    'x-ff-ctx': buildContext(secret),
    'x-ttid': X_TTID,
    'User-Agent': 'flyai-cli/1.0.6',
    'x-flyai-ts': timestampMs,
    'x-flyai-sign-ver': SIGN_VER,
    'x-flyai-sign-alg': 'hmac-sha256',
    'x-flyai-nonce': nonce,
    'x-flyai-sign': sig,
  };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout * 1000);

  try {
    const resp = await fetch(MCP_URL, {
      method: 'POST', headers, body,
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`Fliggy HTTP ${resp.status}`);
    const rj = await resp.json();
    const content = rj?.result?.content;
    if (!content || !content[0]?.text) return [];
    const data = JSON.parse(content[0].text);
    return parseResults(data, stops, cabin);
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('Fliggy timeout');
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function parseResults(data, stops, cabin) {
  const items = data?.data?.itemList;
  if (!items || !Array.isArray(items)) return [];

  const maxStops = stops === 'any' ? Infinity : parseInt(stops);
  const expectedCabinCN = CABIN_CN_MAP[cabin] || '';

  return items
    .map(item => parseItem(item))
    .filter(rec => {
      if (!rec) return false;
      if (rec.stops > maxStops) return false;
      if (expectedCabinCN && rec.cabin_class && expectedCabinCN !== rec.cabin_class) return false;
      return true;
    });
}

function parseItem(item) {
  const journeys = item.journeys;
  if (!journeys || !journeys[0]?.segments) return null;

  const outbound = journeys[0];
  const segs = outbound.segments;
  const firstSeg = segs[0];
  const lastSeg = segs[segs.length - 1];

  let price = parseFloat(item.ticketPrice);
  if (isNaN(price) || price === 0) price = null;

  const depRaw = firstSeg.depDateTime || '';
  const arrRaw = lastSeg.arrDateTime || '';

  const segments = segs.map(s => ({
    flight_number: s.marketingTransportNo || '',
    origin_iata: s.depStationCode || '',
    destination_iata: s.arrStationCode || '',
    departure: (s.depDateTime || '').replace(' ', 'T').slice(0, 16),
    arrival: (s.arrDateTime || '').replace(' ', 'T').slice(0, 16),
    duration_minutes: parseInt(s.duration) || 0,
    terminal: '',
  }));

  return {
    flight_number: firstSeg.marketingTransportNo || '',
    airline: firstSeg.marketingTransportName || '',
    origin_iata: firstSeg.depStationCode || '',
    destination_iata: lastSeg.arrStationCode || '',
    scheduled_departure: depRaw.replace(' ', 'T').slice(0, 16),
    scheduled_arrival: arrRaw.replace(' ', 'T').slice(0, 16),
    price,
    currency: 'CNY',
    stops: segs.length - 1,
    duration_minutes: parseInt(outbound.totalDuration || firstSeg.duration) || 0,
    cabin_class: firstSeg.seatClassName || '',
    aircraft_type: '',
    jump_url: item.jumpUrl || '',
    segments,
    layover_cities: segments.slice(0, -1).map(s => s.destination_iata),
  };
}
