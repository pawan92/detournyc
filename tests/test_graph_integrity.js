#!/usr/bin/env node
/**
 * test_graph_integrity.js — Validates graph.json structure and data rules.
 *
 * Checks that the graph produced by parse_gtfs.py meets every invariant the
 * app relies on: no excluded lines, no orphaned stations, sane edge times,
 * correct borough assignments, and express-train isolation.
 *
 * Usage: node tests/test_graph_integrity.js
 */
'use strict';
const fs   = require('fs');
const path = require('path');

const GRAPH_PATH = path.join(__dirname, '..', 'graph.json');
let graph;
try {
  graph = JSON.parse(fs.readFileSync(GRAPH_PATH, 'utf8'));
} catch (e) {
  console.error(`ERROR: cannot read graph.json — ${e.message}`);
  process.exit(1);
}

let passed = 0, failed = 0;
const failures = [];

function ok(label, cond, detail) {
  if (cond) { passed++; console.log(`  ✓ ${label}`); }
  else       { failed++; failures.push(`${label}: ${detail}`); console.log(`  ✗ ${label}: ${detail}`); }
}

const { stations, edges } = graph;
const stationMap  = new Map(stations.map(s => [s.id, s]));
const byName      = new Map(stations.map(s => [s.name, s]));

console.log('═'.repeat(60));
console.log('Graph Integrity Tests');
console.log(`graph.json: ${stations.length} stations, ${edges.length} edges`);
console.log('═'.repeat(60));

// ── Structure ────────────────────────────────────────────────────────────────
console.log('\nStructure');
ok('has stations array',  Array.isArray(stations),   'stations missing');
ok('has edges array',     Array.isArray(edges),       'edges missing');
ok('station count 420–470', stations.length >= 420 && stations.length <= 470, `got ${stations.length}`);
ok('edge count 1200–1700',  edges.length >= 1200 && edges.length <= 1700,     `got ${edges.length}`);

// ── No excluded lines ────────────────────────────────────────────────────────
console.log('\nExcluded lines');
for (const line of ['Z', '6X', '7X', 'FX', 'SIR']) {
  const bad = edges.filter(e => e.line === line);
  ok(`no ${line} edges`, bad.length === 0, `found ${bad.length}`);
}

// ── Edge validity ────────────────────────────────────────────────────────────
console.log('\nEdge validity');
let missingFrom = 0, missingTo = 0;
for (const e of edges) {
  if (!stationMap.has(e.from)) missingFrom++;
  if (!stationMap.has(e.to))   missingTo++;
}
ok('all edge.from exist',    missingFrom === 0, `${missingFrom} edges reference unknown origin`);
ok('all edge.to exist',      missingTo === 0,   `${missingTo} edges reference unknown dest`);

const badTimes = edges.filter(e => e.line !== 'T' && (e.time <= 0 || e.time > 30));
ok('train edge times 0–30 min', badTimes.length === 0,
  badTimes.slice(0,3).map(e=>`${e.from}→${e.to} ${e.line} ${e.time}min`).join(', '));

const badXferTimes = edges.filter(e => e.line === 'T' && (e.time <= 0 || e.time > 10));
ok('transfer times 0–10 min', badXferTimes.length === 0,
  `${badXferTimes.length} out-of-range transfer times`);

const noLine = edges.filter(e => !e.line);
ok('all edges have line',    noLine.length === 0,  `${noLine.length} edges missing line`);

// ── Station validity ─────────────────────────────────────────────────────────
console.log('\nStation validity');
const dupIds = ids => ids.filter((v,i,a) => a.indexOf(v) !== i);
const dups = dupIds(stations.map(s => s.id));
ok('no duplicate station IDs', dups.length === 0, `duplicates: ${dups.join(', ')}`);

const noLines  = stations.filter(s => !s.lines || s.lines.length === 0);
ok('all stations have ≥1 line', noLines.length === 0, `${noLines.length} stations without lines`);

const noBoro   = stations.filter(s => !s.borough);
ok('all stations have borough', noBoro.length === 0, `${noBoro.length} stations without borough`);

const noCoords = stations.filter(s => !s.lat || !s.lng || s.lat === 0 || s.lng === 0);
ok('all stations have coordinates', noCoords.length === 0, `${noCoords.length} stations without coordinates`);

// ── Borough counts ────────────────────────────────────────────────────────────
console.log('\nBorough coverage');
const boroCounts = {};
for (const s of stations) boroCounts[s.borough] = (boroCounts[s.borough] || 0) + 1;
ok('Manhattan stations 100–160', boroCounts.Manhattan >= 100 && boroCounts.Manhattan <= 160, `got ${boroCounts.Manhattan}`);
ok('Brooklyn stations 130–200',  boroCounts.Brooklyn  >= 130 && boroCounts.Brooklyn  <= 200, `got ${boroCounts.Brooklyn}`);
ok('Queens stations 60–110',     boroCounts.Queens    >= 60  && boroCounts.Queens    <= 110,  `got ${boroCounts.Queens}`);
ok('Bronx stations 50–90',       boroCounts.Bronx     >= 50  && boroCounts.Bronx     <= 90,   `got ${boroCounts.Bronx}`);

// ── Express-train isolation ───────────────────────────────────────────────────
// 2/3 are 7th Ave express — they must not have edges through 1-train-only local stops
console.log('\nExpress-train isolation');
const LOCAL_ONLY_7AV = ['28 St', '23 St', '18 St'];   // served only by 1, not 2/3
for (const stopName of LOCAL_ONLY_7AV) {
  const s = byName.get(stopName);
  if (!s) { console.log(`  ⚠ ${stopName} not found in graph (skipped)`); continue; }
  ok(`2/3 not listed at ${stopName}`,
    !s.lines.includes('2') && !s.lines.includes('3'),
    `lines: ${s.lines.join(',')}`);
  const express2edges = edges.filter(e => e.line === '2' && (e.from === s.id || e.to === s.id));
  ok(`no 2-train edges through ${stopName}`,
    express2edges.length === 0,
    `found ${express2edges.length} edges`);
}

// 4/5 are Lex Ave express — must not have edges through 6-only local stops
const LOCAL_ONLY_LEX = ['28 St', '23 St'];  // Lex Ave 6-only stops
for (const stopName of LOCAL_ONLY_LEX) {
  // There are two "28 St" / "23 St" stations (Lex vs 7th Ave) — check both lines
  const matches = stations.filter(s => s.name === stopName);
  for (const s of matches) {
    const has4 = s.lines.includes('4'), has5 = s.lines.includes('5');
    // Only flag if the borough is Manhattan (Lex Ave corridor)
    if (s.borough === 'Manhattan') {
      ok(`4/5 not at Lex-Ave ${stopName} (${s.id})`,
        !has4 && !has5,
        `lines: ${s.lines.join(',')}`);
    }
  }
}

// ── Known landmark stations exist ─────────────────────────────────────────────
console.log('\nLandmark stations present');
const landmarks = [
  'Times Sq-42 St', 'Grand Central-42 St', 'Atlantic Av-Barclays Ctr',
  'Coney Island-Stillwell Av', 'Flushing-Main St', 'Wakefield-241 St',
  'South Ferry', 'Far Rockaway-Mott Av', 'Jamaica-179 St',
];
for (const name of landmarks) {
  ok(`${name} in graph`, byName.has(name), 'not found');
}

// ── Every station is reachable (has at least one non-T edge) ─────────────────
console.log('\nConnectivity');
const edgeSet = new Set();
for (const e of edges) {
  if (e.line !== 'T') { edgeSet.add(e.from); edgeSet.add(e.to); }
}
const isolated = stations.filter(s => !edgeSet.has(s.id));
ok('no isolated stations (all have train edges)', isolated.length === 0,
  isolated.map(s => s.name).join(', '));

// ── Results ──────────────────────────────────────────────────────────────────
console.log('\n' + '═'.repeat(60));
console.log(`GRAPH INTEGRITY: ${passed} passed, ${failed} failed`);
if (failures.length) {
  console.log('\nFAILURES:');
  failures.forEach((f, i) => console.log(`  ${i+1}. ${f}`));
  process.exitCode = 1;
} else {
  console.log('✅ All graph integrity checks passed.');
}
