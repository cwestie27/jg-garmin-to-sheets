import os
from dotenv import load_dotenv

# Force load the .env file
load_dotenv()

print("--- DIAGNOSTIC REPORT ---")
print(f"Current Folder: {os.getcwd()}")
print(f"Does .env exist here? {os.path.exists('.env')}")

# Check what variables Python can actually read
u1_email = os.getenv("USER1_EMAIL")
u1_pass = os.getenv("USER1_PASSWORD")

print(f"USER1_EMAIL found?   {'YES - ' + u1_email if u1_email else 'NO (Variable is empty or missing)'}")
print(f"USER1_PASSWORD found? {'YES - ******' if u1_pass else 'NO (Variable is empty or missing)'}")

print("--- RAW FILE CONTENTS (First 50 chars) ---")
try:
    with open(".env", "r") as f:
        print(f.read(50) + "...")
except Exception as e:
    print(f"Could not read file: {e}")