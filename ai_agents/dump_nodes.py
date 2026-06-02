import json, os

path = '/app/agents/examples/voice-assistant/tenapp/property.json'
with open(path) as f:
    d = json.load(f)

graphs = d.get('ten', {}).get('predefined_graphs', [])
for g in graphs:
    for n in g.get('graph', {}).get('nodes', []):
        if n.get('type') == 'extension':
            name = n.get('name', '')
            addon = n.get('addon', '')
            prop = n.get('property', {})
            print(f'=== {name} ({addon}) ===')
            print(json.dumps(prop, indent=2, ensure_ascii=False)[:300])
            print()
