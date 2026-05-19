#!/usr/bin/env python3
"""
parse_gtfs.py — Converts MTA NYC Subway GTFS data into graph.json for DetourNYC.

Works with EITHER a zip file OR an already-unzipped folder.

Usage:
    python3 parse_gtfs.py .                     # current folder with .txt files
    python3 parse_gtfs.py google_transit.zip
"""

import csv, io, json, os, statistics, sys, zipfile
from collections import defaultdict


# ── File reading ──────────────────────────────────────────────────────────────

def read_csv(source, filename):
    if isinstance(source, zipfile.ZipFile):
        with source.open(filename) as f:
            content = f.read().decode("utf-8-sig")
    else:
        with open(os.path.join(source, filename), encoding="utf-8-sig") as f:
            content = f.read()
    return list(csv.DictReader(io.StringIO(content)))


# ── Helpers ───────────────────────────────────────────────────────────────────

def time_to_sec(t):
    h, m, s = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def borough_from_coords(lat, lng):
    """
    Derive borough from lat/lng. GTFS has no borough field.

    Polygon approximation:
      - Manhattan is a tilted island; west edge follows the Hudson
        (roughly N-S below lat 40.78, curves east toward Inwood above)
      - Queens carve-out for LIC/Astoria along the East River (lat ≥ 40.74)
      - Diagonal Brooklyn-Queens border from Newtown Creek SE to Cypress Hills

    Note: 1-2 stations near the Cypress Hills / East New York border may
    misclassify by one borough — the actual line there runs through cemetery
    edges and rail right-of-ways and can't be cleanly modeled from coords.
    """
    # Staten Island
    if lng < -74.040:
        return "Staten Island"

    # Manhattan island (south of Harlem River)
    if 40.700 <= lat <= 40.880:
        p = (lat - 40.700) / 0.180
        east_edge = -73.972 + p * 0.061
        if lat < 40.780:
            west_edge = -74.022                           # Hudson runs ~N-S here
        else:
            west_edge = -74.022 + ((lat - 40.780) / 0.100) * 0.076
        if west_edge <= lng <= east_edge:
            return "Manhattan"

    # Bronx (north of Harlem River)
    if lat > 40.785:
        return "Bronx"

    # Queens carve-outs from Brooklyn:
    # - LIC/Astoria along East River, lat >= 40.74
    if lng > -73.960 and lat >= 40.74:
        return "Queens"
    # - Diagonal border between LIC and Ridgewood, lat 40.70 - 40.74
    if 40.700 <= lat < 40.74:
        boundary = -73.910 - 1.0 * (lat - 40.700)
        if lng > boundary:
            return "Queens"
    # - Below lat 40.70, Queens starts roughly east of -73.88
    if lat < 40.700 and lng > -73.880:
        return "Queens"

    return "Brooklyn"


def build_parent_map(stops_raw):
    """
    MTA GTFS has a 3-level hierarchy:
        Station (location_type=1)
          └─ Stop group (location_type=0, parent_station=station_id)
               └─ Platform (location_type=0, parent_station=stop_group_id)

    stop_times.txt references platform IDs.
    We walk UP the parent chain to find the ultimate location_type=1 station.
    Returns: {stop_id → canonical_station_id}
    """
    stop_index = {r["stop_id"]: r for r in stops_raw}

    def ultimate_parent(sid):
        visited = set()
        curr = sid
        while curr and curr not in visited:
            visited.add(curr)
            row = stop_index.get(curr)
            if not row:
                if curr and curr[-1] in ("N", "S"):
                    curr = curr[:-1]
                    continue
                return curr
            if row.get("location_type", "0").strip() == "1":
                return curr
            parent = row.get("parent_station", "").strip()
            if parent:
                curr = parent
            elif curr[-1:] in ("N", "S"):
                curr = curr[:-1]
            else:
                return curr
        return curr

    return {sid: ultimate_parent(sid) for sid in stop_index}


# ── Station complex merging ───────────────────────────────────────────────────

def canonicalize_name(name):
    """Lowercase, collapse whitespace. Used for merge name-matching."""
    return " ".join(name.lower().strip().split())


def merge_complexes(stations, edges):
    """
    Merge stations that share the SAME canonical name AND are connected by
    a T (walk) edge from transfers.txt.

    Examples that merge: Jay St-MetroTech (A/C/F) + Jay St-MetroTech (N/R/W)
    → one. Times Sq-42 St (1/2/3) + (7) + (N/Q/R/W) → one.

    Examples that DON'T merge (different names): Cortlandt St + Chambers
    St-WTC, Lex Av/59 St + 59 St, 51 St + Lex Av/53 St. They stay as
    separate stations connected by T walk edges — routing handles transfers.

    Previously, a 250m proximity fallback was incorrectly merging the WTC
    complex stations under one name; that's been removed.

    Returns (merged_stations dict, updated_edges list).
    """
    parent = {sid: sid for sid in stations}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        pa, pb = find(a), find(b)
        if pa == pb:
            return
        # Keep the station with more lines as canonical
        la = len(stations[pa].get("lines", []))
        lb = len(stations[pb].get("lines", []))
        if la >= lb:
            parent[pb] = pa
        else:
            parent[pa] = pb

    # Merge T-connected stations only when names match exactly (canonicalized)
    for e in edges:
        if e["line"] != "T":
            continue
        fid, tid = e["from"], e["to"]
        if fid not in stations or tid not in stations:
            continue
        if canonicalize_name(stations[fid]["name"]) == canonicalize_name(stations[tid]["name"]):
            union(fid, tid)

    canonical = {sid: find(sid) for sid in stations}

    # Build merged station records
    merged = {}
    for sid, s in stations.items():
        canon = canonical[sid]
        if canon not in merged:
            cs = stations[canon]
            merged[canon] = {
                "id":      canon,
                "name":    cs["name"],
                "lat":     cs["lat"],
                "lng":     cs["lng"],
                "borough": cs["borough"],
                "lines":   set(cs.get("lines", [])),
            }
        merged[canon]["lines"].update(s.get("lines", []))

    # Remap edges. Train edges are directed — dedup on the ordered triple.
    # T (walk) edges are UNDIRECTED — dedup on the unordered pair, otherwise
    # opposite-orientation transfer rows between the same two complexes both
    # survive and buildGraphFromData then adds a reverse for each, leaving
    # duplicate walk edges (with differing min_transfer_time) in the graph.
    seen, new_edges = set(), []
    for e in edges:
        nf = canonical.get(e["from"], e["from"])
        nt = canonical.get(e["to"],   e["to"])
        if nf == nt:
            continue
        if e["line"] == "T":
            key = ("T", min(nf, nt), max(nf, nt))
        else:
            key = (nf, nt, e["line"])
        if key in seen:
            continue
        seen.add(key)
        new_edges.append({**e, "from": nf, "to": nt})

    before = len(stations)
    after  = len(merged)
    print(f"     Merged {before} → {after} stations "
          f"({before - after} same-name complexes collapsed)")
    return merged, new_edges


# ── Core parser ───────────────────────────────────────────────────────────────

def parse(source, out_path):

    # 1. Stops
    print("1/4  Parsing stops...")
    stops_raw = read_csv(source, "stops.txt")

    stop_index = {r["stop_id"]: r for r in stops_raw}
    stations   = {}
    for row in stops_raw:
        if row.get("location_type", "0").strip() != "1":
            continue
        sid = row["stop_id"]
        lat = float(row["stop_lat"]) if row.get("stop_lat") else 0.0
        lng = float(row["stop_lon"]) if row.get("stop_lon") else 0.0
        stations[sid] = {
            "id":      sid,
            "name":    row["stop_name"].strip(),
            "lat":     lat,
            "lng":     lng,
            "borough": borough_from_coords(lat, lng),
            "lines":   set(),
        }
    print(f"     {len(stations)} parent stations (location_type=1)")

    print("     Building parent map...")
    parent_map = build_parent_map(stops_raw)
    print(f"     {len(parent_map)} stops mapped")

    # 2. Routes (subway only)
    EXCLUDE = {"FX", "6X", "7X", "SIR"}

    print("2/4  Parsing routes...")
    routes_raw   = read_csv(source, "routes.txt")
    route_to_line = {
        r["route_id"]: r.get("route_short_name", r["route_id"]).strip()
        for r in routes_raw
        if r.get("route_type", "").strip() == "1"
        and r.get("route_short_name", r["route_id"]).strip() not in EXCLUDE
    }
    print(f"     {len(route_to_line)} subway routes: {sorted(route_to_line.values())}")

    # 3. Trips (weekday regular service only)
    print("3/4  Parsing trips...")

    # Identify weekday service IDs from calendar.txt
    weekday_services = set()
    try:
        calendar_raw = read_csv(source, "calendar.txt")
        for row in calendar_raw:
            days = [row.get(d, "0").strip() for d in
                    ("monday", "tuesday", "wednesday", "thursday", "friday")]
            if all(d == "1" for d in days):
                weekday_services.add(row["service_id"].strip())
        print(f"     Weekday service IDs: {sorted(weekday_services)}")
    except Exception as ex:
        print(f"     ⚠  Could not read calendar.txt ({ex}) — using all trips")

    trips_raw        = read_csv(source, "trips.txt")
    trip_to_line     = {}
    trip_to_headsign = {}
    for t in trips_raw:
        # Skip non-weekday trips when we have calendar data
        if weekday_services and t.get("service_id", "").strip() not in weekday_services:
            continue
        line = route_to_line.get(t["route_id"])
        if line:
            trip_to_line[t["trip_id"]]     = line
            trip_to_headsign[t["trip_id"]] = t.get("trip_headsign", "").strip()

    # 4. Stop times
    print("4/4  Parsing stop_times (may take a moment)...")
    stop_times_raw = read_csv(source, "stop_times.txt")
    print(f"     {len(stop_times_raw):,} rows")

    by_trip = defaultdict(list)
    for row in stop_times_raw:
        if row["trip_id"] in trip_to_line:
            by_trip[row["trip_id"]].append(row)
    for tid in by_trip:
        by_trip[tid].sort(key=lambda r: int(r["stop_sequence"]))

    # Filter to daytime trips (first departure 6:00–22:00).
    # Overnight runs (1am–5am) use express trains running local, which would
    # add false local-stop edges to express lines like the 2/3/4/5.
    DAY_START = 6 * 3600
    DAY_END   = 22 * 3600
    daytime_trips = {}
    for tid, stops in by_trip.items():
        try:
            first_dep = time_to_sec(stops[0]["departure_time"])
            if DAY_START <= first_dep <= DAY_END:
                daytime_trips[tid] = stops
        except (ValueError, KeyError):
            pass
    print(f"     {len(by_trip):,} relevant trips → {len(daytime_trips):,} daytime trips (6am–10pm)")
    by_trip = daytime_trips

    # Build edges
    print("\n     Building edges...")
    edge_times    = defaultdict(list)
    headsign_hits = defaultdict(lambda: defaultdict(int))
    missing_ids   = set()

    for trip_id, stops in by_trip.items():
        line     = trip_to_line[trip_id]
        headsign = trip_to_headsign.get(trip_id, "")
        for i in range(len(stops) - 1):
            raw_from = stops[i]["stop_id"]
            raw_to   = stops[i + 1]["stop_id"]

            fid = parent_map.get(raw_from, raw_from)
            tid = parent_map.get(raw_to,   raw_to)

            if fid == tid:
                continue

            if fid in stations:
                stations[fid]["lines"].add(line)
            else:
                missing_ids.add(fid)

            if tid in stations:
                stations[tid]["lines"].add(line)
            else:
                missing_ids.add(tid)

            try:
                dep  = time_to_sec(stops[i]["departure_time"])
                arr  = time_to_sec(stops[i + 1]["arrival_time"])
                secs = arr - dep
                if 0 < secs < 600:
                    key = (fid, tid, line)
                    edge_times[key].append(secs)
                    headsign_hits[key][headsign] += 1
            except (ValueError, KeyError, IndexError):
                pass

    if missing_ids:
        print(f"     ⚠  {len(missing_ids)} stop IDs in stop_times had no parent station entry")
        print(f"        (first 5: {list(missing_ids)[:5]})")
        for mid in missing_ids:
            row = stop_index.get(mid, {})
            lat = float(row.get("stop_lat") or 0)
            lng = float(row.get("stop_lon") or 0)
            stations[mid] = {
                "id":      mid,
                "name":    row.get("stop_name", mid).strip(),
                "lat":     lat,
                "lng":     lng,
                "borough": borough_from_coords(lat, lng),
                "lines":   set(),
            }
        for (a, b, line), times in edge_times.items():
            if a in stations: stations[a]["lines"].add(line)
            if b in stations: stations[b]["lines"].add(line)

    # ── Phantom-edge filter ──────────────────────────────────────────────────
    # Some GTFS trips include rare service patterns: weekend GO reroutings,
    # express/skip-stop variants, or deadhead moves. These produce edges that
    # don't reflect normal service (e.g. D-line jumping 145 St → Tremont Av,
    # skipping 5 stations).
    #
    # Strategy: per line, an edge must appear in at least 10% of the count of
    # the line's most-observed edge (with a floor of 3 observations). This
    # cleanly removes phantom express edges while preserving legitimate but
    # less-frequent edges (late-night extensions, etc.).

    max_obs = defaultdict(int)
    for (a, b, line), times in edge_times.items():
        if len(times) > max_obs[line]:
            max_obs[line] = len(times)

    MIN_RATIO = 0.10
    kept, dropped = {}, []
    for key, times in edge_times.items():
        line = key[2]
        threshold = max(3, int(MIN_RATIO * max_obs[line]))
        if len(times) >= threshold:
            kept[key] = times
        else:
            dropped.append((key, len(times), threshold))

    edge_times = kept
    print(f"     Filtered {len(dropped)} phantom edges (rare/express/GO patterns)")
    if dropped:
        dropped.sort(key=lambda x: (x[0][2], x[1]))
        print(f"     First few examples:")
        for (a, b, line), n, thr in dropped[:8]:
            af = stations.get(a, {}).get("name", a)
            bf = stations.get(b, {}).get("name", b)
            print(f"       {line:3s}  {af:28s} → {bf:28s}  ({n} obs, threshold {thr})")

    # Re-derive lines per station from KEPT edges only.
    # Without this, stations still claim lines whose only edges got dropped
    # as phantoms, leaving orphan single-station components in the line graph.
    for s in stations.values():
        s["lines"] = set()
    for (a, b, line) in edge_times:
        if a in stations:
            stations[a]["lines"].add(line)
        if b in stations:
            stations[b]["lines"].add(line)

    # Build final directed edge list
    edges = []
    seen_directed = set()
    for (a, b, line), times in edge_times.items():
        key = (a, b, line)
        if key in seen_directed:
            continue
        seen_directed.add(key)
        hs_map  = headsign_hits[key]
        best_hs = max(hs_map, key=hs_map.get) if hs_map else ""
        edges.append({
            "from":     a,
            "to":       b,
            "line":     line,
            "time":     round(statistics.median(times) / 60, 1),
            "headsign": best_hs,
        })

    # 5. Transfers — connects different line families at the same complex
    print("     Reading transfers.txt...")
    try:
        transfers_raw = read_csv(source, "transfers.txt")
        xfer_added = 0
        seen_xfer = set()
        for t in transfers_raw:
            xtype = t.get("transfer_type", "0").strip()
            if xtype == "3":
                continue
            fid = parent_map.get(t.get("from_stop_id",""), t.get("from_stop_id",""))
            tid = parent_map.get(t.get("to_stop_id",""),   t.get("to_stop_id",""))
            if not fid or not tid or fid == tid:
                continue
            if fid not in stations or tid not in stations:
                continue
            pair = (min(fid,tid), max(fid,tid))
            if pair in seen_xfer:
                continue
            seen_xfer.add(pair)
            try:
                xfer_min = round(int(t.get("min_transfer_time","180")) / 60, 1)
            except ValueError:
                xfer_min = 3.0
            xfer_min = max(1.0, min(xfer_min, 10.0))
            edges.append({"from": fid, "to": tid, "line": "T", "time": xfer_min, "headsign": ""})
            xfer_added += 1
        print(f"     {xfer_added} transfer connections added")
    except Exception as ex:
        print(f"     ⚠  Could not read transfers.txt: {ex}")

    # Merge same-name station complexes
    print("     Merging station complexes (same-name only)...")
    stations, edges = merge_complexes(stations, edges)

    # Finalize
    station_list = sorted(
        [{**s, "lines": sorted(s["lines"])} for s in stations.values() if s["lines"]],
        key=lambda s: s["name"]
    )

    print(f"\n Results:")
    print(f"   {len(station_list)} stations with service")
    print(f"   {len(edges)} edges")
    counts = defaultdict(int)
    for s in station_list:
        counts[s["borough"]] += 1
    for b, c in sorted(counts.items()):
        print(f"   {b}: {c} stations")

    graph = {"stations": station_list, "edges": edges}
    with open(out_path, "w") as f:
        json.dump(graph, f, separators=(",", ":"))

    size_kb = os.path.getsize(out_path) / 1024
    print(f"\n Saved {out_path}  ({size_kb:.0f} KB)")
    print("\n Next: place graph.json next to detournyc.html and serve.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    inp = sys.argv[1] if len(sys.argv) > 1 else "."
    out = sys.argv[2] if len(sys.argv) > 2 else "graph.json"

    print(f"\n DetourNYC GTFS Parser")
    print(f" Input:  {inp}")
    print(f" Output: {out}\n")

    if inp.endswith(".zip") and os.path.isfile(inp):
        with zipfile.ZipFile(inp) as zf:
            parse(zf, out)
    elif os.path.isdir(inp):
        required = ["stops.txt", "routes.txt", "trips.txt", "stop_times.txt"]
        missing  = [f for f in required if not os.path.isfile(os.path.join(inp, f))]
        if missing:
            print(f"ERROR: Missing files in '{inp}': {missing}")
            sys.exit(1)
        parse(inp, out)
    else:
        print(f"ERROR: '{inp}' is neither a .zip file nor a folder with GTFS files.")
        sys.exit(1)


if __name__ == "__main__":
    main()
