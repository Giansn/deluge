// Decodes the SysEx messages written by profiler_test.cpp (the firmware's encoders) with the decoder of
// tools/profiler.html, taken from the page itself, and compares every field.
// Usage: node decode_test.js cases.json [path/to/profiler.html]
"use strict";
const fs = require("fs");
const path = require("path");

const casesPath = process.argv[2];
const pagePath = process.argv[3] || path.join(__dirname, "..", "..", "tools", "profiler.html");
const page = fs.readFileSync(pagePath, "utf8");

// The whole script of the page must at least compile
const script = page.match(/<script>([\s\S]*?)<\/script>/)[1];
new Function(script); // throws on a syntax error

const src = page.match(/\/\/ BEGIN decode[^\n]*\n([\s\S]*?)\/\/ END decode/)[1];
const decodeProfiler = new Function(src + "\nreturn decodeProfiler;")();

const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));
let failures = 0, checks = 0;
const fail = (msg) => {
	failures++;
	if (failures <= 10) {
		console.log("FAIL " + msg);
	}
};
const bytes = (hex) => Uint8Array.from(hex.match(/../g).map((h) => parseInt(h, 16)));

for (const c of cases.samples) {
	checks++;
	const d = decodeProfiler(bytes(c.hex));
	if (!d || d.kind !== "samples") {
		fail("samples not decoded: " + c.hex.slice(0, 40));
		continue;
	}
	const got = d.samples.map((s) => [s.address, s.task, s.audio ? 1 : 0, s.otherMode ? 1 : 0, s.output, s.weight]);
	if (d.seq !== c.seq || d.dropped !== c.dropped || JSON.stringify(got) !== JSON.stringify(c.samples)) {
		fail("samples: " + JSON.stringify(d).slice(0, 200));
	}
}
for (const c of cases.names) {
	checks++;
	const d = decodeProfiler(bytes(c.hex));
	if (!d || d.kind !== "names" || d.which !== c.which || JSON.stringify(d.names) !== JSON.stringify(c.names)) {
		fail("names: " + JSON.stringify(d) + " != " + JSON.stringify(c.names));
	}
}
for (const c of cases.output_times) {
	checks++;
	const d = decodeProfiler(bytes(c.hex));
	if (!d || d.kind !== "outputTimes" || d.number !== c.number || d.window !== c.window
		|| JSON.stringify(d.ticks) !== JSON.stringify(c.ticks)) {
		fail("output times: " + JSON.stringify(d).slice(0, 200));
	}
}
checks++;
if (decodeProfiler(Uint8Array.from([0xF0, 0x7E, 0x00, 0x06, 0x01, 0xF7])) !== null) {
	fail("another SysEx isn't the profiler's");
}
console.log("profiler.html decoder: " + checks + " checks, " + failures + " failed");
process.exit(failures ? 1 : 0);
