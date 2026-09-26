// Decodes the SysEx messages written by cpu_stats_test.cpp (the firmware's encoder) with the decoder of
// tools/cpu_monitor.html, taken from the page itself, and compares every field.
// Usage: node decode_test.js cases.json [path/to/cpu_monitor.html]
"use strict";
const fs = require("fs");
const path = require("path");

const casesPath = process.argv[2];
const pagePath = process.argv[3] || path.join(__dirname, "..", "..", "tools", "cpu_monitor.html");
const page = fs.readFileSync(pagePath, "utf8");

// The whole script of the page must at least compile
const script = page.match(/<script>([\s\S]*?)<\/script>/)[1];
new Function(script); // throws on a syntax error

const src = page.match(/\/\/ BEGIN decode[^\n]*\n([\s\S]*?)\/\/ END decode/)[1];
const decodeCpuStats = new Function(src + "\nreturn decodeCpuStats;")();

const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
let failures = 0;
const fail = (msg) => {
	failures++;
	if (failures <= 10) {
		console.log("FAIL " + msg);
	}
};

for (const [i, c] of cases.entries()) {
	const bytes = Uint8Array.from(c.bytes);
	const d = decodeCpuStats(bytes);
	if (!d) {
		fail("case " + i + " not decoded");
		continue;
	}
	for (const [key, want] of Object.entries(c.expected)) {
		if (d[key] !== want) {
			fail("case " + i + " " + key + ": " + d[key] + " != " + want);
		}
	}
	if (Object.keys(d).length !== Object.keys(c.expected).length) {
		fail("case " + i + ": field count");
	}
	// Short header F0 7D, used once a client sent the old developer ID
	const short = Uint8Array.from([0xF0, 0x7D, ...c.bytes.slice(5)]);
	const ds = decodeCpuStats(short);
	if (!ds || JSON.stringify(ds) !== JSON.stringify(d)) {
		fail("case " + i + ": short header");
	}
}

// Things that are not ours, or broken
const good = cases[2].bytes;
const rejects = {
	"other command": good.map((b, i) => (i === 5 ? 0x04 : b)),
	"other manufacturer": good.map((b, i) => (i === 3 ? 0x7C : b)),
	"truncated": good.slice(0, 30).concat([0xF7]),
	"no F7": good.slice(0, good.length - 1),
	"high bit in data": good.map((b, i) => (i === 20 ? 0x80 : b)),
	"version 0": good.map((b, i) => (i === 6 ? 0 : b)),
	"note on": [0x90, 60, 100],
};
for (const [name, bytes] of Object.entries(rejects)) {
	if (decodeCpuStats(Uint8Array.from(bytes)) !== null) {
		fail("accepted " + name);
	}
}
// A later format with more fields still decodes the known ones
const longer = good.slice(0, good.length - 1).concat([1, 2, 3, 0xF7]);
if (!decodeCpuStats(Uint8Array.from(longer))) {
	fail("longer message rejected");
}

if (failures) {
	console.log(failures + " FAILED");
	process.exit(1);
}
console.log("cpu_monitor.html decoder: " + cases.length + " messages ok, rejects ok");
