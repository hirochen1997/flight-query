#!/usr/bin/env python3
"""CDP-based flight scraper using logged-in Chrome sessions.

Connects to Chrome running with --remote-debugging-port=9222.
Scrapes Ctrip, Qunar, Tongcheng flight search results.

Usage:
    python3 cdp_scraper.py <origin_city> <dest_city> <date> [--platform ctrip,qunar]

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

# City name to IATA code mapping
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


# --- CDP Helpers ---

async def cdp_recv(ws, msg_id, timeout=30):
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


async def cdp_send(ws, method, params=None, timeout=30):
    msg_id = int(time.time() * 1000000) % 1000000000
    msg = {"id": msg_id, "method": method, "params": params or {}}
    await ws.send(json.dumps(msg))
    return await cdp_recv(ws, msg_id, timeout)


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


def resolve_city_name(input_str):
    """Resolve a city name or IATA code to Chinese city name."""
    if not input_str:
        return "北京"
    # If already Chinese, return as-is
    if any('一' <= c <= '鿿' for c in input_str):
        return input_str.strip()
    # Try to reverse-lookup IATA code
    iata_to_cn = {v[0]: k for k, v in CITY_TO_IATA.items()}
    return iata_to_cn.get(input_str.strip().upper(), input_str.strip())


# --- Ctrip ---

def parse_ctrip_flight(raw_text):
    """
    Parse Ctrip flight item innerText into structured data.

    Raw text format (| separated lines):
    中国国航 | CA1565 波音789(大) | 当日低价 | 18:30 | 首都国际机场T3 | 20:55 | 虹桥国际机场T2 | ... | ¥300起 | 经济舱1.4折 | 订票
    """
    text = raw_text.strip()
    lines = [l.strip() for l in text.split("|") if l.strip()]

    flight = {
        "airline": "",
        "flight_number": "",
        "aircraft": "",
        "departure_time": "",
        "depAirport": "",
        "arrival_time": "",
        "arrAirport": "",
        "price": 0,
        "cabin": "",
        "discount": "",
        "platform": "ctrip",
    }

    # Line 0: Airline name
    if lines:
        flight["airline"] = lines[0]

    # Line 1: Flight number + aircraft type
    if len(lines) > 1:
        fn_match = re.search(r"\b([A-Z]{2}\d{3,4}|[A-Z]\d{4,5})\b", lines[1])
        if not fn_match:
            fn_match = re.search(r"([A-Z]{2}\d{3,4})", lines[1])
        if fn_match:
            flight["flight_number"] = fn_match.group(1)
        ac_match = re.search(r"([一-龥]+(?:-?\d+)?\s*\([大小中]\)|[一-龥]+\d+.*)", lines[1])
        if ac_match:
            flight["aircraft"] = ac_match.group(1).strip()

    # Find time and airport lines
    time_airport_pattern = re.compile(
        r"^(\d{1,2}:\d{2})$"
    )
    airport_pattern = re.compile(r"^([一-龥]+机场T?\d*)$")

    times = []
    airports = []
    for line in lines:
        if time_airport_pattern.match(line):
            times.append(line)
        elif airport_pattern.match(line):
            airports.append(line)

    if len(times) >= 2:
        flight["departure_time"] = times[0]
        flight["arrival_time"] = times[1]
    if len(airports) >= 2:
        flight["depAirport"] = airports[0]
        flight["arrAirport"] = airports[1]

    # Find price
    price_match = re.search(r"¥([\d,]+)", text)
    if price_match:
        flight["price"] = int(price_match.group(1).replace(",", ""))

    # Discount & cabin
    discount_match = re.search(r"经济舱([\d.]+折)", text)
    if discount_match:
        flight["discount"] = discount_match.group(1)
        flight["cabin"] = "经济舱"

    return flight


async def search_ctrip(origin_iata, dest_iata, date_str, ws_url=None, ws=None):
    """Search Ctrip flights. Returns list of structured flight dicts."""
    url = f"https://flights.ctrip.com/itinerary/oneway/{origin_iata.lower()}-{dest_iata.lower()}?date={date_str}"
    print(f"[Ctrip] {origin_iata}->{dest_iata} {date_str}")

    own_ws = False
    if ws is None:
        own_ws = True
        page_info = create_blank_page()
        ws_url = page_info["webSocketDebuggerUrl"]
        ws = await websockets.connect(ws_url, max_size=10 * 1024 * 1024)

    try:
        await cdp_send(ws, "Page.enable")
        await cdp_send(ws, "Runtime.enable")
        await cdp_send(ws, "Page.navigate", {"url": url})

        # Wait for flight items (up to 40s)
        count = 0
        for i in range(20):
            await asyncio.sleep(2)
            try:
                count = await cdp_eval(
                    ws,
                    "document.querySelectorAll('.flight-item').length",
                    timeout=5,
                )
                if count:
                    break
            except Exception:
                pass

        if not count:
            print("[Ctrip] No flight items found after 40s")
            return []

        print(f"[Ctrip] Found {count} flights")

        # Extract raw text from each flight item
        raw_flights = await cdp_eval(
            ws,
            "(function() {"
            "  var items = document.querySelectorAll('.flight-item');"
            "  return JSON.stringify(Array.from(items).map(function(i) {"
            "    return i.innerText.trim().replace(/\\n/g, ' | ');"
            "  }));"
            "})()",
            timeout=10,
        )
        raw_list = json.loads(raw_flights) if raw_flights else []
        flights = [parse_ctrip_flight(f) for f in raw_list]
        flights = [f for f in flights if f["flight_number"]]
        print(f"[Ctrip] Parsed {len(flights)} valid flights")
        return flights

    finally:
        if own_ws and ws:
            await ws.close()


# --- Qunar ---

def parse_qunar_flight_text(text):
    """Parse a single Qunar flight block (div.b-airfly innerText)."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    flight = {
        "airline": "",
        "flightNo": "",
        "aircraft": "",
        "depTime": "",
        "depAirport": "",
        "arrTime": "",
        "arrAirport": "",
        "duration": "",
        "price": 0,
        "discount": "",
        "platform": "qunar",
        "direct": True,
    }

    if not lines:
        return flight

    # Line 0: airline
    flight["airline"] = lines[0]

    # Line 1: FN + aircraft
    if len(lines) > 1:
        fn_match = re.search(r"([A-Z]{1,2}\d{3,5})", lines[1])
        if fn_match:
            flight["flightNo"] = fn_match.group(1)
            flight["aircraft"] = lines[1].replace(fn_match.group(1), "").strip()

    # Find times (HH:MM format)
    times = [l for l in lines if re.match(r"^\d{1,2}:\d{2}$", l)]
    if len(times) >= 2:
        flight["departure_time"] = times[0]
        flight["arrival_time"] = times[1]

    # Find airports
    airports = [l for l in lines if re.match(r"^[一-龥]+机场T?\d*$", l)]
    if len(airports) >= 2:
        flight["depAirport"] = airports[0]
        flight["arrAirport"] = airports[1]

    # Find duration
    dur_match = re.search(r"(\d+h\d+m?)", text)
    if dur_match:
        flight["duration"] = dur_match.group(1)

    # Find price: look for ¥ followed by price digits
    # Qunar format: ¥ on one line, price digits on next line(s)
    for i, line in enumerate(lines):
        if line == "¥" and i + 1 < len(lines):
            price_digits = re.sub(r"[^\d]", "", lines[i + 1])
            if price_digits:
                flight["price"] = int(price_digits)
            break

    # Also try ¥ followed directly by digits (e.g., "¥500")
    if flight["price"] == 0:
        price_match = re.search(r"¥\s*(\d+)", text)
        if price_match:
            flight["price"] = int(price_match.group(1))

    # Find discount
    disc_match = re.search(r"([\d.]+折)", text)
    if disc_match:
        flight["discount"] = disc_match.group(1)

    return flight


async def search_qunar(origin_cn, dest_cn, date_str, ws_url=None, ws=None):
    """Search Qunar flights with Chinese city names."""
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

        # Wait for flight results (up to 40s)
        for i in range(20):
            await asyncio.sleep(2)
            try:
                count = await cdp_eval(
                    ws,
                    "document.querySelectorAll('div.b-airfly').length",
                    timeout=5,
                )
                if count and count > 0:
                    break
            except Exception:
                pass

        # Extract each flight from DOM
        raw_flights = await cdp_eval(
            ws,
            "(function() {"
            "  var items = document.querySelectorAll('div.b-airfly');"
            "  return JSON.stringify(Array.from(items).map(function(el) {"
            "    return el.innerText.trim().replace(/\\n/g, ' | ');"
            "  }));"
            "})()",
            timeout=10,
        )
        raw_list = json.loads(raw_flights) if raw_flights else []

        flights = []
        for raw_text in raw_list:
            # Parse the |-separated text
            lines = [l.strip() for l in raw_text.split("|") if l.strip()]
            f = {
                "airline": lines[0] if lines else "",
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

            # Flight number
            # Match Chinese domestic flight numbers: CA1234 (2L+3-4D) or Y81234 (1L+4-5D)
            fn_match = re.search(r"\b([A-Z]{2}\d{3,4}|[A-Z]\d{4,5})\b", raw_text)
            if not fn_match:
                fn_match = re.search(r"([A-Z]{2}\d{3,4})", raw_text)
            if fn_match:
                f["flight_number"] = fn_match.group(1)
                # Extract aircraft from the second segment (FN+aircraft line)
                parts = raw_text.split("|")
                if len(parts) > 1:
                    ac_part = parts[1].replace(f["flightNo"], "").strip()
                    # Clean up aircraft string (remove leading/trailing noise)
                    if ac_part:
                        f["aircraft"] = ac_part

            # Times
            times = re.findall(r"\b(\d{1,2}:\d{2})\b", raw_text)
            if len(times) >= 2:
                f["depTime"] = times[0]
                f["arrTime"] = times[1]

            # Airports
            airports = re.findall(r"([一-龥]+机场T?\d*)", raw_text)
            if len(airports) >= 2:
                f["depAirport"] = airports[0]
                f["arrAirport"] = airports[1]

            # Duration
            dur_match = re.search(r"(\d+h\d+m?)", raw_text)
            if dur_match:
                f["duration"] = dur_match.group(1)

            # Price - ¥ followed by optional pipe then price digits
            price_match = re.search(r"¥\s*\|\s*(\d+)", raw_text)
            if not price_match:
                price_match = re.search(r"¥\s*(\d+)", raw_text)
            if price_match:
                p = int(price_match.group(1))
                # Filter unreasonable prices (Qunar calibration: ignore sub-cent digits)
                if 10 < p < 100000:
                    f["price"] = p

            # Discount
            disc_match = re.search(r"([\d.]+折)", raw_text)
            if disc_match:
                f["discount"] = disc_match.group(1)

            if f["flight_number"]:
                flights.append(f)

        print(f"[Qunar] Parsed {len(flights)} flights")
        return flights

    finally:
        if own_ws and ws:
            await ws.close()




# --- Tongcheng ---

async def search_tongcheng(origin_cn, dest_cn, date_str, ws_url=None, ws=None):
    """Search Tongcheng flights. Not yet working - requires Vue form interaction."""
    print(f"[Tongcheng] {origin_cn}->{dest_cn} {date_str} - SKIPPED (form interaction needed)")
    return []


# --- Main ---

async def search_all(origin_cn, dest_cn, date_str, platforms=None):
    """Search all platforms or specified ones. Returns dict of platform -> flights."""
    if platforms is None:
        platforms = ["ctrip", "qunar"]

    # Resolve input to Chinese city names
    origin_cn = resolve_city_name(origin_cn)
    dest_cn = resolve_city_name(dest_cn)

    origin_iata = CITY_TO_IATA.get(origin_cn, ("BJS", origin_cn))[0]
    dest_iata = CITY_TO_IATA.get(dest_cn, ("SHA", dest_cn))[0]

    # Run searches in parallel
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
        # Print summary
        for platform, flights in results.items():
            print(f"\n--- {platform} ---")
            for f in flights[:5]:
                print(f"  {f.get('airline','?')} {f.get('flight_number','?')} "
                      f"{f.get('departure_time','?')}-{f.get('arrival_time','?')} "
                      f"¥{f.get('price','?')}")


if __name__ == "__main__":
    asyncio.run(main())
