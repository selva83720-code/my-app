import pandas as pd
from math import radians, cos, sin, asin, sqrt
import re
import os
import google.generativeai as genai
import folium
import json

# Moved haversine function outside the class to be a static utility function
def haversine(lat1, lon1, lat2, lon2):
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    return 6371 * c

def format_minutes_to_hours(minutes):
    """Converts total minutes into a 'X hr Y min' string format."""
    if minutes < 0:
        return "0 min"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    
    parts = []
    if hours > 0:
        parts.append(f"{hours} hr")
    if mins > 0:
        parts.append(f"{mins} min")
        
    return " ".join(parts) if parts else "0 min"

class WalkarooTravelPlanner:
    def __init__(self):
        # ✅ Configure Gemini API
        GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
        if not GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY environment variable not set. Please add it to your .env file.")
        genai.configure(api_key=GOOGLE_API_KEY)

        # ✅ Load single Excel sheet once
        self.source_file_path = r"public/Travel_plan 3.xlsx"
        self.data_df = pd.read_excel(self.source_file_path, sheet_name="Sheet1")

        # Define constants for routing
        self.VISIT_TIME_PER_SHOP = 20
        self.TOTAL_BREAK_TIME = 75
        self.AVG_SPEED_KMH = 25
        self.TOTAL_WORKDAY_MINUTES = 9 * 60
        self.preprocess()

    def _sanitize_columns(self):
        """Cleans all column names to a standard format (lowercase, snake_case)."""
        cols = self.data_df.columns
        new_cols = []
        for col in cols:
            # Remove special characters like '*', convert to lowercase, replace spaces with underscores
            clean_col = re.sub(r'[^a-zA-Z0-9\s]', '', col).lower().strip().replace(' ', '_')
            new_cols.append(clean_col)
        self.data_df.columns = new_cols
        print(f"[*] Sanitized column names to: {self.data_df.columns.tolist()}")

    def preprocess(self):
        self._sanitize_columns()
        # Normalize key text columns for consistent filtering
        self.data_df['distributorname'] = self.data_df['distributorname'].astype(str).str.strip().str.lower()
        self.data_df["market"] = self.data_df["market"].astype(str).str.strip().str.lower()

    def _find_route_for_9_hours(self, shops, start_lat, start_lon):
        """
        Calculates the optimal route that can be completed within a 9-hour workday.
        This is a private helper method for the class.
        """
        AVAILABLE_TIME = self.TOTAL_WORKDAY_MINUTES - self.TOTAL_BREAK_TIME
        
        # This is a pure nearest-neighbor implementation.
        unvisited = shops.copy()
        route = []
        current_lat, current_lon = start_lat, start_lon
        time_used = 0

        while unvisited and time_used < AVAILABLE_TIME:
            # From the current location, find the nearest shop from ALL unvisited shops.
            nearest_shop = min(unvisited, key=lambda shop: haversine(current_lat, current_lon, shop["lat"], shop["lon"]))
            
            distance_to_shop = haversine(current_lat, current_lon, nearest_shop["lat"], nearest_shop["lon"])
            travel_time = (distance_to_shop / self.AVG_SPEED_KMH) * 60
            
            time_for_this_stop = travel_time + self.VISIT_TIME_PER_SHOP

            if time_used + time_for_this_stop <= AVAILABLE_TIME:
                # If there's enough time, add the shop to the route
                time_used += time_for_this_stop
                nearest_shop['distance_from_previous'] = distance_to_shop
                nearest_shop['travel_time_from_previous'] = travel_time
                route.append(nearest_shop)
                unvisited.remove(nearest_shop)
                # Update the current location to the shop that was just visited
                current_lat, current_lon = nearest_shop["lat"], nearest_shop["lon"]
            else:
                # Not enough time for this stop, so the route is complete.
                break
        return route

    def plan_optimal_route(self, market, dealer):
        # Defensively clean inputs here to handle hidden characters like newlines, ensuring robust filtering.
        market = re.sub(r'\s+', ' ', market).strip().lower()
        dealer = re.sub(r'\s+', ' ', dealer).strip().lower()
        print(f"\n[PROCESS START] Generating route for Market: '{market}', Dealer: '{dealer}'")
        
        # Create a copy of the main dataframe to work with
        combined_df = self.data_df.copy()

        market_mask = combined_df["market"].str.contains(re.escape(market), na=False) if market else True

        if dealer:
            # Preprocess the user's dealer input to be more forgiving.
            # This will handle cases like "saleem brothers(cbe)-rush order" by searching for the core name.
            dealer_search_term = re.split(r'\(|-', dealer)[0].strip()
            print(f"[*] Searching for simplified dealer term: '{dealer_search_term}'")
            # Filter on the sanitized distributorname column
            dealer_mask = combined_df["distributorname"].str.contains(re.escape(dealer_search_term), case=False, na=False)

        final_df = combined_df[market_mask & dealer_mask]
        print(f"[1. FILTERING] Found {len(final_df)} initial records matching criteria.")

        if final_df.empty:
            if dealer:
                print(f"[ERROR] No retailers found for the combination of Market: '{market}' and Dealer: '{dealer}'.")
            else:
                print(f"[ERROR] No retailers found for the given market: '{market}'.")
            return None, None, None

        # Create a working copy to avoid SettingWithCopyWarning
        working_df = final_df.copy()
        
        # Convert 'LAST VISITED DATE' to datetime for proper sorting. 'coerce' handles errors.
        working_df['last_visit_dt'] = pd.to_datetime(working_df['last_visited_date'], errors='coerce')
        
        # Sort by last visit date to prioritize shops that haven't been visited in a long time.
        working_df.sort_values(by='last_visit_dt', ascending=True, na_position='first', inplace=True)

        # Now that shops are prioritized, drop duplicates to keep only the highest-priority entry for each shop.
        selected_shops_df = working_df.drop_duplicates(subset=["outletname"], keep='first').reset_index(drop=True)
        print(f"[2. PRIORITIZING] Sorted {len(selected_shops_df)} unique shops by last visit date (oldest first).")
        
        if selected_shops_df.empty:
            print("No retailers found with valid data.")
            return None, None, None

        # Find the first row with valid salesperson coordinates in the priority-sorted dataframe
        valid_start_point_df = selected_shops_df[
            selected_shops_df["salesperson_latitude"].notna() & 
            selected_shops_df["salesperson_longitude"].notna()
        ].copy()

        if valid_start_point_df.empty:
            print(f"[ERROR] Could not generate route for Market '{market}' and Dealer '{dealer}' because no valid salesperson start coordinates were found.")
            return None, None, None

        # Use the coordinates from the highest-priority shop that has them
        salesperson_lat = valid_start_point_df.iloc[0]["salesperson_latitude"]
        salesperson_lon = valid_start_point_df.iloc[0]["salesperson_longitude"]
        start_shop_name = valid_start_point_df.iloc[0]["outletname"]
        print(f"[3. START POINT] Selected salesperson start location ({salesperson_lat}, {salesperson_lon}) based on highest-priority shop: '{start_shop_name}'.")

        try:
            salesperson_lat = float(salesperson_lat)
            salesperson_lon = float(salesperson_lon)
        except (ValueError, TypeError):
            print(f"[ERROR] Could not convert start coordinates to float for Market '{market}' and Dealer '{dealer}'. Values: {salesperson_lat}, {salesperson_lon}")
            return None, None, None

        shop_distances = []
        for _, row in selected_shops_df.iterrows():
            shop_lat = row["latitude"]
            shop_lon = row["longitude"]
            if pd.isna(shop_lat) or pd.isna(shop_lon):
                continue
            try:
                shop_lat = float(shop_lat)
                shop_lon = float(shop_lon)
                shop_distances.append({
                    "shop": row["outletname"],
                    "lat": shop_lat,
                    "lon": shop_lon,
                    "last_visit": str(row["last_visited_date"]) if pd.notna(row["last_visited_date"]) else "Never"
                })
            except (ValueError, TypeError):
                continue

        print(f"[4. DATA PREP] Prepared {len(shop_distances)} shops with valid coordinates for routing.")

        if not shop_distances:
            print("[ERROR] No retailers found with valid coordinates. Please check your data.")
            return None, None, None

        print("[5. ROUTING] Starting 9-hour nearest-neighbor route calculation...")
        optimal_route = self._find_route_for_9_hours(shop_distances, salesperson_lat, salesperson_lon)
        print(f"[5. ROUTING] Calculation complete. Optimal route contains {len(optimal_route)} stops.")

        # Prepare data for the prompt
        total_distance = sum(s.get('distance_from_previous', 0) for s in optimal_route)
        total_travel_time = sum(s.get('travel_time_from_previous', 0) for s in optimal_route)
        total_visit_time = len(optimal_route) * self.VISIT_TIME_PER_SHOP
        total_workday_time = total_travel_time + total_visit_time + self.TOTAL_BREAK_TIME

        # Format times into "X hr Y min" strings for display
        total_travel_time_str = format_minutes_to_hours(total_travel_time)
        total_visit_time_str = format_minutes_to_hours(total_visit_time)
        total_break_time_str = format_minutes_to_hours(self.TOTAL_BREAK_TIME)
        total_workday_time_str = format_minutes_to_hours(total_workday_time)

        # Since dealer name is now required, we can use it directly.
        dealer_name_for_output = dealer

        # Create a detailed list of stops for the AI to use
        shops_info_for_prompt = ""
        for i, shop in enumerate(optimal_route, 1):
            shops_info_for_prompt += (
                f"Stop {i}:\n"
                f"  Shop Name: {shop['shop']}\n"
                f"  Last Visit: {shop['last_visit']}\n"
                f"  Distance from Previous: {shop.get('distance_from_previous', 0):.2f} km\n"
                f"  Travel Time: {shop.get('travel_time_from_previous', 0):.0f} min\n\n"
            )

        # Prepare the prompt for Gemini
        system_prompt = """
You are a route planning assistant. Your task is to format a pre-calculated travel plan into a specific report format.
You will be given a list of stops in the correct order, along with travel details for each stop.
You must use the provided data to generate a report that follows the user's requested format EXACTLY.
Do not add any extra text, explanations, or summaries beyond what is requested in the format.
"""


        user_prompt = f"""
**TASK:**
Format the output EXACTLY like this, using the data provided below.

**FORMAT:**
- Market Name: {market}
- Dealer Name: {dealer_name_for_output}
1) First Stop  
   Shop Name: [Shop Name]  
   Last Visit: [Date]  
   Distance from Previous: [km]  
   Travel Time (with traffic): [min]

2) Next Stop  
   Shop Name: [Shop Name]  
   Last Visit: [Date]  
   Distance from Previous: [km]  
   Travel Time (with traffic): [min]

[Continue for all stops]

At the end, output:
- Total Distance: [km]
- Total Travel Time (travel only, with traffic): [hr min]
- Total Visit Time: [hr min]
- Break Time: [hr min]
- Total Workday Time: [hr min]

- - -

**ROUTE DATA:**
{shops_info_for_prompt}

**SUMMARY TOTALS:**
- Total Distance: {total_distance:.2f} km
- Total Travel Time: {total_travel_time_str}
- Total Visit Time: {total_visit_time_str}
- Break Time: {total_break_time_str}
- Total Workday Time: {total_workday_time_str}

Do not add any explanation or extra text.
"""

        print("[6. REPORTING] Sending calculated route to AI for formatting.")
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            response = model.generate_content(system_prompt + user_prompt)
            gpt_route = response.text.strip()
        except genai.types.generation_types.BlockedPromptException as e:
            print(f"[ERROR] The prompt was blocked by Google's safety settings: {e}")
            gpt_route = "Report generation failed because the content was blocked by safety filters. Please check the data for any sensitive information."
        except Exception as e:
            # Catch other potential API errors, including the ResourceExhausted error.
            print(f"[ERROR] An API error occurred during AI formatting: {e}")
            # Provide a user-friendly message that will be displayed in the results box.
            gpt_route = "Could not generate the final report due to an API error. This is often caused by exceeding the daily usage quota. Please try again later or check your API plan."


        # Create route points directly from optimal route
        route_points = [(salesperson_lat, salesperson_lon)]
        for shop in optimal_route:
            route_points.append((shop['lat'], shop['lon']))
        route_points.append((salesperson_lat, salesperson_lon))

        print("[7. MAP GENERATION] Creating interactive map file.")
        m = folium.Map(location=[salesperson_lat, salesperson_lon], zoom_start=12)
        folium.Marker(
            [salesperson_lat, salesperson_lon],
            tooltip="Salesperson Start/End",
            icon=folium.Icon(color="green", icon="star")
        ).add_to(m)

        for i, shop in enumerate(optimal_route):
            folium.Marker(
                [shop['lat'], shop['lon']], 
                tooltip=f"{i+1}. {shop['shop']}",
                popup=f"<b>Stop {i+1}</b><br>Shop: {shop['shop']}<br>Last Visit: {shop['last_visit']}"
            ).add_to(m)

        folium.PolyLine(route_points, color="blue", weight=4, opacity=0.7).add_to(m)

        os.makedirs("static", exist_ok=True)
        map_path = os.path.join("static", "route_map.html")
        m.save(map_path)
        map_url = "/" + map_path

        retailers_json = [
            {"name": "Salesperson Start/End", "lat": salesperson_lat, "lng": salesperson_lon, "type": "start"}
        ] + [
            {"name": shop['shop'], "lat": shop['lat'], "lng": shop['lon'], "type": "shop"}
            for shop in optimal_route
        ]

        print("[PROCESS END] Route plan generated successfully. Returning results.")
        return (gpt_route, map_url, retailers_json)
