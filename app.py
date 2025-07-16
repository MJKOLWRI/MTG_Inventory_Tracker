import datetime
import shutil

import webview
from flask import Flask, render_template, jsonify, send_file, request, redirect, url_for, flash, send_from_directory
import json
import secrets
import requests
import re # Import the regular expression module
import os
import sys
import glob
import threading
import time
import qrcode
import socket
from io import BytesIO
import base64
import io
import zipfile


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if getattr(sys, 'frozen', False):
        # We are running in a bundled PyInstaller app
        # sys.executable is the path to the .exe
        base_path = os.path.dirname(sys.executable)
    else:
        # We are running in a normal .py script
        # __file__ is the path to the script
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)



# --- (Keep existing NAV_ITEMS, DATA_FILE, load_data, save_data functions) ---
NAV_ITEMS = [
    {'url': '/', 'label': 'Library'},
    {'url': '/import', 'label': 'Import'},
    {'url': '/decks', 'label': 'Decks'},
    {'url': '/settings', 'label': 'Settings'},
]
DATA_FILE = resource_path('data.json')
CONFIG_FILE = resource_path('config.json')
DECKS_DIR = resource_path('decks')
BACKUP_DIR = resource_path('backups')
STATIC_DIR = resource_path('static')
CARD_IMAGES_DIR = resource_path('card_images')

app = Flask(__name__)

from flask import jsonify # Make sure jsonify is imported

@app.route('/export_collection')
def export_collection():
    """Gathers all user data into a single downloadable .zip file."""
    try:
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            if os.path.exists(DATA_FILE):
                zf.write(DATA_FILE, arcname='data.json')

            deck_files = glob.glob(os.path.join(DECKS_DIR, '*.json'))
            for f_path in deck_files:
                zf.write(f_path, arcname=os.path.join('decks', os.path.basename(f_path)))

            if os.path.exists(CARD_IMAGES_DIR):
                for root, dirs, files in os.walk(CARD_IMAGES_DIR):
                    for file in files:
                        file_path = os.path.join(root, file)
                        archive_name = os.path.relpath(file_path, STATIC_DIR)
                        zf.write(file_path, arcname=os.path.join('static', archive_name))

        memory_file.seek(0)
        return send_file(
            memory_file,
            download_name='collection_backup.zip',
            as_attachment=True,
            mimetype='application/zip'
        )
    except Exception as e:
        # --- THIS PART IS CHANGED ---
        # Instead of flashing, return a JSON error response
        print(f"EXPORT ERROR: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/card_images/<path:filename>')
def serve_card_image(filename):
    """Serves a card image from the root card_images directory."""
    return send_from_directory(CARD_IMAGES_DIR, filename)

@app.route('/import_collection', methods=['POST'])
def import_collection():
    """Restores a collection from a .zip backup using absolute paths."""
    if 'backup_file' not in request.files:
        flash('No file part in the request.', 'error')
        return redirect(url_for('settings_page'))

    file = request.files['backup_file']
    if file.filename == '' or not file.filename.endswith('.zip'):
        flash('No file selected or invalid file type. Please upload a .zip file.', 'error')
        return redirect(url_for('settings_page'))

    try:
        # --- SAFETY FIRST: BACKUP CURRENT DATA ---
        # Get the base path of the application
        app_base_path = resource_path('.')

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        # Use the BACKUP_DIR global path variable
        backup_zip_path = os.path.join(BACKUP_DIR, f'pre-import-backup-{timestamp}.zip')

        # Create a zip of the current state
        with zipfile.ZipFile(backup_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf_backup:
            if os.path.exists(DATA_FILE): zf_backup.write(DATA_FILE, arcname='data.json')
            if os.path.exists(DECKS_DIR):
                for f in glob.glob(os.path.join(DECKS_DIR, '*.json')):
                    zf_backup.write(f, arcname=os.path.join('decks', os.path.basename(f)))
            if os.path.exists(CARD_IMAGES_DIR):
                for root, _, files in os.walk(CARD_IMAGES_DIR):
                    for f in files: zf_backup.write(os.path.join(root, f),
                                                    arcname=os.path.relpath(os.path.join(root, f), app_base_path))

        # --- CLEAR OLD DATA using absolute paths ---
        if os.path.exists(DATA_FILE): os.remove(DATA_FILE)
        if os.path.exists(DECKS_DIR): shutil.rmtree(DECKS_DIR)
        if os.path.exists(CARD_IMAGES_DIR): shutil.rmtree(CARD_IMAGES_DIR)

        # --- EXTRACT NEW DATA to the application's base path ---
        with zipfile.ZipFile(file, 'r') as zf_import:
            # This ensures files are extracted relative to the .exe location
            zf_import.extractall(path=app_base_path)

        flash('Collection imported successfully! Please restart the application to apply all changes.', 'success')
    except Exception as e:
        flash(f'An error occurred during import: {e}', 'error')

    return redirect(url_for('settings_page'))


def load_config():
    """Loads the configuration from config.json, ensuring all default keys are present."""
    # Define the complete default structure for the config
    default_config = {
        "network_server_enabled": False,
        "debug_mode": False,
        "import_default": {
            "mode": "last_selected",
            "default_location": ""
        }
    }
    try:
        with open(CONFIG_FILE, 'r') as f:
            user_config = json.load(f)
        # Merge user's config over the defaults to ensure all keys exist
        default_config.update(user_config)
    except (FileNotFoundError, json.JSONDecodeError):
        # If file doesn't exist or is invalid, we'll use the complete default config
        pass

    return default_config

def get_local_ip():
    """Finds the local IP address of the machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP


@app.route('/settings')
def settings_page():
    """Displays the settings page and generates a QR code if needed."""
    config = load_config()
    qr_code_data_uri = None
    all_locations = []

    # Get all boxes and decks to populate the dropdown
    all_data = load_data()
    for box in all_data.get('boxes', []):
        all_locations.append(
            {'type': 'box', 'id': box.get('id'), 'name': f"Box #{box.get('id')} ({box.get('alias', '')})"})
    deck_files = glob.glob(os.path.join(DECKS_DIR, '*.json'))
    for f_path in deck_files:
        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                deck_data = json.load(f).get('deck', {})
                if deck_data.get('name'):
                    all_locations.append(
                        {'type': 'deck', 'id': deck_data['name'], 'name': f"Deck: {deck_data['name']}"})
        except Exception:
            continue

    # (The existing QR code logic remains here)
    if config.get('network_server_enabled'):
        local_ip = get_local_ip()
        if local_ip:
            # Generate the QR code image
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(f"http://{local_ip}:5000")
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")

            # Convert image to a data URI to embed in HTML
            buffered = BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            qr_code_data_uri = f"data:image/png;base64,{img_str}"

    return render_template('settings.html',
                           nav_items=NAV_ITEMS, active_page='Settings', config=config,
                           qr_code_data_uri=qr_code_data_uri, local_ip_address=get_local_ip(),
                           all_locations=all_locations)

@app.route('/save_settings', methods=['POST'])
def save_settings():
    """Saves settings to config.json."""
    new_config = request.get_json()
    with open(CONFIG_FILE, 'w') as f:
        json.dump(new_config, f, indent=2)
    return jsonify({'success': True, 'message': 'Settings saved! Please restart the application for changes to take effect.'})


def load_data():
    try:
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"boxes": [], "card_notes": {}}


def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)


@app.route('/decks')
def decks_view():
    """Displays the list of all decks and calculates their color identity."""
    if not os.path.exists(DECKS_DIR):
        os.makedirs(DECKS_DIR)

    deck_files = glob.glob(os.path.join(DECKS_DIR, '*.json'))
    all_decks = []
    # Define the canonical color order
    wubrg_order = ['W', 'U', 'B', 'R', 'G']

    for f_path in deck_files:
        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                deck_data = json.load(f)
                deck_obj = deck_data.get('deck', {})

                # --- NEW: Calculate combined color identity ---
                commanders = deck_obj.get('commander', [])
                combined_identity = set()
                for commander_card in commanders:
                    combined_identity.update(commander_card.get('color_identity', []))

                # Sort the identity into WUBRG order
                sorted_identity = [color for color in wubrg_order if color in combined_identity]
                deck_obj['calculated_identity'] = sorted_identity
                # --- End of new logic ---

                all_decks.append(deck_obj)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error loading or parsing {f_path}: {e}")

    return render_template('decks.html',
                           nav_items=NAV_ITEMS,
                           active_page='Decks',
                           decks=all_decks)


@app.route('/create_deck', methods=['POST'])
def create_deck():
    """Creates a new, empty deck JSON file."""
    req = request.get_json()
    deck_name = req.get('deck_name')
    if not deck_name:
        return jsonify({'success': False, 'error': 'Deck name cannot be empty.'}), 400

    if not os.path.exists(DECKS_DIR):
        os.makedirs(DECKS_DIR)

    # Sanitize filename
    safe_filename = "".join(c for c in deck_name if c.isalnum() or c in (' ', '_')).rstrip()
    filepath = os.path.join(DECKS_DIR, f"{safe_filename}.json")

    if os.path.exists(filepath):
        return jsonify({'success': False, 'error': 'A deck with this name already exists.'}), 400

    # Create the default deck structure
    new_deck_data = {
        "deck": {
            "name": deck_name,
            "commander": [],
            "mainboard": [],
            "tags": []
        }
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(new_deck_data, f, indent=2)

    return jsonify({'success': True, 'message': f"Deck '{deck_name}' created."})


@app.route('/delete_deck', methods=['POST'])
def delete_deck():
    """Deletes a deck JSON file by its name."""
    req = request.get_json()
    deck_name = req.get('deck_name')
    if not deck_name:
        return jsonify({'success': False, 'error': 'Deck name not provided.'}), 400

    # Find the corresponding file and delete it
    safe_filename = "".join(c for c in deck_name if c.isalnum() or c in (' ', '_')).rstrip()
    filepath = os.path.join(DECKS_DIR, f"{safe_filename}.json")

    if os.path.exists(filepath):
        os.remove(filepath)
        return jsonify({'success': True})

    return jsonify({'success': False, 'error': 'Deck file not found.'}), 404


def get_deck_filepath(deck_name):
    """Safely creates a filename from a deck name."""
    safe_filename = "".join(c for c in deck_name if c.isalnum() or c in (' ', '_')).rstrip()
    return os.path.join(DECKS_DIR, f"{safe_filename}.json")


@app.route('/deck/<deck_name>')
def deck_view(deck_name):
    """Displays the detailed view for a single deck."""
    filepath = get_deck_filepath(deck_name)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            deck_data = json.load(f).get('deck', {})

            # Calculate total card count
            commander_count = sum(c.get('quantity', 1) for c in deck_data.get('commander', []))
            mainboard_count = sum(c.get('quantity', 1) for c in deck_data.get('mainboard', []))
            card_count = commander_count + mainboard_count

    except (FileNotFoundError, json.JSONDecodeError):
        deck_data = None
        card_count = 0

    if not deck_data:
        # handle deck not found
        return "Deck not found", 404

        # 1. Get sorting and grouping options from URL
        # Default to 'name' for sorting and 'none' for grouping
    sort_by = request.args.get('sort_by', 'name')
    group_by = request.args.get('group_by', 'none')

    cards = deck_data.get('mainboard', [])  # Get the list of cards

    # 2. Sort the list of cards FIRST
    if sort_by == 'cmc':
        # Sort by Converted Mana Cost, then by name as a tie-breaker
        cards.sort(key=lambda c: (c.get('cmc', 0), c.get('name', '')))
    elif sort_by == 'set':
        # Sort by set (alphabetical), then collector number (numerical)
        # We cast collector_number to int to ensure '10' comes after '2'
        cards.sort(key=get_card_sort_key)
    else:  # Default sort_by == 'name'
        cards.sort(key=lambda c: c.get('name', ''))

    # 3. Group the sorted list of cards
    grouped_cards = {}
    if group_by == 'none':
        grouped_cards['All Cards'] = cards
    elif group_by == 'type':
        for card in cards:
            # Splits "Artifact Creature — Golem" into just "Artifact" and "Creature"
            # This handles multi-type cards cleanly
            types = card.get('type_line', '').split(' — ')[0].split()
            for t in types:
                grouped_cards.setdefault(t, []).append(card)
    elif group_by == 'tags':
        # Assuming you have a way to get tags for a card, e.g., card.get('tags', [])
        # This part will depend on how your card tag data is structured
        untagged_cards = []
        for card in cards:
            tags = card.get('tags', [])  # You might need to fetch this data
            if tags:
                for tag in tags:
                    grouped_cards.setdefault(tag, []).append(card)
            else:
                untagged_cards.append(card)
        if untagged_cards:
            grouped_cards['Untagged'] = untagged_cards

    return render_template('deck_view.html',
                           nav_items=NAV_ITEMS,
                           active_page='Decks',
                           deck=deck_data,
                           card_count=card_count,
                           cards_data=grouped_cards,
                           current_sort=sort_by,
                           current_group=group_by)


@app.route('/update_deck_card', methods=['POST'])
def update_deck_card():
    """Updates the quantity of a card within a specific deck file."""
    req = request.get_json()
    deck_name, card_id, change = req.get('deck_name'), req.get('card_id'), req.get('change')

    filepath = get_deck_filepath(deck_name)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        card_to_update = next((c for c in data['deck']['mainboard'] if c.get('id') == card_id), None)

        if card_to_update:
            card_to_update['quantity'] += change
            if card_to_update['quantity'] <= 0:
                data['deck']['mainboard'].remove(card_to_update)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            return jsonify({'success': True, 'new_quantity': card_to_update.get('quantity', 0)})
        return jsonify({'success': False, 'error': 'Card not found in deck'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/rename_deck', methods=['POST'])
def rename_deck():
    """Renames a deck file and the name property inside it."""
    req = request.get_json()
    old_name, new_name = req.get('old_name'), req.get('new_name')

    if not all([old_name, new_name]) or old_name == new_name:
        return jsonify({'success': False, 'error': 'Invalid names provided.'}), 400

    old_filepath = get_deck_filepath(old_name)
    new_filepath = get_deck_filepath(new_name)

    if not os.path.exists(old_filepath):
        return jsonify({'success': False, 'error': 'Original deck not found.'}), 404
    if os.path.exists(new_filepath):
        return jsonify({'success': False, 'error': 'A deck with the new name already exists.'}), 400

    try:
        # Read data, update internal name, then rename file
        with open(old_filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        data['deck']['name'] = new_name

        with open(old_filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        os.rename(old_filepath, new_filepath)

        return jsonify({'success': True, 'new_name': new_name})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/update_deck_tags', methods=['POST'])
def update_deck_tags():
    """Updates the list of tags for a specific deck."""
    req = request.get_json()
    deck_name, tags = req.get('deck_name'), req.get('tags')

    filepath = get_deck_filepath(deck_name)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        data['deck']['tags'] = tags

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        return jsonify({'success': True, 'message': 'Tags updated!'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/move_deck_card', methods=['POST'])
def move_deck_card():
    """Moves a card between the mainboard and commander lists in a deck."""
    req = request.get_json()
    deck_name, card_id, destination = req.get('deck_name'), req.get('card_id'), req.get('destination')

    if not all([deck_name, card_id, destination]):
        return jsonify({'success': False, 'error': 'Missing data.'}), 400

    filepath = get_deck_filepath(deck_name)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        deck = data.get('deck', {})
        mainboard = deck.get('mainboard', [])
        commander_list = deck.get('commander', [])
        card_to_move = None
        source_list = None

        # Find card in mainboard
        for card in mainboard:
            if card.get('id') == card_id:
                card_to_move = card
                source_list = mainboard
                break

        # If not in mainboard, find in commander list
        if not card_to_move:
            for card in commander_list:
                if card.get('id') == card_id:
                    card_to_move = card
                    source_list = commander_list
                    break

        if card_to_move:
            source_list.remove(card_to_move)
            if destination == 'commander':
                deck.setdefault('commander', []).append(card_to_move)
            else:  # destination is 'mainboard'
                deck.setdefault('mainboard', []).append(card_to_move)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            return jsonify({'success': True})

        return jsonify({'success': False, 'error': 'Card not found in deck.'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/create_box', methods=['POST'])
def create_box():
    data = load_data()
    boxes = data.get('boxes', [])
    # UPDATED: Generate sequential hexadecimal IDs
    if boxes:
        last_id = max(int(b['id'], 16) for b in boxes if b.get('id'))
        new_id = f"{last_id + 1:04x}"  # 'x' for lowercase hex
    else:
        new_id = "0001"

    new_box = {"id": new_id, "alias": f"New Box {new_id}", "cards": []}
    data.setdefault('boxes', []).append(new_box)
    save_data(data)
    return jsonify({'success': True, 'new_box': new_box})


# --- NEW: Route for renaming a box ---
@app.route('/rename_box', methods=['POST'])
def rename_box():
    req = request.get_json()
    box_id, new_alias = req.get('box_id'), req.get('new_alias')
    data = load_data()
    box = next((b for b in data.get('boxes', []) if b.get('id') == box_id), None)
    if box and new_alias:
        box['alias'] = new_alias
        save_data(data)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Box not found or alias empty'}), 404


# --- NEW: Route for deleting a box ---
@app.route('/delete_box', methods=['POST'])
def delete_box():
    req = request.get_json()
    box_id = req.get('box_id')
    data = load_data()
    original_len = len(data.get('boxes', []))
    data['boxes'] = [b for b in data.get('boxes', []) if b.get('id') != box_id]
    if len(data['boxes']) < original_len:
        save_data(data)
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Box not found'}), 404


# --- NEW: Route for pasting cards ---
# In app.py

@app.route('/paste_cards', methods=['POST'])
def paste_cards():
    req = request.get_json()
    box_id, clipboard = req.get('box_id'), req.get('clipboard')
    data = load_data()
    box = next((b for b in data.get('boxes', []) if b.get('id') == box_id), None)

    if not box or not clipboard:
        return jsonify({'success': False, 'error': 'Box or clipboard empty'}), 400

    # Ensure box['cards'] exists and is a list
    if 'cards' not in box or not isinstance(box['cards'], list):
        box['cards'] = []

    for clip_card in clipboard:
        existing_card = next((c for c in box['cards'] if c.get('id') == clip_card.get('id')), None)
        if existing_card:
            existing_card['quantity'] += clip_card['quantity']
        else:
            box['cards'].append(clip_card)

    save_data(data)
    # Return the entire updated box object
    return jsonify({'success': True, 'updated_box': box})


# In app.py
def download_and_get_local_path(card_data):
    """
    Downloads images based on card layout (single, transform, adventure, etc.)
    """
    image_paths = {}
    images_dir = CARD_IMAGES_DIR
    if not os.path.exists(images_dir):
        os.makedirs(images_dir)

    def download_image(url, card_id, face_suffix=''):
        if not url: return None
        filename = f"{card_id}{face_suffix}.jpg"
        local_path = os.path.join(images_dir, filename)
        saved_path = f"{filename}"
        if not os.path.exists(local_path):
            try:
                res = requests.get(url, stream=True)
                res.raise_for_status()
                with open(local_path, 'wb') as f:
                    for chunk in res.iter_content(chunk_size=8192): f.write(chunk)
            except requests.exceptions.RequestException as e:
                print(f"Error downloading {url}: {e}")
                return None
        return saved_path

    # Check the card's layout to determine how to handle images
    layout = card_data.get('layout')

    # These layouts have two distinct images
    if layout in ['transform', 'modal_dfc', 'reversible_card']:
        face1_url = card_data['card_faces'][0].get('image_uris', {}).get('normal')
        face2_url = card_data['card_faces'][1].get('image_uris', {}).get('normal')
        # The main image is always the front face
        image_paths['main'] = download_image(face1_url, card_data['id'], '_face0')
        # Store each face separately for card_view
        image_paths['face0'] = image_paths['main']
        image_paths['face1'] = download_image(face2_url, card_data['id'], '_face1')
    else:
        # All other layouts (normal, adventure, split) use a single image
        main_url = card_data.get('image_uris', {}).get('normal')
        image_paths['main'] = download_image(main_url, card_data['id'])

    return image_paths


# Make sure you have this import at the top of your file
from flask import request


def get_card_sort_key(card):
    """Creates a robust sort key for cards (set, collector_number)."""
    set_code = card.get('set', '').lower()

    collector_num_str = str(card.get('collector_number', '0'))

    # This safely extracts only the digits from the collector number string
    numeric_part = ''.join(filter(str.isdigit, collector_num_str))

    try:
        # Convert the extracted digits to an integer
        collector_num = int(numeric_part) if numeric_part else 0
    except (ValueError, TypeError):
        collector_num = 0  # Default to 0 if something goes wrong

    # --- ADD THIS DEBUG LINE ---
    # print(f"Sorting '{card.get('name')}': Key is ({set_code}, {collector_num})")
    # ---------------------------

    return (set_code, collector_num)

@app.route('/box/<box_id>')
def box_view(box_id):
    """Displays a detailed view for a single box with sorting and grouping."""
    all_data = load_data()
    box_to_view = next((b for b in all_data.get('boxes', []) if b.get('id') == box_id), None)

    if not box_to_view:
        return "Box not found", 404

    # --- Start of New Logic ---

    # 1. Get sorting and grouping options from URL query
    sort_by = request.args.get('sort_by', 'name')
    group_by = request.args.get('group_by', 'none')

    cards = box_to_view.get('cards', [])

    # 2. Sort the list of cards
    if sort_by == 'cmc':
        cards.sort(key=lambda c: (c.get('cmc', 0), c.get('name', '')))
    elif sort_by == 'set':
        cards.sort(key=get_card_sort_key)
    else:  # Default to 'name'
        cards.sort(key=lambda c: c.get('name', ''))

    # 3. Group the sorted list of cards
    grouped_cards = {}
    if group_by == 'none':
        grouped_cards['All Cards'] = cards
    elif group_by == 'type':
        for card in cards:
            types = card.get('type_line', '').split(' — ')[0].split()
            for t in types:
                grouped_cards.setdefault(t, []).append(card)
    elif group_by == 'tags':
        # NOTE: This assumes card objects have a 'tags' list.
        # You may need to adjust how you retrieve tags for each card.
        untagged_cards = []
        for card in cards:
            tags = card.get('tags', [])
            if tags:
                for tag in tags:
                    grouped_cards.setdefault(tag, []).append(card)
            else:
                untagged_cards.append(card)
        if untagged_cards:
            grouped_cards['Untagged'] = untagged_cards

    # Pre-calculate counts for the title
    cards_in_box = box_to_view.get('cards', [])
    box_to_view['unique_count'] = len(cards_in_box)
    box_to_view['total_count'] = sum(card.get('quantity', 0) for card in cards_in_box)

    # 4. Pass new structured data to the template
    return render_template('box_view.html',
                           nav_items=NAV_ITEMS,
                           active_page='Library',
                           box=box_to_view,
                           cards_data=grouped_cards,
                           current_sort=sort_by,
                           current_group=group_by)


@app.route('/search')
def search_results():
    """Handles both simple and advanced search queries, respecting library/scryfall toggles."""
    args = request.args
    query_parts = []

    # Process advanced search form fields to build a Scryfall query string
    if args.get('card_name'):
        query_parts.append(args['card_name'].strip())
    if args.get('oracle_text'):
        query_parts.append(f"oracle:\"{args['oracle_text']}\"")
    if args.get('card_type'):
        query_parts.append(f"type:{args['card_type']}")
    if args.get('set_code'):
        query_parts.append(f"set:{args['set_code']}")
    if args.get('mana_value'):
        operator = args.get('mv_operator', '=')
        query_parts.append(f"cmc{operator}{args['mana_value']}")

    colors = args.getlist('colors')
    if colors:
        color_operator = args.get('color_operator', '=')
        color_string = "".join(colors)
        query_parts.append(f"color{color_operator}{color_string}")

    color_identity = args.getlist('color_identity')
    if color_identity:
        identity_string = "".join(color_identity)
        query_parts.append(f"identity<={identity_string}")

    # Use the simple query from the header if it exists, otherwise use the advanced query
    query = args.get('q') or " ".join(query_parts)
    query = query.strip()

    # Get toggle and sorting options from URL
    search_lib = args.get('library_search') == 'on'
    search_scryfall = args.get('non_library_search') == 'on'
    sort_order = args.get('sort_order', 'name')
    sort_dir = args.get('sort_dir', 'asc')
    tag_query = args.get('card_tag', '').strip().lower()

    results = []
    error = None

    if not query and (not tag_query):
        error = "Please enter a search query."
    elif search_lib and not search_scryfall:
        # --- Library-Only Search (by name) ---
        # --- NEW & CORRECTED Library-Only Advanced Search Logic ---
        all_data = load_data()
        all_owned_cards = {}
        for box in all_data.get('boxes', []):
            for card in box.get('cards', []):
                if card.get('id') and card['id'] not in all_owned_cards: all_owned_cards[card['id']] = card
        deck_files = glob.glob(os.path.join(DECKS_DIR, '*.json'))
        for f_path in deck_files:
            try:
                with open(f_path, 'r', encoding='utf-8') as f:
                    deck = json.load(f).get('deck', {})
                    for card in deck.get('mainboard', []) + deck.get('commander', []):
                        if card.get('id') and card['id'] not in all_owned_cards: all_owned_cards[card['id']] = card
            except Exception:
                continue

        filtered_results = list(all_owned_cards.values())

        # --- Handle Card Tag Filter ---
        if tag_query:
            card_tags_data = all_data.get('card_tags', {})
            filtered_results = [
                card for card in filtered_results
                if tag_query in [t.lower() for t in card_tags_data.get(card.get('name'), [])]
            ]

        # This simplified parser loops through each part of the query, e.g., "type:creature"
        # Note: This parser does not handle spaces in quoted values (e.g., o:"draw a card")
        for part in query.split():
            # Handle key:value filters (e.g., type:creature)
            if ":" in part:
                key, value = part.split(":", 1)
                value = value.lower()
                if key in ('type', 't'):
                    filtered_results = [c for c in filtered_results if value in c.get('type_line', '').lower()]
                elif key in ('oracle', 'o'):
                    filtered_results = [c for c in filtered_results if value in c.get('oracle_text', '').lower()]
                continue

            # Handle filters with operators (e.g., cmc>=5 or id<=W)
            op_match = re.match(r"(id|identity|c|color|cmc|mv)(<=|>=|=|<|>)(\S+)", part, re.IGNORECASE)
            if op_match:
                key, op, value = op_match.groups()
                key = key.lower()
                value = value.lower()

                if key in ('cmc', 'mv'):
                    try:
                        cmc_val = float(value)
                        if op == '=':
                            filtered_results = [c for c in filtered_results if c.get('cmc', 0) == cmc_val]
                        elif op == '>=':
                            filtered_results = [c for c in filtered_results if c.get('cmc', 0) >= cmc_val]
                        elif op == '<=':
                            filtered_results = [c for c in filtered_results if c.get('cmc', 0) <= cmc_val]
                    except ValueError:
                        pass
                elif key in ('id', 'identity'):
                    filter_identity = set(value.upper())
                    if op == '<=': filtered_results = [c for c in filtered_results if
                                                       set(c.get('color_identity', [])).issubset(filter_identity)]
                continue

            # Handle bare words as name searches
            filtered_results = [c for c in filtered_results if part.lower() in c.get('name', '').lower()]

        results = filtered_results
        for card in results: card['is_owned'] = True
        if not results: error = f"No cards matching your criteria found in your library."

    elif search_scryfall:
        # --- Scryfall Search ---
        try:
            api_url = f"https://api.scryfall.com/cards/search?q={query}&unique=prints&order={sort_order}&dir={sort_dir}"
            response = requests.get(api_url)
            response.raise_for_status()
            search_data = response.json()
            scryfall_results = search_data.get('data', [])

            # If library search is also on, add the 'owned' flag
            if search_lib:
                all_data = load_data()
                owned_card_ids = {c['id'] for b in all_data.get('boxes', []) for c in b.get('cards', [])}
                deck_files = glob.glob(os.path.join(DECKS_DIR, '*.json'))
                for f_path in deck_files:
                    with open(f_path, 'r', encoding='utf-8') as f:
                        deck = json.load(f).get('deck', {})
                        for card in deck.get('mainboard', []) + deck.get('commander', []):
                            owned_card_ids.add(card['id'])

                for card in scryfall_results:
                    card['is_owned'] = card['id'] in owned_card_ids

            results = scryfall_results
        except requests.exceptions.HTTPError:
            error = "No cards found on Scryfall for your criteria."
        except requests.exceptions.RequestException:
            error = "Could not connect to the card database."
    else:
        error = "Please select a search type (Library or Scryfall)."

    return render_template('search_results.html',
                           nav_items=NAV_ITEMS, active_page=None,
                           query=query, results=results, error=error)


# In app.py

@app.route('/advanced_search')
def advanced_search_page():
    """Displays the advanced search form page."""
    set_list = []
    try:
        # Fetch all sets from Scryfall to populate the dropdown
        response = requests.get("https://api.scryfall.com/sets")
        response.raise_for_status()
        set_data = response.json().get('data', [])
        # Filter to only get major expansion types, etc.
        set_list = [s for s in set_data if s.get('set_type') in ['core', 'expansion', 'masters', 'draft_innovation']]
    except requests.exceptions.RequestException as e:
        print(f"Could not fetch set list from Scryfall: {e}")

    return render_template('advanced_search.html',
                           nav_items=NAV_ITEMS,
                           active_page=None,
                           set_list=set_list)

# --- (Keep existing / and /import routes) ---
# The NEW, corrected version
@app.route('/')
def home():
    """Renders the main Library view."""
    data = load_data()
    boxes_data = data.get('boxes', [])

    # Calculate counts for each box
    for box in boxes_data:
        cards_in_box = box.get('cards', [])
        box['unique_count'] = len(cards_in_box)
        box['total_count'] = sum(card.get('quantity', 0) for card in cards_in_box)

    return render_template('index.html',
                           nav_items=NAV_ITEMS,
                           active_page='Library',
                           all_boxes=boxes_data)


# In app.py

@app.route('/import')
def import_page():
    """Renders the Import page with all location data."""
    all_data = load_data()
    all_locations = []
    all_decks_data = []
    config = load_config()

    # Get all boxes
    boxes_data = all_data.get('boxes', [])
    for box in boxes_data:
        all_locations.append(
            {'type': 'box', 'id': box.get('id'), 'name': f"Box #{box.get('id')} ({box.get('alias', '')})"})

    # Get all decks
    deck_files = glob.glob(os.path.join(DECKS_DIR, '*.json'))
    for f_path in deck_files:
        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                deck_data = json.load(f).get('deck', {})
                if deck_data.get('name'):
                    all_locations.append(
                        {'type': 'deck', 'id': deck_data['name'], 'name': f"Deck: {deck_data['name']}"})
                    all_decks_data.append(deck_data)
        except Exception:
            continue

    return render_template('import.html',
                           nav_items=NAV_ITEMS,
                           active_page='Import',
                           all_locations=all_locations,
                           # Pass the full collection data to the template
                           all_boxes=boxes_data,
                           all_decks=all_decks_data,
                           config=config)


@app.route('/import_decklist', methods=['POST'])
def import_decklist():
    """Parses a decklist and creates a new deck file."""
    req = request.get_json()
    deck_name, decklist_text = req.get('deck_name'), req.get('decklist_text')

    if not all([deck_name, decklist_text]):
        return jsonify({'success': False, 'error': 'Deck name and list cannot be empty.'}), 400

    filepath = get_deck_filepath(deck_name)
    if os.path.exists(filepath):
        return jsonify({'success': False, 'error': 'A deck with this name already exists.'}), 400

    mainboard = []
    errors = []
    # Regex to capture: (1-quantity) (2-name) (3-set) (4-number)
    pattern = re.compile(r'(\d+)\s+(.+?)\s+\((\w+)\)\s+(\w+)')

    for line in decklist_text.strip().split('\n'):
        match = pattern.match(line.strip())
        if not match:
            if line.strip(): errors.append(f"Could not parse line: {line}")
            continue

        quantity, _, set_code, number = match.groups()
        try:
            response = requests.get(f"https://api.scryfall.com/cards/{set_code.lower()}/{number}")
            response.raise_for_status()
            card_data = response.json()

            # Check if this card printing is already in the list
            existing_card = next((c for c in mainboard if c['id'] == card_data['id']), None)
            if existing_card:
                existing_card['quantity'] += int(quantity)
            else:
                card_obj = {
                    'id': card_data['id'], 'name': card_data['name'],
                    'image_url': card_data.get('image_uris', {}).get('normal') or card_data.get('card_faces', [{}])[
                        0].get('image_uris', {}).get('normal'),
                    'quantity': int(quantity), 'mana_cost': card_data.get('mana_cost', ''),
                    'oracle_text': card_data.get('oracle_text', ''), 'flavor_text': card_data.get('flavor_text', ''),
                    'power': card_data.get('power', None), 'toughness': card_data.get('toughness', None),
                    'type_line': card_data.get('type_line', '')
                }
                mainboard.append(card_obj)

        except requests.exceptions.RequestException:
            errors.append(f"Failed to fetch: {line}")

    new_deck_data = {
        "deck": {"name": deck_name, "commander": [], "mainboard": mainboard, "tags": []}
    }
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(new_deck_data, f, indent=2)

    return jsonify({'success': True, 'message': f"Deck '{deck_name}' created successfully!", 'errors': errors})


# --- (Keep existing /search_scryfall route) ---
# In app.py

@app.route('/search_scryfall', methods=['POST'])
def search_scryfall():
    """Searches the Scryfall API based on the provided query and type."""
    search_data = request.get_json()
    search_type = search_data.get('type')
    query = search_data.get('query')

    if not query:
        return jsonify({'error': 'Search query cannot be empty'}), 400

    scryfall_url = ""
    if search_type == 'name':
        # MODIFIED: Added "&unique=prints" to get all versions of a card
        scryfall_url = f"https://api.scryfall.com/cards/search?q={query}&unique=prints"
    elif search_type == 'number_set':
        parts = query.split()
        if len(parts) != 2:
            return jsonify({'error': 'Format must be "Number Set" (e.g., "123 2X2")'}), 400
        collector_number, set_code = parts
        scryfall_url = f"https://api.scryfall.com/cards/{set_code.lower()}/{collector_number}"

    try:
        response = requests.get(scryfall_url)
        response.raise_for_status()
        card_data = response.json()
        return jsonify(card_data)
    except requests.exceptions.HTTPError as err:
        if err.response.status_code == 404:
            return jsonify({'error': 'Card not found'}), 404
        return jsonify({'error': f'Scryfall API error: {err}'}), 500
    except requests.exceptions.RequestException as err:
        return jsonify({'error': f'Request failed: {err}'}), 500


# --- NEW: Route to handle updating card quantities ---
@app.route('/update_card_quantity', methods=['POST'])
def update_card_quantity():
    req_data = request.get_json()
    box_id = req_data.get('box_id')
    card_id = req_data.get('card_id')  # We only need the ID now
    change = req_data.get('change')

    data = load_data()
    boxes = data.setdefault('boxes', [])
    target_box = None
    new_box_created = False

    # If box_id is empty, create a new box
    if not box_id:
        if change < 0:
            return jsonify({'success': False, 'error': 'Cannot remove cards from a new box.'}), 400

        if boxes:
            # Find the highest existing ID and increment it
            last_id = max(int(b['id']) for b in boxes if b['id'].isdigit())
            new_id = f"{last_id + 1:04d}"
        else:
            new_id = "0001"

        new_box = {
            "id": new_id,
            "hex_id": secrets.token_hex(2),
            "cards": []
        }
        boxes.append(new_box)
        target_box = new_box
        new_box_created = True
    else:
        # Find the existing box
        for box in boxes:
            if box['id'] == box_id:
                target_box = box
                break

    if not target_box:
        return jsonify({'success': False, 'error': 'Box not found'}), 404

    # Find the card in the box using a generator expression, or None if not found
    card_in_box = next((card for card in target_box['cards'] if card.get('id') == card_id), None)

    if card_in_box:
        # If card is already in the box, just update its quantity
        card_in_box['quantity'] += change
    elif change > 0:
        # If card is NOT in the box and we are adding, fetch its full data
        try:
            response = requests.get(f"https://api.scryfall.com/cards/{card_id}")
            response.raise_for_status()
            card_data = response.json()

            # Create a new, detailed card object to save
            card_in_box = {
                'id': card_data['id'],
                'name': card_data['name'],
                'image_url': card_data.get('image_uris', {}).get('normal') or card_data.get('card_faces', [{}])[0].get(
                    'image_uris', {}).get('normal'),
                'quantity': 1,
                'mana_cost': card_data.get('mana_cost', ''),
                'oracle_text': card_data.get('oracle_text', ''),
                'flavor_text': card_data.get('flavor_text', ''),
                'power': card_data.get('power', None),
                'toughness': card_data.get('toughness', None),
                'type_line': card_data.get('type_line', '')
            }
            target_box.setdefault('cards', []).append(card_in_box)
        except requests.exceptions.RequestException as e:
            return jsonify({'success': False, 'error': f'Could not fetch card data: {e}'}), 500

    # If quantity is 0 or less after the change, remove the card from the list
    if card_in_box and card_in_box['quantity'] <= 0:
        target_box['cards'].remove(card_in_box)

    save_data(data)

    return jsonify({
        'success': True,
        'new_quantity': card_in_box['quantity'] if card_in_box and card_in_box['quantity'] > 0 else 0,
        'new_box_info': target_box if new_box_created else None
    })


@app.route('/card/<card_id>')
def card_view(card_id):
    """
    Displays the detailed view for a single card, correctly finding all locations
    and alternate printings from the entire collection.
    """
    all_data = load_data()
    card_details = None

    # 1. Get the primary card's details (from local data if possible)
    all_owned_cards = {c['id']: c for b in all_data.get('boxes', []) for c in b.get('cards', [])}
    deck_files = glob.glob(os.path.join(DECKS_DIR, '*.json'))
    for f_path in deck_files:
        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                deck = json.load(f).get('deck', {})
                for card in deck.get('mainboard', []) + deck.get('commander', []):
                    all_owned_cards[card['id']] = card
        except Exception:
            continue

    if card_id in all_owned_cards:
        card_details = all_owned_cards[card_id]
    else:
        try:
            response = requests.get(f"https://api.scryfall.com/cards/{card_id}")
            response.raise_for_status()
            card_details = response.json()
        except requests.exceptions.RequestException:
            pass

    if not card_details:
        return render_template('card_view.html', nav_items=NAV_ITEMS, card=None)

    # 2. Aggregate all data for the template
    canonical_name = card_details.get('name')
    current_locations = []
    alternate_printings = {}
    total_quantity = 0
    all_locations_for_dropdown = []  # This will hold ALL possible destinations

    # --- CORRECTED: This section now correctly builds all location lists ---
    # A. Loop through BOXES
    for box in all_data.get('boxes', []):
        all_locations_for_dropdown.append(
            {'type': 'box', 'id': box['id'], 'name': f"Box #{box['id']} ({box.get('alias', '')})"})
        for card in box.get('cards', []):
            if card.get('name') == canonical_name:
                total_quantity += card.get('quantity', 0)
                if card.get('id') == card_id:
                    current_locations.append({'type': 'box', 'id': box['id'], 'name': f"Box #{box['id']}",
                                              'quantity': card.get('quantity', 0)})
                else:
                    alternate_printings[card['id']] = card.copy()

    # B. Loop through DECKS
    for f_path in deck_files:
        try:
            with open(f_path, 'r', encoding='utf-8') as f:
                deck = json.load(f).get('deck', {})
                deck_name = deck.get('name')
                if not deck_name: continue
                all_locations_for_dropdown.append({'type': 'deck', 'id': deck_name, 'name': f"Deck: {deck_name}"})
                for card in deck.get('mainboard', []) + deck.get('commander', []):
                    if card.get('name') == canonical_name:
                        total_quantity += card.get('quantity', 0)
                        if card.get('id') == card_id:
                            current_locations.append({'type': 'deck', 'id': deck_name, 'name': f"Deck: {deck_name}",
                                                      'quantity': card.get('quantity', 0)})
                        else:
                            if card['id'] not in alternate_printings:
                                alternate_printings[card['id']] = card.copy()
        except Exception as e:
            print(f"Error processing deck file {f_path}: {e}")

    # 3. Get notes and tags
    notes = all_data.get('card_notes', {}).get(canonical_name, "")
    card_tags = all_data.get('card_tags', {}).get(canonical_name, [])

    return render_template('card_view.html',
                           nav_items=NAV_ITEMS,
                           active_page=None,
                           card=card_details,
                           total_quantity=total_quantity,
                           notes=notes,
                           card_tags=card_tags,
                           current_locations=current_locations,
                           all_locations=all_locations_for_dropdown,
                           alternate_printings=list(alternate_printings.values()))


# In app.py

@app.route('/update_card_tags', methods=['POST'])
def update_card_tags():
    """Updates the list of tags for a specific card name."""
    req = request.get_json()
    card_name, tags = req.get('card_name'), req.get('tags')

    if not card_name:
        return jsonify({'success': False, 'error': 'Card name not provided.'}), 400

    all_data = load_data()
    all_data.setdefault('card_tags', {})[card_name] = tags
    save_data(all_data)

    return jsonify({'success': True})


@app.route('/manage_inventory', methods=['POST'])
def manage_inventory():
    """Handles adding or moving cards between any location (box or deck)."""
    req = request.get_json()
    action = req.get('action')
    card_id = req.get('card_id')
    quantity = int(req.get('quantity', 0))

    if not all([action, card_id]) or quantity == 0:
        return jsonify({'success': False, 'error': 'Missing required data.'}), 400

    card_to_add = None

    # --- Source Logic (only for 'move' action) ---
    if action == 'move':
        source_type, source_id = req.get('source', {}).get('type'), req.get('source', {}).get('id')

        source_data = load_data()  # Load a fresh copy to find the source card
        source_list = None
        source_file_to_save = None

        if source_type == 'box':
            source_loc = next((b for b in source_data['boxes'] if b['id'] == source_id), None)
            if source_loc: source_list = source_loc.get('cards', [])
            source_file_to_save = source_data
        elif source_type == 'deck':
            source_path = get_deck_filepath(source_id)
            if os.path.exists(source_path):
                with open(source_path, 'r', encoding='utf-8') as f:
                    deck_data = json.load(f)
                source_list = deck_data['deck'].get('mainboard', [])
                source_file_to_save = deck_data

        if source_list is None: return jsonify({'success': False, 'error': 'Source location not found.'}), 404

        card_in_source = next((c for c in source_list if c['id'] == card_id), None)
        if not card_in_source: return jsonify({'success': False, 'error': 'Source card not found.'}), 404

        # Prepare the full card object to be moved
        card_to_add = json.loads(json.dumps(card_in_source))
        card_to_add['quantity'] = quantity

        # Update source location
        card_in_source['quantity'] -= quantity
        if card_in_source['quantity'] <= 0:
            source_list[:] = [c for c in source_list if c['id'] != card_id]

        # Save the updated source file
        if source_type == 'box':
            save_data(source_file_to_save)
        elif source_type == 'deck':
            with open(source_path, 'w', encoding='utf-8') as f:
                json.dump(source_file_to_save, f, indent=2)

    # --- Destination Logic (for both 'add' and 'move') ---
    dest_type, dest_id = req.get('destination', {}).get('type'), req.get('destination', {}).get('id')
    new_box_info = None
    all_data = load_data()  # Load fresh data again for destination

    target_list = None
    dest_loc_obj = None

    if dest_type == 'box':
        dest_loc = next((b for b in all_data['boxes'] if b['id'] == dest_id), None)
        if not dest_loc and dest_id == '':
            last_id = max((int(b['id'], 16) for b in all_data.get('boxes', []) if b.get('id')), default=0)
            new_id = f"{last_id + 1:04x}"
            dest_loc = {"id": new_id, "alias": f"New Box {new_id}", "cards": []}
            all_data.setdefault('boxes', []).append(dest_loc)
            new_box_info = dest_loc
        if dest_loc: target_list = dest_loc.setdefault('cards', [])
        dest_loc_obj = all_data

    elif dest_type == 'deck':
        dest_path = get_deck_filepath(dest_id)
        if os.path.exists(dest_path):
            with open(dest_path, 'r', encoding='utf-8') as f: deck_data = json.load(f)
            target_list = deck_data['deck'].setdefault('mainboard', [])
            dest_loc_obj = deck_data

    if target_list is None: return jsonify({'success': False, 'error': 'Destination not found.'}), 404

    card_in_dest = next((c for c in target_list if c.get('id') == card_id), None)

    if card_in_dest:
        card_in_dest['quantity'] += quantity
    else:
        if action == 'move':
            target_list.append(card_to_add)  # Add the card object we prepared earlier
        elif action == 'add':
            # If adding from scratch, fetch full data
            try:
                response = requests.get(f"https://api.scryfall.com/cards/{card_id}")
                response.raise_for_status()
                card_data = response.json()
                local_image_paths = download_and_get_local_path(card_data)
                # Build the complete object we want to save
                new_card_obj = {
                    'id': card_data.get('id'),
                    'name': card_data.get('name'),
                    'image_url': local_image_paths.get('main'),
                    'quantity': quantity,
                    'mana_cost': card_data.get('mana_cost', '') or card_data.get('card_faces', [{}])[0].get(
                        'mana_cost', ''),
                    'oracle_text': card_data.get('oracle_text', '') or card_data.get('card_faces', [{}])[0].get(
                        'oracle_text', ''),
                    'type_line': card_data.get('type_line', ''),
                    'power': card_data.get('power'),
                    'toughness': card_data.get('toughness'),
                    'flavor_text': card_data.get('flavor_text', '') or card_data.get('card_faces', [{}])[0].get(
                        'flavor_text', ''),
                    'card_faces': card_data.get('card_faces', None),
                    'color_identity': card_data.get('color_identity', []),
                    'cmc': card_data.get('cmc', 0.0),
                    'colors': card_data.get('colors', []),
                    'layout': card_data.get('layout'),
                    'set': card_data.get('set'),
                    'collector_number': card_data.get('collector_number')
                }

                if new_card_obj['card_faces']:
                    if local_image_paths.get('face0'):
                        new_card_obj['card_faces'][0]['image_uris'] = {'normal': local_image_paths['face0']}
                    if local_image_paths.get('face1'):
                        new_card_obj['card_faces'][1]['image_uris'] = {'normal': local_image_paths['face1']}
                target_list.append(new_card_obj)
            except Exception as e:
                return jsonify({'success': False, 'error': f'Could not fetch card data: {e}'}), 500

    # Save the updated destination file
    if dest_type == 'box':
        save_data(dest_loc_obj)
    elif dest_type == 'deck':
        with open(dest_path, 'w', encoding='utf-8') as f:
            json.dump(dest_loc_obj, f, indent=2)

    return jsonify({'success': True, 'message': 'Inventory updated.', 'new_box_info': new_box_info})


# In app.py

@app.route('/refresh_box', methods=['POST'])
def refresh_box():
    """
    Loops through all cards in a given box, refetches their full data
    from Scryfall, and updates them while preserving quantity.
    """
    req = request.get_json()
    box_id = req.get('box_id')
    if not box_id:
        return jsonify({'success': False, 'error': 'Box ID not provided.'}), 400

    all_data = load_data()
    box_to_update = next((b for b in all_data.get('boxes', []) if b.get('id') == box_id), None)

    if not box_to_update:
        return jsonify({'success': False, 'error': 'Box not found.'}), 404

    updated_cards = []
    for old_card in box_to_update.get('cards', []):
        try:
            # Preserve the original quantity and ID
            original_quantity = old_card.get('quantity', 1)
            card_id = old_card.get('id')
            if not card_id:
                continue  # Skip cards that have no ID

            # Fetch new full data from Scryfall
            response = requests.get(f"https://api.scryfall.com/cards/{card_id}")
            response.raise_for_status()
            scryfall_data = response.json()
            local_image_paths = download_and_get_local_path(scryfall_data)

            # Build the new, complete card object
            new_card_obj = {
                'id': scryfall_data.get('id'),
                'name': scryfall_data.get('name'),
                'image_url': local_image_paths.get('main'),
                'quantity': original_quantity,
                'mana_cost': scryfall_data.get('mana_cost', '') or scryfall_data.get('card_faces', [{}])[0].get(
                    'mana_cost', ''),
                'oracle_text': scryfall_data.get('oracle_text', '') or scryfall_data.get('card_faces', [{}])[0].get(
                    'oracle_text', ''),
                'type_line': scryfall_data.get('type_line', ''),
                'power': scryfall_data.get('power'),
                'toughness': scryfall_data.get('toughness'),
                'flavor_text': scryfall_data.get('flavor_text', '') or scryfall_data.get('card_faces', [{}])[0].get(
                    'flavor_text', ''),
                'card_faces': scryfall_data.get('card_faces', None),
                'color_identity': scryfall_data.get('color_identity', []),
                'cmc': scryfall_data.get('cmc', 0.0),
                'colors': scryfall_data.get('colors', []),
                'layout': scryfall_data.get('layout'),
                'set': scryfall_data.get('set'),
                'collector_number': scryfall_data.get('collector_number')
            }

            if new_card_obj['card_faces']:
                if local_image_paths.get('face0'):
                    new_card_obj['card_faces'][0]['image_uris'] = {'normal': local_image_paths['face0']}
                if local_image_paths.get('face1'):
                    new_card_obj['card_faces'][1]['image_uris'] = {'normal': local_image_paths['face1']}

            updated_cards.append(new_card_obj)

        except requests.exceptions.RequestException:
            # If a card fails to fetch, we can choose to keep the old data
            updated_cards.append(old_card)
            print(f"Could not refresh data for card ID {old_card.get('id')}")

    # Replace the old card list with the newly updated one
    box_to_update['cards'] = updated_cards
    save_data(all_data)

    return jsonify({'success': True, 'message': 'Box data refreshed successfully.'})


# Add this helper function somewhere in app.py
# Make sure 'requests' is imported and 'download_and_get_local_path' is defined
def _refresh_single_card(old_card):
    """
    Takes an old card object, fetches the latest data from Scryfall,
    and returns a new, fully updated card object.
    Preserves the original quantity.
    """
    try:
        original_quantity = old_card.get('quantity', 1)
        card_id = old_card.get('id')
        if not card_id:
            return old_card # Return the old card if it has no ID

        # Fetch new full data from Scryfall
        response = requests.get(f"https://api.scryfall.com/cards/{card_id}")
        response.raise_for_status()
        scryfall_data = response.json()
        local_image_paths = download_and_get_local_path(scryfall_data)

        # Build the new, complete card object
        new_card_obj = {
            'id': scryfall_data.get('id'),
            'name': scryfall_data.get('name'),
            'image_url': local_image_paths.get('main'),
            'quantity': original_quantity,
            'mana_cost': scryfall_data.get('mana_cost', '') or scryfall_data.get('card_faces', [{}])[0].get('mana_cost', ''),
            'oracle_text': scryfall_data.get('oracle_text', '') or scryfall_data.get('card_faces', [{}])[0].get('oracle_text', ''),
            'type_line': scryfall_data.get('type_line', ''),
            'power': scryfall_data.get('power'),
            'toughness': scryfall_data.get('toughness'),
            'flavor_text': scryfall_data.get('flavor_text', '') or scryfall_data.get('card_faces', [{}])[0].get('flavor_text', ''),
            'card_faces': scryfall_data.get('card_faces', None),
            'color_identity': scryfall_data.get('color_identity', []),
            'cmc': scryfall_data.get('cmc', 0.0),
            'colors': scryfall_data.get('colors', []),
            'layout': scryfall_data.get('layout'),
            'set': scryfall_data.get('set'),
            'collector_number': scryfall_data.get('collector_number')
        }

        # Update image paths for multi-faced cards
        if new_card_obj.get('card_faces'):
            if local_image_paths.get('face0'):
                new_card_obj['card_faces'][0]['image_uris'] = {'normal': local_image_paths['face0']}
            if new_card_obj.get('card_faces') and len(new_card_obj.get('card_faces')) > 1 and local_image_paths.get('face1'):
                new_card_obj['card_faces'][1]['image_uris'] = {'normal': local_image_paths['face1']}

        return new_card_obj

    except requests.exceptions.RequestException:
        # If a card fails to fetch, we keep the old data
        print(f"Could not refresh data for card ID {old_card.get('id')}")
        return old_card

# Replace your old /refresh_deck route with this one

@app.route('/refresh_deck', methods=['POST'])
def refresh_deck():
    """Refreshes all card data in a specific deck file using the helper function."""
    deck_name = request.json.get('deck_name')
    if not deck_name:
        return jsonify({'success': False, 'error': 'Deck name not provided.'}), 400

    deck_filename = f"{deck_name}.json"
    deck_filepath = os.path.join('decks', deck_filename)

    try:
        with open(deck_filepath, 'r', encoding='utf-8') as f:
            deck_data = json.load(f)

        # Use the helper function to refresh the commander list
        updated_commanders = [_refresh_single_card(card) for card in deck_data['deck']['commander']]
        deck_data['deck']['commander'] = updated_commanders

        # Use the helper function to refresh the mainboard list
        updated_mainboard = [_refresh_single_card(card) for card in deck_data['deck']['mainboard']]
        deck_data['deck']['mainboard'] = updated_mainboard

        # Save the fully updated deck data back to its file
        with open(deck_filepath, 'w', encoding='utf-8') as f:
            json.dump(deck_data, f, indent=2)

        return jsonify({'success': True, 'message': f"Deck '{deck_name}' refreshed successfully!"})

    except FileNotFoundError:
        return jsonify({'success': False, 'error': f"Deck file not found for '{deck_name}'."}), 404
    except Exception as e:
        print(f"Error refreshing deck '{deck_name}': {e}")
        return jsonify({'success': False, 'error': 'An unexpected error occurred during refresh.'}), 500

@app.route('/save_note', methods=['POST'])
def save_note():
    """Saves a user's note for a specific card name."""
    note_data = request.get_json()
    card_name = note_data.get('card_name')
    note_text = note_data.get('note_text')

    if not card_name:
        return jsonify({'success': False, 'error': 'Card name not provided.'}), 400

    all_data = load_data()
    all_data.setdefault('card_notes', {})[card_name] = note_text
    save_data(all_data)

    return jsonify({'success': True, 'message': 'Note saved!'})


def replace_symbols(text):
    """
    A Jinja2 filter to replace Scryfall symbols like {T} or {C}
    with locally stored SVG images.
    """
    if not isinstance(text, str):
        return text

    def get_replacement(match):
        code = match.group(1).upper().replace('/', '')
        style = "width:1em; height:1em; vertical-align:-0.125em; margin:0 0.05em;"
        # CORRECTED: This now points to your local static folder
        return f'<img src="/static/mana_symbols/{code}.svg" style="{style}" alt="{match.group(0)}">'

    return re.sub(r'\{([A-Z0-9/]+)\}', get_replacement, text)

# Register the function as a filter in our Flask app
app.jinja_env.filters['replace_symbols'] = replace_symbols


@app.route('/restart_app', methods=['POST'])
def restart_app():
    """Restarts the entire Python application."""
    print("Restarting application...")

    def do_restart():
        """Function to run after a delay."""
        # This function call replaces the current process with a new one
        os.execv(sys.executable, ['python'] + sys.argv)

    # Give the server a moment to respond to the request before restarting
    t = threading.Timer(1.0, do_restart)
    t.start()

    return jsonify({'success': True, 'message': 'Application is restarting...'})

def run_server(app_instance):
    """Function to run the Flask server in a separate thread."""
    # Running on 0.0.0.0 makes it accessible on your network
    app_instance.run(host='0.0.0.0', port=5000)

if __name__ == '__main__':
    config = load_config()
    is_network_mode = config.get('network_server_enabled', False)
    is_debug_mode = config.get('debug_mode', False)
    if is_network_mode:
        # Network-accessible mode
        server_thread = threading.Thread(target=run_server, args=(app,))
        server_thread.daemon = True
        server_thread.start()
        time.sleep(1) # Give server a moment to start
        window = webview.create_window('MTG Inventory Tracker', 'http://127.0.0.1:5000')
        webview.start(debug=is_debug_mode)
    else:
        # Standard private mode
        window = webview.create_window('MTG Inventory Tracker', app)
        # And also add download=True here
        webview.start(debug=is_debug_mode)