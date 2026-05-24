/**
 * Flight merge logic — combines results from multiple OTA platforms by flight number.
 */

// Normalize flight number for matching
function normalizeFN(fn) {
  if (!fn) return '';
  const match = fn.match(/^([A-Z]{2}|[A-Z]\d|\d[A-Z])?(\d+)/);
  return match ? (match[1] || '') + match[2] : fn.toUpperCase().replace(/\s/g, '');
}

// Extract time from ISO string or HH:MM
function timeToMinutes(t) {
  if (!t) return null;
  const m = t.match(/(\d{2}):(\d{2})/);
  return m ? parseInt(m[1]) * 60 + parseInt(m[2]) : null;
}

const AIRLINE_ALIASES = {
  '山航': '山东航空', '国航': '中国国航', '东航': '东方航空', '南航': '南方航空',
  '海航': '海南航空', '厦航': '厦门航空', '川航': '四川航空', '深航': '深圳航空',
  '吉祥': '吉祥航空', '中联航': '中国联合航空',
  '新海航': '海南航空', '金鹏': '金鹏航空',
};
function normAirline(a) {
  let s = (a || '').replace(/\s+/g, '').replace(/[｜|]/g, '');
  if (AIRLINE_ALIASES[s]) s = AIRLINE_ALIASES[s];
  s = s.replace(/航空$/, '').replace(/^中国/, '');
  return s;
}

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
  const base = name.replace(/T\d+$/, '').trim();
  return AIRPORT_NAME_TO_IATA[base] || '';
}

function durationToMinutes(dur) {
  if (!dur) return 0;
  if (typeof dur === 'number') return dur;
  if (typeof dur !== 'string') return 0;
  const h = dur.match(/(\d+)h/);
  const m = dur.match(/(\d+)m/);
  if (h || m) {
    let mins = 0;
    if (h) mins += parseInt(h[1]) * 60;
    if (m) mins += parseInt(m[1]);
    return mins;
  }
  const n = parseInt(dur);
  if (!isNaN(n) && n > 0) return n;
  return 0;
}

/**
 * Merge Fliggy flights with OTA platform flights by flight number.
 * @param {Array} fliggyFlights - normalized Flight[] from Fliggy
 * @param {Object} otaResults - { ctrip: Flight[], qunar: Flight[], tongcheng: Flight[] }
 * @param {string} date - YYYY-MM-DD
 * @returns {Array} merged flights with platform pricing
 */
export function mergeFlightsByNumber(fliggyFlights, otaResults, date) {
  const merged = {};

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
    if (!merged[fn]) {
      merged[fn] = {
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
    merged[fn].prices.fliggy = f.price || 0;
    merged[fn].booking_urls.fliggy = f.jump_url || '';
    merged[fn].search_urls.fliggy = `https://www.fliggy.com/flight-search?departureCityCode=${f.origin_iata}&arrivalCityCode=${f.destination_iata}&departureDate=${date}`;
  }

  // Add OTA flights
  for (const [platform, flights] of Object.entries(otaResults)) {
    if (!Array.isArray(flights)) continue;
    for (const f of flights) {
      if (f.departure_time && /^\d{2}:\d{2}$/.test(f.departure_time)) {
        f.departure_time = `${date}T${f.departure_time}:00`;
      }
      if (f.arrival_time) {
        const tm = f.arrival_time.match(/^(\d{2}):(\d{2})(\+(\d+))?$/);
        if (tm) {
          const h = parseInt(tm[1]), m = parseInt(tm[2]), delta = parseInt(tm[4] || '0');
          if (delta > 0) {
            const d = new Date(date + 'T00:00:00');
            d.setDate(d.getDate() + delta);
            f.arrival_time = `${d.toISOString().slice(0, 10)}T${tm[1]}:${tm[2]}:00`;
          } else {
            f.arrival_time = `${date}T${tm[1]}:${tm[2]}:00`;
          }
        }
      }
      if (f.airline) f.airline = normAirline(f.airline).replace(/航空$/, '') + '航空';
      if (!f.depAirportCode && f.depAirport) f.depAirportCode = airportNameToIata(f.depAirport);
      if (!f.arrAirportCode && f.arrAirport) f.arrAirportCode = airportNameToIata(f.arrAirport);
      if (!f.duration_minutes && f.duration) f.duration_minutes = durationToMinutes(f.duration);

      let fn = normalizeFN(f.flight_number);

      if (!fn) {
        const otaDepMin = timeToMinutes(f.departure_time);
        const otaAirline = normAirline(f.airline);
        if (otaDepMin) {
          const match = fliggyByTime.find(ff =>
            ff.depMin !== null && Math.abs(ff.depMin - otaDepMin) <= 60 &&
            (otaAirline.includes(ff.airline) || ff.airline.includes(otaAirline) || ff.airline === otaAirline)
          );
          if (match) fn = match.fn;
        }
        if (!fn) continue;
      }

      if (!merged[fn]) {
        merged[fn] = {
          flight_number: f.flight_number || fn,
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
      const otaPrice = f.price || f.lowestPrice || 0;
      if (!merged[fn].prices[platform] || otaPrice < merged[fn].prices[platform]) {
        merged[fn].prices[platform] = otaPrice;
      }
      if (f.prices && f.prices.length > 0) {
        merged[fn].allPrices[platform] = f.prices;
      }
      if (f.booking_url) merged[fn].booking_urls[platform] = f.booking_url;
      if (f.search_url) merged[fn].search_urls[platform] = f.search_url;
      if (!merged[fn].airline && f.airline) merged[fn].airline = f.airline;
      if (!merged[fn].departure_time && f.departure_time) merged[fn].departure_time = f.departure_time;
      if (!merged[fn].arrival_time && f.arrival_time) merged[fn].arrival_time = f.arrival_time;
      if (!merged[fn].origin_iata && f.depAirportCode) merged[fn].origin_iata = f.depAirportCode;
      if (!merged[fn].destination_iata && f.arrAirportCode) merged[fn].destination_iata = f.arrAirportCode;
      if (!merged[fn].duration_minutes && f.duration_minutes) merged[fn].duration_minutes = f.duration_minutes;
      if (!merged[fn].aircraft_type && f.aircraft) merged[fn].aircraft_type = f.aircraft;
      if (!merged[fn].flight_number || merged[fn].flight_number === fn) {
        if (f.flight_number) merged[fn].flight_number = f.flight_number;
      }
      if (f.ctripSegments && f.ctripSegments.length > 0 && (!merged[fn].segments || merged[fn].segments.length === 0)) {
        merged[fn].segments = f.ctripSegments.map(seg => ({
          flight_number: seg.flight_number,
          origin_iata: seg.origin_iata,
          destination_iata: seg.destination_iata,
          departure: seg.departure,
          arrival: seg.arrival,
          duration_minutes: seg.duration_minutes,
          aircraft_type: seg.aircraft || '',
          terminal: seg.terminal || '',
        }));
      }
    }
  }

  return Object.values(merged).map(flight => {
    const platforms = Object.entries(flight.prices).filter(([, p]) => p > 0);
    platforms.sort(([, a], [, b]) => a - b);
    flight.best_price_platform = platforms[0]?.[0] || null;
    flight.best_price = platforms[0]?.[1] || null;
    flight.all_platforms = platforms.map(([name]) => name);
    return flight;
  });
}

export function extractCarrierCode(flightNumber) {
  if (!flightNumber) return '';
  const match = flightNumber.match(/^([A-Z]{2}|[A-Z]\d|[0-9][A-Z])/);
  return match ? match[1] : '';
}

export function formatDuration(minutes) {
  if (!minutes) return '';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h}h${m > 0 ? m + 'm' : ''}`;
}
