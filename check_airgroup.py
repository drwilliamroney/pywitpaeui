import json
path = r"C:\Matrix Games\War in the Pacific Admiral's Edition\SAVE\ALLIED\airgroups.json"
with open(path) as f:
    data = json.load(f)
    ag = data if isinstance(data, list) else data.get("records", data.get("airgroups", []))

print(f"Type of ag: {type(ag)}, len: {len(ag) if ag else 0}")
if ag and len(ag) > 0 and isinstance(ag[0], dict):
    print(f"First item sample keys: {list(ag[0].keys())[:5]}")
    # Show first airgroup with rebasing
    for a in ag[:10]:
        if a.get("is_rebasing"):
            print(f"\nFirst rebasing airgroup: {a.get('name', 'NO NAME')}")
            if "35th" in str(a.get('name', '')):
                print("  ^^ This is 35th PG")
            break

print("\n=== 35th PG/HqS ===")
found = False
for a in ag:
    name = a.get("name") if isinstance(a, dict) else None
    if name and "35th PG/HqS" in str(name):
        print(f"Name: {a.get('name')}")
        print(f"Aircraft: {a.get('aircraft_name')}")
        print(f"Is Rebasing: {a.get('is_rebasing')}")
        print(f"Rebase Target: {a.get('rebase_target_base_name')}")
        print(f"Rebase Coords: ({a.get('rebase_target_x')}, {a.get('rebase_target_y')})")
        print(f"Current Loc: ({a.get('x')}, {a.get('y')})")
        found = True
        break
if not found:
    print("NOT FOUND - searching for '35th' in any name...")
    for a in ag:
        name = a.get("name") if isinstance(a, dict) else None
        if name and "35th" in str(name):
            print(f"Found: {name}")
            print(f"  Is Rebasing: {a.get('is_rebasing')}")
            print(f"  Rebase Target: {a.get('rebase_target_base_name')}")
            break

# Check Lahaina in bases
print("\n=== Lahaina in bases ===")
base_path = r"C:\Matrix Games\War in the Pacific Admiral's Edition\SAVE\ALLIED\bases.json"
with open(base_path) as f:
    bases_data = json.load(f)
    bases = bases_data if isinstance(bases_data, list) else bases_data.get("records", [])
for b in bases:
    base_name = b.get("name") if isinstance(b, dict) else None
    if base_name and "Lahaina" in str(base_name):
        print(f"Name: {b.get('name')}")
        print(f"Coords: ({b.get('x')}, {b.get('y')})")
        break

# Check task forces targeting Lahaina
print("\n=== Task forces targeting Lahaina (182, 108) ===")
tf_path = r"C:\Matrix Games\War in the Pacific Admiral's Edition\SAVE\ALLIED\taskforces.json"
with open(tf_path) as f:
    tf_data = json.load(f)
    tfs = tf_data if isinstance(tf_data, list) else tf_data.get("records", [])
lahaina_tfs = []
for tf in tfs:
    target_x = tf.get("target_x")
    target_y = tf.get("target_y")
    if target_x == 182 and target_y == 108:
        lahaina_tfs.append(tf)
        print(f"TF ID: {tf.get('record_id')}, Flagship: {tf.get('flagship_name')}, Mission: {tf.get('mission')}")
        print(f"  End of Day: ({tf.get('end_of_day_x')}, {tf.get('end_of_day_y')})")
        print(f"  Target: ({tf.get('target_x')}, {tf.get('target_y')})")

if not lahaina_tfs:
    print("No task forces target Lahaina!")
    print("\nAll task force targets:")
    targets_seen = set()
    for tf in tfs[:20]:
        target = (tf.get("target_x"), tf.get("target_y"))
        if target not in targets_seen:
            targets_seen.add(target)
            print(f"  TF {tf.get('record_id')} ({tf.get('flagship_name')}) -> {target}")

# Check for airgroups rebasing to Lahaina
print("\n=== Airgroups rebasing to Lahaina ===")
lahaina_ag = []
for a in ag:
    if not a.get("is_rebasing"):
        continue
    rebase_x = a.get("rebase_target_x")
    rebase_y = a.get("rebase_target_y")
    if rebase_x == 182 and rebase_y == 108:
        lahaina_ag.append(a)
        print(f"Name: {a.get('name')}")
        print(f"  Aircraft: {a.get('aircraft_name')}")
        print(f"  Current: ({a.get('x')}, {a.get('y')})")
        print(f"  Target: {a.get('rebase_target_base_name')} ({rebase_x}, {rebase_y})")

if not lahaina_ag:
    print("No airgroups rebasing to Lahaina!")

# Check for airgroups on TF 391 ships
print("\n=== Ships in TF 391 and their airgroups ===")
ship_path = r"C:\Matrix Games\War in the Pacific Admiral's Edition\SAVE\ALLIED\ships.json"
with open(ship_path) as f:
    ship_data = json.load(f)
    ships = ship_data if isinstance(ship_data, list) else ship_data.get("records", [])

tf391_ship_ids = set()
for ship in ships:
    if ship.get("task_force_id") == 391:
        tf391_ship_ids.add(ship.get("record_id"))
        print(f"Ship: {ship.get('name')} (ID: {ship.get('record_id')})")
        print(f"  Loaded airgroup: {ship.get('loaded_airgroup_cargo_id')}")

print(f"\nLooking for airgroups loaded on TF391 ships: {tf391_ship_ids}")
for a in ag:
    if a.get("loaded_on_ship_id") in tf391_ship_ids or a.get("loaded_as_cargo_on_ship_id") in tf391_ship_ids:
        print(f"Found: {a.get('name')} on ship ID {a.get('loaded_on_ship_id') or a.get('loaded_as_cargo_on_ship_id')}")
