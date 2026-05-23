import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { spawn } from 'child_process';
import { searchFliggy } from './fliggy.js';

const __dirname = (() => {
  try { return dirname(fileURLToPath(import.meta.url)); }
  catch { return process.cwd(); }
})();
const app = express();
const PORT = process.env.PORT || 3000;

// FlyClaw installation path (cloned to /tmp/FlyClaw) — kept as fallback
const FLYCLAW_DIR = '/tmp/FlyClaw';
const PYTHON = 'python3';

app.use(cors());
app.use(express.json());

// Static files (local dev only — Netlify serves public/ natively)
if (!process.env.NETLIFY) {
  app.use(express.static(join(__dirname, 'public'), {
    setHeaders: (res) => {
      res.set('Cache-Control', 'no-cache, no-store, must-revalidate');
      res.set('Pragma', 'no-cache');
      res.set('Expires', '0');
    },
  }));
}

// ---- City → IATA code mapping ----
const CITY_MAP = {
  '北京': 'PEK', 'beijing': 'PEK', '北京市': 'PEK',
  '上海': 'SHA', 'shanghai': 'SHA',
  '广州': 'CAN', 'guangzhou': 'CAN',
  '深圳': 'SZX', 'shenzhen': 'SZX',
  '成都': 'CTU', 'chengdu': 'CTU',
  '杭州': 'HGH', 'hangzhou': 'HGH',
  '重庆': 'CKG', 'chongqing': 'CKG',
  '西安': 'XIY', "xi'an": 'XIY', 'xian': 'XIY',
  '昆明': 'KMG', 'kunming': 'KMG',
  '南京': 'NKG', 'nanjing': 'NKG',
  '武汉': 'WUH', 'wuhan': 'WUH',
  '长沙': 'CSX', 'changsha': 'CSX',
  '厦门': 'XMN', 'xiamen': 'XMN',
  '三亚': 'SYX', 'sanya': 'SYX',
  '海口': 'HAK', 'haikou': 'HAK',
  '青岛': 'TAO', 'qingdao': 'TAO',
  '大连': 'DLC', 'dalian': 'DLC',
  '天津': 'TSN', 'tianjin': 'TSN',
  '郑州': 'CGO', 'zhengzhou': 'CGO',
  '济南': 'TNA', 'jinan': 'TNA',
  '哈尔滨': 'HRB', 'harbin': 'HRB',
  '沈阳': 'SHE', 'shenyang': 'SHE',
  '贵阳': 'KWE', 'guiyang': 'KWE',
  '南宁': 'NNG', 'nanning': 'NNG',
  '福州': 'FOC', 'fuzhou': 'FOC',
  '石家庄': 'SJW', 'shijiazhuang': 'SJW',
  '太原': 'TYN', 'taiyuan': 'TYN',
  '乌鲁木齐': 'URC', 'urumqi': 'URC',
  '兰州': 'LHW', 'lanzhou': 'LHW',
  '呼和浩特': 'HET', 'hohhot': 'HET',
  '银川': 'INC', 'yinchuan': 'INC',
  '西宁': 'XNN', 'xining': 'XNN',
  '拉萨': 'LXA', 'lhasa': 'LXA',
  '长春': 'CGQ', 'changchun': 'CGQ',
  '合肥': 'HFE', 'hefei': 'HFE',
  '南昌': 'KHN', 'nanchang': 'KHN',
  '桂林': 'KWL', 'guilin': 'KWL',
  '丽江': 'LJG', 'lijiang': 'LJG',
  '张家界': 'DYG', 'zhangjiajie': 'DYG',
  '香港': 'HKG', 'hongkong': 'HKG', 'hong kong': 'HKG',
  '澳门': 'MFM', 'macau': 'MFM', 'macao': 'MFM',
  '台北': 'TPE', 'taipei': 'TPE',
  '东京': 'NRT', 'tokyo': 'NRT',
  '大阪': 'KIX', 'osaka': 'KIX',
  '首尔': 'ICN', 'seoul': 'ICN',
  '曼谷': 'BKK', 'bangkok': 'BKK',
  '新加坡': 'SIN', 'singapore': 'SIN',
  '吉隆坡': 'KUL', 'kualalumpur': 'KUL',
  '伦敦': 'LHR', 'london': 'LHR',
  '巴黎': 'CDG', 'paris': 'CDG',
  '纽约': 'JFK', 'newyork': 'JFK', 'new york': 'JFK',
  '洛杉矶': 'LAX', 'losangeles': 'LAX', 'los angeles': 'LAX',
  '旧金山': 'SFO', 'sanfrancisco': 'SFO',
  '悉尼': 'SYD', 'sydney': 'SYD',
  '墨尔本': 'MEL', 'melbourne': 'MEL',
  '迪拜': 'DXB', 'dubai': 'DXB',
  '多哈': 'DOH', 'doha': 'DOH',
  '莫斯科': 'SVO', 'moscow': 'SVO',
};

// IATA city codes for multi-airport cities (used by Ctrip for city-level search)
const AIRPORT_TO_CITY = {
  'PEK': 'BJS', 'PKX': 'BJS',  // Beijing
  'SHA': 'SHA', 'PVG': 'SHA',  // Shanghai
  'CTU': 'CTU', 'TFU': 'CTU',  // Chengdu
};
function getCityCode(iata) {
  return AIRPORT_TO_CITY[iata] || iata;
}

function resolveCity(input) {
  if (!input) return null;
  let trimmed = input.trim();
  const parenMatch = trimmed.match(/\(([A-Z]{3})\)$/i);
  if (parenMatch) return parenMatch[1].toUpperCase();
  trimmed = trimmed.replace(/\s*\(.*?\)\s*$/, '').trim();
  if (/^[A-Z]{3}$/i.test(trimmed)) return trimmed.toUpperCase();
  const key = trimmed.toLowerCase();
  if (CITY_MAP[trimmed]) return CITY_MAP[trimmed];
  if (CITY_MAP[key]) return CITY_MAP[key];
  return null;
}

// Get city name for FlyClaw multi-airport search.
// Returns Chinese city name if input is a known city, otherwise the IATA code.
// FlyClaw's airport_manager.resolve_all() handles city→multi-airport expansion.
function resolveFliggyInput(input) {
  if (!input) return null;
  let trimmed = input.trim();
  // "北京 (PEK)" → extract city name "北京", pass to FlyClaw for multi-airport search
  const parenMatch = trimmed.match(/\(([A-Z]{3})\)$/i);
  if (parenMatch) {
    const cityPart = trimmed.replace(/\s*\(.*?\)\s*$/, '').trim();
    if (CITY_MAP[cityPart]) return cityPart;  // Known city → pass name
    return parenMatch[1].toUpperCase();       // Unknown → pass IATA
  }
  // Pure IATA code → pass as-is
  if (/^[A-Z]{3}$/i.test(trimmed)) return trimmed.toUpperCase();
  // City name → pass to FlyClaw for resolution
  if (CITY_MAP[trimmed]) return trimmed;
  const key = trimmed.toLowerCase();
  if (CITY_MAP[key]) return key;
  return null;
}

// ---- Fliggy search via Node.js MCP client ----
async function fliggySearch(params) {
  const cabinMap = {
    'ECONOMY': 'economy', 'PREMIUM_ECONOMY': 'premium',
    'BUSINESS': 'business', 'FIRST': 'first',
  };
  return searchFliggy({
    origin: params.origin,
    destination: params.destination,
    date: params.date,
    cabin: params.cabin ? cabinMap[params.cabin] || params.cabin : 'economy',
    stops: params.stops !== undefined ? params.stops : 'any',
    limit: params.limit,
    timeout: 15,
  });
}

// ---- FlyClaw subprocess call (Fliggy) — kept as fallback ----
function flyclawSearchLegacy(params) {
  return new Promise((resolve, reject) => {
    const args = [
      join(FLYCLAW_DIR, 'flyclaw.py'), 'search',
      '--from', params.origin,
      '--to', params.destination,
      '--date', params.date,
    ];
    if (params.stops !== undefined) args.push('--stops', String(params.stops));
    if (params.sort) args.push('--sort', params.sort);
    if (params.cabin) args.push('--cabin', params.cabin);
    if (params.adults) args.push('--adults', String(params.adults));
    if (params.children) args.push('--children', String(params.children));
    if (params.infants) args.push('--infants', String(params.infants));
    if (params.limit) args.push('--limit', String(params.limit));
    if (params.layoverMaxHours) args.push('--layover-max-hours', String(params.layoverMaxHours));

    console.log('FlyClaw args:', args.join(' '));

    const proc = spawn(PYTHON, args, {
      cwd: FLYCLAW_DIR,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      timeout: 45000,
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => { stdout += data.toString(); });
    proc.stderr.on('data', (data) => { stderr += data.toString(); });

    proc.on('close', (code) => {
      if (stderr) {
        const infoLines = stderr.split('\n').filter(l =>
          l.includes('[INFO]') || l.includes('[WARNING]') || l.includes('[Note]') || l.includes('[Error]')
        );
        if (infoLines.length > 0) console.log('FlyClaw:', infoLines.join('\n'));
      }

      if (code !== 0 && code !== null) {
        if (!stdout.trim()) {
          return reject(new Error(`FlyClaw exited with code ${code}: ${stderr.slice(-200)}`));
        }
      }

      try {
        const jsonMatch = stdout.match(/\[[\s\S]*\]/);
        if (!jsonMatch) {
          console.log('FlyClaw raw stdout:', stdout.slice(0, 500));
          return resolve([]);
        }
        const flights = JSON.parse(jsonMatch[0]);
        resolve(flights);
      } catch (err) {
        console.error('FlyClaw parse error:', err.message);
        resolve([]);
      }
    });

    proc.on('error', (err) => {
      reject(new Error(`Failed to start FlyClaw: ${err.message}`));
    });
  });
}

// ---- CDP Scraper subprocess call (Ctrip + Qunar via Chrome CDP) ----
// Requires Chrome running with --remote-debugging-port=9222 and user logged into platforms.
// Falls back to empty results if Chrome is not available.
function cdpScraperSearch(params) {
  return new Promise((resolve) => {
    const args = [
      join(__dirname, 'cdp_scraper.py'),
      params.origin,
      params.destination,
      params.date,
      '--json',
    ];

    console.log('CDP Scraper args:', args.join(' '));

    const proc = spawn(PYTHON, args, {
      cwd: __dirname,
      env: { ...process.env, NO_PROXY: 'localhost,127.0.0.1,::1', PYTHONIOENCODING: 'utf-8' },
      timeout: 180000,
    });

    let stdout = '';
    let stderr = '';

    proc.stdout.on('data', (data) => { stdout += data.toString(); });
    proc.stderr.on('data', (data) => { stderr += data.toString(); });

    proc.on('close', (code) => {
      if (stderr) console.log('CDP Scraper:', stderr.split('\n').filter(l => l.trim()).join('\n'));
      try {
        // Find JSON object in output (may be mixed with log lines)
        const jsonMatch = stdout.match(/\{[\s\S]*\}/);
        if (!jsonMatch) {
          console.log('CDP Scraper no JSON found in stdout');
          return resolve({ ctrip: [], qunar: [], tongcheng: [] });
        }
        const parsed = JSON.parse(jsonMatch[0]);
        resolve(parsed);
      } catch (err) {
        console.error('CDP Scraper parse error:', err.message);
        resolve({ ctrip: [], qunar: [], tongcheng: [] });
      }
    });

    proc.on('error', (err) => {
      console.error('CDP Scraper start error:', err.message);
      resolve({ ctrip: [], qunar: [], tongcheng: [] });
    });
  });
}

// ---- Merge flights from multiple platforms ----
function mergeFlightsByNumber(fliggyFlights, otaResults, date) {
  const merged = {};  // key: flight_number, value: { flight info, prices by platform }

  // Helper to normalize flight number for matching
  const normalizeFN = (fn) => {
    if (!fn) return '';
    const match = fn.match(/^([A-Z]{2}|[A-Z]\d|\d[A-Z])?(\d+)/);
    return match ? (match[1] || '') + match[2] : fn.toUpperCase().replace(/\s/g, '');
  };

  // Helper to extract time from ISO string or HH:MM
  const timeToMinutes = (t) => {
    if (!t) return null;
    const m = t.match(/(\d{2}):(\d{2})/);
    return m ? parseInt(m[1]) * 60 + parseInt(m[2]) : null;
  };

  // Airline abbreviation mapping
  const AIRLINE_ALIASES = {
    '山航': '山东航空', '国航': '中国国航', '东航': '东方航空', '南航': '南方航空',
    '海航': '海南航空', '厦航': '厦门航空', '川航': '四川航空', '深航': '深圳航空',
    '吉祥': '吉祥航空', '中联航': '中国联合航空',
    '新海航': '海南航空', '金鹏': '金鹏航空',
  };
  const normAirline = (a) => {
    let s = (a || '').replace(/\s+/g, '').replace(/[｜|]/g, '');
    // Expand abbreviations first
    if (AIRLINE_ALIASES[s]) s = AIRLINE_ALIASES[s];
    // Remove common suffixes/prefixes
    s = s.replace(/航空$/, '').replace(/^中国/, '');
    return s;
  };

  // Build Fliggy flight list for time-based matching (for OTA flights without flight numbers)
  const fliggyByTime = fliggyFlights.map(f => ({
    fn: normalizeFN(f.flight_number),
    depMin: timeToMinutes(f.scheduled_departure),
    arrMin: timeToMinutes(f.scheduled_arrival),
    origin: f.origin_iata,
    dest: f.destination_iata,
    airline: normAirline(f.airline),
    price: f.price || 0,
  }));

  // Add Fliggy flights
  for (const f of fliggyFlights) {
    const fn = normalizeFN(f.flight_number);
    if (!fn) continue;
    const key = fn;
    if (!merged[key]) {
      merged[key] = {
        flight_number: f.flight_number,
        airline: f.airline || '',
        origin_iata: f.origin_iata,
        destination_iata: f.destination_iata,
        departure_time: f.scheduled_departure,
        arrival_time: f.scheduled_arrival,
        stops: f.stops || 0,
        duration_minutes: f.duration_minutes,
        cabin_class: f.cabin_class || '',
        aircraft_type: f.aircraft_type || '',
        segments: f.segments || [],
        prices: {},
        allPrices: {},
        booking_urls: {},
        search_urls: {},
      };
    }
    merged[key].prices.fliggy = f.price || 0;
    merged[key].booking_urls.fliggy = f.jump_url || '';
    merged[key].search_urls.fliggy = `https://www.fliggy.com/flight-search?departureCityCode=${f.origin_iata}&arrivalCityCode=${f.destination_iata}&departureDate=${date}`;
  }

  // Airport name → IATA code (Chinese names from Qunar DOM)
  const AIRPORT_NAME_TO_IATA = {
    '白云机场': 'CAN', '白云': 'CAN', '广州白云': 'CAN',
    '首都机场': 'PEK', '首都': 'PEK', '北京首都': 'PEK',
    '大兴机场': 'PKX', '大兴': 'PKX', '北京大兴': 'PKX',
    '虹桥机场': 'SHA', '虹桥': 'SHA', '上海虹桥': 'SHA',
    '浦东机场': 'PVG', '浦东': 'PVG', '上海浦东': 'PVG',
    '宝安机场': 'SZX', '宝安': 'SZX', '深圳宝安': 'SZX',
    '双流机场': 'CTU', '双流': 'CTU', '成都双流': 'CTU',
    '天府机场': 'TFU', '天府': 'TFU', '成都天府': 'TFU',
    '萧山机场': 'HGH', '萧山': 'HGH', '杭州萧山': 'HGH',
    '江北机场': 'CKG', '江北': 'CKG', '重庆江北': 'CKG',
    '咸阳机场': 'XIY', '咸阳': 'XIY', '西安咸阳': 'XIY',
    '长水机场': 'KMG', '长水': 'KMG', '昆明长水': 'KMG',
    '禄口机场': 'NKG', '禄口': 'NKG', '南京禄口': 'NKG',
    '天河机场': 'WUH', '天河': 'WUH', '武汉天河': 'WUH',
    '黄花机场': 'CSX', '黄花': 'CSX', '长沙黄花': 'CSX',
    '高崎机场': 'XMN', '高崎': 'XMN', '厦门高崎': 'XMN',
    '凤凰机场': 'SYX', '凤凰': 'SYX', '三亚凤凰': 'SYX',
    '美兰机场': 'HAK', '美兰': 'HAK', '海口美兰': 'HAK',
    '胶东机场': 'TAO', '胶东': 'TAO', '青岛胶东': 'TAO',
    '流亭机场': 'TAO', '流亭': 'TAO',
    '周水子机场': 'DLC', '周水子': 'DLC', '大连周水子': 'DLC',
    '滨海机场': 'TSN', '滨海': 'TSN', '天津滨海': 'TSN',
    '新郑机场': 'CGO', '新郑': 'CGO', '郑州新郑': 'CGO',
    '遥墙机场': 'TNA', '遥墙': 'TNA', '济南遥墙': 'TNA',
    '太平机场': 'HRB', '太平': 'HRB', '哈尔滨太平': 'HRB',
    '桃仙机场': 'SHE', '桃仙': 'SHE', '沈阳桃仙': 'SHE',
    '龙洞堡机场': 'KWE', '龙洞堡': 'KWE', '贵阳龙洞堡': 'KWE',
    '吴圩机场': 'NNG', '吴圩': 'NNG', '南宁吴圩': 'NNG',
    '长乐机场': 'FOC', '长乐': 'FOC', '福州长乐': 'FOC',
    '正定机场': 'SJW', '正定': 'SJW', '石家庄正定': 'SJW',
    '武宿机场': 'TYN', '武宿': 'TYN', '太原武宿': 'TYN',
    '地窝堡机场': 'URC', '地窝堡': 'URC', '乌鲁木齐地窝堡': 'URC',
    '中川机场': 'LHW', '中川': 'LHW', '兰州中川': 'LHW',
    '白塔机场': 'HET', '白塔': 'HET', '呼和浩特白塔': 'HET',
    '河东机场': 'INC', '河东': 'INC', '银川河东': 'INC',
    '曹家堡机场': 'XNN', '曹家堡': 'XNN', '西宁曹家堡': 'XNN',
    '贡嘎机场': 'LXA', '贡嘎': 'LXA', '拉萨贡嘎': 'LXA',
    '龙嘉机场': 'CGQ', '龙嘉': 'CGQ', '长春龙嘉': 'CGQ',
    '新桥机场': 'HFE', '新桥': 'HFE', '合肥新桥': 'HFE',
    '昌北机场': 'KHN', '昌北': 'KHN', '南昌昌北': 'KHN',
    '两江机场': 'KWL', '两江': 'KWL', '桂林两江': 'KWL',
    '三义机场': 'LJG', '三义': 'LJG', '丽江三义': 'LJG',
    '荷花机场': 'DYG', '荷花': 'DYG', '张家界荷花': 'DYG',
    '成田机场': 'NRT', '关西机场': 'KIX', '羽田机场': 'HND',
    '仁川机场': 'ICN', '素万那普机场': 'BKK',
    '樟宜机场': 'SIN', '希思罗机场': 'LHR',
    '戴高乐机场': 'CDG', '肯尼迪机场': 'JFK',
  };
  function airportNameToIata(name) {
    if (!name) return '';
    // Strip terminal suffix: 白云机场T2 → 白云机场
    const base = name.replace(/T\d+$/, '').trim();
    return AIRPORT_NAME_TO_IATA[base] || '';
  }
  // Duration to minutes: handles numeric (Ctrip: 125 = 125 minutes),
  // string like "3h5m" (Qunar), or already ISO/empty
  function durationToMinutes(dur) {
    if (!dur) return 0;
    if (typeof dur === 'number') return dur;  // Ctrip: raw minutes
    if (typeof dur !== 'string') return 0;
    // "3h5m" or "2h15m" format (Qunar)
    const h = dur.match(/(\d+)h/);
    const m = dur.match(/(\d+)m/);
    if (h || m) {
      let mins = 0;
      if (h) mins += parseInt(h[1]) * 60;
      if (m) mins += parseInt(m[1]);
      return mins;
    }
    // Try plain number string "125"
    const n = parseInt(dur);
    if (!isNaN(n) && n > 0) return n;
    return 0;
  }

  // Add OTA platform flights
  for (const [platform, flights] of Object.entries(otaResults)) {
    if (!Array.isArray(flights)) continue;
    for (const f of flights) {
      // Normalize times BEFORE use: bare "HH:MM" or "HH:MM+1" → ISO datetime
      if (f.departure_time && /^\d{2}:\d{2}$/.test(f.departure_time)) {
        f.departure_time = `${date}T${f.departure_time}:00`;
      }
      if (f.arrival_time) {
        const tm = f.arrival_time.match(/^(\d{2}):(\d{2})(\+(\d+))?$/);
        if (tm) {
          const h = parseInt(tm[1]), m = parseInt(tm[2]), delta = parseInt(tm[4] || '0');
          if (delta > 0) {
            // +1 day → adjust date
            const d = new Date(date + 'T00:00:00');
            d.setDate(d.getDate() + delta);
            const iso = d.toISOString().slice(0, 10);
            f.arrival_time = `${iso}T${tm[1]}:${tm[2]}:00`;
          } else {
            f.arrival_time = `${date}T${tm[1]}:${tm[2]}:00`;
          }
        }
      }
      // Normalize airline: Qunar uses "｜" separator (e.g. "新海航｜海南航空")
      if (f.airline) f.airline = normAirline(f.airline).replace(/航空$/, '') + '航空';
      // Map airport names to IATA codes (Qunar uses Chinese names)
      if (!f.depAirportCode && f.depAirport) f.depAirportCode = airportNameToIata(f.depAirport);
      if (!f.arrAirportCode && f.arrAirport) f.arrAirportCode = airportNameToIata(f.arrAirport);
      // Convert duration string to minutes
      if (!f.duration_minutes && f.duration) f.duration_minutes = durationToMinutes(f.duration);

      let fn = normalizeFN(f.flight_number);

      // If no flight number, try to match by time + airline
      if (!fn) {
        const otaDepMin = timeToMinutes(f.departure_time);
        const otaAirline = normAirline(f.airline);
        if (otaDepMin) {
          const match = fliggyByTime.find(ff =>
            ff.depMin !== null && Math.abs(ff.depMin - otaDepMin) <= 60 &&
            (otaAirline.includes(ff.airline) || ff.airline.includes(otaAirline) || ff.airline === otaAirline)
          );
          if (match) {
            fn = match.fn;
          }
        }
        if (!fn) continue; // Can't match without flight number
      }

      const key = fn;
      if (!merged[key]) {
        merged[key] = {
          flight_number: f.flight_number || key,
          airline: f.airline || '',
          origin_iata: f.origin_iata || f.depAirportCode || '',
          destination_iata: f.destination_iata || f.arrAirportCode || '',
          departure_time: f.departure_time,
          arrival_time: f.arrival_time,
          stops: f.stops || 0,
          duration_minutes: f.duration_minutes || 0,
          aircraft_type: f.aircraft || '',
          prices: {},
          allPrices: {},
          booking_urls: {},
          search_urls: {},
        };
      }
      // Use lowest price for comparison, store all price variants
      const otaPrice = f.price || f.lowestPrice || 0;
      if (!merged[key].prices[platform] || otaPrice < merged[key].prices[platform]) {
        merged[key].prices[platform] = otaPrice;
      }
      if (f.prices && f.prices.length > 0) {
        merged[key].allPrices[platform] = f.prices;
      }
      if (f.booking_url) merged[key].booking_urls[platform] = f.booking_url;
      if (f.search_url) merged[key].search_urls[platform] = f.search_url;
      // Fill in missing info from OTA data (with normalized values)
      if (!merged[key].airline && f.airline) merged[key].airline = f.airline;
      if (!merged[key].departure_time && f.departure_time) merged[key].departure_time = f.departure_time;
      if (!merged[key].arrival_time && f.arrival_time) merged[key].arrival_time = f.arrival_time;
      if (!merged[key].origin_iata && f.depAirportCode) merged[key].origin_iata = f.depAirportCode;
      if (!merged[key].destination_iata && f.arrAirportCode) merged[key].destination_iata = f.arrAirportCode;
      if (!merged[key].duration_minutes && f.duration_minutes) merged[key].duration_minutes = f.duration_minutes;
      if (!merged[key].aircraft_type && f.aircraft) merged[key].aircraft_type = f.aircraft;
      if (!merged[key].flight_number || merged[key].flight_number === key) {
        if (f.flight_number) merged[key].flight_number = f.flight_number;
      }
    }
  }

  // Calculate best price platform
  return Object.values(merged).map(flight => {
    const platforms = Object.entries(flight.prices).filter(([, p]) => p > 0);
    platforms.sort(([, a], [, b]) => a - b);
    flight.best_price_platform = platforms[0]?.[0] || null;
    flight.best_price = platforms[0]?.[1] || null;
    flight.all_platforms = platforms.map(([name]) => name);
    return flight;
  });
}

// ---- Flight Search Endpoint ----
app.post('/api/search', async (req, res) => {
  try {
    const {
      origin,
      destination,
      departureDate,
      adults = 1,
      children = 0,
      infants = 0,
      travelClass,
      nonStop = false,
      maxPrice,
      airlines,
      maxResults = 20,
    } = req.body;

    if (!origin || !destination || !departureDate) {
      return res.status(400).json({ error: '出发地、目的地和出发日期为必填项' });
    }

    const originCode = resolveCity(origin);
    const destCode = resolveCity(destination);
    if (!originCode) return res.status(400).json({ error: `无法识别出发地: ${origin}，请输入城市名或三字码` });
    if (!destCode) return res.status(400).json({ error: `无法识别目的地: ${destination}，请输入城市名或三字码` });
    if (originCode === destCode) return res.status(400).json({ error: '出发地和目的地不能相同' });

    // FlyClaw can resolve city names to all airports (e.g. 北京→PEK+PKX)
    const fliggyOrigin = resolveFliggyInput(origin);
    const fliggyDest = resolveFliggyInput(destination);

    console.log(`Searching: ${originCode} → ${destCode} on ${departureDate} (Fliggy: ${fliggyOrigin} → ${fliggyDest})`);

    const cabinMap = {
      'ECONOMY': 'economy',
      'PREMIUM_ECONOMY': 'premium',
      'BUSINESS': 'business',
      'FIRST': 'first',
    };

    // Fire both searches in parallel
    const flyclawParams = {
      origin: fliggyOrigin,
      destination: fliggyDest,
      date: departureDate,
      stops: nonStop ? 0 : 'any',
      sort: 'cheapest',
      cabin: travelClass ? cabinMap[travelClass] : undefined,
      adults,
      children,
      infants,
      limit: maxResults,
    };

    const originCityCode = getCityCode(originCode);
    const destCityCode = getCityCode(destCode);
    console.log(`City codes for Ctrip: ${originCityCode} → ${destCityCode}`);

    const [fliggyFlights, otaResults] = await Promise.all([
      fliggySearch(flyclawParams).catch(err => {
        console.error('Fliggy search failed:', err.message, err.stack);
        return [];
      }),
      cdpScraperSearch({
        origin: origin,           // City name or IATA code (scraper handles both)
        destination: destination,
        date: departureDate,
      }).catch(err => {
        console.error('CDP scraper failed:', err.message);
        return { ctrip: [], qunar: [], tongcheng: [] };
      }),
    ]);

    console.log(`FlyClaw: ${fliggyFlights.length} flights, OTA: ctrip=${otaResults.ctrip?.length || 0} qunar=${otaResults.qunar?.length || 0} tongcheng=${otaResults.tongcheng?.length || 0}`);

    // Merge by flight number
    const mergedFlights = mergeFlightsByNumber(fliggyFlights, otaResults, departureDate);

    // Filter by max price and specific airlines if requested
    let filteredFlights = mergedFlights;
    if (maxPrice) {
      filteredFlights = filteredFlights.filter(f => f.best_price <= parseFloat(maxPrice));
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
    const flights = filteredFlights.map((f, idx) => {
      const segments = (f.segments || []).map(seg => ({
        id: seg.flight_number,
        departure: {
          iata: seg.origin_iata,
          time: seg.departure,
          terminal: seg.terminal,
        },
        arrival: {
          iata: seg.destination_iata,
          time: seg.arrival,
          terminal: seg.terminal,
        },
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

      // Build platform price list
      const platformPrices = Object.entries(f.prices)
        .filter(([, p]) => p > 0)
        .map(([platform, price]) => ({
          platform,
          price,
          formatted: `¥${price.toLocaleString('zh-CN')}`,
          isBest: platform === f.best_price_platform,
          bookingUrl: f.booking_urls[platform] || null,
          searchUrl: f.search_urls[platform] || null,
          // Include all price variants for this platform (Ctrip returns multiple cabins/discounts)
          allPrices: f.allPrices[platform] || [],
        }))
        .sort((a, b) => a.price - b.price);

      const platformLabels = {
        fliggy: '飞猪',
        ctrip: '携程',
        qunar: '去哪儿',
        tongcheng: '同程旅行',
      };

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
        // Multi-platform data
        platformPrices,
        platformLabels,
        bestPricePlatform: f.best_price_platform,
        platformCount: platformPrices.length,
        // Direct booking link from best price platform (Fliggy only - has jumpUrl)
        bookingLink: f.booking_urls[f.best_price_platform] || null,
        // Per-platform links: direct booking for Fliggy, search for others
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
        source: 'fliggy+ctrip',
      },
    });
  } catch (err) {
    console.error('Search error:', err);
    res.status(500).json({ error: err.message || '服务器内部错误' });
  }
});

function extractCarrierCode(flightNumber) {
  if (!flightNumber) return '';
  const match = flightNumber.match(/^([A-Z]{2}|[A-Z]\d|[0-9][A-Z])/);
  return match ? match[1] : '';
}

function formatDuration(minutes) {
  if (!minutes) return '';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h}h${m > 0 ? m + 'm' : ''}`;
}

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
  res.json({ status: 'ok', message: '飞猪航班数据 (Fliggy MCP)' });
});

// ---- SPA fallback (local only) ----
app.get('*', (req, res) => {
  res.sendFile(join(__dirname, 'public', 'index.html'));
});

// Only listen when running as standalone server
if (!process.env.NETLIFY) {
  app.listen(PORT, () => {
    console.log(`Flight Query Server running at http://localhost:${PORT}`);
    console.log('Data source: Fliggy MCP (Node.js) + Ctrip/Qunar (Chrome CDP)');
  });
}

export default app;
