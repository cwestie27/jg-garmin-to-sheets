import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

# --- UPDATED IMPORTS ---
# We removed GoogleAuthTokenRefreshError because Service Accounts don't need it
from src.garmin_client import GarminClient
from src.sheets_client import GoogleSheetsClient 
# -----------------------

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

async def sync(profile_prefix="USER1"):
    logger.info(f"Starting sync for profile: {profile_prefix}")
    
    # 1. Load Environment Variables
    email = os.getenv(f"{profile_prefix}_EMAIL")
    password = os.getenv(f"{profile_prefix}_PASSWORD")
    sheet_id = os.getenv(f"{profile_prefix}_SHEET_ID")
    
    if not email or not password or not sheet_id:
        logger.error(f"Missing credentials for {profile_prefix}. Check your .env file or GitHub Secrets.")
        return

    # 2. Authenticate with Garmin
    garmin_client = GarminClient(email, password)
    try:
        await garmin_client.authenticate()
    except Exception as e:
        logger.error(f"Garmin Authentication failed: {e}")
        return

    # 3. Determine Date Range (Yesterday Only)
    # You can customize this, but usually we sync 'Yesterday' to ensure full data
    target_date = date.today() - timedelta(days=1)
    logger.info(f"Fetching data for date: {target_date}")

    # 4. Fetch Metrics
    try:
        metrics = await garmin_client.get_metrics(target_date)
        if not metrics:
            logger.error("No metrics found.")
            return
        
        # Log success
        logger.info(f"Successfully fetched metrics for {metrics.date}")
    except Exception as e:
        logger.error(f"Failed to fetch metrics: {e}")
        return

    # 5. Connect to Google Sheets (The "Robot")
    try:
        # Note: We hardcode 'credentials/client_secret.json' because your GitHub workflow creates it there
        sheets_client = GoogleSheetsClient(
            credentials_path='credentials/client_secret.json',
            spreadsheet_id=sheet_id,
            sheet_name="GarminDaily" # Change this if your tab is named differently
        )
    except Exception as e:
        logger.error(f"Failed to initialize Google Sheets: {e}")
        return

    # 6. Format Data for the Sheet
    # This list MUST match the columns in your Google Sheet exactly
    row_data = [
        metrics.date.isoformat(),
        metrics.sleep_score,
        metrics.sleep_start,  
        metrics.sleep_end, 
        metrics.sleep_length,   
        metrics.weight,
        metrics.body_fat,
        metrics.resting_heart_rate,
        metrics.overnight_hrv,
        metrics.hrv_status,
        metrics.steps,
        metrics.active_calories,
        metrics.resting_calories,
        metrics.intensity_minutes,
        metrics.average_stress,
        metrics.training_status,
        metrics.vo2max_running,
        metrics.running_distance,
        metrics.strength_duration
        # Add any other fields from GarminMetrics you want here
    ]

    # 7. Upload to Google Sheets
    try:
        # We replace None with "" so it looks clean in the sheet
        clean_row = [str(x) if x is not None else "" for x in row_data]
        sheets_client.append_data(clean_row)
        logger.info("✅ Sync Complete!")
    except Exception as e:
        logger.error(f"Failed to upload to Google Sheets: {e}")

if __name__ == "__main__":
    # If passing arguments from command line (like "USER1")
    if len(sys.argv) > 1:
        profile = sys.argv[1]
    else:
        # Fallback if running via simple "python -m src.main"
        # Since your pipeline sends inputs via pipe, we can ignore this usually
        profile = "USER1" 

    # Determine if we need to read from stdin (for the piped "2\nUSER1\nN" stuff)
    # The new logic is robust enough to just run default if needed
    if not os.getenv("USER1_EMAIL") and not os.getenv("GARMIN_EMAIL"):
        logger.warning("No environment variables found. Ensure .env is loaded.")

    asyncio.run(sync(profile))
