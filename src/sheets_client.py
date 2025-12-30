import logging
import os.path
from google.oauth2.service_account import Credentials  # CHANGED: Use Service Account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

logger = logging.getLogger(__name__)

class GoogleSheetsClient:
    def __init__(self, credentials_path: str, spreadsheet_id: str, sheet_name: str = None):
        self.credentials_path = credentials_path
        self.spreadsheet_id = spreadsheet_id
        self.sheet_name = sheet_name
        self.creds = self._get_credentials()
        # Build the service using the loaded credentials
        self.service = build('sheets', 'v4', credentials=self.creds)

    def _get_credentials(self):
        """Gets credentials from a Service Account JSON file."""
        try:
            logger.info(f"Loading Service Account from: {self.credentials_path}")
            # CHANGED: Load as Service Account instead of InstalledAppFlow
            creds = Credentials.from_service_account_file(
                self.credentials_path, 
                scopes=SCOPES
            )
            return creds
        except Exception as e:
            logger.error(f"Failed to load Service Account credentials: {e}")
            raise

    def append_data(self, data: list):
        """Appends a row of data to the sheet."""
        if not data:
            logger.warning("No data to append.")
            return

        range_name = f"{self.sheet_name}!A1" if self.sheet_name else "A1"
        body = {
            'values': [data]
        }
        
        try:
            result = self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            
            logger.info(f"{result.get('updates').get('updatedCells')} cells appended.")
            return result
        except HttpError as error:
            logger.error(f"An error occurred appending data: {error}")
            return None
            
    def update_data(self, data: list, range_name: str = "A1"):
        """Updates data in a specific range."""
        # (Included for compatibility if your main.py uses it)
        body = {
            'values': [data]
        }
        try:
            result = self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            logger.info(f"{result.get('updatedCells')} cells updated.")
            return result
        except HttpError as error:
            logger.error(f"An error occurred updating data: {error}")
            return None
