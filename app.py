import os
from flask import Flask, render_template, request, jsonify, send_file
from travel_plan import WalkarooTravelPlanner
import pandas as pd
import re
from dotenv import load_dotenv

app = Flask(__name__)
app.secret_key = "walkaroo-travel-planner-secret"

load_dotenv() # Load environment variables from .env file

# Initialize Walkaroo Travel Planner
travel_planner = WalkarooTravelPlanner()

# -------------------------------
# Web frontend (HTML form)
# -------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # Aggressively clean inputs at the source to handle hidden characters
        market_name = re.sub(r'\s+', ' ', request.form.get('market', '')).strip()
        dealer_name = re.sub(r'\s+', ' ', request.form.get('dealer', '')).strip()

        if not market_name:
            return render_template("index.html", error="Market name is required")
        
        if not dealer_name:
            return render_template("index.html", error="Dealer name is required to generate a plan", market=market_name)

        # Unpack tuple return values
        route_text, map_url, retailers_json = travel_planner.plan_optimal_route(market_name, dealer_name)

        if not route_text:
            return render_template(
                "index.html",
                error="No route could be generated",
                market=market_name,
                dealer=dealer_name
            )
        
        return render_template(
            "index.html",
            result=route_text,
            map_url=map_url,
            retailers=retailers_json,
            market=market_name,
            dealer=dealer_name
        )
    else:
        return render_template("index.html")


# -------------------------------
# JSON API (for Postman / cURL)
# -------------------------------
@app.route("/api/plan_route", methods=["POST"])
def api_plan_route():
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Request must be JSON"}), 400

    # Aggressively clean inputs at the source
    market_name = re.sub(r'\s+', ' ', data.get("market", "")).strip()
    dealer_name = re.sub(r'\s+', ' ', data.get("dealer", "")).strip()

    if not market_name:
        return jsonify({"error": "Market name is required"}), 400
    
    if not dealer_name:
        return jsonify({"error": "Dealer name is required"}), 400

    route_text, map_url, retailers_json = travel_planner.plan_optimal_route(market_name, dealer_name)

    if not route_text:
        return jsonify({"error": "No route could be generated"}), 404

    return jsonify({
        "market": market_name,
        "dealer": dealer_name,
        "route_plan": route_text,
        "map_url": map_url,
        "retailers": retailers_json
    })


# -------------------------------
# Source Data Viewer & Downloader
# -------------------------------
@app.route("/view-source")
def view_source_data():
    try:
        source_path = travel_planner.source_file_path
        # Load the single sheet
        source_df = pd.read_excel(source_path, sheet_name="Sheet1")

        # Convert to HTML
        source_html = source_df.to_html(classes='table table-striped table-hover', index=False, border=0)

        return render_template('view_source.html', source_table=source_html)
    except Exception as e:
        print(f"Error loading source data for viewing: {e}")
        return render_template("index.html", error=f"Could not load source data viewer: {e}")


@app.route("/download/source-data")
def download_source_data():
    try:
        source_path = travel_planner.source_file_path
        return send_file(source_path, as_attachment=True, download_name='source_data.xlsx')
    except Exception as e:
        return jsonify({"error": f"File not found or error sending file: {str(e)}"}), 404


if __name__ == "__main__":
    # Debug mode is controlled by the FLASK_DEBUG environment variable
    app.run(debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true', port=5004)
