python3 -m venv venv && \
source venv/bin/activate && \
pip install -r requirements.txt

# Test authentication and connection to the sheet
python3 test_google_sheets.py

# Run comps
python3 real_estate_comps_scraper.py
python3 rentcast_lookup.py