python3 -m venv venv && \
source venv/bin/activate && \
pip install -r requirements.txt

# Deploy
cd rentcast_function/
gcloud functions deploy rentcast_handler \
  --gen2 \
  --runtime python311 \
  --region us-central1 \
  --entry-point rentcast_handler \
  --source=. \
  --trigger-http \
  --allow-unauthenticated \
  --set-env-vars=RENTCAST_API_KEY=<api key>,GITHUB_TOKEN=<api key>