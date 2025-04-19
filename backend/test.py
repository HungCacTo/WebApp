import sqlite3
import math
import requests
import json
from datetime import datetime

# Configuration
CATEGORIES = ["Clothes", "Food", "Books", "Toys", "Electronics"]
WAREHOUSES = [
    {"name": "Charity Hub A", "lat": 37.7749, "lon": -122.4194},  # San Francisco
    {"name": "Charity Hub B", "lat": 40.7128, "lon": -74.0060},   # New York
    {"name": "Charity Hub C", "lat": 34.0522, "lon": -118.2437},  # Los Angeles
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"

# SQLite database setup
def setup_database():
    conn = sqlite3.connect("donating.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT,
            user_lat REAL,
            user_lon REAL,
            user_address TEXT,
            category TEXT,
            warehouse_name TEXT,
            warehouse_lat REAL,
            warehouse_lon REAL,
            distance TEXT,
            duration TEXT,
            polyline TEXT,
            status TEXT,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

# Haversine formula
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a))
    return R * c

# Find nearest warehouse
def find_nearest_warehouse(user_lat, user_lon):
    nearest = None
    min_distance = float("inf")
    for warehouse in WAREHOUSES:
        distance = haversine(user_lat, user_lon, warehouse["lat"], warehouse["lon"])
        if distance < min_distance:
            min_distance = distance
            nearest = warehouse
    return nearest, min_distance

# Geocode address to lat/lon
def geocode_address(address):
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": "CharityDonationApp"},
            timeout=5
        )
        if response.status_code != 200:
            return None, None, f"Nominatim HTTP error: {response.status_code}"
        data = response.json()
        if not data or 'lat' not in data[0] or 'lon' not in data[0]:
            return None, None, "No valid results found for address"
        lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None, None, f"Invalid coordinates: ({lat}, {lon})"
        return lat, lon, None
    except ValueError as e:
        return None, None, f"Geocoding parse error: {str(e)}"
    except Exception as e:
        return None, None, f"Geocoding error: {str(e)}"

# Reverse geocode lat/lon to address
def reverse_geocode(lat, lon):
    try:
        response = requests.get(
            NOMINATIM_REVERSE_URL,
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "CharityDonationApp"},
            timeout=5
        )
        data = response.json()
        return data.get("display_name", "Unknown address")
    except:
        return "Unknown address"

# Get OSRM directions
def get_directions(origin_lon, origin_lat, dest_lon, dest_lat):
    try:
        # Validate coordinates
        for coord, name in [(origin_lat, "origin_lat"), (origin_lon, "origin_lon"),
                          (dest_lat, "dest_lat"), (dest_lon, "dest_lon")]:
            if not isinstance(coord, (int, float)):
                return None, f"Non-numeric coordinate: {name}={coord}"
            if name in ["origin_lat", "dest_lat"] and not (-90 <= coord <= 90):
                return None, f"Invalid latitude: {name}={coord}"
            if name in ["origin_lon", "dest_lon"] and not (-180 <= coord <= 180):
                return None, f"Invalid longitude: {name}={coord}"

        # Check if coordinates are identical or very close
        if abs(origin_lon - dest_lon) < 1e-6 and abs(origin_lat - dest_lat) < 1e-6:
            return {
                "distance": "0 km",
                "duration": "0 mins",
                "steps": [{"instruction": "No route needed (same location)", "distance": "0 km", "duration": "0 mins"}],
                "polyline": [[origin_lat, origin_lon]]
            }, None

        # Format for OSRM (lon,lat)
        origin = f"{origin_lon},{origin_lat}"
        destination = f"{dest_lon},{dest_lat}"
        url = f"http://router.project-osrm.org/route/v1/driving/{origin};{destination}?overview=full&steps=true&geometries=geojson"
        print(f"OSRM URL: {url}")
        
        response = requests.get(url, timeout=5)
        print(f"OSRM status: {response.status_code}")
        if response.status_code != 200:
            return None, f"OSRM request failed: HTTP {response.status_code}"

        # Parse JSON
        try:
            data = response.json()
        except ValueError as e:
            return None, f"OSRM returned invalid JSON: {str(e)}"

        # Validate response
        if not isinstance(data, dict):
            return None, f"OSRM response is not a dict: {data}"
        if data.get("code") != "Ok":
            return None, f"OSRM error: {data.get('message', 'Unknown error')}"

        routes = data.get("routes")
        if not routes:
            return None, "No routes found"

        route = routes[0]
        coordinates = route.get("geometry", {}).get("coordinates", [])
        if not coordinates:
            return None, "No route geometry found"

        polyline = [[lat, lon] for lon, lat in coordinates]

        return {
            "distance": f"{route.get('distance', 0)/1000:.1f} km",
            "duration": f"{route.get('duration', 0)/60:.1f} mins",
            "steps": [
                {
                    "instruction": step["maneuver"].get("instruction", "Proceed"),
                    "distance": f"{step.get('distance', 0)/1000:.1f} km",
                    "duration": f"{step.get('duration', 0)/60:.1f} mins"
                } for step in route.get("legs", [{}])[0].get("steps", [])
            ],
            "polyline": polyline
        }, None
    except requests.RequestException as e:
        return None, f"OSRM network error: {str(e)}"
    except Exception as e:
        return None, f"Routing error: {str(e)}"

# Save donation
def save_donation(user_name, user_lat, user_lon, user_address, category, warehouse, distance, duration, polyline):
    conn = sqlite3.connect("donating.db")
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO donations (
            user_name, user_lat, user_lon, user_address, category,
            warehouse_name, warehouse_lat, warehouse_lon,
            distance, duration, polyline, status, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_name, user_lat, user_lon, user_address or "", category,
        warehouse["name"], warehouse["lat"], warehouse["lon"],
        distance, duration, json.dumps(polyline), "Pending", timestamp
    ))
    conn.commit()
    conn.close()

# Retrieve donations
def get_donations():
    conn = sqlite3.connect("donating.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM donations ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    donations = [
        {
            "id": row[0],
            "username": row[1],
            "user_latitude": row[2],
            "user_longitude": row[3],
            "user_address": row[4] or reverse_geocode(row[2], row[3]),
            "category": row[5],
            "warehouse_name": row[6],
            "warehouse_latitude": row[7],
            "warehouse_longitude": row[8],
            "warehouse_address": reverse_geocode(row[7], row[8]),
            "distance": row[9],
            "duration": row[10],
            "polyline": json.loads(row[11]),
            "status": row[12],
            "timestamp": row[13]
        }
        for row in rows
    ]
    return donations

# Test function
def test_donation(category, address, user_name):
    print(f"\nTesting donation: Category={category}, Address={address}")
    print(f"\nTesting donation: Name = {user_name}")
    
    # Validate category
    if category not in CATEGORIES:
        print(f"Error: Invalid category. Choose from: {', '.join(CATEGORIES)}")
        return
    
    # Geocode address
    user_lat, user_lon, error = geocode_address(address)
    if user_lat is None:
        print(f"Error: Could not geocode address: {error}")
        return
    
    print(f"Geocoded: ({user_lat}, {user_lon})")
    
    # Find nearest warehouse
    warehouse, min_distance_km = find_nearest_warehouse(user_lat, user_lon)
    if not warehouse:
        print("Error: No warehouses found")
        return
    
    print(f"Nearest warehouse: {warehouse['name']} at ({warehouse['lat']}, {warehouse['lon']})")
    
    # Get directions
    route, error = get_directions(user_lon, user_lat, warehouse["lon"], warehouse["lat"])
    if not route:
        print(f"Error: Could not calculate route: {error}")
        return
    
    print(f"Route: Distance={route['distance']}, Duration={route['duration']}")
    print(f"Polyline: {route['polyline'][:2]}... ({len(route['polyline'])} points)")
    
    # Save donation
    save_donation(user_name, user_lat, user_lon, address, category, warehouse,
                 route["distance"], route["duration"], route["polyline"])
    print("Donation saved")
    
    # Retrieve and print donations
    donations = get_donations()
    print("\nCurrent donations:")
    for d in donations:
        print(f"ID: {d['id']}, Category: {d['category']}, Address: {d['user_address']}, "
              f"Warehouse: {d['warehouse_name']}, Distance: {d['distance']}, Status: {d['status']}")

def clear_database():
        conn = sqlite3.connect('donations.db')
        c = conn.cursor()
        c.execute('DELETE FROM donations')
        max_id = conn.fetchone()[0] or 0
        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'donations'", (max_id,))
        conn.commit()
        conn.close()
# Main test
if __name__ == "__main__":
    clear_database()