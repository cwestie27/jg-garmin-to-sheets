import os
import garth

print("--- TOKEN LOCATION FINDER ---")
# This asks the library exactly where it saves files
print(f"Garth thinks the home directory is: {garth.home}")
print(f"Looking for folder at: {os.path.join(garth.home, '.garth')}")

# Check if it exists there
path = os.path.join(garth.home, '.garth')
if os.path.exists(path):
    print("✅ FOUND IT! The folder is here.")
    print("Files inside:")
    for f in os.listdir(path):
        print(f" - {f}")
else:
    print("❌ Not found in default location.")
    
    # Check the current folder as a backup
    local_path = os.path.join(os.getcwd(), ".garth")
    if os.path.exists(local_path):
         print(f"✅ FOUND IT LOCALLY! It is in your project folder: {local_path}")
    else:
         print("❌ strictly confusing. Checking AppData...")