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
        # Launch headless browser with defensive settings to avoid detection
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        all_results = []
        
        # Calculate outbound dates
        current_outbound = START_OUTBOUND
        outbound_dates = []
        while current_outbound <= END_OUTBOUND:
            outbound_dates.append(current_outbound)
            current_outbound += timedelta(days=1)
            
        print(f"✈️ Starting search loop for {len(outbound_dates)} outbound dates...")

        for out_date in outbound_dates:
            # Check 24 and 25 days trip durations (~3.5 weeks)
            for duration_days in [24, 25]:
                ret_date = out_date + timedelta(days=duration_days)
                
                out_str = out_date.strftime("%Y-%m-%d")
                ret_str = ret_date.strftime("%Y-%m-%d")
                
                print(f"Scanning: Outbound {out_str} | Return {ret_str} ({duration_days} days total)...")
                
                # Construct Natural Language Google Flights Deep Link
                url = f"https://www.google.com/travel/flights?q=Flights+from+{ORIGIN}+to+{DESTINATION}+on+{out_str}+through+{ret_str}&hl=en&curr=EUR"
                
                try:
                    await page.goto(url, wait_until="load", timeout=30000)
                    await page.wait_for_timeout(random.uniform(2000, 4000))
                    
                    # Intercept and auto-dismiss Google's European Cookie Consent banner if it triggers
                    consent_button = page.locator('button:has-text("Accept all"), button:has-text("Agree"), button:has-text("Ik ga akkoord")').first
                    if await consent_button.is_visible():
                        await consent_button.click()
                        await page.wait_for_timeout(1500)
                    
                    # Wait for flight card container rows to render
                    await page.wait_for_selector('role=listitem', timeout=10000)
                    flight_rows = await page.locator('role=listitem').all()
                    
                    for row in flight_rows:
                        text_content = await row.inner_text()
                        if not text_content or "€" not in text_content:
                            continue
                        
                        # Clean and split elements inside the flight card text block
                        lines = [line.strip() for line in text_content.split("\n") if line.strip()]
                        
                        # Skip administrative or UI-only blocks
                        if len(lines) < 4 or "Hide" in lines[0] or "Separate tickets" in lines[0]:
                            continue
                        
                        # Data Extraction safely wrapped via Text Processing
                        try:
                            # 1. Price Matching (Looks for standard € pricing)
                            price_match = re.search(r'€\s*([\d.,]+)', text_content)
                            if not price_match: continue
                            price_per_person = int(price_match.group(1).replace('.', '').replace(',', ''))
                            total_price_for_two = price_per_person * 2  # Scaled for 2 persons
                            
                            # 2. Flight Duration Check
                            duration_match = re.search(r'(\d+\s*hr\s*\d*\s*min|\d+\s*hr)', text_content)
                            duration_str = duration_match.group(1) if duration_match else "Unknown"
                            total_hours = parse_duration(duration_str)
                            
                            # Filter Rule 1: Max travel time cap (19 hours)
                            if total_hours > MAX_DURATION_HOURS:
                                continue
                            
                            # 3. Stopover Verification
                            stops = 0
                            layover_city = "Nonstop"
                            if "1 stop" in text_content:
                                stops = 1
                                # Isolate layout code tokens for layover city codes (e.g., SIN, DXB)
                                stop_match = re.search(r'1 stop\s*([\d\s*hr\s*min]*)\s*([A-Z]{3})', text_content)
                                if stop_match: layover_city = stop_match.group(2)
                            elif "2 stops" in text_content or "3 stops" in text_content:
                                stops = 2 # Will be excluded via standard filters
                                
                            # Filter Rule 2: Max Stops Cap (1 stop)
                            if stops > MAX_STOPS:
                                continue
                            
                            # 4. Extracting Core Identifiers (Airline & Depart Time)
                            depart_time = lines[0]  # Usually the first element in the text stack
                            airline = lines[1] if len(lines) > 1 else "Unknown Airline"
                            
                            # 5. Identifying Strange/Irregular Conditions
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
                                "total_price_2_passengers": f"€{total_price_for_two}",
                                "strange_conditions": strange_conditions,
                                "numeric_price": total_price_for_two
                            })
                            
                            # Break out early after capturing the cheapest options on the page
                            break 
                        except Exception as e:
                            continue
                            
                except Exception as e:
                    print(f"Skipping {out_str} due to quick layout bypass error.")
                    continue
                    
        # Sort master list by price to immediately highlight the top choice
        all_results = sorted(all_results, key=lambda x: x["numeric_price"])
        
        # Save structural JSON output
        os.makedirs("output", exist_ok=True)
        with open("output/flights_report.json", "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=4)
            
        print(f"🎉 Scraping complete. Successfully compiled {len(all_results)} valid flight configurations.")

if __name__ == "__main__":
    asyncio.run(scrape_flights())
