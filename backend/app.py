from flask import Flask, request, jsonify, render_template
import sqlite3
import math
import requests
import json
from datetime import datetime
import polyline
import os

from flask_cors import CORS

app = Flask(__name__)
CORS(app)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Configuration
CATEGORIES = ["Thực phẩm khô", "Tươi sống", "Đồ hộp", "Sữa/ Đồ lạnh","Gia vị"]
DATE = ["Trong ngày", "Trong tuần", "Trong tháng", "Trên 6 tháng"]
PSMETHOD = ["Bình thường", "Cần kho mát", "Đông lạnh"]
WAREHOUSES = [
    {"name": "Foodbank kho chính", "lat": 10.85609385, "lon": 106.76522639999999}, 
    {"name": "Foodbank Quận 1", "lat": 10.7707525, "lon": 106.6976235},   
    {"name": "Foodbank Quận Bình Thạnh", "lat": 10.80484845, "lon": 106.71676215550468}, 
]
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search?"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"

# SQLite database setup
def setup_database():
    # Donations database
    conn = sqlite3.connect(os.path.join(BASE_DIR, "donations.db"))
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
            timestamp TEXT,
            date TEXT,
            quantity REAL,
            weight REAL,
            method TEXT,
            exp TEXT
        )
    """)
    conn.commit()
    conn.close()

    # Requests database
    conn = sqlite3.connect(os.path.join(BASE_DIR, "requestlist.db"))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            rid INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_name TEXT,
            requester_lat REAL,
            requester_lon REAL,
            requester_address TEXT,
            warehouse_name TEXT,
            warehouse_lat REAL,
            warehouse_lon REAL,
            distance TEXT,
            duration TEXT,
            polyline TEXT,
            status TEXT,
            timestamp TEXT,
            dry_food_qty REAL,
            fresh_food_qty REAL,
            canned_food_qty REAL,
            milk_cold_qty REAL,
            spice_qty REAL
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
            params={
                "q": address,
                "format": "json",
                "limit": 1
            },
            headers={"User-Agent": "CharityDonationApp"}
        )
        data = response.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
        return None, "No results found for address"
    except Exception as e:
        return None, str(e)

# Reverse geocode lat/lon to address
def reverse_geocode(lat, lon):
    try:
        response = requests.get(
            NOMINATIM_REVERSE_URL,
            params={
                "lat": lat,
                "lon": lon,
                "format": "json"
            },
            headers={"User-Agent": "CharityDonationApp"}
        )
        data = response.json()
        return data.get("display_name", "Unknown address")
    except:
        return "Unknown address"

# Get OSRM directions
def get_directions(origin, destination):
    try:
        origin_lon, origin_lat = map(float, origin.split(','))
        dest_lon, dest_lat = map(float, destination.split(','))

        if not (-90 <= origin_lat <= 90 and -180 <= origin_lon <= 180 and
                -90 <= dest_lat <= 90 and -180 <= dest_lon <= 180):
            return None, "Invalid coordinate values"

        if origin_lon == dest_lon and origin_lat == dest_lat:
            return {
                "distance": "0 km",
                "duration": "0 mins",
                "steps": [{"instruction": "No route needed (same location)", "distance": "0 km", "duration": "0 mins"}],
                "polyline": [[origin_lat, origin_lon]]
            }, None

        url = f"http://router.project-osrm.org/route/v1/driving/{origin};{destination}?overview=full&steps=true"
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            return None, f"OSRM request failed: HTTP {response.status_code}"

        data = response.json()
        if not isinstance(data, dict) or "code" not in data or data["code"] != "Ok":
            return None, f"OSRM error: {data.get('message', 'Unknown error')}"

        route = data["routes"][0]
        encoded_polyline = route["geometry"]
        decoded_coords = polyline.decode(encoded_polyline)
        polyline_coords = [[lat, lon] for lat, lon in decoded_coords]

        return {
            "distance": f"{route['distance']/1000:.1f} km",
            "duration": f"{route['duration']/60:.1f} mins",
            "steps": [
                {
                    "instruction": step["maneuver"].get("instruction", "Proceed"),
                    "distance": f"{step['distance']/1000:.1f} km",
                    "duration": f"{step['duration']/60:.1f} mins"
                } for step in route["legs"][0]["steps"]
            ],
            "polyline": polyline_coords
        }, None
    except ValueError:
        return None, "Invalid coordinate format"
    except requests.RequestException as e:
        return None, f"OSRM network error: {str(e)}"
    except Exception as e:
        return None, f"Routing error: {str(e)}"

# Save donation
def save_donation(user_name, user_lat, user_lon, user_address, category, warehouse, distance, duration, polyline, date, quantity, weight, method, exp):
    conn = sqlite3.connect(os.path.join(BASE_DIR, "donations.db"))
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO donations (
            user_name, user_lat, user_lon, user_address, category,
            warehouse_name, warehouse_lat, warehouse_lon,
            distance, duration, polyline, status, timestamp, date, quantity, weight, method, exp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_name, user_lat, user_lon, user_address or "", category,
        warehouse["name"], warehouse["lat"], warehouse["lon"],
        distance, duration, json.dumps(polyline), "Pending", timestamp, date, quantity, weight, method, exp
    ))
    conn.commit()
    conn.close()

# Save request
def save_request(requester_name, requester_lat, requester_lon, requester_address, warehouse, distance, duration, polyline, dry_food_qty, fresh_food_qty, canned_food_qty, milk_cold_qty, spice_qty):
    conn = sqlite3.connect(os.path.join(BASE_DIR, "requestlist.db"))
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO requests (
            requester_name, requester_lat, requester_lon, requester_address,
            warehouse_name, warehouse_lat, warehouse_lon,
            distance, duration, polyline, status, timestamp,
            dry_food_qty, fresh_food_qty, canned_food_qty, milk_cold_qty, spice_qty
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        requester_name, requester_lat, requester_lon, requester_address or "",
        warehouse["name"], warehouse["lat"], warehouse["lon"],
        distance, duration, json.dumps(polyline), "Pending", timestamp,
        dry_food_qty, fresh_food_qty, canned_food_qty, milk_cold_qty, spice_qty
    ))
    conn.commit()
    conn.close()

# API Endpoints
@app.route("/donate", methods=["POST"])
def donate():
    data = request.get_json()
    user_lat = None
    user_lon = None
    user_address = None
    user_name = data.get("user_name", "Anonymous")
    quantity = data.get("quantity")
    weight = data.get("weight")
    exp = data.get("exp")
    
    try:
        category = data["category"].capitalize()
        date = data["date"]
        method = data["method"]
        if "latitude" in data and "longitude" in data:
            user_lat = float(data["latitude"])
            user_lon = float(data["longitude"])
        elif "address" in data:
            user_address = data["address"]
            user_lat, user_lon = geocode_address(user_address)
            if user_lat is None:
                return jsonify({"error": f"Could not geocode address: {user_lon}"}), 400
        else:
            return jsonify({"error": "Provide either latitude/longitude or address."}), 400
    except (KeyError, ValueError):
        return jsonify({"error": "Invalid input. Provide category, date and location."}), 400

    if category not in CATEGORIES:
        return jsonify({"error": f"Invalid category. Vui long chon lai: {', '.join(CATEGORIES)}"}), 400
    if date not in DATE:
        return jsonify({"error": f"Invalid date. Vui long chon lai: {', '.join(DATE)}"}), 400
    
    warehouse, min_distance_km = find_nearest_warehouse(user_lat, user_lon)
    if not warehouse:
        return jsonify({"error": "No warehouses found."}), 404
    
    origin = f"{user_lon},{user_lat}"
    destination = f"{warehouse['lon']},{warehouse['lat']}"
    route, error = get_directions(origin, destination)
    
    if not route:
        return jsonify({"error": f"Could not calculate route: {error}"}), 500
    
    distance = route["distance"]
    duration = route["duration"]
    polyline = route["polyline"]
    
    save_donation(user_name, user_lat, user_lon, user_address, category, warehouse, distance, duration, polyline, date, quantity, weight, method, exp)
    
    response = {
        "message": "Donation request submitted successfully",
        "warehouse": {
            "name": warehouse["name"],
            "distance": distance,
            "duration": duration
        },
        "category": category,
    }
    
    return jsonify(response), 200

@app.route("/request", methods=["POST"])
def request_goods():
    data = request.get_json()
    requester_lat = None
    requester_lon = None
    requester_address = None

    try:
        requester_name = data.get("requester_name", "")
        dry_food_qty = float(data.get("dry_food_qty", 0))
        fresh_food_qty = float(data.get("fresh_food_qty", 0))
        canned_food_qty = float(data.get("canned_food_qty", 0))
        milk_cold_qty = float(data.get("milk_cold_qty", 0))
        spice_qty = float(data.get("spice_qty", 0))

        if not requester_name:
            return jsonify({"error": "Requester name is required."}), 400
        if dry_food_qty < 0 or fresh_food_qty < 0 or canned_food_qty < 0 or milk_cold_qty < 0 or spice_qty < 0:
            return jsonify({"error": "Quantities must be non-negative."}), 400

        if "latitude" in data and "longitude" in data:
            requester_lat = float(data["latitude"])
            requester_lon = float(data["longitude"])
        elif "address" in data:
            requester_address = data["address"]
            requester_lat, requester_lon = geocode_address(requester_address)
            if requester_lat is None:
                return jsonify({"error": f"Could not geocode address: {requester_lon}"}), 400
        else:
            return jsonify({"error": "Provide either latitude/longitude or address."}), 400
    except (KeyError, ValueError):
        return jsonify({"error": "Invalid input. Provide name, quantities, and location."}), 400
    
    warehouse, min_distance_km = find_nearest_warehouse(requester_lat, requester_lon)
    if not warehouse:
        return jsonify({"error": "No warehouses found."}), 404
    
    origin = f"{requester_lon},{requester_lat}"
    destination = f"{warehouse['lon']},{warehouse['lat']}"
    route, error = get_directions(origin, destination)
    
    if not route:
        return jsonify({"error": f"Could not calculate route: {error}"}), 500
    
    distance = route["distance"]
    duration = route["duration"]
    polyline = route["polyline"]
    
    save_request(requester_name, requester_lat, requester_lon, requester_address, warehouse, distance, duration, polyline, dry_food_qty, fresh_food_qty, canned_food_qty, milk_cold_qty, spice_qty)
    
    response = {
        "message": "Request submitted successfully",
        "warehouse": {
            "name": warehouse["name"],
            "distance": distance,
            "duration": duration
        }
    }
    
    return jsonify(response), 200

@app.route("/donations", methods=["GET"])
def get_donations():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "donations.db"))
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
            "timestamp": row[13],
            "date": row[14],
            "quantity": row[15],
            "weight": row[16],
            "method": row[17],
            "exp": row[18]
        }
        for row in rows
    ]
    
    return jsonify(donations), 200

@app.route("/requests", methods=["GET"])
def get_requests():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "requestlist.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM requests ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    
    requests = [
        {
            "rid": row[0],
            "requester_name": row[1],
            "requester_latitude": row[2],
            "requester_longitude": row[3],
            "requester_address": row[4] or reverse_geocode(row[2], row[3]),
            "warehouse_name": row[5],
            "warehouse_latitude": row[6],
            "warehouse_longitude": row[7],
            "warehouse_address": reverse_geocode(row[6], row[7]),
            "distance": row[8],
            "duration": row[9],
            "polyline": json.loads(row[10]),
            "status": row[11],
            "timestamp": row[12],
            "dry_food_qty": row[13],
            "fresh_food_qty": row[14],
            "canned_food_qty": row[15],
            "milk_cold_qty": row[16],
            "spice_qty": row[17]
        }
        for row in rows
    ]
    
    return jsonify(requests), 200

@app.route("/update_status/<int:id>", methods=["POST"])
def update_status(id):
    data = request.get_json()
    status = data.get("status")
    if status not in ["Pending", "Processed", "Picked Up"]:
        return jsonify({"error": "Invalid status."}), 400
    
    # Try updating donations
    cconn = sqlite3.connect(os.path.join(BASE_DIR, "donations.db"))
    cursor = conn.cursor()
    cursor.execute("UPDATE donations SET status = ? WHERE id = ?", (status, id))
    conn.commit()
    conn.close()
    
    # Try updating requests
    conn = sqlite3.connect(os.path.join(BASE_DIR, "requestlist.db"))
    cursor = conn.cursor()
    cursor.execute("UPDATE requests SET status = ? WHERE id = ?", (status, id))
    conn.commit()
    conn.close()
    
    return jsonify({"message": f"Status for ID {id} updated to {status}"}), 200

@app.route("/delete_donation/<int:donation_id>", methods=["DELETE"])
def delete_donation(donation_id):
    conn = sqlite3.connect(os.path.join(BASE_DIR, "donations.db"))
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM donations WHERE id = ?", (donation_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": f"Donation {donation_id} not found."}), 404
    
    cursor.execute("DELETE FROM donations WHERE id = ?", (donation_id,))
    
    cursor.execute("SELECT id FROM donations ORDER BY id ASC")
    rows = cursor.fetchall()
    for new_id, (old_id,) in enumerate(rows, 1):
        if old_id != new_id:
            cursor.execute("UPDATE donations SET id = ? WHERE id = ?", (new_id, old_id))
    
    cursor.execute("SELECT MAX(id) FROM donations")
    max_id = cursor.fetchone()[0] or 0
    cursor.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'donations'", (max_id,))
    
    conn.commit()
    conn.close()
    
    return jsonify({"message": f"Donation {donation_id} deleted and IDs reassigned."}), 200

@app.route("/delete_request/<int:request_id>", methods=["DELETE"])
def delete_request(request_id):
    conn = sqlite3.connect(os.path.join(BASE_DIR, "requestlist.db"))
    cursor = conn.cursor()
    
    cursor.execute("SELECT rid FROM requests WHERE rid = ?", (request_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": f"Request {request_id} not found."}), 404

    cursor.execute("DELETE FROM requests WHERE rid = ?", (request_id,))

    cursor.execute("SELECT rid FROM requests ORDER BY rid ASC")
    rows = cursor.fetchall()
    for new_rid, (old_rid,) in enumerate(rows, 1):
        if old_rid != new_rid:
            cursor.execute("UPDATE requests SET rid = ? WHERE rid = ?", (new_rid, old_rid))

    cursor.execute("SELECT MAX(rid) FROM requests")
    max_id = cursor.fetchone()[0] or 0
    cursor.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'requests'", (max_id,))
        
    conn.commit()
    conn.close()
    
    return jsonify({"message": f"Request {request_id} deleted and IDs reassigned."}), 200



@app.route("/admin")
def admin_dashboard():
    return render_template("admin.html")

@app.route("/admin_requester")
def admin_requester_dashboard():
    return render_template("admin_requester.html")

@app.route("/test_donate", methods=["GET", "POST"])
def test_donate():
    if request.method == "GET":
        return render_template("test_donate.html", categories=CATEGORIES, dates=DATE, methods=PSMETHOD)
    
    date = request.form.get("date")
    category = request.form.get("category")
    exp = request.form.get("exp")
    method = request.form.get("method")
    user_address = request.form.get("address")
    user_name = request.form.get("user_name", "Anonymous")
    quantity = request.form.get("quantity")    
    weight = request.form.get("weight")    
    
    if not category or not user_address:
        return render_template("test_donate.html", categories=CATEGORIES, dates=DATE, methods=PSMETHOD,
                              error="Please provide category and address.")
    
    if category not in CATEGORIES:
        return render_template("test_donate.html", categories=CATEGORIES, dates=DATE, methods=PSMETHOD,
                              error=f"Invalid category. Choose from: {', '.join(CATEGORIES)}")
    
    user_lat, user_lon = geocode_address(user_address)
    if user_lat is None:
        return render_template("test_donate.html", categories=CATEGORIES, dates=DATE, methods=PSMETHOD,
                              error=f"Could not geocode address: {user_lon}")
    
    warehouse, min_distance_km = find_nearest_warehouse(user_lat, user_lon)
    if not warehouse:
        return render_template("test_donate.html", categories=CATEGORIES, dates=DATE, methods=PSMETHOD,
                              error="No warehouses found.")
    
    origin = f"{user_lon},{user_lat}"
    destination = f"{warehouse['lon']},{warehouse['lat']}"
    route, error = get_directions(origin, destination)
    
    if not route:
        return render_template("test_donate.html", categories=CATEGORIES, dates=DATE, methods=PSMETHOD,
                              error=f"Could not calculate route: {error}")
    
    distance = route["distance"]
    duration = route["duration"]
    polyline = route["polyline"]
    
    save_donation(user_name, user_lat, user_lon, user_address, category, warehouse, distance, duration, polyline, date, quantity, weight, method, exp)
    
    return render_template(
        "test_donate.html",
        categories=CATEGORIES, dates=DATE, methods=PSMETHOD,
        success=f"Donation submitted! Category: {category}, Warehouse: {warehouse['name']}, Distance: {distance}"
    )

@app.route("/test_donate_requester", methods=["GET", "POST"])
def test_donate_requester():
    if request.method == "GET":
        return render_template("test_donate_requester.html")
    
    requester_name = request.form.get("requester_name")
    requester_address = request.form.get("requester_address")
    dry_food_qty = request.form.get("dry_food_qty")  # Map to Thực phẩm khô
    fresh_food_qty = request.form.get("fresh_food_qty")  # Map to Tươi sống
    canned_food_qty = request.form.get("canned_food_qty")  # Map to Đồ hộp
    milk_cold_qty = request.form.get("milk_cold_qty")  # Map to Sữa/ Đồ lạnh
    spice_qty = request.form.get("spice_qty")  # Map to Gia vị

    if not requester_name or not requester_address:
        return render_template("test_donate_requester.html",
                              error="Please provide requester name and address.")
    
    try:
        dry_food_qty = float(dry_food_qty) if dry_food_qty else 0
        fresh_food_qty = float(fresh_food_qty) if fresh_food_qty else 0
        canned_food_qty = float(canned_food_qty) if canned_food_qty else 0
        milk_cold_qty = float(milk_cold_qty) if milk_cold_qty else 0
        spice_qty = float(spice_qty) if spice_qty else 0
        if dry_food_qty < 0 or fresh_food_qty < 0 or canned_food_qty < 0 or milk_cold_qty < 0 or spice_qty < 0:
            return render_template("test_donate_requester.html",
                                  error="Quantities must be non-negative.")
    except ValueError:
        return render_template("test_donate_requester.html",
                              error="Quantities must be numbers.")
    
    requester_lat, requester_lon = geocode_address(requester_address)
    if requester_lat is None:
        return render_template("test_donate_requester.html",
                              error=f"Could not geocode address: {requester_lon}")
    
    warehouse, min_distance_km = find_nearest_warehouse(requester_lat, requester_lon)
    if not warehouse:
        return render_template("test_donate_requester.html",
                              error="No warehouses found.")
    
    origin = f"{requester_lon},{requester_lat}"
    destination = f"{warehouse['lon']},{warehouse['lat']}"
    route, error = get_directions(origin, destination)
    
    if not route:
        return render_template("test_donate_requester.html",
                              error=f"Could not calculate route: {error}")
    
    distance = route["distance"]
    duration = route["duration"]
    polyline = route["polyline"]
    
    save_request(requester_name, requester_lat, requester_lon, requester_address, warehouse, distance, duration, polyline, dry_food_qty, fresh_food_qty, canned_food_qty, milk_cold_qty, spice_qty)
    
    return render_template(
        "test_donate_requester.html",
        success=f"Request submitted! Warehouse: {warehouse['name']}, Distance: {distance}"
    )

if __name__ == "__main__":
    setup_database()
    app.run(host="0.0.0.0", port=5000, debug=True)