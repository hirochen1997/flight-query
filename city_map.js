/**
 * City name → IATA code mapping and resolution.
 */

export const CITY_MAP = {
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

// IATA city codes for multi-airport cities (used by Ctrip)
const AIRPORT_TO_CITY = {
  'PEK': 'BJS', 'PKX': 'BJS',
  'SHA': 'SHA', 'PVG': 'SHA',
  'CTU': 'CTU', 'TFU': 'CTU',
};

export function getCityCode(iata) {
  return AIRPORT_TO_CITY[iata] || iata;
}

export function resolveCity(input) {
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

export function resolveFliggyInput(input) {
  if (!input) return null;
  let trimmed = input.trim();
  const parenMatch = trimmed.match(/\(([A-Z]{3})\)$/i);
  if (parenMatch) {
    const cityPart = trimmed.replace(/\s*\(.*?\)\s*$/, '').trim();
    if (CITY_MAP[cityPart]) return cityPart;
    return parenMatch[1].toUpperCase();
  }
  if (/^[A-Z]{3}$/i.test(trimmed)) return trimmed.toUpperCase();
  if (CITY_MAP[trimmed]) return trimmed;
  const key = trimmed.toLowerCase();
  if (CITY_MAP[key]) return key;
  return null;
}
