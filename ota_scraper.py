"""
Multi-platform OTA flight scraper.
Uses Playwright with system Chrome to scrape Ctrip, Qunar, Tongcheng.

Usage: python3 ota_scraper.py --from PEK --to SHA --date 2026-06-20
Output: JSON to stdout
"""

import asyncio
import json
import sys
import argparse
import re

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
]

STEALTH_SCRIPTS = [
    'Object.defineProperty(navigator, "webdriver", {get: () => undefined})',
    'window.chrome = {runtime: {}}',
    'const originalQuery = window.navigator.permissions.query;'
    'window.navigator.permissions.query = (parameters) => ('
    '  parameters.name === "notifications" ?'
    '  Promise.resolve({state: Notification.permission}) :'
    '  originalQuery(parameters)'
    ')',
    'Object.defineProperty(navigator, "plugins", {get: () => [1,2,3,4,5]})',
    'Object.defineProperty(navigator, "languages", {get: () => ["zh-CN","zh","en"]})',
]


async def create_stealth_context(browser):
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/131.0.0.0 Safari/537.36",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    for script in STEALTH_SCRIPTS:
        await context.add_init_script(script)
    return context


# ============================================================
#  Ctrip (携程)
# ============================================================

async def search_ctrip(origin, dest, date, city_origin=None, city_dest=None):
    # Use city codes for Ctrip to get all airports in a city (e.g., BJS covers PEK+PKX)
    ctrip_origin = (city_origin or origin).lower()
    ctrip_dest = (city_dest or dest).lower()
    results = []
    browser = None
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME_PATH, headless=True, args=STEALTH_ARGS,
            )
            context = await create_stealth_context(browser)
            page = await context.new_page()

            url = f"https://flights.ctrip.com/itinerary/oneway/{ctrip_origin}-{ctrip_dest}?date={date}"
            print(f"[Ctrip] Loading: {url}", file=sys.stderr)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(10000)

            try:
                await page.wait_for_selector('.flight-item', timeout=15000)
            except:
                print("[Ctrip] No flight items found", file=sys.stderr)

            flights = await page.evaluate("""() => {
                const results = [];
                const items = document.querySelectorAll('.flight-item');
                items.forEach(item => {
                    try {
                        // Flight number from .plane-No span
                        const planeNoEl = item.querySelector('.plane-No, [class*="plane-No"]');
                        let flightNumber = '';
                        let aircraft = '';
                        if (planeNoEl) {
                            const txt = planeNoEl.textContent.trim();
                            const parts = txt.split(/\\s+/);
                            flightNumber = parts[0] || '';
                            aircraft = parts.slice(1).join(' ') || '';
                        }

                        // Airline name - first span in .airline-name or .flight-airline
                        const airlineEl = item.querySelector('.airline-name span, .airline-name');
                        const airline = airlineEl ? airlineEl.textContent.trim().replace(/\\s+/g, '') : '';

                        // Times - look for time pattern HH:MM
                        const allText = item.textContent;
                        const timeRegex = /(\\d{2}:\\d{2})/g;
                        const times = [];
                        let tm;
                        while ((tm = timeRegex.exec(allText)) !== null) {
                            times.push(tm[1]);
                        }

                        // Airport names
                        const airportEls = item.querySelectorAll('.name');
                        const airports = [];
                        airportEls.forEach(el => airports.push(el.textContent.trim()));

                        // Terminals
                        const terminalEls = item.querySelectorAll('[class*="terminal"]');
                        const terminals = [];
                        terminalEls.forEach(el => terminals.push(el.textContent.trim()));

                        // Price
                        const priceEl = item.querySelector('.price dfn, .price');
                        let price = null;
                        if (priceEl) {
                            const priceText = priceEl.textContent.trim();
                            const match = priceText.match(/(\\d[\\d,]*)/);
                            if (match) price = parseFloat(match[1].replace(/,/g, ''));
                        }

                        // Extract booking/deep links from <a> tags
                        const links = [];
                        const anchors = item.querySelectorAll('a[href]');
                        anchors.forEach(a => {
                            const href = a.getAttribute('href');
                            const text = a.textContent.trim();
                            if (href && !href.startsWith('javascript:') && !href.startsWith('#')) {
                                links.push(href.startsWith('http') ? href : 'https://flights.ctrip.com' + href);
                            }
                        });

                        // Extract data attributes that may contain product/flight IDs
                        const dataAttrs = {};
                        for (const attr of item.attributes) {
                            if (attr.name.startsWith('data-') || attr.name === 'u_key') {
                                dataAttrs[attr.name] = attr.value;
                            }
                        }

                        // Also check child elements for data attributes and u_key
                        const childrenWithData = item.querySelectorAll('[data-pid], [data-productid], [data-flightid], [data-cache-key], [u_key]');
                        childrenWithData.forEach(el => {
                            for (const attr of el.attributes) {
                                if ((attr.name.startsWith('data-') || attr.name === 'u_key') && !dataAttrs[attr.name]) {
                                    dataAttrs[attr.name] = attr.value;
                                }
                            }
                        });

                        // Also capture onclick handlers that may contain URLs
                        const bookBtn = item.querySelector('[u_key*="book"], [u_key*="expand"], [class*="btn"], [class*="book"]');
                        const onclick = bookBtn ? bookBtn.getAttribute('onclick') || '' : '';

                        results.push({
                            flightNumber,
                            airline,
                            aircraft,
                            departureTime: times[0] || '',
                            arrivalTime: times[times.length - 1] || '',
                            departureAirport: airports[0] || '',
                            arrivalAirport: airports[1] || '',
                            departureTerminal: terminals[0] || '',
                            arrivalTerminal: terminals[1] || '',
                            price,
                            stops: allText.includes('中转') || allText.includes('转') ? 1 : 0,
                            links,
                            dataAttrs,
                            onclick,
                        });
                    } catch(e) {
                        results.push({error: e.message});
                    }
                });
                return results;
            }""")

            for f in flights:
                if f.get('price') and f['price'] > 0:
                    fn = f.get("flightNumber", "")
                    # Build the most specific Ctrip URL possible:
                    # 1. Use extracted booking link from DOM if available
                    # 2. Construct search URL with flight number pre-filter
                    booking_url = ""
                    links = f.get("links", [])
                    if links:
                        booking_url = links[0]  # Use first extracted link
                    if not booking_url and fn:
                        # Try flight-number-filtered search URL
                        booking_url = f"https://flights.ctrip.com/itinerary/oneway/{ctrip_origin}-{ctrip_dest}?date={date}&flightno={fn}"

                    results.append({
                        "flight_number": fn,
                        "airline": f.get("airline", ""),
                        "origin_iata": origin,
                        "destination_iata": dest,
                        "departure_time": f.get("departureTime", ""),
                        "arrival_time": f.get("arrivalTime", ""),
                        "departure_airport": f.get("departureAirport", ""),
                        "arrival_airport": f.get("arrivalAirport", ""),
                        "departure_terminal": f.get("departureTerminal", ""),
                        "arrival_terminal": f.get("arrivalTerminal", ""),
                        "aircraft": f.get("aircraft", ""),
                        "stops": f.get("stops", 0),
                        "price": f.get("price"),
                        "platform": "ctrip",
                        "booking_url": booking_url,
                        "search_url": f"https://flights.ctrip.com/itinerary/oneway/{ctrip_origin}-{ctrip_dest}?date={date}",
                    })

            print(f"[Ctrip] Extracted {len(results)} flights", file=sys.stderr)
            await browser.close()

    except Exception as e:
        print(f"[Ctrip] Error: {e}", file=sys.stderr)
        if browser:
            try:
                await browser.close()
            except:
                pass

    return results


# ============================================================
#  Qunar (去哪儿)
# ============================================================

async def search_qunar(origin, dest, date):
    results = []
    browser = None
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME_PATH, headless=True, args=STEALTH_ARGS,
            )
            context = await create_stealth_context(browser)
            page = await context.new_page()

            url = f"https://flight.qunar.com/site/oneway_list.htm?fromCity={origin}&toCity={dest}&fromDate={date}"
            print(f"[Qunar] Loading: {url}", file=sys.stderr)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(10000)

            # Try to extract flights using Qunar's DOM structure
            flights = await page.evaluate("""() => {
                const results = [];
                // Qunar flight items
                const items = document.querySelectorAll('.m-flight-item, .flight-item, [class*="flight-item"]');
                items.forEach(item => {
                    const txt = item.textContent.trim();
                    if (txt.length < 20) return;

                    // Flight number
                    const fnMatch = txt.match(/([A-Z]{2}\\d+)/);
                    const flightNumber = fnMatch ? fnMatch[1] : '';

                    // Times
                    const timeRegex = /(\\d{2}:\\d{2})/g;
                    const times = [];
                    let tm;
                    while ((tm = timeRegex.exec(txt)) !== null) {
                        times.push(tm[1]);
                    }

                    // Price
                    const priceMatch = txt.match(/[¥￥]\\s*(\\d[\\d,]*)/);
                    let price = null;
                    if (priceMatch) price = parseFloat(priceMatch[1].replace(/,/g, ''));

                    results.push({
                        flightNumber,
                        fullText: txt.slice(0, 300),
                        departureTime: times[0] || '',
                        arrivalTime: times[times.length - 1] || '',
                        price,
                        stops: txt.includes('中转') || txt.includes('经停') ? 1 : 0,
                    });
                });

                // If no structured items found, try full page text
                if (results.length === 0) {
                    const bodyText = document.body.innerText;
                    // Look for flight number patterns near prices
                    const lines = bodyText.split('\\n');
                    for (let i = 0; i < lines.length; i++) {
                        const line = lines[i];
                        const fnMatch = line.match(/([A-Z]{2}\\d+)/);
                        const priceMatch = line.match(/[¥￥]\\s*(\\d[\\d,]*)/);
                        if (fnMatch && priceMatch) {
                            const price = parseFloat(priceMatch[1].replace(/,/g, ''));
                            const timeMatch = line.match(/(\\d{2}:\\d{2})/g);
                            results.push({
                                flightNumber: fnMatch[1],
                                departureTime: timeMatch ? timeMatch[0] : '',
                                arrivalTime: timeMatch && timeMatch.length > 1 ? timeMatch[timeMatch.length - 1] : '',
                                price,
                                stops: line.includes('中转') ? 1 : 0,
                                fullText: line.slice(0, 300),
                            });
                        }
                    }
                }

                return results;
            }""")

            for f in flights:
                if f.get('price') and f['price'] > 0:
                    results.append({
                        "flight_number": f.get("flightNumber", ""),
                        "airline": "",
                        "origin_iata": origin,
                        "destination_iata": dest,
                        "departure_time": f.get("departureTime", ""),
                        "arrival_time": f.get("arrivalTime", ""),
                        "price": f.get("price"),
                        "stops": f.get("stops", 0),
                        "platform": "qunar",
                        "booking_url": "",
                        "search_url": f"https://flight.qunar.com/site/oneway_list.htm?fromCity={origin}&toCity={dest}&fromDate={date}",
                    })

            print(f"[Qunar] Extracted {len(results)} flights", file=sys.stderr)
            await browser.close()

    except Exception as e:
        print(f"[Qunar] Error: {e}", file=sys.stderr)
        if browser:
            try:
                await browser.close()
            except:
                pass

    return results


# ============================================================
#  Tongcheng (同程旅行)
# ============================================================

async def search_tongcheng(origin, dest, date):
    results = []
    browser = None
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=CHROME_PATH, headless=True, args=STEALTH_ARGS,
            )
            context = await create_stealth_context(browser)
            page = await context.new_page()

            url = f"https://www.ly.com/flights/search?from={origin}&to={dest}&date={date}"
            print(f"[Tongcheng] Loading: {url}", file=sys.stderr)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(10000)

            flights = await page.evaluate("""() => {
                const results = [];
                const bodyText = document.body.innerText;

                // Find flight patterns in text
                const lines = bodyText.split('\\n');
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i];
                    const fnMatch = line.match(/([A-Z]{2}\\d+)/);
                    const priceMatch = line.match(/[¥￥]\\s*(\\d[\\d,]*)/);
                    if (fnMatch && priceMatch) {
                        const price = parseFloat(priceMatch[1].replace(/,/g, ''));
                        const timeMatch = line.match(/(\\d{2}:\\d{2})/g);
                        results.push({
                            flightNumber: fnMatch[1],
                            departureTime: timeMatch ? timeMatch[0] : '',
                            arrivalTime: timeMatch && timeMatch.length > 1 ? timeMatch[timeMatch.length-1] : '',
                            price,
                            stops: line.includes('中转') ? 1 : 0,
                            fullText: line.slice(0, 300),
                        });
                    }
                }

                // If nothing found, try structured selectors
                if (results.length === 0) {
                    const items = document.querySelectorAll('[class*="flight"], [class*="Flight"], [class*="card"], [class*="item"]');
                    items.forEach(item => {
                        const txt = item.textContent.trim();
                        if (txt.length > 20 && txt.length < 600) {
                            const priceMatch = txt.match(/[¥￥]\\s*(\\d[\\d,]*)/);
                            const fnMatch = txt.match(/([A-Z]{2}\\d+)/);
                            if (priceMatch && fnMatch) {
                                const timeMatch = txt.match(/(\\d{2}:\\d{2})/g);
                                results.push({
                                    flightNumber: fnMatch[1],
                                    departureTime: timeMatch ? timeMatch[0] : '',
                                    arrivalTime: timeMatch && timeMatch.length > 1 ? timeMatch[timeMatch.length-1] : '',
                                    price: parseFloat(priceMatch[1].replace(/,/g, '')),
                                    stops: txt.includes('中转') ? 1 : 0,
                                    fullText: txt.slice(0, 300),
                                });
                            }
                        }
                    });
                }

                return results;
            }""")

            for f in flights:
                if f.get('price') and f['price'] > 0:
                    results.append({
                        "flight_number": f.get("flightNumber", ""),
                        "airline": "",
                        "origin_iata": origin,
                        "destination_iata": dest,
                        "departure_time": f.get("departureTime", ""),
                        "arrival_time": f.get("arrivalTime", ""),
                        "price": f.get("price"),
                        "stops": f.get("stops", 0),
                        "platform": "tongcheng",
                        "booking_url": "",
                        "search_url": f"https://www.ly.com/flights/search?from={origin}&to={dest}&date={date}",
                    })

            print(f"[Tongcheng] Extracted {len(results)} flights", file=sys.stderr)
            await browser.close()

    except Exception as e:
        print(f"[Tongcheng] Error: {e}", file=sys.stderr)
        if browser:
            try:
                await browser.close()
            except:
                pass

    return results


# ============================================================
#  Main
# ============================================================

async def search_all(origin, dest, date, city_origin=None, city_dest=None):
    tasks = [
        search_ctrip(origin, dest, date, city_origin, city_dest),
        search_qunar(origin, dest, date),
        search_tongcheng(origin, dest, date),
    ]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    merged = {
        "ctrip": all_results[0] if not isinstance(all_results[0], Exception) else [],
        "qunar": all_results[1] if not isinstance(all_results[1], Exception) else [],
        "tongcheng": all_results[2] if not isinstance(all_results[2], Exception) else [],
    }
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="origin", required=True)
    parser.add_argument("--to", dest="dest", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--city-origin", dest="city_origin", default=None)
    parser.add_argument("--city-dest", dest="city_dest", default=None)
    parser.add_argument("--platform", choices=["ctrip", "qunar", "tongcheng", "all"], default="all")
    args = parser.parse_args()

    if args.platform == "all":
        results = asyncio.run(search_all(args.origin, args.dest, args.date, args.city_origin, args.city_dest))
    elif args.platform == "ctrip":
        results = {"ctrip": asyncio.run(search_ctrip(args.origin, args.dest, args.date, args.city_origin, args.city_dest))}
    elif args.platform == "qunar":
        results = {"qunar": asyncio.run(search_qunar(args.origin, args.dest, args.date))}
    elif args.platform == "tongcheng":
        results = {"tongcheng": asyncio.run(search_tongcheng(args.origin, args.dest, args.date))}

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
