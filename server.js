import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

import { resolveCity, resolveFliggyInput, getCityCode, CITY_MAP } from './city_map.js';
import { mergeFlightsByNumber, extractCarrierCode, formatDuration } from './merge.js';
import { fliggy, ctrip, qunar, tongcheng, getAvailableSources } from './sources/registry.js';

const __dirname = (() => {
  try { return dirname(fileURLToPath(import.meta.url)); }
  catch { return process.cwd(); }
})();

const app = express();
const PORT = process.env.PORT || 3000;

// ---- Headless Chrome lifecycle (local dev only) ----
const CDP_PORT = 9223;
async function ensureHeadlessChrome() {
  try {
    await fetch(`http://localhost:${CDP_PORT}/json/version`);
    console.log(`Headless Chrome already running on port ${CDP_PORT}`);
  } catch {
    console.log(`Launching headless Chrome on port ${CDP_PORT}...`);
    spawn(
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      [
        '--headless=new',
        `--remote-debugging-port=${CDP_PORT}`,
        '--user-data-dir=/tmp/chrome_scraper_profile',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-blink-features=AutomationControlled',
        '--window-size=1920,1080',
      ],
      { detached: true, stdio: 'ignore' }
    ).unref();
    for (let i = 0; i < 10; i++) {
      await new Promise(r => setTimeout(r, 500));
      try {
        await fetch(`http://localhost:${CDP_PORT}/json/version`);
        console.log('Headless Chrome started');
        return;
      } catch {}
    }
    console.warn('Warning: Headless Chrome failed to start, Ctrip may not work');
  }
}

app.use(cors());
app.use(express.json());

// Static files (local dev only)
if (!process.env.NETLIFY) {
  app.use(express.static(join(__dirname, 'public'), {
    setHeaders: (res) => {
      res.set('Cache-Control', 'no-cache, no-store, must-revalidate');
      res.set('Pragma', 'no-cache');
      res.set('Expires', '0');
    },
  }));
}

// ---- Common search params for sources ----
function buildSourceParams({ fliggyOrigin, fliggyDest, departureDate, travelClass, nonStop, maxResults, adults, children, infants }) {
  const cabinMap = { 'ECONOMY': 'economy', 'PREMIUM_ECONOMY': 'premium', 'BUSINESS': 'business', 'FIRST': 'first' };
  return {
    origin: fliggyOrigin,
    destination: fliggyDest,
    date: departureDate,
    cabin: travelClass ? (cabinMap[travelClass] || travelClass) : 'economy',
    stops: nonStop ? 0 : 'any',
    limit: maxResults,
    adults,
    children,
    infants,
  };
}

// ---- Flight Search Endpoint ----
app.post('/api/search', async (req, res) => {
  try {
    const {
      origin, destination, departureDate,
      adults = 1, children = 0, infants = 0,
      travelClass, nonStop = false,
      maxPrice, airlines, maxResults = 20,
    } = req.body;

    if (!origin || !destination || !departureDate) {
      return res.status(400).json({ error: '出发地、目的地和出发日期为必填项' });
    }

    const originCode = resolveCity(origin);
    const destCode = resolveCity(destination);
    if (!originCode) return res.status(400).json({ error: `无法识别出发地: ${origin}，请输入城市名或三字码` });
    if (!destCode) return res.status(400).json({ error: `无法识别目的地: ${destination}，请输入城市名或三字码` });
    if (originCode === destCode) return res.status(400).json({ error: '出发地和目的地不能相同' });

    const fliggyOrigin = resolveFliggyInput(origin);
    const fliggyDest = resolveFliggyInput(destination);
    console.log(`Searching: ${originCode} → ${destCode} on ${departureDate} (Fliggy: ${fliggyOrigin} → ${fliggyDest})`);

    const params = buildSourceParams({ fliggyOrigin, fliggyDest, departureDate, travelClass, nonStop, maxResults, adults, children, infants });

    const originCityCode = getCityCode(originCode);
    const destCityCode = getCityCode(destCode);
    console.log(`City codes for Ctrip: ${originCityCode} → ${destCityCode}`);

    // Search Fliggy (MCP) + OTA sources in parallel
    const otaSourceParams = { origin, destination, date: departureDate };

    const [fliggyFlights, ctripFlights, qunarFlights, tongchengFlights] = await Promise.allSettled([
      fliggy.search(params),
      ctrip.search(otaSourceParams),
      qunar.search(otaSourceParams),
      tongcheng.search(otaSourceParams),
    ]);

    const fFliggy = fliggyFlights.status === 'fulfilled' ? fliggyFlights.value : [];
    const fCtrip = ctripFlights.status === 'fulfilled' ? ctripFlights.value : [];
    const fQunar = qunarFlights.status === 'fulfilled' ? qunarFlights.value : [];
    const fTongcheng = tongchengFlights.status === 'fulfilled' ? tongchengFlights.value : [];

    console.log(`Fliggy: ${fFliggy.length}, Ctrip: ${fCtrip.length}, Qunar: ${fQunar.length}, Tongcheng: ${fTongcheng.length}`);

    // Build per-source result metadata
    const sourceResults = [
      { id: 'fliggy', name: '飞猪', count: fFliggy.length, success: fliggyFlights.status === 'fulfilled' },
      { id: 'ctrip', name: '携程', count: fCtrip.length, success: ctripFlights.status === 'fulfilled' },
      { id: 'qunar', name: '去哪儿', count: fQunar.length, success: qunarFlights.status === 'fulfilled' },
      { id: 'tongcheng', name: '同程旅行', count: fTongcheng.length, success: tongchengFlights.status === 'fulfilled' },
    ];

    // Merge by flight number
    const otaResults = { ctrip: fCtrip, qunar: fQunar, tongcheng: fTongcheng };
    const mergedFlights = mergeFlightsByNumber(fFliggy, otaResults, departureDate);

    // Filters
    let filteredFlights = mergedFlights;
    if (maxPrice) {
      filteredFlights = filteredFlights.filter(f => f.best_price && f.best_price <= parseFloat(maxPrice));
    }
    if (airlines) {
      const airlineSet = new Set(airlines.split(',').map(a => a.trim().toUpperCase()));
      filteredFlights = filteredFlights.filter(f => {
        const fn = f.flight_number || '';
        const carrier = fn.match(/^[A-Z0-9]+/)?.[0] || '';
        return airlineSet.has(carrier.toUpperCase());
      });
    }

    // Transform to frontend format
    const platformLabels = {
      fliggy: '飞猪', ctrip: '携程', qunar: '去哪儿', tongcheng: '同程旅行',
    };

    const flights = filteredFlights.map((f, idx) => {
      const segments = (f.segments || []).map(seg => ({
        id: seg.flight_number,
        departure: { iata: seg.origin_iata, time: seg.departure, terminal: seg.terminal },
        arrival: { iata: seg.destination_iata, time: seg.arrival, terminal: seg.terminal },
        duration: formatDuration(seg.duration_minutes),
        carrierCode: extractCarrierCode(seg.flight_number),
        carrierName: f.airline || seg.flight_number,
        flightNumber: seg.flight_number,
        aircraft: f.aircraft_type || '',
        numberOfStops: 0,
      }));

      const effectiveSegments = segments.length > 0 ? segments : [{
        id: f.flight_number,
        departure: { iata: f.origin_iata, time: f.departure_time },
        arrival: { iata: f.destination_iata, time: f.arrival_time },
        duration: formatDuration(f.duration_minutes),
        carrierCode: extractCarrierCode(f.flight_number),
        carrierName: f.airline,
        flightNumber: f.flight_number,
        aircraft: f.aircraft_type || '',
        numberOfStops: 0,
      }];

      const firstSeg = effectiveSegments[0];
      const lastSeg = effectiveSegments[effectiveSegments.length - 1];

      const platformPrices = Object.entries(f.prices)
        .filter(([, p]) => p > 0)
        .map(([platform, price]) => ({
          platform,
          price,
          formatted: `¥${price.toLocaleString('zh-CN')}`,
          isBest: platform === f.best_price_platform,
          bookingUrl: f.booking_urls[platform] || null,
          searchUrl: f.search_urls[platform] || null,
          allPrices: f.allPrices[platform] || [],
        }))
        .sort((a, b) => a.price - b.price);

      return {
        id: `${f.flight_number || 'flight'}-${idx}`,
        price: {
          amount: f.best_price || 0,
          currency: '¥',
          formatted: `¥${(f.best_price || 0).toLocaleString('zh-CN')}`,
        },
        segments: effectiveSegments,
        departureTime: firstSeg.departure.time,
        arrivalTime: lastSeg.arrival.time,
        duration: formatDuration(f.duration_minutes),
        stops: f.stops || 0,
        stopIatas: f.layover_cities || [],
        carrierCodes: [...new Set(effectiveSegments.map(s => s.carrierCode))],
        carrierNames: [...new Set(effectiveSegments.map(s => s.carrierName))],
        cabinClass: f.cabin_class || '经济舱',
        platformPrices,
        platformLabels,
        bestPricePlatform: f.best_price_platform,
        platformCount: platformPrices.length,
        bookingLink: f.booking_urls[f.best_price_platform] || null,
        searchLinks: platformPrices.map(p => ({
          name: platformLabels[p.platform] || p.platform,
          platform: p.platform,
          url: p.bookingUrl || p.searchUrl || '',
          isDirectBook: p.platform === 'fliggy' && !!p.bookingUrl,
          isTargetedSearch: p.platform === 'ctrip' && p.bookingUrl && p.bookingUrl.includes('flightno='),
        })).filter(s => s.url),
      };
    });

    res.json({
      flights,
      meta: {
        count: flights.length,
        origin: originCode,
        destination: destCode,
        departureDate,
        sources: sourceResults.filter(s => s.success),
        allSources: sourceResults,
      },
    });
  } catch (err) {
    console.error('Search error:', err);
    res.status(500).json({ error: err.message || '服务器内部错误' });
  }
});

// ---- Airport autocomplete ----
app.get('/api/airports', (req, res) => {
  const { q } = req.query;
  if (!q || q.length < 1) return res.json([]);
  const query = q.toLowerCase();
  const results = [];
  for (const [name, code] of Object.entries(CITY_MAP)) {
    if ((name.toLowerCase().includes(query) || code.toLowerCase().includes(query))
        && !results.some(r => r.code === code)) {
      results.push({ name, code });
    }
  }
  const isChineseQuery = /[一-鿿]/.test(query);
  results.sort((a, b) => {
    const aCN = /[一-鿿]/.test(a.name);
    const bCN = /[一-鿿]/.test(b.name);
    if (isChineseQuery) return (bCN ? 0 : 1) - (aCN ? 0 : 1);
    return 0;
  });
  res.json(results.slice(0, 12));
});

// ---- Health check ----
app.get('/api/health', async (req, res) => {
  const sources = getAvailableSources();
  const statuses = await Promise.allSettled(
    sources.map(async s => ({ id: s.meta.id, name: s.meta.name, available: await s.isAvailable().catch(() => false) }))
  );
  res.json({
    status: 'ok',
    sources: statuses.map(r => r.status === 'fulfilled' ? r.value : null).filter(Boolean),
  });
});

// ---- SPA fallback (local only) ----
app.get('*', (req, res) => {
  res.sendFile(join(__dirname, 'public', 'index.html'));
});

// Only listen when running as standalone server
if (!process.env.NETLIFY) {
  app.listen(PORT, async () => {
    console.log(`Flight Query Server running at http://localhost:${PORT}`);
    console.log('Sources:', getAvailableSources().map(s => s.meta.name).join(', '));
    await ensureHeadlessChrome();
  });
}

export default app;
