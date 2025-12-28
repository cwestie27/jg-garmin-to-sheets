import typer
import sys
from datetime import datetime, timedelta, date
import asyncio
from typing import Optional
import os
import csv
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
import logging
import re

from src.garmin_client import GarminClient
from src.sheets_client import GoogleSheetsClient, GoogleAuthTokenRefreshError
from src.exceptions import MFARequiredException
from src.config import HEADERS, HEADER_TO_ATTRIBUTE_MAP, GarminMetrics

# Suppress noisy library warnings to clean up output
logging.getLogger('google_auth_oauthlib.flow').setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = typer.Typer()

async def sync(email: str, password: str, start_date: date, end_date: date, output_type: str, profile_data: dict, profile_name: str = ""):
    """Core sync logic. Fetches data and writes to the specified output."""
    try:
        garmin_client = GarminClient(email, password)
        await garmin_client.authenticate()

    except MFARequiredException as e:
        logger.error("MFA is required but cannot be entered in automated mode.")
        sys.exit(1)
    
    except Exception as e:
        logger.error(f"Authentication failed: {e}", exc_info=True)
        sys.exit(1)

    logger.info(f"Fetching metrics from {start_date.isoformat()} to {end_date.isoformat()}...")
    metrics_to_write = []
    current_date = start_date
    while current_date <= end_date:
        logger.info(f"Fetching metrics for {current_date.isoformat()}")
        daily_metrics = await garmin_client.get_metrics(current_date)
        metrics_to_write.append(daily_metrics)
        current_date += timedelta(days=1)

    if not metrics_to_write:
        logger.warning("No metrics fetched. Nothing to write.")
        return

    if output_type == 'sheets':
        sheets_id = profile_data.get('sheet_id')
        sheet_name = profile_data.get('sheet_name', 'Raw Data')
        display_name = profile_data.get('spreadsheet_name', f"ID: {sheets_id}")

        logger.info(f"Initializing Google Sheets client for spreadsheet: '{display_name}'")
        try:
            sheets_client = GoogleSheetsClient(
                credentials_path='credentials/client_secret.json',
                spreadsheet_id=sheets_id,
                sheet_name=sheet_name
            )
            sheets_client.update_metrics(metrics_to_write)
            logger.info("Google Sheets sync completed successfully!")
        
        except GoogleAuthTokenRefreshError as auth_error:
            logger.error(f"Google Token Expired: {auth_error}")
            # In automated mode, we cannot ask to delete the token.
            print("Google Token likely expired. Please re-generate credentials locally.")
            sys.exit(1)
        
        except Exception as sheet_error:
            logger.error(f"An error occurred during Google Sheets operation: {str(sheet_error)}", exc_info=True)
            print(f"\nAn error occurred while updating Google Sheets: {sheet_error}")
            sys.exit(1)

    elif output_type == 'csv':
        # Use configured CSV path or default to output directory with profile name
        if 'csv_path' in profile_data and profile_data['csv_path']:
            csv_path = Path(profile_data['csv_path'])
        else:
            output_dir = Path("./output")
            output_dir.mkdir(parents=True, exist_ok=True)
            csv_path = output_dir / f"garmingo_{profile_name if profile_name else 'output'}.csv"
        
        logger.info(f"Writing metrics to CSV file: {csv_path}")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(csv_path, 'a', newline='') as f:
            writer = csv.writer(f)
            if f.tell() == 0: # Write header if file is new/empty
                writer.writerow(HEADERS)
            for metric in metrics_to_write:
                writer.writerow([getattr(metric, HEADER_TO_ATTRIBUTE_MAP.get(h, ""), "") for h in HEADERS])
        logger.info("CSV file sync completed successfully!")

def load_user_profiles():
    """Parses .env for user profiles, now including SPREADSHEET_NAME."""
    profiles = {}
    profile_pattern = re.compile(r"^(USER\d+)_(GARMIN_EMAIL|GARMIN_PASSWORD|SHEET_ID|SHEET_NAME|SPREADSHEET_NAME|CSV_PATH)$")

    for key, value in os.environ.items():
        match = profile_pattern.match(key)
        if match:
            profile_name, var_type = match.groups()
            if profile_name not in profiles:
                profiles[profile_name] = {}
            
            key_map = {
                "GARMIN_EMAIL": "email",
                "GARMIN_PASSWORD": "password",
                "SHEET_ID": "sheet_id",
                "SHEET_NAME": "sheet_name",
                "SPREADSHEET_NAME": "spreadsheet_name",
                "CSV_PATH": "csv_path"
            }
            profiles[profile_name][key_map[var_type]] = value
    return profiles

@app.command()
def cli_sync(
    start_date: datetime = typer.Option(..., help="Start date in YYYY-MM-DD format."),
    end_date: datetime = typer.Option(..., help="End date in YYYY-MM-DD format."),
    profile: str = typer.Option("USER1", help="The user profile from .env to use (e.g., USER1)."),
    output_type: str = typer.Option("sheets", help="Output type: 'sheets' or 'csv'.")
):
    """Run the Garmin sync from the command line."""
    user_profiles = load_user_profiles()
    selected_profile_data = user_profiles.get(profile)

    if not selected_profile_data:
        logger.error(f"Profile '{profile}' not found in .env file.")
        sys.exit(1)

    email = selected_profile_data.get('email')
    password = selected_profile_data.get('password')

    if not email or not password:
        logger.error(f"Email or password not configured for profile '{profile}'.")
        sys.exit(1)

    asyncio.run(sync(
        email=email,
        password=password,
        start_date=start_date.date(),
        end_date=end_date.date(),
        output_type=output_type,
        profile_data=selected_profile_data,
        profile_name=profile
    ))

async def run_interactive_sync():
    """
    MODIFIED: Handles the sync automatically for GitHub Actions / Cron.
    Hardcodes choices to avoid 'EOFError' when no user input is available.
    """
    logger.info("Starting AUTOMATED sync setup...")

    # HARDCODED: Always use Sheets
    output_type = "sheets"
    logger.info(f"Selected output type: {output_type}")

    # Load user profiles
    user_profiles = load_user_profiles()
    if not user_profiles:
        logger.error("No user profiles found in .env file.")
        sys.exit(1)
    
    # HARDCODED: Default to USER1, or the first available profile
    selected_profile_name = "USER1"
    if selected_profile_name not in user_profiles:
        selected_profile_name = list(user_profiles.keys())[0]

    selected_profile_data = user_profiles[selected_profile_name]
    logger.info(f"Using profile: {selected_profile_name}")

    # HARDCODED: Date Selection (Sync Yesterday)
    # We sync "Yesterday" because if this runs in the morning,
    # "Today's" data (like sleep) might not be fully finalized yet.
    # To sync TODAY, change days=1 to days=0.
    end_date = date.today()
    start_date = end_date - timedelta(days=1)
    
    logger.info(f"Date range selected: {start_date} to {end_date}")

    # Call Core Sync Logic
    await sync(
        email=selected_profile_data.get('email'),
        password=selected_profile_data.get('password'),
        start_date=start_date,
        end_date=end_date,
        output_type=output_type,
        profile_data=selected_profile_data,
        profile_name=selected_profile_name
    )

def main():
    """Main entry point for the application."""
    env_file_path = find_dotenv(usecwd=True)
    if env_file_path:
        load_dotenv(dotenv_path=env_file_path)
    else:
        logger.warning(".env file not found. Please ensure it's in the root directory.")
    
    try:
        # Check if any CLI arguments were provided
        if len(sys.argv) > 1:
            # CLI mode: use typer to parse arguments
            app()
        else:
            # AUTO mode: run the modified automated session
            asyncio.run(run_interactive_sync())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()
