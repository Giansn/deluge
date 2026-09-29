// Host test of the Scan view's analysis (mastertune-v18.4, dsp/scan): the pitch, tempo and key of the audio input, fed as
// the audio routine feeds it (stereo, 32 bits, 128 frames at a time) and analysed as the UI does (every 15 ms).
//
// 1. Pitch of harmonic tones, B0 to C7, at A = 440 and 432 Hz: clean (to a hundredth of a cent), with vibrato, with
//    noise; nothing for silence, noise and drum hits.
// 2. Pitch of the card's multisamples (double bass with vibrato, oboe staccato): the note of the file name (C3 = 60),
//    within 7 cents: the median of what the display showed while the note sounded. The recordings themselves are a
//    few cents off: the oboe's F5 is 6.2 cents flat in its loudest 0.25 s by a zero-padded FFT's peak.
// 3. Tempo of loops made from the card's kick, clap and hi-hat (house, breakbeat, drum and bass, hip-hop, swing, a
//    kick alone): within 1 BPM, or the double or half where the pattern allows both (reported as such); none for
//    silence and a held tone.
// 4. Key of I-vi-IV-V (major) and i-VI-iv-V (minor, with the major V) in all 24 keys, oboe triads over double bass
//    roots played from the card's multisamples as a sampler plays them, at A = 440 and 432 Hz (as the music analysis
//    tested the computer's methods, device/analysis/music_analysis.py), with the house loop of 3. as loud mixed in,
//    and analysed every 116 ms only (a UI held up: pitch and key take turns); none for silence, noise, drums and a
//    held note, and none once 12 s of drums followed. In the emulator a few of the keys only (the rest takes hours
//    there).
// 5. Cost: instructions per call in the emulator (ARM=1), time on the PC.
//
// Usage: scan_test <card's SAMPLES folder>
#include "dsp/scan/scan.h"
#include "emu_count.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

using deluge::dsp::Scan;

static int failures = 0;
#define CHECK(cond, ...)                                                                                               \
	do {                                                                                                               \
		if (!(cond)) {                                                                                                 \
			printf("FAIL: " __VA_ARGS__);                                                                              \
			printf("\n");                                                                                              \
			failures++;                                                                                                \
		}                                                                                                              \
	} while (0)

static std::string samples;
static uint32_t seed = 12345;
static double random01() { // A small LCG: the same numbers on the PC and in the emulator
	seed = seed * 1664525u + 1013904223u;
	return (seed >> 8) / 16777216.0;
}

/// A WAV file (PCM 16 or 24 bit, float 32) as mono at its own rate
static std::vector<float> readWav(const std::string& path, int32_t* rate = nullptr) {
	std::vector<float> out;
	FILE* f = fopen(path.c_str(), "rb");
	if (!f) {
		printf("can't open %s\n", path.c_str());
		return out;
	}
	std::vector<uint8_t> d;
	uint8_t buf[65536];
	size_t n;
	while ((n = fread(buf, 1, sizeof(buf), f)) > 0) {
		d.insert(d.end(), buf, buf + n);
	}
	fclose(f);
	auto u16 = [&](size_t p) { return (uint32_t)(d[p] | d[p + 1] << 8); };
	auto u32 = [&](size_t p) { return u16(p) | u16(p + 2) << 16; };
	int32_t channels = 0, bits = 0, format = 0;
	for (size_t p = 12; p + 8 <= d.size();) {
		uint32_t size = u32(p + 4);
		if (!memcmp(&d[p], "fmt ", 4)) {
			format = u16(p + 8);
			channels = u16(p + 10);
			if (rate) {
				*rate = (int32_t)u32(p + 12);
			}
			bits = u16(p + 22);
		}
		else if (!memcmp(&d[p], "data", 4) && channels) {
			int32_t width = bits / 8;
			size_t frames = std::min<size_t>(size, d.size() - p - 8) / (width * channels);
			out.resize(frames);
			for (size_t i = 0; i < frames; i++) {
				float sum = 0;
				for (int32_t c = 0; c < channels; c++) {
					const uint8_t* s = &d[p + 8 + (i * channels + c) * width];
					float v;
					if (format == 3 && bits == 32) {
						memcpy(&v, s, 4);
					}
					else if (bits == 16) {
						v = (int16_t)(s[0] | s[1] << 8) / 32768.0f;
					}
					else {
						v = (float)((int32_t)((uint32_t)(s[0] << 8 | s[1] << 16 | s[2] << 24)) >> 8) / 8388608.0f;
					}
					sum += v;
				}
				out[i] = sum / (float)channels;
			}
			break;
		}
		p += 8 + size + (size & 1);
	}
	return out;
}

/// Feeds a mono signal at 44.1 kHz as both channels, 128 frames at a time (as the audio routine), analysing every
/// 5 blocks (15 ms, as the UI's timer) or as many as given
static std::vector<float> shown; ///< The pitches the display showed while feeding, one per UI call with a pitch

static float shownMedian() {
	if (shown.empty()) {
		return 0;
	}
	std::vector<float> s = shown;
	std::nth_element(s.begin(), s.begin() + s.size() / 2, s.end());
	return s[s.size() / 2];
}

static void feed(Scan& scan, const std::vector<float>& x, bool count = false, int32_t every = 5) {
	shown.clear();
	int32_t block[256];
	size_t blocks = 0;
	for (size_t i = 0; i < x.size(); i += 128) {
		int32_t n = (int32_t)std::min<size_t>(128, x.size() - i);
		for (int32_t j = 0; j < n; j++) {
			float v = std::clamp(x[i + j], -1.0f, 0.9999999f);
			block[2 * j] = block[2 * j + 1] = (int32_t)lrintf(v * 2147483648.0f);
		}
		if (count) {
			EMU_COUNT_BEGIN("feed 128 frames");
		}
		scan.feed(block, n, Scan::Mix::STEREO);
		if (count) {
			EMU_COUNT_END();
		}
		if (++blocks % every == 0) {
			if (count) {
				EMU_COUNT_BEGIN("analyse every 15 ms");
			}
			scan.analyse();
			if (count) {
				EMU_COUNT_END();
			}
			if (scan.pitchHz() > 0) {
				shown.push_back(scan.pitchHz());
			}
		}
	}
	scan.analyse();
}

/// A note: harmonics at 1/k, a vibrato of that many cents at 5 Hz, noise that many dB below the tone
static std::vector<float> tone(double freq, double seconds, double vibratoCents = 0, double noiseDb = -300,
                               int harmonics = 6) {
	size_t n = (size_t)(seconds * 44100);
	std::vector<float> x(n);
	double phase = 0, peak = 0;
	std::vector<double> y(n);
	for (size_t i = 0; i < n; i++) {
		double t = i / 44100.0;
		phase += 2 * M_PI * freq * pow(2, vibratoCents * sin(2 * M_PI * 5 * t) / 1200) / 44100;
		double v = 0;
		for (int k = 1; k <= harmonics; k++) {
			if (k * freq < 20000) {
				v += sin(k * phase) / k;
			}
		}
		y[i] = v;
		peak = std::max(peak, fabs(v));
	}
	double noise = pow(10, noiseDb / 20) * 0.35 / sqrt(1.0 / 3); // Uniform noise that many dB under the tone's RMS
	for (size_t i = 0; i < n; i++) {
		x[i] = (float)(0.5 * y[i] / peak + noise * (2 * random01() - 1));
	}
	return x;
}

static double cents(double f, double ref) {
	return 1200 * log2(f / ref);
}

static const char* const kNames[] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
static const std::vector<const char*> kBass = {
    "MultiSample/Kontrabass/BKCtbss_SusVib_A#0_v1_rr1.wav", "MultiSample/Kontrabass/BKCtbss_SusVib_A1_v1_rr1.wav",
    "MultiSample/Kontrabass/BKCtbss_SusVib_B2_v1_rr1.wav",  "MultiSample/Kontrabass/BKCtbss_SusVib_C#2_v1_rr1.wav",
    "MultiSample/Kontrabass/BKCtbss_SusVib_C1_v1_rr1.wav",  "MultiSample/Kontrabass/BKCtbss_SusVib_D1_v1_rr1.wav",
    "MultiSample/Kontrabass/BKCtbss_SusVib_E1_v1_rr1.wav",  "MultiSample/Kontrabass/BKCtbss_SusVib_E2_v1_rr1.wav",
    "MultiSample/Kontrabass/BKCtbss_SusVib_F#0_v1_rr1.wav", "MultiSample/Kontrabass/BKCtbss_SusVib_F#1_v1_rr1.wav",
    "MultiSample/Kontrabass/BKCtbss_SusVib_G#1_v1_rr1.wav", "MultiSample/Kontrabass/BKCtbss_SusVib_G#2_v1_rr1.wav",
    "MultiSample/Kontrabass/BKCtbss_SusVib_G0_v1_rr1.wav"};
static const std::vector<const char*> kOboe = {"MultiSample/Oboe Stacc 1-2/1-2/Oboe_Stacc_A#2_v1_rr2_Main.wav",
                                               "MultiSample/Oboe Stacc 1-2/1-2/Oboe_Stacc_A#3_v1_rr2_Main.wav",
                                               "MultiSample/Oboe Stacc 1-2/1-2/Oboe_Stacc_D3_v1_rr2_Main.wav",
                                               "MultiSample/Oboe Stacc 1-2/1-2/Oboe_Stacc_D4_v1_rr2_Main.wav",
                                               "MultiSample/Oboe Stacc 1-2/1-2/Oboe_Stacc_D5_v1_rr2_Main.wav",
                                               "MultiSample/Oboe Stacc 1-2/1-2/Oboe_Stacc_F3_v1_rr2_Main.wav",
                                               "MultiSample/Oboe Stacc 1-2/1-2/Oboe_Stacc_F5_v1_rr2_Main.wav"};

/// The note a multisample's file name gives after its second underscore ("BKCtbss_SusVib_A#0", "Oboe_Stacc_A#2"),
/// as MIDI with C3 = 60, as the libraries count
static int32_t namedNote(const std::string& file) {
	size_t us = file.find('_', file.rfind('/') + 1);
	us = file.find('_', us + 1);
	std::string note = file.substr(us + 1, file.find('_', us + 1) - us - 1);
	int32_t pc = 0;
	for (int32_t i = 0; i < 12; i++) {
		if (note.substr(0, note.size() - 1) == kNames[i]) {
			pc = i;
		}
	}
	return 12 * (note.back() - '0' + 2) + pc;
}

static void testTones() {
	struct Case {
		double a4, vibrato, noiseDb, tolerance;
		const char* what;
	};
	const Case cases[] = {{440, 0, -300, 0.25, "clean"},
	                      {432, 0, -300, 0.25, "clean"},
	                      {440, 10, -300, 12, "vibrato +-10 cents"},
	                      {432, 0, -30, 1.0, "noise -30 dB"},
	                      {440, 0, -15, 5.0, "noise -15 dB"}};
	for (const Case& c : cases) {
		int32_t notes = 0, none = 0, wrong = 0;
		double worst = 0;
		for (int32_t midi = 23; midi <= 96; midi += 5) { // B0 (30.9 Hz at 440) to C7 (2093 Hz)
			double f = c.a4 * pow(2, (midi - 69) / 12.0);
			auto scan = std::make_unique<Scan>();
			feed(*scan, tone(f, 1.0, c.vibrato, c.noiseDb));
			notes++;
			if (scan->pitchHz() <= 0) {
				none++;
				printf("  %.2f Hz: nothing\n", f);
				continue;
			}
			double off = cents(scan->pitchHz(), f);
			if (fabs(off) > 50) {
				wrong++;
				printf("  %.2f Hz read as %.2f Hz\n", f, scan->pitchHz());
				continue;
			}
			worst = std::max(worst, fabs(off));
		}
		printf("tones at A = %.0f Hz, %s: %d notes, %d without pitch, %d wrong, the worst %.4f cents off\n", c.a4,
		       c.what, notes, none, wrong, worst);
		CHECK(none == 0 && wrong == 0 && worst <= c.tolerance, "tones %s at %.0f Hz", c.what, c.a4);
	}

	// No pitch: silence, noise, a drum hit
	auto scan = std::make_unique<Scan>();
	feed(*scan, std::vector<float>(44100, 0.0f));
	CHECK(scan->pitchHz() == 0 && scan->levelDb() < -100, "silence: %.2f Hz, %.1f dB", scan->pitchHz(),
	      scan->levelDb());
	std::vector<float> noise(44100);
	for (float& v : noise) {
		v = (float)(0.3 * (2 * random01() - 1));
	}
	scan = std::make_unique<Scan>();
	feed(*scan, noise);
	CHECK(scan->pitchHz() == 0, "noise read as %.2f Hz", scan->pitchHz());
	printf("silence: %.1f dB, no pitch; white noise: %.1f dB, %s\n", -120.0, scan->levelDb(),
	       scan->pitchHz() == 0 ? "no pitch" : "a pitch");
}

static void testMultisamples() {
	std::vector<const char*> files = kBass;
	files.insert(files.end(), kOboe.begin(), kOboe.end());
	int32_t right = 0, total = 0;
	double worst = 0;
	for (const char* file : files) {
		std::string name = file;
		size_t us = name.find('_', name.find('_', name.rfind('/') + 1) + 1);
		std::string note = name.substr(us + 1, name.find('_', us + 1) - us - 1);
		int32_t midi = namedNote(name);
		int32_t pc = midi % 12;
		int32_t rate = 0;
		std::vector<float> x = readWav(samples + "/" + file, &rate);
		if (x.empty() || rate != 44100) {
			CHECK(false, "%s: not read (%d Hz)", file, rate);
			continue;
		}
		x.resize(std::min<size_t>(x.size(), 44100 * 3 / 2)); // The first 1.5 s: the oboe's notes are shorter
		auto scan = std::make_unique<Scan>();
		feed(*scan, x);
		total++;
		double f = shownMedian(); // What the display showed while the note sounded
		double exact = 440 * pow(2, (midi - 69) / 12.0);
		double off = f > 0 ? cents(f, exact) : 999;
		printf("  %-22s %8.2f Hz, %+7.2f cents from %s%d\n", note.c_str(), f, off, kNames[pc], midi / 12 - 1);
		if (fabs(off) <= 7) {
			right++;
			worst = std::max(worst, fabs(off));
		}
	}
	printf("multisamples: %d of %d on their note, the worst %.2f cents off\n", right, total, worst);
	CHECK(right == total, "multisamples: %d of %d on their note", right, total);
}

/// A loop from the card's hits: 16ths per bar for kick, clap and hi-hat, the odd 16ths late by swing, 4 ms of human
/// timing, seconds long
static std::vector<float> loop(double bpm, const std::vector<int32_t>& kick, const std::vector<int32_t>& clap,
                               const std::vector<int32_t>& hat, double swing, double seconds) {
	static std::vector<float> k, c, h;
	if (k.empty()) {
		k = readWav(samples + "/Kick/DUME1_Kick01.wav");
		c = readWav(samples + "/rattle/LP24_OrgPerc_Clap_10.wav");
		h = readWav(samples + "/Hat/KOD_Render_Closed_Hat.wav");
		k.resize(std::min<size_t>(k.size(), 17640));
		c.resize(std::min<size_t>(c.size(), 13230));
		h.resize(std::min<size_t>(h.size(), 5292));
	}
	std::vector<float> x((size_t)(seconds * 44100), 0.0f);
	double sixteenth = 60.0 / bpm / 4;
	auto put = [&](const std::vector<float>& s, double t, double gain) {
		int64_t at = (int64_t)((t + 0.004 * (2 * random01() - 1)) * 44100);
		for (size_t i = 0; i < s.size() && at + (int64_t)i < (int64_t)x.size(); i++) {
			if (at + (int64_t)i >= 0) {
				x[at + i] += (float)(gain * s[i]);
			}
		}
	};
	for (int32_t step = 0; step * sixteenth < seconds; step++) {
		int32_t s = step % 16;
		double t = 0.25 + step * sixteenth + ((s & 1) ? swing * sixteenth : 0);
		if (std::find(kick.begin(), kick.end(), s) != kick.end()) {
			put(k, t, 0.9);
		}
		if (std::find(clap.begin(), clap.end(), s) != clap.end()) {
			put(c, t, 0.6);
		}
		if (std::find(hat.begin(), hat.end(), s) != hat.end()) {
			put(h, t, 0.25 * (0.6 + 0.4 * random01()));
		}
	}
	float peak = 0;
	for (float v : x) {
		peak = std::max(peak, fabsf(v));
	}
	for (float& v : x) {
		v *= 0.8f / peak;
	}
	return x;
}

static void testTempo() {
	struct Case {
		const char* name;
		double bpm;
		std::vector<int32_t> kick, clap, hat;
		double swing;
		bool octaveOk; ///< The double or the half counts too (a pattern that has both readings)
	};
	std::vector<int32_t> eighths = {0, 2, 4, 6, 8, 10, 12, 14}, sixteenths;
	for (int32_t i = 0; i < 16; i++) {
		sixteenths.push_back(i);
	}
	const Case cases[] = {
	    {"house", 120, {0, 4, 8, 12}, {4, 12}, {2, 6, 10, 14}, 0, false},
	    {"house", 128, {0, 4, 8, 12}, {4, 12}, {2, 6, 10, 14}, 0, false},
	    {"house", 133, {0, 4, 8, 12}, {4, 12}, {2, 6, 10, 14}, 0, false},
	    {"breakbeat", 100, {0, 7, 10}, {4, 12}, sixteenths, 0, true},
	    {"breakbeat", 140, {0, 7, 10}, {4, 12}, sixteenths, 0, false},
	    {"drum and bass", 174, {0, 10}, {4, 12}, eighths, 0, true},
	    {"hip-hop", 88, {0, 7, 8}, {4, 12}, eighths, 0, true},
	    {"swing", 96, {0, 10}, {4, 12}, sixteenths, 0.33, true},
	    {"kick alone", 70, {0, 4, 8, 12}, {}, {}, 0, true},
	};
	int32_t exact = 0, octave = 0;
	for (const Case& c : cases) {
		auto scan = std::make_unique<Scan>();
		feed(*scan, loop(c.bpm, c.kick, c.clap, c.hat, c.swing, 12));
		double got = scan->bpm();
		bool isExact = fabs(got - c.bpm) <= 1.0;
		bool isOctave = fabs(got - 2 * c.bpm) <= 2.0 || fabs(got - c.bpm / 2) <= 0.5;
		exact += isExact;
		octave += !isExact && isOctave;
		printf("  %-14s %5.1f BPM: %6.2f%s\n", c.name, c.bpm, got,
		       isExact ? "" : (isOctave ? "  (double or half)" : "  WRONG"));
		CHECK(isExact || (c.octaveOk && isOctave), "tempo %s %.0f read as %.2f", c.name, c.bpm, got);
	}
	printf("tempo: %d of %zu right, %d the double or half\n", exact, sizeof(cases) / sizeof(cases[0]), octave);

	auto scan = std::make_unique<Scan>();
	feed(*scan, std::vector<float>(44100 * 8, 0.0f));
	CHECK(scan->bpm() == 0, "silence read as %.2f BPM", scan->bpm());
	scan = std::make_unique<Scan>();
	feed(*scan, tone(220, 8));
	CHECK(scan->bpm() == 0, "a held tone read as %.2f BPM", scan->bpm());
	printf("silence and a held tone: no tempo\n");
}

/// A multisample as a sampler plays it: the nearest sampled note, its pitch moved by resampling (linear), so many
/// seconds of it ending in a short fade, at a master tune of a4 Hz
class Sampler {
public:
	explicit Sampler(const std::vector<const char*>& files) {
		for (const char* file : files) {
			notes_.push_back({namedNote(file), readWav(samples + "/" + file)});
		}
	}
	[[nodiscard]] std::vector<float> play(int32_t note, double seconds, double a4) const {
		const auto* nearest = &notes_[0];
		for (const auto& n : notes_) {
			if (abs(n.first - note) < abs(nearest->first - note)) {
				nearest = &n;
			}
		}
		const std::vector<float>& x = nearest->second;
		double step = pow(2, (note - nearest->first) / 12.0) * a4 / 440;
		size_t n = std::min<size_t>((size_t)(seconds * 44100), (size_t)((double)(x.size() - 1) / step));
		std::vector<float> y(n);
		for (size_t i = 0; i < n; i++) {
			double at = (double)i * step;
			size_t j = (size_t)at;
			double frac = at - (double)j;
			double fade = std::min(1.0, 30.0 * (double)(n - i) / (double)n); // The last thirtieth
			y[i] = (float)(((1 - frac) * x[j] + frac * x[j + 1]) * fade);
		}
		return y;
	}

private:
	std::vector<std::pair<int32_t, std::vector<float>>> notes_;
};

/// I-vi-IV-V (major) or i-VI-iv-V (minor, with the major V) in a key: 8 bars of 2 s, an oboe triad on each beat
/// (C3 to B3) over the chord's root on the double bass (C1 to B1), as the music analysis renders them
static std::vector<float> progression(const Sampler& oboe, const Sampler& bass, int32_t tonic, bool minor, double a4) {
	struct Chord {
		int32_t root;
		bool minor;
	};
	const Chord majorKey[] = {{0, false}, {9, true}, {5, false}, {7, false}};
	const Chord minorKey[] = {{0, true}, {8, false}, {5, true}, {7, false}};
	constexpr double bar = 2.0;
	std::vector<float> y((size_t)((8 * bar + 1) * 44100), 0.0f);
	auto put = [&](const std::vector<float>& s, double t, float gain) {
		size_t at = (size_t)(t * 44100);
		for (size_t i = 0; i < s.size() && at + i < y.size(); i++) {
			y[at + i] += gain * s[i];
		}
	};
	// Not (minor ? minorKey : majorKey)[i]: GCC 13.3 with -fsanitize=undefined reads a wrong element there
	const Chord* chords = minor ? minorKey : majorKey;
	for (int32_t i = 0; i < 8; i++) {
		const Chord& chord = chords[i % 4];
		put(bass.play(36 + (tonic + chord.root) % 12, bar, a4), i * bar, 0.5f);
		for (int32_t beat = 0; beat < 4; beat++) {
			for (int32_t interval : {0, chord.minor ? 3 : 4, 7}) {
				put(oboe.play(60 + (tonic + chord.root + interval) % 12, bar / 4, a4), i * bar + beat * bar / 4, 0.25f);
			}
		}
	}
	float peak = 0;
	for (float v : y) {
		peak = std::max(peak, fabsf(v));
	}
	for (float& v : y) {
		v *= 0.8f / peak;
	}
	return y;
}

static std::string keyName(int32_t tonic, bool minor) {
	return tonic < 0 ? std::string("none") : std::string(kNames[tonic]) + (minor ? " minor" : " major");
}

static void testKeys() {
	Sampler oboe(kOboe), bass(kBass);
#if defined(__arm__)
	const std::vector<int32_t> tonics = {0, 6, 9}; // In the emulator: a few (the rest takes hours there)
#else
	const std::vector<int32_t> tonics = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11};
#endif
	const std::vector<float> drums = loop(124, {0, 4, 8, 12}, {4, 12}, {2, 6, 10, 14}, 0, 17);
	struct Case {
		double a4;
		bool withDrums;
		int32_t every = 5; ///< Blocks of 128 between two analyses: 40 is a UI that comes every 116 ms
	};
	for (const Case& c : {Case{440, false}, Case{432, false}, Case{440, true}, Case{440, false, 40}}) {
		int32_t right = 0, related = 0, total = 0;
		std::string errors;
		for (int32_t tonic : tonics) {
			for (bool minor : {false, true}) {
				std::vector<float> x = progression(oboe, bass, tonic, minor, c.a4);
				if (c.withDrums) {
					for (size_t i = 0; i < x.size(); i++) {
						x[i] = 0.5f * x[i] + 0.5f * drums[i];
					}
				}
				auto scan = std::make_unique<Scan>();
				scan->setReference((int32_t)lround(c.a4 * 10));
				feed(*scan, x, false, c.every);
				int32_t got = scan->keyTonic();
				bool gotMinor = scan->keyMinor();
				total++;
				if (got == tonic && gotMinor == minor) {
					right++;
					continue;
				}
				// Next door on the Camelot wheel (DJs mix these): the relative key, a fifth up or down
				int32_t relative = minor ? (tonic + 3) % 12 : (tonic + 9) % 12;
				bool isRelated = got >= 0
				                 && ((got == relative && gotMinor != minor)
				                     || (gotMinor == minor && (got == (tonic + 7) % 12 || got == (tonic + 5) % 12)));
				related += isRelated;
				errors += "  " + keyName(tonic, minor) + " as " + keyName(got, gotMinor) + (isRelated ? "" : " (!)");
			}
		}
		const char* how = c.withDrums ? " with drums" : (c.every != 5) ? ", analysed every 116 ms" : "";
		printf("keys at A = %.0f Hz%s: %d of %d right, %d next door%s\n", c.a4, how, right, total, related,
		       errors.c_str());
		// The drums take a few away where they are as loud as the chords, and none may be far off
		CHECK(c.withDrums ? (right + related == total && right >= total - total / 12) : right == total,
		      "keys at %.0f Hz%s: %d of %d right", c.a4, how, right, total);
	}

	// No key: silence, noise, drums, a held note
	auto scan = std::make_unique<Scan>();
	feed(*scan, std::vector<float>(44100 * 12, 0.0f));
	CHECK(scan->keyTonic() < 0, "silence read as %s", keyName(scan->keyTonic(), scan->keyMinor()).c_str());
	std::vector<float> noise(44100 * 12);
	for (float& v : noise) {
		v = (float)(0.3 * (2 * random01() - 1));
	}
	scan = std::make_unique<Scan>();
	feed(*scan, noise);
	CHECK(scan->keyTonic() < 0, "noise read as %s", keyName(scan->keyTonic(), scan->keyMinor()).c_str());
	scan = std::make_unique<Scan>();
	feed(*scan, drums);
	CHECK(scan->keyTonic() < 0, "drums read as %s", keyName(scan->keyTonic(), scan->keyMinor()).c_str());
	scan = std::make_unique<Scan>();
	std::vector<float> held = bass.play(40, 12, 440);
	for (float& v : held) {
		v *= 0.8f;
	}
	feed(*scan, held);
	CHECK(scan->keyTonic() < 0, "a held E1 read as %s", keyName(scan->keyTonic(), scan->keyMinor()).c_str());
	printf("silence, noise, drums, a held note: no key\n");

	// Gone again: 12 s of C major, then 12 s of drums alone
	std::vector<float> x = progression(oboe, bass, 0, false, 440);
	x.insert(x.end(), drums.begin(), drums.begin() + 44100 * 12);
	scan = std::make_unique<Scan>();
	feed(*scan, x);
	CHECK(scan->keyTonic() < 0, "C major then 12 s of drums: still %s", keyName(scan->keyTonic(), scan->keyMinor()).c_str());
}

static void testCost() {
	// 10 s of a house loop with a bass note: the calls as the firmware makes them, counted in the emulator
	std::vector<float> x = loop(124, {0, 4, 8, 12}, {4, 12}, {2, 6, 10, 14}, 0, 10);
	std::vector<float> bass = tone(55, 10);
	for (size_t i = 0; i < x.size(); i++) {
		x[i] = 0.7f * x[i] + 0.3f * bass[i];
	}
	auto scan = std::make_unique<Scan>();
	auto t0 = std::chrono::steady_clock::now();
	feed(*scan, x, true);
	double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
	printf("cost: 10 s of input fed and analysed in %.3f s on this computer (%.2f %% of real time); %.1f BPM, "
	       "%.2f Hz\n",
	       seconds, seconds * 10, scan->bpm(), scan->pitchHz());
	printf("the object: %zu bytes\n", sizeof(Scan));

	// The parts on their own: a pitch frame (a bass note, the dearest: the longest periods) and a tempo estimate
	std::vector<float> note = tone(55, 0.1);
	std::vector<float> frame(Scan::kPitchFrame);
	for (int32_t i = 0; i < Scan::kPitchFrame; i++) {
		frame[i] = note[2 * i]; // Near enough to the halved rate for the cost
	}
	float f = 0;
	for (int32_t i = 0; i < 20; i++) {
		EMU_COUNT_BEGIN("pitch frame");
		f = scan->pitchOf(frame.data());
		EMU_COUNT_END();
	}
	std::vector<float> onsets(Scan::kTempoWindow);
	for (int32_t i = 0; i < Scan::kTempoWindow; i++) {
		onsets[i] = (i % 86 < 2) ? 100.0f : (float)(random01());
	}
	float bpm = 0;
	for (int32_t i = 0; i < 5; i++) {
		std::vector<float> x = onsets;
		EMU_COUNT_BEGIN("tempo estimate");
		bpm = scan->tempoOf(x.data(), (int32_t)x.size());
		EMU_COUNT_END();
	}
	for (int32_t i = 0; i < 5; i++) {
		EMU_COUNT_BEGIN("chroma frame");
		scan->chromaFrame(); // Of the house loop and the bass note fed above
		EMU_COUNT_END();
	}
	printf("parts: a pitch frame %.2f Hz, a tempo estimate %.2f BPM\n", f, bpm);
}

int main(int argc, char** argv) {
	samples = argc > 1 ? argv[1] : "device/card/SAMPLES";
	testTones();
	testMultisamples();
	testTempo();
	testKeys();
	testCost();
	printf(failures ? "%d FAILURES\n" : "all checks passed\n", failures);
	return failures ? 1 : 0;
}
