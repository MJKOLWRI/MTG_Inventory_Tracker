import os
import requests
import re


def download_all_symbols():
    """
    Connects to the Scryfall API to download all card symbol SVGs.
    """
    SYMBOLS_API_URL = "https://api.scryfall.com/symbology"
    TARGET_DIR = os.path.join("static", "mana_symbols")

    # 1. Create the target directory if it doesn't exist
    os.makedirs(TARGET_DIR, exist_ok=True)
    print(f"Ensuring directory exists: {TARGET_DIR}")

    # 2. Fetch the list of all symbols from Scryfall
    try:
        print("Fetching symbol list from Scryfall...")
        response = requests.get(SYMBOLS_API_URL)
        response.raise_for_status()
        symbols_data = response.json().get('data', [])
        print(f"Found {len(symbols_data)} symbols.")
    except requests.exceptions.RequestException as e:
        print(f"Error: Could not connect to Scryfall API. {e}")
        return

    # 3. Loop through each symbol and download it if it's missing
    for symbol_obj in symbols_data:
        svg_url = symbol_obj.get('svg_uri')
        symbol_code = symbol_obj.get('symbol')

        if not svg_url or not symbol_code:
            continue

        # Create a safe filename from the symbol code (e.g., {T} -> T.svg)
        # This matches the logic in our app.py filter
        filename = re.sub(r'[{}/]', '', symbol_code) + ".svg"
        local_path = os.path.join(TARGET_DIR, filename)

        # Only download if the file doesn't already exist
        if not os.path.exists(local_path):
            print(f"Downloading {filename}...")
            try:
                img_response = requests.get(svg_url, stream=True)
                img_response.raise_for_status()
                with open(local_path, 'wb') as f:
                    for chunk in img_response.iter_content(chunk_size=8192):
                        f.write(chunk)
            except requests.exceptions.RequestException as e:
                print(f"  -> Failed to download {filename}: {e}")
        else:
            # Optional: uncomment the line below if you want to see which files are being skipped
            # print(f"Skipping {filename}, already exists.")
            pass

    print("\nSymbol download process complete.")


if __name__ == '__main__':
    download_all_symbols()