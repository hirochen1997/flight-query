#!/usr/bin/env python3
"""CDP-based flight scraper using Ctrip internal API via fetch interception.

Connects to dedicated headless Chrome on port 9223 (separate from user's Chrome).
Uses Page.addScriptToEvaluateOnNewDocument to intercept batchSearch API responses
before the SPA loads, capturing complete structured flight data.

Usage:
    python3 cdp_scraper.py <origin_city> <dest_city> <date> [--platform ctrip] [--json]
    python3 cdp_scraper.py 北京 上海 2026-06-20
"""

CDP_PORT = 9223  # Dedicated headless Chrome, NOT the user's Chrome on 9222

import asyncio
import json
import os
import re
import time
import sys
import subprocess
import urllib.parse
import urllib.request
import websockets

os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")

# --- City name to IATA code mapping ---
CITY_TO_IATA = {
    "北京": ("BJS", "北京"),
    "上海": ("SHA", "上海"),
    "广州": ("CAN", "广州"),
    "深圳": ("SZX", "深圳"),
    "成都": ("CTU", "成都"),
    "杭州": ("HGH", "杭州"),
    "南京": ("NKG", "南京"),
    "武汉": ("WUH", "武汉"),
    "西安": ("SIA", "西安"),
    "重庆": ("CKG", "重庆"),
    "青岛": ("TAO", "青岛"),
    "厦门": ("XMN", "厦门"),
    "长沙": ("CSX", "长沙"),
    "昆明": ("KMG", "昆明"),
    "天津": ("TSN", "天津"),
    "大连": ("DLC", "大连"),
    "三亚": ("SYX", "三亚"),
    "海口": ("HAK", "海口"),
    "哈尔滨": ("HRB", "哈尔滨"),
    "沈阳": ("SHE", "沈阳"),
    "郑州": ("CGO", "郑州"),
    "贵阳": ("KWE", "贵阳"),
    "南宁": ("NNG", "南宁"),
    "乌鲁木齐": ("URC", "乌鲁木齐"),
}


def resolve_city_name(input_str):
    """Resolve a city name or IATA code to Chinese city name."""
    if not input_str:
        return "北京"
    if any('一' <= c <= '鿿' for c in input_str):
        return input_str.strip()
    iata_to_cn = {v[0]: k for k, v in CITY_TO_IATA.items()}
    return iata_to_cn.get(input_str.strip().upper(), input_str.strip())


# --- CDP Helpers ---

async def cdp_send(ws, method, params=None, timeout=30):
    msg_id = int(time.time() * 1000000) % 1000000000
    msg = {"id": msg_id, "method": method, "params": params or {}}
    await ws.send(json.dumps(msg))
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = deadline - time.time()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 3))
        except asyncio.TimeoutError:
            continue
        resp = json.loads(raw)
        if resp.get("id") == msg_id:
            if resp.get("error"):
                raise Exception(f"CDP error: {resp['error']}")
            return resp
    raise TimeoutError(f"CDP timeout: {method}")


async def cdp_eval(ws, expression, timeout=15):
    result = await cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
    }, timeout=timeout)
    exc = result.get("result", {}).get("exceptionDetails")
    if exc:
        raise Exception(f"JS exception: {exc}")
    return result.get("result", {}).get("result", {}).get("value")


def create_blank_page():
    """Create a blank CDP tab on dedicated headless Chrome, return its info dict."""
    raw = subprocess.check_output(
        ["curl", "-s", "-X", "PUT", f"http://localhost:{CDP_PORT}/json/new?about:blank"],
        text=True,
    )
    return json.loads(raw)


def create_page_with_url(url):
    """Create a CDP tab on dedicated headless Chrome, return its info dict."""
    encoded = urllib.parse.quote(url, safe="")
    raw = subprocess.check_output(
        ["curl", "-s", "-X", "PUT", f"http://localhost:{CDP_PORT}/json/new?{encoded}"],
        text=True,
    )
    return json.loads(raw)


# --- Ctrip API-based search ---

def parse_ctrip_api_flight(itinerary):
    """Parse a single Ctrip flight itinerary from batchSearch API response.

    Returns a list with ONE flight entry per itinerary (not per segment).
    For connecting flights, all segments are combined into one journey entry.

    priceList is at the ITINERARY level.
    Each price references segmentNo/sequenceNo via priceUnitList[].flightSeatList[].
    """
    segments_raw = itinerary.get("flightSegments", [])
    if not segments_raw:
        return []

    all_prices = itinerary.get("priceList", [])

    # Collect all flights across all segments
    all_flights_in_itinerary = []
    total_duration = 0
    total_stops = 0

    for seg in segments_raw:
        flight_list = seg.get("flightList", [])
        seg_no = seg.get("segmentNo", 1)
        seg_duration = seg.get("duration", 0)
        total_duration += seg_duration
        total_stops = max(total_stops, seg.get("stopCount", 0))

        for fl in flight_list:
            all_flights_in_itinerary.append({
                "flightNo": fl.get("flightNo", ""),
                "departureDateTime": fl.get("departureDateTime", ""),
                "arrivalDateTime": fl.get("arrivalDateTime", ""),
                "departureAirportCode": fl.get("departureAirportCode", ""),
                "arrivalAirportCode": fl.get("arrivalAirportCode", ""),
                "departureAirportName": fl.get("departureAirportName", ""),
                "arrivalAirportName": fl.get("arrivalAirportName", ""),
                "departureTerminal": fl.get("departureTerminal", ""),
                "arrivalTerminal": fl.get("arrivalTerminal", ""),
                "aircraftName": fl.get("aircraftName", ""),
                "aircraftCode": fl.get("aircraftCode", ""),
                "operateFlightNo": fl.get("operateFlightNo", ""),
                "operateAirlineName": fl.get("operateAirlineName", ""),
                "sequenceNo": fl.get("sequenceNo", 1),
                "segmentNo": seg_no,
                "duration": seg_duration,
            })

    if not all_flights_in_itinerary:
        return []

    first_flight = all_flights_in_itinerary[0]
    last_flight = all_flights_in_itinerary[-1]

    # Build combined flight entry
    flight = {
        "airline": segments_raw[0].get("airlineName", ""),
        "flight_number": first_flight["flightNo"],
        "aircraft": first_flight.get("aircraftName", ""),
        "departure_time": _format_datetime(first_flight["departureDateTime"]),
        "arrival_time": _format_datetime(last_flight["arrivalDateTime"]),
        "depAirport": first_flight["departureAirportName"],
        "arrAirport": last_flight["arrivalAirportName"],
        "depAirportCode": first_flight["departureAirportCode"],
        "arrAirportCode": last_flight["arrivalAirportCode"],
        "depTerminal": first_flight.get("departureTerminal", ""),
        "arrTerminal": last_flight.get("arrivalTerminal", ""),
        "duration": total_duration,
        "stops": total_stops,
        "crossDays": segments_raw[-1].get("crossDays", 0),
        "platform": "ctrip",
        # Build segments for multi-leg display
        "ctripSegments": [{
            "flight_number": f["flightNo"],
            "origin_iata": f["departureAirportCode"],
            "destination_iata": f["arrivalAirportCode"],
            "departure": _format_datetime(f["departureDateTime"]),
            "arrival": _format_datetime(f["arrivalDateTime"]),
            "duration_minutes": f["duration"],
            "aircraft": f.get("aircraftName", ""),
            "airline": f.get("operateAirlineName", "") or segments_raw[0].get("airlineName", ""),
            "terminal": f.get("departureTerminal", ""),
        } for f in all_flights_in_itinerary],
    }

    # Extract all price variants for this itinerary.
    # Match prices to the itinerary (segmentNo 1, sequenceNo 1 typically).
    prices = []
    for p in all_prices:
        adult_price = p.get("adultPrice", 0)
        if adult_price <= 0:
            continue

        discount_rate = 0
        product_types = []
        seat_class = ""
        price_matches = not p.get("priceUnitList")

        if not price_matches:
            for pu in p.get("priceUnitList", []):
                for seat in pu.get("flightSeatList", []):
                    # Match to first segment's first flight by default
                    if seat.get("segmentNo") == 1 and seat.get("sequenceNo") == 1:
                        price_matches = True
                        discount_rate = seat.get("discountRate", 0)
                        product_types = seat.get("productTypes", [])
                        seat_class = seat.get("seatClass", "")

        if not price_matches:
            continue

        # Get baggage info
        baggage_info = ""
        if "baggage" in p:
            for bl in p["baggage"].get("dataList", []):
                if bl.get("segmentNo") == 1 and bl.get("sequenceNo") == 1:
                    adult = bl.get("adultBaggage", {})
                    checked = adult.get("checkedBaggage", {})
                    if checked.get("hasFreeBaggage"):
                        baggage_info = checked.get("baggageContent", "")

        prices.append({
            "price": adult_price,
            "childPrice": p.get("childPrice", 0),
            "cabin": p.get("cabin", ""),
            "seatClass": seat_class,
            "discountRate": discount_rate,
            "discount": f"{discount_rate * 10:.1f}折" if discount_rate else "",
            "productTypes": product_types,
            "baggage": baggage_info,
            "invoiceType": p.get("invoiceType", ""),
            "miseryIndex": p.get("miseryIndex", 0),
            "routeSearchToken": p.get("routeSearchToken", ""),
        })

    # Sort and deduplicate
    prices.sort(key=lambda x: x["price"])
    seen = set()
    unique_prices = []
    for p in prices:
        key = (p["price"], p["cabin"], p["seatClass"])
        if key not in seen:
            seen.add(key)
            unique_prices.append(p)
    prices = unique_prices

    if prices:
        flight["price"] = prices[0]["price"]
        flight["lowestPrice"] = prices[0]["price"]
    else:
        flight["price"] = 0
        flight["lowestPrice"] = 0

    flight["prices"] = prices
    flight["priceCount"] = len(prices)

    # Build search URL (use first flight's info)
    fn = flight["flight_number"]
    dep_code = first_flight["departureAirportCode"]
    arr_code = last_flight["arrivalAirportCode"]
    dep_date = first_flight["departureDateTime"][:10] if first_flight["departureDateTime"] else ""
    flight["search_url"] = (
        f"https://flights.ctrip.com/online/list/oneway-{dep_code.lower()}-{arr_code.lower()}"
        f"?depdate={dep_date}&flightno={fn}"
    )

    return [flight]


def _format_datetime(datetime_str):
    """Convert Ctrip '2026-06-20 20:00:00' to ISO '2026-06-20T20:00:00'."""
    if not datetime_str:
        return ""
    return datetime_str.replace(" ", "T")


def _extract_time(datetime_str):
    """Extract HH:MM from '2026-06-20 20:00:00' format."""
    if not datetime_str:
        return ""
    parts = datetime_str.split(" ")
    if len(parts) >= 2:
        return parts[1][:5]
    return datetime_str


async def search_ctrip(origin_iata, dest_iata, date_str, ws_url=None, ws=None):
    """Search Ctrip flights via batchSearch API interception.

    Uses Fetch.enable to pause all responses, read the batchSearch response body
    directly via CDP, then parse flight data. More reliable than JS injection.
    Uses a single message-read loop so CDP command responses are routed correctly.
    """
    url = f"https://flights.ctrip.com/itinerary/oneway/{origin_iata.lower()}-{dest_iata.lower()}?date={date_str}"
    print(f"[Ctrip] {origin_iata}->{dest_iata} {date_str}")

    own_ws = False
    page_id = None
    if ws is None:
        own_ws = True
        page_info = create_blank_page()
        page_id = page_info.get("id", "")
        ws_url = page_info["webSocketDebuggerUrl"]
        ws = await websockets.connect(ws_url, max_size=100 * 1024 * 1024)

    try:
        await cdp_send(ws, "Page.enable")
        await cdp_send(ws, "Network.enable")
        await cdp_send(ws, "Runtime.enable")

        # Install JS fetch + XHR interceptor as the PRIMARY capture method.
        # This runs before any page scripts and is the simplest reliable approach.
        # We also enable Fetch domain as backup to pause responses at CDP level.
        intercept_js = """
        (function() {
            window.__ctripFlightData = null;
            window.__ctripFetchDone = false;
            var _origFetch = window.fetch;
            window.fetch = function() {
                var args = arguments;
                var url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                var p = _origFetch.apply(this, args);
                if (url.indexOf('batchSearch') !== -1 && url.indexOf('search/api/search') !== -1) {
                    p.then(function(resp) {
                        var cloned = resp.clone();
                        cloned.json().then(function(data) {
                            if (data && data.data && data.data.flightItineraryList) {
                                window.__ctripFlightData = data.data.flightItineraryList;
                                window.__ctripFetchDone = true;
                            }
                        }).catch(function(){});
                    });
                }
                return p;
            };
            // Also intercept XHR (some Ctrip pages use XMLHttpRequest)
            var _origXHROpen = XMLHttpRequest.prototype.open;
            var _origXHRSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this.__ctrip_url = url;
                return _origXHROpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function() {
                var self = this;
                var url = self.__ctrip_url || '';
                this.addEventListener('load', function() {
                    if (url.indexOf('batchSearch') !== -1 && url.indexOf('search/api/search') !== -1) {
                        try {
                            var data = JSON.parse(self.responseText);
                            if (data && data.data && data.data.flightItineraryList) {
                                window.__ctripFlightData = data.data.flightItineraryList;
                                window.__ctripFetchDone = true;
                            }
                        } catch(e) {}
                    }
                });
                return _origXHRSend.apply(this, arguments);
            };
        })()
        """
        await cdp_send(ws, "Page.addScriptToEvaluateOnNewDocument", {
            "source": intercept_js,
        })

        # Navigate to trigger the API calls
        await cdp_send(ws, "Page.navigate", {"url": url})

        # Wait for flight data (fast polling first 3s, then slower)
        # Two strategies interleaved: check JS capture, and check Network domain
        flight_count = 0
        captured_itineraries = []

        for i in range(12):
            await asyncio.sleep(0.5 if i < 6 else 1.0)
            try:
                raw_len = await cdp_eval(
                    ws,
                    "window.__ctripFlightData ? window.__ctripFlightData.length : 0",
                    timeout=3,
                )
                if raw_len and raw_len > 0:
                    elapsed = (i + 1) * 0.5 if i < 6 else 3 + (i - 5) * 1.0
                    if isinstance(raw_len, int) and raw_len > flight_count:
                        print(f"[Ctrip] JS capture: {raw_len} itineraries ({elapsed:.1f}s)")
                        flight_count = raw_len
                        break
            except Exception:
                pass

        if not flight_count:
            # Diagnose what went wrong
            try:
                page_url = await cdp_eval(ws, "window.location.href", timeout=3)
                print(f"[Ctrip] No API data. Page URL: {page_url}")
                doc_ready = await cdp_eval(ws, "document.readyState", timeout=3)
                print(f"[Ctrip] document.readyState: {doc_ready}")
                # Check if network requests are happening
                perf_entries = await cdp_eval(
                    ws,
                    "JSON.stringify(performance.getEntriesByType('resource').map(function(e) { return e.name; }).filter(function(n) { return n.indexOf('batchSearch') !== -1 || n.indexOf('search') !== -1; }))",
                    timeout=3,
                )
                if perf_entries:
                    print(f"[Ctrip] Related network requests: {perf_entries}")
            except Exception as e:
                print(f"[Ctrip] Diagnostics error: {e}")

        # Extract flight data from JS capture
        all_flights = []
        if flight_count > 0:
            batch_size = 50
            for offset in range(0, flight_count, batch_size):
                try:
                    raw = await cdp_eval(
                        ws,
                        f"JSON.stringify(window.__ctripFlightData.slice({offset}, {offset + batch_size}))",
                        timeout=15,
                    )
                    if raw:
                        itineraries = json.loads(raw)
                        captured_itineraries.extend(itineraries)
                except Exception as e:
                    print(f"[Ctrip] Extract error at offset {offset}: {e}")
                    break

            for it in captured_itineraries:
                flights = parse_ctrip_api_flight(it)
                all_flights.extend(flights)

        print(f"[Ctrip] Parsed {len(all_flights)} flights with price data")
        return all_flights

    finally:
        if own_ws and ws:
            await ws.close()
        if own_ws and page_id:
            try:
                urllib.request.urlopen(
                    f"http://localhost:{CDP_PORT}/json/close/{page_id}"
                )
            except Exception:
                pass


async def search_ctrip_dom(ws, origin_iata, dest_iata, date_str):
    """Fallback: DOM-based Ctrip scraping."""
    await cdp_send(ws, "Page.navigate", {"url": f"https://flights.ctrip.com/itinerary/oneway/{origin_iata.lower()}-{dest_iata.lower()}?date={date_str}"})

    for i in range(8):
        await asyncio.sleep(1)
        try:
            count = await cdp_eval(ws, "document.querySelectorAll('.flight-item').length", timeout=5)
            if count:
                break
        except Exception:
            pass

    raw_flights = await cdp_eval(ws, """
        (function() {
            var items = document.querySelectorAll('.flight-item');
            return JSON.stringify(Array.from(items).map(function(i) {
                return i.innerText.trim().replace(/\\n/g, ' | ');
            }));
        })()
    """, timeout=10)

    raw_list = json.loads(raw_flights) if raw_flights else []
    flights = [_parse_ctrip_dom(f) for f in raw_list]
    flights = [f for f in flights if f.get("flight_number")]
    return flights


def _parse_ctrip_dom(raw_text):
    """Parse Ctrip flight item innerText (fallback)."""
    text = raw_text.strip()
    lines = [l.strip() for l in text.split("|") if l.strip()]
    flight = {"airline": "", "flight_number": "", "aircraft": "",
              "departure_time": "", "depAirport": "", "arrival_time": "",
              "arrAirport": "", "price": 0, "cabin": "", "discount": "", "platform": "ctrip"}
    if lines:
        flight["airline"] = lines[0]
    if len(lines) > 1:
        fn_match = re.search(r"\b([A-Z]{2}\d{3,4}|[A-Z]\d{4,5})\b", lines[1])
        if not fn_match:
            fn_match = re.search(r"([A-Z]{2}\d{3,4})", lines[1])
        if fn_match:
            flight["flight_number"] = fn_match.group(1)
        ac_match = re.search(r"([一-鿿]+(?:-?\d+)?\s*\([大小中]\)|[一-鿿]+\d+.*)", lines[1])
        if ac_match:
            flight["aircraft"] = ac_match.group(1).strip()

    times = []
    airports = []
    for line in lines:
        if re.match(r"^\d{1,2}:\d{2}$", line):
            times.append(line)
        elif re.match(r"^[一-鿿]+机场T?\d*$", line):
            airports.append(line)
    if len(times) >= 2:
        flight["departure_time"] = times[0]
        flight["arrival_time"] = times[1]
    if len(airports) >= 2:
        flight["depAirport"] = airports[0]
        flight["arrAirport"] = airports[1]

    price_match = re.search(r"¥([\d,]+)", text)
    if price_match:
        flight["price"] = int(price_match.group(1).replace(",", ""))
    discount_match = re.search(r"经济舱([\d.]+折)", text)
    if discount_match:
        flight["discount"] = discount_match.group(1)
        flight["cabin"] = "经济舱"
    return flight


# --- Qunar search ---

async def search_qunar(origin_cn, dest_cn, date_str, ws_url=None, ws=None):
    """Search Qunar flights via DOM scraping.

    Qunar's internal API has strong anti-bot protection (device fingerprinting,
    challenge-response). DOM scraping is more reliable for now.
    """
    url = (
        f"https://flight.qunar.com/site/oneway_list.htm"
        f"?fromCity={urllib.parse.quote(origin_cn)}"
        f"&toCity={urllib.parse.quote(dest_cn)}"
        f"&fromDate={date_str}"
    )
    print(f"[Qunar] {origin_cn}->{dest_cn} {date_str}")

    own_ws = False
    if ws is None:
        own_ws = True
        page_info = create_page_with_url(url)
        ws_url = page_info["webSocketDebuggerUrl"]
        ws = await websockets.connect(ws_url, max_size=10 * 1024 * 1024)

    try:
        await cdp_send(ws, "Page.enable")
        await cdp_send(ws, "Runtime.enable")

        for i in range(20):
            await asyncio.sleep(2)
            try:
                count = await cdp_eval(ws, "document.querySelectorAll('div.b-airfly').length", timeout=5)
                if count and count > 0:
                    break
            except Exception:
                pass

        raw_flights = await cdp_eval(ws, """
            (function() {
                var items = document.querySelectorAll('div.b-airfly');
                return JSON.stringify(Array.from(items).map(function(el) {
                    return el.innerText.trim().replace(/\\n/g, ' | ');
                }));
            })()
        """, timeout=10)

        raw_list = json.loads(raw_flights) if raw_flights else []
        flights = []
        for raw_text in raw_list:
            f = parse_qunar_dom(raw_text)
            if f and f.get("flight_number"):
                flights.append(f)

        print(f"[Qunar] Parsed {len(flights)} flights")
        return flights

    finally:
        if own_ws and ws:
            await ws.close()
        if own_ws and page_id:
            try:
                urllib.request.urlopen(
                    f"http://localhost:{CDP_PORT}/json/close/{page_id}"
                )
            except Exception:
                pass


def parse_qunar_dom(raw_text):
    """Parse Qunar flight DOM text.

    Text comes from innerText of div.b-airfly, with newlines replaced by ' | '.
    Direct flight format:
      九元航空 | AQ1019波音7M8(中) | 07:25 |  | 白云机场T2 |  | 3h5m | 10:30 |  | 胶东机场T1 |  | ¥ | 509 | ...
    Connecting flight format:
      中国国航 | CA1302空客350(大) | CA4680737-800共享 | 19:40 |  | 白云机场T3 |  | 停留10h55m | +1天 | 11:20 |  | 胶东机场 |  | 转北京 | ...
    """
    parts = [p.strip() for p in raw_text.split("|")]
    has_next_day = "+1天" in raw_text

    f = {
        "airline": parts[0] if parts else "",
        "flight_number": "",
        "aircraft": "",
        "departure_time": "",
        "depAirport": "",
        "arrival_time": "",
        "arrAirport": "",
        "duration": "",
        "price": 0,
        "discount": "",
        "platform": "qunar",
        "direct": True,
    }

    # --- Flight number(s) and aircraft ---
    # Pattern: "AQ1019波音7M8(中)" or "CA1302空客350(大)"
    fn_aircraft_re = re.compile(r'([A-Z]{2}\d{3,4}|[A-Z]\d{4,5})(.*)')
    flight_nums = []
    aircrafts = []

    for part in parts[1:4]:  # First 3 parts may contain flight info
        m = fn_aircraft_re.match(part)
        if m:
            flight_nums.append(m.group(1))
            rest = m.group(2).strip()
            if rest and not rest.startswith(('0', '1', '2', '3', '4', '5', '6', '7', '8', '9')):
                aircrafts.append(rest)
            elif rest:
                # "CA4680737-800共享" → second flight number "CA4680", aircraft "737-800共享"
                m2 = fn_aircraft_re.match(rest)
                if m2:
                    flight_nums.append(m2.group(1))
                    if m2.group(2).strip():
                        aircrafts.append(m2.group(2).strip())
                else:
                    aircrafts.append(rest)
            break  # Stop after first match

    if flight_nums:
        f["flight_number"] = flight_nums[0]
    if aircrafts:
        f["aircraft"] = aircrafts[0]
    f["_all_flight_numbers"] = flight_nums

    # --- Direct vs connecting ---
    if len(flight_nums) > 1 or "停留" in raw_text or "转" in raw_text:
        f["direct"] = False

    # --- Times: first \d{2}:\d{2} is departure, last is arrival ---
    times = re.findall(r'\b(\d{1,2}:\d{2})\b', raw_text)
    if len(times) >= 2:
        f["departure_time"] = times[0]
        f["arrival_time"] = times[-1]  # Last time in text is arrival
        if has_next_day:
            f["arrival_time"] += "+1"

    # --- Airports: Chinese names ending in 机场 ---
    airports = re.findall(r'([一-鿿]+机场T?\d*)', raw_text)
    if len(airports) >= 2:
        f["depAirport"] = airports[0]
        f["arrAirport"] = airports[-1]

    # --- Duration: "3h5m" for direct, "停留10小时55分钟" for layover ---
    dur_match = re.search(r'(\d+h\d+m?)', raw_text)
    if dur_match:
        f["duration"] = dur_match.group(1)
    else:
        # Layover format: "停留10小时55分钟"
        stay_match = re.search(r'停留(\d+)小时(\d+)分钟', raw_text)
        if stay_match:
            h, m = int(stay_match.group(1)), int(stay_match.group(2))
            f["_layover_minutes"] = h * 60 + m

    # If no direct duration but we have departure/arrival times, compute it
    if not f["duration"] and f["departure_time"] and f["arrival_time"]:
        dep_clean = f["departure_time"].replace("+1", "")
        arr_clean = f["arrival_time"].replace("+1", "")
        try:
            dep_h, dep_m = map(int, dep_clean.split(":"))
            arr_h, arr_m = map(int, arr_clean.split(":"))
            total_mins = arr_h * 60 + arr_m - (dep_h * 60 + dep_m)
            if has_next_day:
                total_mins += 24 * 60
            if total_mins > 0:
                f["duration"] = f"{total_mins // 60}h{total_mins % 60}m"
        except (ValueError, AttributeError):
            pass

    # --- Price: ¥ | 509 or ¥509 ---
    price_match = re.search(r'¥\s*\|\s*(\d+)', raw_text)
    if not price_match:
        price_match = re.search(r'¥\s*(\d+)', raw_text)
    if price_match:
        p = int(price_match.group(1))
        if 10 < p < 100000:
            f["price"] = p

    # --- Discount: "2.3折" ---
    disc_match = re.search(r'([\d.]+折)', raw_text)
    if disc_match:
        f["discount"] = disc_match.group(1)

    return f


# --- Tongcheng (not yet working) ---

async def search_tongcheng(origin_cn, dest_cn, date_str, ws_url=None, ws=None):
    """Search Tongcheng flights. Not yet working - requires Vue form interaction."""
    print(f"[Tongcheng] {origin_cn}->{dest_cn} {date_str} - SKIPPED (form interaction needed)")
    return []


# --- Main ---

async def search_all(origin_cn, dest_cn, date_str, platforms=None):
    """Search all platforms or specified ones. Returns dict of platform -> flights."""
    if platforms is None:
        platforms = ["ctrip"]  # Qunar skipped: unreliable DOM scraping + slow

    origin_cn = resolve_city_name(origin_cn)
    dest_cn = resolve_city_name(dest_cn)

    origin_iata = CITY_TO_IATA.get(origin_cn, ("BJS", origin_cn))[0]
    dest_iata = CITY_TO_IATA.get(dest_cn, ("SHA", dest_cn))[0]

    tasks = {}
    if "ctrip" in platforms:
        tasks["ctrip"] = search_ctrip(origin_iata, dest_iata, date_str)
    if "qunar" in platforms:
        tasks["qunar"] = search_qunar(origin_cn, dest_cn, date_str)
    if "tongcheng" in platforms:
        tasks["tongcheng"] = search_tongcheng(origin_cn, dest_cn, date_str)

    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
    results = {}
    for key, result in zip(tasks.keys(), gathered):
        if isinstance(result, Exception):
            print(f"[{key}] Error: {result}")
            results[key] = []
        else:
            results[key] = result

    return results


async def main():
    if len(sys.argv) < 2:
        print("Usage: python3 cdp_scraper.py <origin> <dest> <date> [--platform ctrip,qunar] [--json]")
        print("Example: python3 cdp_scraper.py 北京 上海 2026-06-20")
        return

    origin = sys.argv[1]
    dest = sys.argv[2]
    date_str = sys.argv[3]
    platforms = ["ctrip"]  # Qunar skipped: unreliable DOM + slow (40s+)
    json_output = False

    for arg in sys.argv[4:]:
        if arg.startswith("--platform"):
            platforms = arg.split("=", 1)[1].split(",") if "=" in arg else sys.argv[sys.argv.index(arg) + 1].split(",")
        elif arg == "--json":
            json_output = True

    results = await search_all(origin, dest, date_str, platforms)

    total = sum(len(v) for v in results.values())
    print(f"\n{'='*60}")
    for platform, flights in results.items():
        print(f"  {platform}: {len(flights)} flights")
    print(f"  Total: {total} flights")

    if json_output:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for platform, flights in results.items():
            print(f"\n--- {platform} ---")
            for f in flights[:5]:
                fn = f.get('flight_number', '?')
                dep = f.get('departure_time', '?')
                arr = f.get('arrival_time', '?')
                price = f.get('price', '?')
                pc = f.get('priceCount', 0)
                print(f"  {f.get('airline','?')} {fn} {dep}-{arr} ¥{price} ({pc} prices)")


if __name__ == "__main__":
    asyncio.run(main())
