import os
import json
import concurrent.futures

import functions_framework
import requests

from google.cloud import bigquery

# --- Config ---
DATA_STORE = {
    "max_radius": 0.5,
    "days_old": 180,
    "max_comps_count": 15
}

# --- Helpers ---
def make_rentcast_headers(api_key):
    return {
        "accept": "application/json",
        "X-Api-Key": api_key
    }

def make_github_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github+json"
    }

def safe_request(url, headers, params=None):
    try:
        resp = requests.get(url, headers=headers, params=params)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Failed request to {url}: {e}")
    return {}

def save_to_github_gist(property_data, full_data, github_token):
    gist_id = property_data["id"].replace(",", "")
    description = property_data.get("legalDescription") or f"Property {gist_id}"
    formatted = json.dumps(full_data, indent=2)

    headers = make_github_headers(github_token)
    body = {
        "files": {f"{gist_id}.json": {"content": formatted}},
        "public": False,
        "description": description
    }

    url = f"https://api.github.com/gists/{gist_id}"
    resp = requests.get(url, headers=headers)

    if resp.status_code == 200:
        resp = requests.patch(url, headers=headers, json=body)
    elif resp.status_code == 404:
        resp = requests.post("https://api.github.com/gists", headers=headers, json=body)
    else:
        resp.raise_for_status()

    resp.raise_for_status()
    return resp.json()

# --- Cloud Function Entry ---
@functions_framework.http
def rentcast_handler(request):
    request_json = request.get_json(silent=True)
    if not request_json or "address" not in request_json:
        return {"error": "Missing 'address'"}, 400

    address = request_json["address"]
    api_key = os.environ.get("RENTCAST_API_KEY")
    github_token = os.environ.get("GITHUB_TOKEN")
    if not api_key or not github_token:
        return {"error": "Missing required API keys"}, 500

    max_radius = request_json.get("max_radius", DATA_STORE["max_radius"])
    days_old = request_json.get("days_old", DATA_STORE["days_old"])
    comp_count = request_json.get("max_comps_count", DATA_STORE["max_comps_count"])

    headers = make_rentcast_headers(api_key)

    # Step 1: Get subject property
    prop_resp = requests.get("https://api.rentcast.io/v1/properties", headers=headers, params={"address": address})
    if not prop_resp.ok or not prop_resp.json():
        return {"error": f"[Property Lookup] RentCast API failed with status {prop_resp.status_code}"}, 500

    property_data = prop_resp.json()[0]

    # Step 2: Prepare parameters
    zip_code = property_data.get("zipCode")
    property_type = property_data.get("propertyType")
    bedrooms = property_data.get("bedrooms")
    bathrooms = property_data.get("bathrooms")
    square_footage = property_data.get("squareFootage")

    # Step 3: Fetch comps/market data in parallel
    def get_market_stats():
        url = f"https://api.rentcast.io/v1/markets?zipCode={zip_code}&dataType=All&historyRange=6"
        return safe_request(url, headers)

    def get_rental_estimate():
        url = "https://api.rentcast.io/v1/avm/rent/long-term"
        return safe_request(url, headers, {
            "address": address,
            "propertyType": property_type,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "squareFootage": square_footage,
            "maxRadius": max_radius,
            "daysOld": days_old,
            "compCount": comp_count
        })

    def get_sales_comps():
        url = "https://api.rentcast.io/v1/avm/value"
        return safe_request(url, headers, {
            "address": address,
            "propertyType": property_type,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "squareFootage": square_footage,
            "maxRadius": max_radius,
            "daysOld": days_old,
            "compCount": comp_count
        })

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {
            "market_statistics": executor.submit(get_market_stats),
            "rental_comps": executor.submit(get_rental_estimate),
            "sales_comps": executor.submit(get_sales_comps)
        }
        results = {key: future.result() for key, future in futures.items()}

    # Step 4: Output and Upload
    final_data = {
        "property_record": property_data,
        **results
    }

    try:
        print(f"Saving data to BigQuery. Id: {property['id']}")
        bq_client = bigquery.Client()
        table_id = "real-estate-comps-gcp.real_estate_data.raw_property_data"
        row = {
            "property_id": property_data["id"],
            "full_json": json.dumps(final_data)
        }
        bq_client.insert_rows_json(table_id, [row])
    except Exception as e:
        print(f"[WARN] BigQuery insert failed: {e}")

    gist = save_to_github_gist(property_data, final_data, github_token)

    return {
        "message": "✓ Data saved to Gist",
        "gist_url": gist["html_url"],
        "property_id": property_data["id"],
        "address": address
    }