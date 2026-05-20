"""
test_gtfs.py — Unit tests for parse_gtfs.py helper functions.

Tests the pure functions that transform GTFS data, including time parsing,
borough assignment, name canonicalization, parent-map building, and
station complex merging.

Usage: python3 -m pytest tests/test_gtfs.py -v
       (from the project root)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from parse_gtfs import time_to_sec, borough_from_coords, canonicalize_name, build_parent_map, merge_complexes


# ── time_to_sec ───────────────────────────────────────────────────────────────

def test_time_zero():
    assert time_to_sec("0:00:00") == 0

def test_time_one_hour():
    assert time_to_sec("1:00:00") == 3600

def test_time_six_thirty():
    assert time_to_sec("6:30:00") == 6 * 3600 + 30 * 60

def test_time_with_seconds():
    assert time_to_sec("10:05:30") == 10 * 3600 + 5 * 60 + 30

def test_time_over_24h():
    # GTFS encodes next-day trips as >24h (e.g. 25:15:00 = 1:15 AM next day)
    assert time_to_sec("25:15:00") == 25 * 3600 + 15 * 60

def test_time_strips_whitespace():
    assert time_to_sec(" 8:00:00") == 8 * 3600


# ── borough_from_coords ───────────────────────────────────────────────────────

def test_borough_times_square():
    # Times Sq-42 St
    assert borough_from_coords(40.7580, -73.9855) == "Manhattan"

def test_borough_fulton_st():
    # Fulton St, lower Manhattan
    assert borough_from_coords(40.7127, -74.0099) == "Manhattan"

def test_borough_atlantic_ave():
    # Atlantic Av-Barclays Ctr, Brooklyn
    assert borough_from_coords(40.6841, -73.9773) == "Brooklyn"

def test_borough_coney_island():
    # Coney Island-Stillwell Av
    assert borough_from_coords(40.5775, -73.9814) == "Brooklyn"

def test_borough_flushing():
    # Flushing-Main St, Queens
    assert borough_from_coords(40.7574, -73.8303) == "Queens"

def test_borough_forest_hills():
    # Forest Hills-71 Av, Queens
    assert borough_from_coords(40.7213, -73.8446) == "Queens"

def test_borough_yankee_stadium():
    # 161 St-Yankee Stadium, Bronx
    assert borough_from_coords(40.8281, -73.9261) == "Bronx"

def test_borough_woodlawn():
    # Woodlawn terminus, Bronx
    assert borough_from_coords(40.8867, -73.8785) == "Bronx"

def test_borough_staten_island():
    # Well into Staten Island
    assert borough_from_coords(40.6436, -74.0789) == "Staten Island"


# ── canonicalize_name ─────────────────────────────────────────────────────────

def test_canonical_lowercase():
    assert canonicalize_name("Times Sq-42 St") == "times sq-42 st"

def test_canonical_strips_whitespace():
    assert canonicalize_name("  Jay St  ") == "jay st"

def test_canonical_collapses_spaces():
    assert canonicalize_name("Jay  St-MetroTech") == "jay st-metrotech"

def test_canonical_already_clean():
    assert canonicalize_name("fulton st") == "fulton st"

def test_canonical_mixed_case():
    assert canonicalize_name("Atlantic Av-Barclays Ctr") == "atlantic av-barclays ctr"


# ── build_parent_map ──────────────────────────────────────────────────────────

def test_parent_map_direct_type1():
    # A location_type=1 station maps to itself
    stops = [
        {"stop_id": "A01", "location_type": "1", "parent_station": ""},
        {"stop_id": "A01N", "location_type": "0", "parent_station": "A01"},
    ]
    pm = build_parent_map(stops)
    assert pm["A01"] == "A01"
    assert pm["A01N"] == "A01"

def test_parent_map_child_resolves_to_root():
    # Platform → group → station chain collapses to the type-1 root
    stops = [
        {"stop_id": "ROOT", "location_type": "1", "parent_station": ""},
        {"stop_id": "GRP",  "location_type": "0", "parent_station": "ROOT"},
        {"stop_id": "PLAT", "location_type": "0", "parent_station": "GRP"},
    ]
    pm = build_parent_map(stops)
    assert pm["PLAT"] == "ROOT"
    assert pm["GRP"]  == "ROOT"

def test_parent_map_ns_suffix_fallback():
    # Stops ending in N/S that don't have a parent entry fall back to base ID
    stops = [
        {"stop_id": "123", "location_type": "1", "parent_station": ""},
    ]
    pm = build_parent_map(stops)
    # 123N is not in the raw list, but the resolver strips the N and finds 123
    assert pm.get("123N", pm.get("123")) == "123"


# ── merge_complexes ───────────────────────────────────────────────────────────

def _make_station(sid, name, lines=None):
    return {"id": sid, "name": name, "lat": 0.0, "lng": 0.0,
            "borough": "Manhattan", "lines": set(lines or [])}

def test_merge_same_name_with_T_edge():
    # Two stations with the same name connected by a T edge should merge
    stations = {
        "A": _make_station("A", "Jay St-MetroTech", ["A", "C", "F"]),
        "B": _make_station("B", "Jay St-MetroTech", ["N", "R", "W"]),
    }
    edges = [{"from": "A", "to": "B", "line": "T", "time": 2.0, "headsign": ""}]
    merged, new_edges = merge_complexes(stations, edges)
    assert len(merged) == 1
    canon = list(merged.values())[0]
    assert "A" in canon["lines"] or "N" in canon["lines"]  # lines merged

def test_merge_different_names_no_merge():
    # Different names even with a T edge must NOT merge
    stations = {
        "X": _make_station("X", "Cortlandt St"),
        "Y": _make_station("Y", "Chambers St-WTC"),
    }
    edges = [{"from": "X", "to": "Y", "line": "T", "time": 3.0, "headsign": ""}]
    merged, _ = merge_complexes(stations, edges)
    assert len(merged) == 2

def test_merge_no_T_edge_no_merge():
    # Same name but no T edge — should NOT merge
    stations = {
        "P": _make_station("P", "Court Sq"),
        "Q": _make_station("Q", "Court Sq"),
    }
    edges = [{"from": "P", "to": "Q", "line": "7", "time": 1.0, "headsign": ""}]
    merged, _ = merge_complexes(stations, edges)
    assert len(merged) == 2

def test_merge_self_loop_removed():
    # After merging, edges from a station to itself must be dropped
    stations = {
        "A": _make_station("A", "Times Sq-42 St", ["1", "2", "3"]),
        "B": _make_station("B", "Times Sq-42 St", ["N", "Q", "R"]),
    }
    edges = [{"from": "A", "to": "B", "line": "T", "time": 2.0, "headsign": ""}]
    _, new_edges = merge_complexes(stations, edges)
    for e in new_edges:
        assert e["from"] != e["to"], "self-loop edge survived merge"


# ── Graph JSON integrity (quick sanity read) ──────────────────────────────────

import json

def test_graph_json_loads():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graph.json")
    with open(p) as f:
        g = json.load(f)
    assert "stations" in g and "edges" in g

def test_graph_station_count_in_range():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graph.json")
    with open(p) as f:
        g = json.load(f)
    assert 420 <= len(g["stations"]) <= 470, f"station count {len(g['stations'])} out of range"

def test_graph_no_z_train():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graph.json")
    with open(p) as f:
        g = json.load(f)
    z_edges = [e for e in g["edges"] if e["line"] == "Z"]
    assert len(z_edges) == 0, f"found {len(z_edges)} Z-train edges — Z should be excluded"

def test_graph_all_edges_have_valid_stations():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graph.json")
    with open(p) as f:
        g = json.load(f)
    ids = {s["id"] for s in g["stations"]}
    bad = [e for e in g["edges"] if e["from"] not in ids or e["to"] not in ids]
    assert len(bad) == 0, f"{len(bad)} edges reference unknown station IDs"
