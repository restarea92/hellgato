"""Keep one persistent CORA identity across bridge and installer restarts."""

import json
import os
from pathlib import Path
import re
import secrets


def settings_path():
    return Path(os.environ.get('HELLGATO_STATE_DIR', Path(os.environ['LOCALAPPDATA']) / 'Hellgato')) / 'settings.json'


def existing_identity():
    profiles = Path(os.environ['APPDATA']) / 'Elgato/StreamDeck/ProfilesV3'
    candidates = []
    for manifest in profiles.glob('*/manifest.json'):
        try:
            data = json.loads(manifest.read_text(encoding='utf-8-sig'))
            match = re.search(r'/HGMOCK(\d{6})\]', data.get('Device', {}).get('UUID', ''))
            if match:
                candidates.append((manifest.stat().st_mtime_ns, int(match[1])))
        except (OSError, ValueError):
            continue
    return max(candidates)[1] if candidates else secrets.randbelow(999999) + 1


def load_settings(path=None, identity=None, dock_identity=None):
    path = path or settings_path()
    if path.exists():
        data = json.loads(path.read_text(encoding='utf-8'))
    else:
        data = {'version': 1, 'identity': identity or existing_identity(),
                'dockIdentity': dock_identity or 1, 'stripMode': 'frame'}
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError(f'Unsupported settings in {path}; identity was not regenerated')
    for key in ('identity', 'dockIdentity'):
        if type(data.get(key)) is not int or not 1 <= data[key] <= 999999:
            raise ValueError(f'Invalid {key} in {path}; identity was not regenerated')
    for key, supplied in (('identity', identity), ('dockIdentity', dock_identity)):
        if supplied is not None and supplied != data[key]:
            raise ValueError(f'{key} is fixed at {data[key]} in {path}; refusing to create another device')
    if data.get('stripMode') not in ('frame', 'panels'):
        raise ValueError(f'Invalid stripMode in {path}')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
    return data
