# DETOUR NYC

**Subway routes for people not in a hurry.**

Instead of finding the fastest route, DETOUR NYC finds the most roundabout, scenic, and chaotic path between any two stations. Built for exploration, not efficiency.

🌐 **Live at [detournyc.com](https://detournyc.com)**

---

## Modes

| Mode | What it does |
|------|-------------|
| 🐢 The Long Way | Maximum travel time — the most roundabout route possible |
| 🔀 Transfer Frenzy | Most line changes — zigzag across the entire system |
| 📍 Every Stop | Maximum stations visited — the grand borough-hopping tour |
| 🎲 Detour Roulette | A random unexpected detour before your destination |

---

## How it works

The app uses a weighted graph of the NYC subway system built from MTA GTFS data. Instead of running Dijkstra to minimize time, it runs variations that maximize time, transfers, or stops — subject to a 3-hour cap so routes stay absurd but not impossible.

### Data pipeline

1. Download the [MTA GTFS Static Feed](https://new.mta.info/developers)
2. Run `parse_gtfs.py` to build `graph.json`:
   ```bash
   python3 parse_gtfs.py
   ```
3. `graph.json` contains all stations and directed edges with travel times and headsigns

The parser filters to **weekday daytime trips only** (6am–10pm). This ensures express lines like the 2/3/4/5 only show their actual express stops — overnight service runs these trains local, which would otherwise inject false local-stop edges into the graph.

### Stack

- Vanilla HTML/CSS/JS — no framework, no build step
- `graph.json` served as a static file
- Deployed on [Vercel](https://vercel.com)

---

## Local development

```bash
# Serve locally (any static server works)
npx serve .
# or
python3 -m http.server 8080
```

Then open `http://localhost:8080`.

> **Note:** `graph.json` must be present. Run `parse_gtfs.py` first if it's missing.

---

## Data

Static schedule data from the [MTA GTFS feeds](https://new.mta.info/developers). Not affiliated with the MTA. For entertainment only — please don't actually take these routes to work.
