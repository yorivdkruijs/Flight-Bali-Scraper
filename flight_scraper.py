import asyncio
import random
from datetime import datetime, timedelta
import re
from playwright.async_api import async_playwright

# Configuration Parameters
ORIGIN = "AMS"
DESTINATION = "DPS"
YEAR = 2026  
START_OUTBOUND = datetime(YEAR, 7, 19)
END_OUTBOUND = datetime(YEAR, 8, 1)
MAX_STOPS = 1
MAX_DURATION_HOURS = 19

def parse_duration(duration_str):
    """Converts a string like '16 hr 45 min' into total decimal hours."""
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
        
        # Optimize asset routing to save bandwidth and speed up actions
        async def intercept_route(route):
            if route.request.resource_type in ["image", "font", "media"]:
                await route.abort()
            elif any(track in route.request.url for track in ["analytics", "stats", "doubleclick", "google-analytics"]):
                await route.abort()
            else:
                await route.continue_()
        
        await page.route("**/*", intercept_route)
        
        all_results = []
        
        current_outbound = START_OUTBOUND
        outbound_dates = []
        while current_outbound <= END_OUTBOUND:
            outbound_dates.append(current_outbound)
            current_outbound += timedelta(days=1)
            
        print(f"✈️ Starting sequential flight search matrix execution (Strategy 2)...")

        for out_date in outbound_dates:
            for duration_days in [24, 25]: 
                ret_date = out_date + timedelta(days=duration_days)
                
                out_str = out_date.strftime("%Y-%m-%d")
                ret_str = ret_date.strftime("%Y-%m-%d")
                
                # FIXED: Swapped out the ghost domain for the true live production URL
                query = f"Flights from {ORIGIN} to {DESTINATION} for 2 adults on {out_str} through {ret_str}"
                url = f"https://www.google.com/travel/flights?q={query.replace(' ', '+')}&hl=en&curr=EUR"
                
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await page.wait_for_timeout(random.uniform(800, 1500))
                    
                    # Bypass potential European cookie frames dynamically
                    consent_button = page.locator('button:has-text("Accept all"), button:has-text("Agree"), button:has-text("Ik ga akkoord"), button:has-text("Alles accepteren")').first
                    if await consent_button.is_visible():
                        await consent_button.click()
                        await page.wait_for_timeout(800)
                    
                    # Look for flight item blocks across standard and fallback class definitions
                    await page.wait_for_selector('role=listitem, [data-flight-ticket]', timeout=6000)
                    flight_rows = await page.locator('role=listitem, [data-flight-ticket]').all()
                    
                    for row in flight_rows:
                        text_content = await row.inner_text()
                        if not text_content or "€" not in text_content:
                            continue
                        
                        # Validate departure origin directly from text signature
                        if "AMS" not in text_content:
                            continue
                        
                        lines = [line.strip() for line in text_content.split("\n") if line.strip()]
                        if len(lines) < 4 or "Hide" in lines[0] or "Separate tickets" in lines[0]:
                            continue
                        
                        try:
                            # Clean price parsing
                            price_match = re.search(r'€\s*([\d.,]+)', text_content)
                            if not price_match: continue
                            total_price_int = int(price_match.group(1).replace('.', '').replace(',', ''))
                            
                            # Duration filters enforcement
                            duration_match = re.search(r'(\d+\s*hr\s*\d*\s*min|\d+\s*hr)', text_content)
                            duration_str = duration_match.group(1) if duration_match else "Unknown"
                            total_hours = parse_duration(duration_str)
                            
                            if total_hours > MAX_DURATION_HOURS:
                                continue
                            
                            # Layover step validation
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
                            
                            # Cleanly extract airline signature
                            depart_time = lines[0]
                            airline = "Unknown Airline"
                            for line in lines:
                                if re.search(r'\d{1,2}:\d{2}', line): continue
                                if line in ["–", "-", "—", "Separate tickets", "Best", "Cheapest"]: continue
                                if "hr" in line or "min" in line or "stop" in line or "Nonstop" in line: continue
                                if "€" in line: continue
                                if re.search(r'^[A-Z]{3}[–\-][A-Z]{3}$', line): continue
                                airline = line
                                break

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
                                "total_price_2_passengers": total_price_int,
                                "strange_conditions": strange_conditions
                            })
                            break # Step out to avoid capturing duplicate rows from single date loop
                        except Exception:
                            continue
                except Exception:
                    continue
                    
        # Sort listings to present absolute lowest cost pairs first
        all_results = sorted(all_results, key=lambda x: x["total_price_2_passengers"])
        
        # --- OUTPUT PANEL ---
        print("\n" + "="*80)
        print("🏆 TOP 5 CHEAPEST FLIGHT COMBINATIONS FOUND (AMS ✈️ DPS)")
        print("="*80)
        
        if not all_results:
            print("❌ No valid flights matching your rules were parsed during this execution sequence.")
        else:
            for idx, flight in enumerate(all_results[:5], start=1):
                print(f"\n🔥 [OPTION #{idx}]")
                print(f"   📅 Dates: {flight['departure_date']} to {flight['return_date']} ({flight['trip_duration_days']} days)")
                print(f"   💺 Airline: {flight['airline']}")
                print(f"   🛑 Stops: {flight['stops']} ({flight['layover_airport']})")
                print(f"   🕒 Departs: {flight['departing_time']} | ⏳ Duration: {flight['travel_time']}")
                print(f"   💰 Price (2 Pax): €{flight['total_price_2_passengers']}")
                print(f"   ⚠️ Conditions: {flight['strange_conditions']}")
        
        # --- GENERAL HEALTH CHECK & VALIDATION STATUS ---
        print("\n" + "="*80)
        print("🔍 RUNTIME HEALTH CHECK VERIFICATION SUMMARY")
        print("="*80)
        print(f"📊 Total raw combinations analyzed: {len(outbound_dates) * 2}")
        print(f"✅ Total successful filter matches: {len(all_results)}")
        if len(all_results) > 0:
            print("🟢 STATUS: SUCCESS. Flight parsing completed with valid relevant data output.")
        else:
            print("🔴 STATUS: EMPTY OUTPUT. Google served a dynamic wall or zero items passed your criteria.")
        print("="*80 + "\n")

if __name__ == "__main__":
    asyncio.run(scrape_flights())
