import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

# ============================================================
# Configuration Parameters
# ============================================================
ORIGIN = "AMS"
DESTINATION = "DPS"
YEAR = 2026
START_OUTBOUND = datetime(YEAR, 7, 19)
END_OUTBOUND = datetime(YEAR, 8, 1)
MAX_STOPS = 1
MAX_DURATION_HOURS = 19
TRIP_LENGTHS_DAYS = [24, 25]

DEBUG_DIR = Path("debug")          # screenshots/html dumps land here on failure
HEADLESS = True                    # set False locally to watch the browser
NAV_TIMEOUT_MS = 20000
ROW_WAIT_TIMEOUT_MS = 10000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("flight_scraper")


def parse_duration(duration_str: str) -> float:
    """Converts a string like '16 hr 45 min' into total decimal hours."""
    if not duration_str:
        return 0.0
    hr_match = re.search(r'(\d+)\s*hr', duration_str)
    min_match = re.search(r'(\d+)\s*min', duration_str)
    hours = int(hr_match.group(1)) if hr_match else 0
    minutes = int(min_match.group(1)) if min_match else 0
    return hours + (minutes / 60.0)


async def dismiss_consent(page) -> None:
    """Click through Google's cookie/consent dialog if one appears.

    Uses a short *waiting* check (wait_for with a small timeout) rather than
    a single instantaneous is_visible() call, since the dialog can render a
    moment after navigation completes.
    """
    selector = (
        'button:has-text("Accept all"), '
        'button:has-text("I agree"), '
        'button:has-text("Agree"), '
        'button:has-text("Ik ga akkoord"), '
        'button:has-text("Alles accepteren")'
    )
    try:
        button = page.locator(selector).first
        await button.wait_for(state="visible", timeout=3000)
        await button.click()
        await page.wait_for_timeout(600)
        log.info("Dismissed cookie/consent dialog.")
    except PWTimeoutError:
        # No consent dialog appeared — that's fine, not every locale shows one.
        pass


async def wait_for_flight_rows(page):
    """Try a sequence of selector strategies and return whichever matches.

    Google Flights changes its markup periodically and doesn't reliably use
    role="listitem" or any stable data-* attribute. We try several
    candidates in order and log which one (if any) actually worked, instead
    of guessing a single selector and silently failing.
    """
    candidate_selectors = [
        '[role="listitem"]',
        'li[data-id]',
        'div[jsname][role="link"]',
        'ul[role="list"] > li',
        # Generic fallback: any element whose visible text contains a euro
        # price AND looks like a flight row (has a time pattern). This is
        # intentionally broad as a last resort.
    ]

    for sel in candidate_selectors:
        try:
            await page.wait_for_selector(sel, timeout=ROW_WAIT_TIMEOUT_MS)
            rows = await page.locator(sel).all()
            if rows:
                log.info(f"Matched {len(rows)} candidate rows using selector: {sel}")
                return rows
        except PWTimeoutError:
            continue

    return []


async def dump_debug_artifacts(page, tag: str) -> None:
    """Save a screenshot + HTML snapshot so failures are diagnosable."""
    DEBUG_DIR.mkdir(exist_ok=True)
    safe_tag = re.sub(r'[^a-zA-Z0-9_-]', '_', tag)
    png_path = DEBUG_DIR / f"{safe_tag}.png"
    html_path = DEBUG_DIR / f"{safe_tag}.html"
    try:
        await page.screenshot(path=str(png_path), full_page=True)
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        log.warning(f"Saved debug artifacts: {png_path}, {html_path}")
    except Exception as e:
        log.warning(f"Could not save debug artifacts: {e}")


def extract_airline(lines: list[str]) -> str:
    for line in lines:
        if re.search(r'\d{1,2}:\d{2}', line):
            continue
        if line in ["–", "-", "—", "Separate tickets", "Best", "Cheapest"]:
            continue
        if "hr" in line or "min" in line or "stop" in line or "Nonstop" in line:
            continue
        if "€" in line:
            continue
        if re.search(r'^[A-Z]{3}[–\-][A-Z]{3}$', line):
            continue
        return line
    return "Unknown Airline"


def parse_row(text_content: str, out_str: str, ret_str: str, duration_days: int):
    """Parse a single flight row's inner_text into a structured dict, or None."""
    if not text_content or "€" not in text_content:
        return None
    if ORIGIN not in text_content:
        return None

    lines = [l.strip() for l in text_content.split("\n") if l.strip()]
    if len(lines) < 4 or "Hide" in lines[0] or "Separate tickets" in lines[0]:
        return None

    price_match = re.search(r'€\s*([\d.,]+)', text_content)
    if not price_match:
        return None
    total_price_int = int(price_match.group(1).replace('.', '').replace(',', ''))

    duration_match = re.search(r'(\d+\s*hr\s*\d*\s*min|\d+\s*hr)', text_content)
    duration_str = duration_match.group(1) if duration_match else "Unknown"
    total_hours = parse_duration(duration_str)
    if total_hours and total_hours > MAX_DURATION_HOURS:
        return None

    stops = 0
    layover_city = "Nonstop"
    if "1 stop" in text_content:
        stops = 1
        stop_match = re.search(r'1 stop\s*([\d\s]*hr[\d\s]*min)?\s*([A-Z]{3})', text_content)
        if stop_match and stop_match.group(2):
            layover_city = stop_match.group(2)
    elif "2 stops" in text_content or "3 stops" in text_content:
        return None

    if stops > MAX_STOPS:
        return None

    airline = extract_airline(lines)

    condition_flags = ["Change of airport", "Overnight stay", "Long layover", "Separate tickets"]
    matched_conditions = [c for c in condition_flags if c.lower() in text_content.lower()]
    strange_conditions = ", ".join(matched_conditions) if matched_conditions else "None"

    return {
        "departure_date": out_str,
        "return_date": ret_str,
        "trip_duration_days": duration_days,
        "airline": airline,
        "stops": stops,
        "layover_airport": layover_city,
        "departing_time": lines[0],
        "travel_time": duration_str,
        "total_price_2_passengers": total_price_int,
        "strange_conditions": strange_conditions,
    }


async def scrape_one_date_pair(page, out_date, duration_days, debug_first_failure_done):
    ret_date = out_date + timedelta(days=duration_days)
    out_str = out_date.strftime("%Y-%m-%d")
    ret_str = ret_date.strftime("%Y-%m-%d")

    query = f"Flights from {ORIGIN} to {DESTINATION} for 2 adults on {out_str} through {ret_str}"
    url = f"https://www.google.com/travel/flights?q={query.replace(' ', '+')}&hl=en&curr=EUR"

    results = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(random.uniform(800, 1500))

        await dismiss_consent(page)

        flight_rows = await wait_for_flight_rows(page)
        if not flight_rows:
            log.warning(f"No flight rows found for {out_str} -> {ret_str} (selectors all missed).")
            if not debug_first_failure_done[0]:
                await dump_debug_artifacts(page, f"no_rows_{out_str}_{ret_str}")
                debug_first_failure_done[0] = True
            return results, debug_first_failure_done

        for row in flight_rows:
            try:
                text_content = await row.inner_text()
            except Exception as e:
                log.debug(f"Could not read row text: {e}")
                continue

            parsed = parse_row(text_content, out_str, ret_str, duration_days)
            if parsed:
                results.append(parsed)
                break  # only take the first matching row per date pair

    except PWTimeoutError as e:
        log.warning(f"Timeout for {out_str} -> {ret_str}: {e}")
        if not debug_first_failure_done[0]:
            await dump_debug_artifacts(page, f"timeout_{out_str}_{ret_str}")
            debug_first_failure_done[0] = True
    except Exception as e:
        log.error(f"Unexpected error for {out_str} -> {ret_str}: {e}")

    return results, debug_first_failure_done


async def scrape_flights():
    all_results = []
    current_outbound = START_OUTBOUND
    outbound_dates = []
    while current_outbound <= END_OUTBOUND:
        outbound_dates.append(current_outbound)
        current_outbound += timedelta(days=1)

    log.info(f"Searching {len(outbound_dates)} outbound dates x {len(TRIP_LENGTHS_DAYS)} trip lengths "
              f"= {len(outbound_dates) * len(TRIP_LENGTHS_DAYS)} combinations.")

    debug_first_failure_done = [False]  # mutable flag so we only dump once

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        async def intercept_route(route):
            if route.request.resource_type in ["image", "font", "media"]:
                await route.abort()
            elif any(t in route.request.url for t in ["analytics", "doubleclick", "google-analytics"]):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", intercept_route)

        for out_date in outbound_dates:
            for duration_days in TRIP_LENGTHS_DAYS:
                results, debug_first_failure_done = await scrape_one_date_pair(
                    page, out_date, duration_days, debug_first_failure_done
                )
                all_results.extend(results)
                # be polite / reduce chance of getting blocked
                await page.wait_for_timeout(random.uniform(400, 900))

        await browser.close()

    all_results.sort(key=lambda x: x["total_price_2_passengers"])

    print("\n" + "=" * 80)
    print(f"TOP 5 CHEAPEST FLIGHT COMBINATIONS FOUND ({ORIGIN} -> {DESTINATION})")
    print("=" * 80)

    if not all_results:
        print("No valid flights matching your rules were parsed during this run.")
        print(f"Check the '{DEBUG_DIR}/' folder for a screenshot + HTML dump of what Google actually served.")
    else:
        for idx, flight in enumerate(all_results[:5], start=1):
            print(f"\n[OPTION #{idx}]")
            print(f"   Dates: {flight['departure_date']} to {flight['return_date']} ({flight['trip_duration_days']} days)")
            print(f"   Airline: {flight['airline']}")
            print(f"   Stops: {flight['stops']} ({flight['layover_airport']})")
            print(f"   Departs: {flight['departing_time']} | Duration: {flight['travel_time']}")
            print(f"   Price (2 Pax): EUR {flight['total_price_2_passengers']}")
            print(f"   Conditions: {flight['strange_conditions']}")

    print("\n" + "=" * 80)
    print("RUNTIME HEALTH CHECK SUMMARY")
    print("=" * 80)
    print(f"Total combinations attempted: {len(outbound_dates) * len(TRIP_LENGTHS_DAYS)}")
    print(f"Total successful matches: {len(all_results)}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(scrape_flights())
