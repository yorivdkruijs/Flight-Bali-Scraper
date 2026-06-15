import asyncio
import os
import random
import re
from playwright.async_api import async_playwright

async def scrape_flight_grid():
    async with async_playwright() as p:
        # Launch headless browser with stealth arguments
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Fast-Route Interceptor: Aborts high-overhead media assets instantly
        async def intercept_route(route):
            if route.request.resource_type in ["image", "font", "media"]:
                await route.abort()
            elif any(track in route.request.url for track in ["analytics", "stats", "doubleclick"]):
                await route.abort()
            else:
                await route.continue_()
        
        await page.route("**/*", intercept_route)
        
        # Construct an anchor query using the exact filters to pre-sort the grid state
        origin = "AMS"
        destination = "DPS"
        
        # Injecting filters directly into the text query ensures the Date Grid honors your constraints
        query = f"Flights from {origin} to {destination} for 2 adults with 2 checked bags max 1 stop under 19 hours on 2026-07-25 through 2026-08-19"
        base_url = f"https://www.google.com/travel/flights?q={query.replace(' ', '+')}&hl=en&curr=EUR"
        
        print("🌐 Navigating to optimized Google Flights anchor profile...")
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1000)
            
            # Auto-clear European consent frames
            consent_button = page.locator('button:has-text("Accept all"), button:has-text("Agree"), button:has-text("Ik ga akkoord")').first
            if await consent_button.is_visible():
                await consent_button.click()
                await page.wait_for_timeout(1000)
            
            print("📊 Clicking and expanding the visual Date Grid engine...")
            # Target and fire the primary Date Grid modal controller
            date_grid_trigger = page.locator('button:has-text("Date grid")').first
            await date_grid_trigger.click()
            
            # Wait for the table dialogue overlay to present itself safely
            await page.wait_for_selector('role=dialog', timeout=10000)
            await page.wait_for_timeout(2000) # Settle rendering layout shifts
            
            # Google utilizes robust accessibility labels on active price elements within the grid matrix
            # Target cells containing pricing text strings
            cells = await page.locator('[role="gridcell"][aria-label*="Price"], [role="gridcell"][aria-label*="€"]').all()
            
            all_matrix_deals = []
            
            for cell in cells:
                aria_text = await cell.get_attribute("aria-label")
                if not aria_text:
                    continue
                
                # Match structures like: "Leaving Saturday, July 25 and returning Wednesday, August 19. Price from €1,150."
                # Or short tags: "Depart Jul 25, Return Aug 19. Price from €1,150"
                try:
                    price_match = re.search(r'€\s*([\d.,]+)', aria_text)
                    if not price_match:
                        continue
                        
                    # Isolate clean total cost integer values
                    total_price_int = int(price_match.group(1).replace('.', '').replace(',', ''))
                    
                    # Store data points extracted safely via string isolation patterns
                    all_matrix_deals.append({
                        "raw_label": aria_text,
                        "price": total_price_int
                    })
                except Exception:
                    continue
            
            # Sort full collective array cleanly by base price minimums
            all_matrix_deals = sorted(all_matrix_deals, key=lambda x: x["price"])
            
            print("\n" + "="*80)
            print("🏆 TOP 10 CHEAPEST VISUAL DATE GRID FARES IDENTIFIED (AMS ✈️ DPS Matrix)")
            print("="*80)
            
            if not all_matrix_deals:
                print("❌ No active matrix data points located. Verify UI selector tags.")
            else:
                for idx, deal in enumerate(all_matrix_deals[:10], start=1):
                    print(f"\n🔥 [GRID OPTION #{idx}]")
                    print(f"   📝 Route Profile: {deal['raw_label']}")
                    print(f"   💰 Total Collective Matrix Cost: €{deal['price']}")
            print("\n" + "="*80)
            
        except Exception as e:
            print(f"❌ Automation runtime disruption detected: {str(e)}")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(scrape_flight_grid())
