#!/usr/bin/env python3
"""CDP-based flight scraper using Ctrip/Qunar internal APIs via fetch interception.

Connects to Chrome running with --remote-debugging-port=9222.
Uses Page.addScriptToEvaluateOnNewDocument to intercept API responses
before the SPA loads, capturing complete structured flight data.

Usage:
    python3 cdp_scraper.py <origin_city> <dest_city> <date> [--platform ctrip,qunar] [--json]
    python3 cdp_scraper.py 北京 上海 2026-06-20
"""

import asyncio
import json
import os
import re
import time
import sys
import subprocess
import urllib.parse
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
    """Create a blank CDP tab, return its info dict."""
    raw = subprocess.check_output(
        ["curl", "-s", "-X", "PUT", "http://localhost:9222/json/new?about:blank"],
        text=True,
    )
    return json.loads(raw)


def create_page_with_url(url):
    """Create a CDP tab navigating to the given URL, return its info dict."""
    encoded = urllib.parse.quote(url, safe="")
    raw = subprocess.check_output(
        ["curl", "-s", "-X", "PUT", f"http://localhost:9222/json/new?{encoded}"],
        text=True,
    )
    return json.loads(raw)


# --- Ctrip API-based search ---

def parse_ctrip_api_flight(itinerary):
    """Parse a single Ctrip flight itinerary from batchSearch API response.

    Returns a list of flight entries (one per unique flightNo across segments).
    Each entry includes all price variants.

    priceList is at the ITINERARY level, not segment level.
    Each price references segmentNo/sequenceNo via priceUnitList[].flightSeatList[].
    """
    flights = []
    all_prices = itinerary.get("priceList", [])

    for seg in itinerary.get("flightSegments", []):
        flight_list = seg.get("flightList", [])
        seg_no = seg.get("segmentNo", 1)

        for fl in flight_list:
            flight = {
                "airline": seg.get("airlineName", ""),
                "flight_number": fl.get("flightNo", ""),
                "operateFlightNo": fl.get("operateFlightNo", ""),
                "operateAirline": fl.get("operateAirlineName", ""),
                "aircraft": fl.get("aircraftName", ""),
                "aircraftCode": fl.get("aircraftCode", ""),
                "departure_time": _format_datetime(fl.get("departureDateTime", "")),
                "arrival_time": _format_datetime(fl.get("arrivalDateTime", "")),
                "depAirport": fl.get("departureAirportName", ""),
                "arrAirport": fl.get("arrivalAirportName", ""),
                "depAirportCode": fl.get("departureAirportCode", ""),
                "arrAirportCode": fl.get("arrivalAirportCode", ""),
                "depTerminal": fl.get("departureTerminal", ""),
                "arrTerminal": fl.get("arrivalTerminal", ""),
                "duration": seg.get("duration", 0),
                "stopCount": seg.get("stopCount", 0),
                "transferCount": seg.get("transferCount", 0),
                "crossDays": seg.get("crossDays", 0),
                "platform": "ctrip",
            }

            # Extract price variants matching this flight's segmentNo + sequenceNo
            flight_seq = fl.get("sequenceNo", 1)
            prices = []
            for p in all_prices:
                adult_price = p.get("adultPrice", 0)
                if adult_price <= 0:
                    continue

                # Match this price to the flight via priceUnitList
                discount_rate = 0
                product_types = []
                seat_class = ""
                price_matches = not p.get("priceUnitList")  # If no priceUnitList, applies to all
                for pu in p.get("priceUnitList", []):
                    for seat in pu.get("flightSeatList", []):
                        if seat.get("segmentNo") == seg_no and seat.get("sequenceNo") == flight_seq:
                            price_matches = True
                            discount_rate = seat.get("discountRate", 0)
                            product_types = seat.get("productTypes", [])
                            seat_class = seat.get("seatClass", "")

                if not price_matches:
                    continue

                # Get baggage info for this segment
                baggage_info = ""
                if "baggage" in p:
                    for bl in p["baggage"].get("dataList", []):
                        if bl.get("segmentNo") == seg_no and bl.get("sequenceNo") == flight_seq:
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

            # Sort prices low to high and deduplicate by (price, cabin, seatClass)
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

            # Build search URL
            fn = flight["flight_number"]
            dep_code = flight.get("depAirportCode", "")
            arr_code = flight.get("arrAirportCode", "")
            dep_date = fl.get("departureDateTime", "")[:10] if fl.get("departureDateTime") else ""
            flight["search_url"] = (
                f"https://flights.ctrip.com/online/list/oneway-{dep_code.lower()}-{arr_code.lower()}"
                f"?depdate={dep_date}&flightno={fn}"
            )

            flights.append(flight)

    return flights


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

    Injects fetch interceptor before page load, then navigates to search URL.
    The SPA's own batchSearch call is captured, giving us complete flight data.
    """
    url = f"https://flights.ctrip.com/itinerary/oneway/{origin_iata.lower()}-{dest_iata.lower()}?date={date_str}"
    print(f"[Ctrip] {origin_iata}->{dest_iata} {date_str}")

    own_ws = False
    if ws is None:
        own_ws = True
        page_info = create_blank_page()
        ws_url = page_info["webSocketDebuggerUrl"]
        ws = await websockets.connect(ws_url, max_size=100 * 1024 * 1024)

    try:
        await cdp_send(ws, "Page.enable")
        await cdp_send(ws, "Runtime.enable")

        # Inject fetch interceptor BEFORE page scripts run.
        # This captures the SPA's batchSearch API response containing all flights.
        intercept_js = """
        (function() {
            window.__ctripFlightData = null;
            const origFetch = window.fetch.bind(window);
            window.fetch = function(...args) {
                const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url) || '';
                return origFetch.apply(window, args).then(function(resp) {
                    if (url.includes('batchSearch') && url.includes('search/api/search')) {
                        const cloned = resp.clone();
                        cloned.json().then(function(data) {
                            if (data && data.data && data.data.flightItineraryList) {
                                window.__ctripFlightData = data.data.flightItineraryList;
                            }
                        }).catch(function(){});
                    }
                    return resp;
                });
            };
        })()
        """
        await cdp_send(ws, "Page.addScriptToEvaluateOnNewDocument", {
            "source": intercept_js,
        })

        # Navigate to trigger the API calls
        await cdp_send(ws, "Page.navigate", {"url": url})

        # Wait for flight data to be captured (up to 60s)
        flight_data = None
        for i in range(30):
            await asyncio.sleep(2)
            try:
                raw_len = await cdp_eval(
                    ws,
                    "window.__ctripFlightData ? window.__ctripFlightData.length : 0",
                    timeout=5,
                )
                if raw_len and raw_len > 0:
                    print(f"[Ctrip] API captured: {raw_len} itinerary items")
                    flight_data = raw_len
                    break
            except Exception:
                pass

        if not flight_data:
            print("[Ctrip] No API response captured after 60s, falling back to DOM")
            return await search_ctrip_dom(ws, origin_iata, dest_iata, date_str)

        # Extract flight data in batches to avoid CDP size limits.
        # Each itinerary is ~40KB, we fetch them one at a time.
        all_flights = []
        batch_size = 10
        for offset in range(0, flight_data, batch_size):
            expr = (
                f"(function() {{"
                f"  var slice = window.__ctripFlightData.slice({offset}, {offset + batch_size});"
                f"  return JSON.stringify(slice);"
                f"}})()"
            )
            try:
                raw = await cdp_eval(ws, expr, timeout=15)
                if raw:
                    itineraries = json.loads(raw)
                    for it in itineraries:
                        flights = parse_ctrip_api_flight(it)
                        all_flights.extend(flights)
            except Exception as e:
                print(f"[Ctrip] Error extracting batch {offset}: {e}")
                # Try one-by-one for this batch
                for j in range(batch_size):
                    idx = offset + j
                    if idx >= flight_data:
                        break
                    try:
                        raw = await cdp_eval(
                            ws,
                            f"JSON.stringify(window.__ctripFlightData[{idx}])",
                            timeout=10,
                        )
                        if raw:
                            flights = parse_ctrip_api_flight(json.loads(raw))
                            all_flights.extend(flights)
                    except Exception:
                        pass

        print(f"[Ctrip] Parsed {len(all_flights)} flights with price data")
        return all_flights

    finally:
        if own_ws and ws:
            await ws.close()


async def search_ctrip_dom(ws, origin_iata, dest_iata, date_str):
    """Fallback: DOM-based Ctrip scraping."""
    await cdp_send(ws, "Page.navigate", {"url": f"https://flights.ctrip.com/itinerary/oneway/{origin_iata.lower()}-{dest_iata.lower()}?date={date_str}"})

    for i in range(15):
        await asyncio.sleep(2)
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
        platforms = ["ctrip", "qunar"]

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
    platforms = ["ctrip", "qunar"]
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
