import asyncio
import json
import os
import random
from datetime import datetime, timedelta
import re
from playwright.async_api import async_playwright

# Configuration Parameters
ORIGIN = "AMS"
DESTINATION = "DPS"
YEAR = 2026  # Swap to 2027 once schedules release
START_OUTBOUND = datetime(YEAR, 7, 19)
END_OUTBOUND = datetime(YEAR, 8, 1)
MAX_STOPS = 1
MAX_DURATION_HOURS = 19

def parse_duration(duration_str):
    """Converts a string like '16 hr 45 min' or '12h 30m' into total hours."""
    hours = 0
    minutes = 0
    hr_match = re.search(r'(\d+)\s*hr', duration_str)
    min_match = re.search(r'(\d+)\s*min', duration_str)
    if hr_match: hours = int(hr_match.group(1))
    if min_match: minutes = int(min_match.group(1))
    return hours + (minutes / 60.0)

async def scrape_flights():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # --- STRATEGY 2: Network Optimization Interceptor ---
        # Aborts heavy, non-essential data transfers instantly before they hit the runner
        async def intercept_route(route):
            if route.request.resource_type in ["image", "font", "media"]:
                await route.abort()
            elif any(track in route.request.url for track in ["analytics", "stats", "doubleclick", "google-analytics"]):
                await route.abort()
            else:
                await route.continue_()
        
        await page.route("**/*", intercept_route)
        # ----------------------------------------------------
        
        all_results = []
        
        current_outbound = START_OUTBOUND
        outbound_dates = []
        while current_outbound <= END_OUTBOUND:
            outbound_dates.append(current_outbound)
            current_outbound += timedelta(days=1)
            
        print(f"✈️ Starting optimized search loop for {len(outbound_dates)} outbound dates...")

        for out_date in outbound_dates:
            for duration_days in [24, 25]: # ~3.5 weeks trip durations
                ret_date = out_date + timedelta(days=duration_days)
                
                out_str = out_date.strftime("%Y-%m-%d")
                ret_str = ret_date.strftime("%Y-%m-%d")
                
                print(f"Scanning: Outbound {out_str} | Return {ret_str}...")
                
                # Query specifying 2 adults and 2 checked bags (implies 1 bag per person, min 20-25kg standard)
                query = f"Flights from {ORIGIN} to {DESTINATION} for 2 adults with 2 checked bags on {out_str} through {ret_str}"
                url = f"https://www.google.com/travel/flights?q={query.replace(' ', '+')}&hl=en&curr=EUR"
                
                try:
                    # Swapped 'load' to 'domcontentloaded' to skip waiting for trailing asset downloads
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(random.uniform(1000, 2000))
                    
                    # Handle cookie consent wall
                    consent_button = page.locator('button:has-text("Accept all"), button:has-text("Agree"), button:has-text("Ik ga akkoord")').first
                    if await consent_button.is_visible():
                        await consent_button.click()
                        await page.wait_for_timeout(1000)
                    
                    await page.wait_for_selector('role=listitem', timeout=8000)
                    flight_rows = await page.locator('role=listitem').all()
                    
                    for row in flight_rows:
                        text_content = await row.inner_text()
                        if not text_content or "€" not in text_content:
                            continue
                        
                        # Rule Verification: Confirm flight strictly departs from AMS
                        if "AMS" not in text_content:
                            continue
                        
                        lines = [line.strip() for line in text_content.split("\n") if line.strip()]
                        if len(lines) < 4 or "Hide" in lines[0] or "Separate tickets" in lines[0]:
                            continue
                        
                        try:
                            # 1. Parse total combined price directly as an Integer
                            price_match = re.search(r'€\s*([\d.,]+)', text_content)
                            if not price_match: continue
                            total_price_int = int(price_match.group(1).replace('.', '').replace(',', ''))
                            
                            # 2. Flight Duration Check
                            duration_match = re.search(r'(\d+\s*hr\s*\d*\s*min|\d+\s*hr)', text_content)
                            duration_str = duration_match.group(1) if duration_match else "Unknown"
                            total_hours = parse_duration(duration_str)
                            
                            if total_hours > MAX_DURATION_HOURS:
                                continue
                            
                            # 3. Stopover Check
                            stops = 0
                            layover_city = "Nonstop"
                            if "1 stop" in text_content:
                                stops = 1
                                stop_match = re.search(r'1 stop\s*([\d\s*hr\s*min]*)\s*([A-Z]{3})', text_content)
                                if stop_match: layover_city = stop_match.group(2)
                            elif "2 stops" in text_content or "3 stops" in text_content:
                                continue
                            
                            if stops > MAX_STOPS:
                                continue
                            
                            # 4. Filter and Isolate Clean Airline String
                            depart_time = lines[0]
                            airline = "Unknown Airline"
                            
                            for line in lines:
                                if re.search(r'\d{1,2}:\d{2}', line): continue # Filter times
                                if line in ["–", "-", "—", "Separate tickets"]: continue # Filter symbols/artifacts
                                if "hr" in line or "min" in line or "stop" in line or "Nonstop" in line: continue
                                if "€" in line: continue
                                if re.search(r'^[A-Z]{3}[–\-][A-Z]{3}$', line): continue # Filter routing stacks (e.g. AMS-DPS)
                                
                                airline = line
                                break

                            # 5. Strange/Irregular Conditions Verification
                            strange_conditions = "None"
                            condition_flags = ["Change of airport", "Overnight stay", "Long layover", "Separate tickets"]
                            matched_conditions = [c for c in condition_flags if c.lower() in text_content.lower()]
                            if matched_conditions:
                                strange_conditions = ", ".join(matched_conditions)

                            all_results.append({
                                "departure_date": out_str,
                                "return_date": ret_str,
                                "trip_duration_days": duration_days,
                                "airline": airline,
                                "stops": stops,
                                "layover_airport": layover_city,
                                "departing_time": depart_time,
                                "travel_time": duration_str,
                                "total_price_2_passengers": total_price_int, # Saved cleanly as int
                                "strange_conditions": strange_conditions
                            })
                            break
                        except Exception as e:
                            continue
                            
                except Exception as e:
                    continue
                    
        # Sort entirely by cheapest option
        all_results = sorted(all_results, key=lambda x: x["total_price_2_passengers"])
        
        os.makedirs("output", exist_ok=True)
        with open("output/flights_report.json", "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=4)
            
        print(f"🎉 Scraping complete. Clean results compiled successfully.")

if __name__ == "__main__":
    asyncio.run(scrape_flights())
