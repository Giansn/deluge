// Host test for the SysEx file access port (storage/smsysex.cpp): runs the firmware code on a FAT32 RAM disk and
// speaks the protocol over stdin/stdout, one hex-encoded SysEx message per line.
#include "io/midi/midi_device.h"
#include "io/midi/midi_device_manager.h"
#include "storage/smsysex.h"
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
extern "C" {
#include "fatfs/ff.h"
}

void intToString(int32_t number, char* buffer, int32_t minNumDigits) {
	snprintf(buffer, 12, "%0*d", (int)minNumDigits, (int)number);
}

struct FakeDevice : MIDIDevice {
	const char* name;
	int32_t space = 3072;
	explicit FakeDevice(const char* n) : name(n) {}
	void sendSysex(const uint8_t* data, int32_t len) override {
		printf("R ");
		for (int32_t i = 0; i < len; i++)
			printf("%02x", data[i]);
		printf("\n");
	}
	int32_t sendBufferSpace() override { return space; }
};

FakeDevice usbDevice("usb");
FakeDevice dinDevice("din");
MIDIDevice& MIDIDeviceManager::dinMIDIPorts = dinDevice;

static std::vector<uint8_t> fromHex(const std::string& s) {
	std::vector<uint8_t> v;
	for (size_t i = 0; i + 1 < s.size(); i += 2)
		v.push_back((uint8_t)std::stoi(s.substr(i, 2), nullptr, 16));
	return v;
}

// What MidiEngine::midiSysexReceived does before handing a JSON message on
static void deliver(FakeDevice& dev, std::vector<uint8_t> msg) {
	size_t off;
	if (msg.size() > 5 && msg[1] == 0x00 && msg[2] == 0x21 && msg[3] == 0x7B && msg[4] == 0x01)
		off = 5;
	else if (msg.size() > 2 && msg[1] == 0x7D)
		off = 2;
	else
		return;
	if (msg.size() > 1024) // 1.2.1's incoming SysEx buffer
		return;
	if (msg[off] == 4)
		smSysex::sysexReceived(dev, msg.data() + off, msg.size() - off);
}

int main() {
	static FATFS fs;
	static BYTE work[4096];
	MKFS_PARM opt = {FM_FAT32, 0, 0, 0, 0};
	if (f_mkfs("", &opt, work, sizeof work) != FR_OK || f_mount(&fs, "", 1) != FR_OK) {
		printf("E mkfs/mount failed\n");
		return 1;
	}
	char line[70000];
	while (fgets(line, sizeof line, stdin)) {
		std::string s(line);
		while (!s.empty() && (s.back() == '\n' || s.back() == '\r'))
			s.pop_back();
		if (s.rfind("S ", 0) == 0 || s.rfind("D ", 0) == 0) {
			deliver(s[0] == 'S' ? usbDevice : dinDevice, fromHex(s.substr(2)));
		}
		else if (s.rfind("SPACE ", 0) == 0) {
			usbDevice.space = std::stoi(s.substr(6));
		}
		else if (s == "RUN") {
			for (int i = 0; i < 1000; i++)
				smSysex::handleNextSysEx();
		}
		else if (s.rfind("RUN1", 0) == 0) {
			smSysex::handleNextSysEx();
		}
		else if (s.rfind("PUT ", 0) == 0) { // PUT path hexdata
			size_t sp = s.find(' ', 4);
			std::string path = s.substr(4, sp - 4);
			auto data = fromHex(s.substr(sp + 1));
			FIL f;
			UINT bw;
			FRESULT r = f_open(&f, path.c_str(), FA_WRITE | FA_CREATE_ALWAYS);
			if (r == FR_OK) {
				f_write(&f, data.data(), data.size(), &bw);
				f_close(&f);
			}
			printf("P %d\n", r);
		}
		else if (s.rfind("MKDIR ", 0) == 0) {
			printf("P %d\n", f_mkdir(s.substr(6).c_str()));
		}
		else if (s.rfind("GET ", 0) == 0) {
			FIL f;
			FRESULT r = f_open(&f, s.substr(4).c_str(), FA_READ);
			printf("G %d ", r);
			if (r == FR_OK) {
				uint8_t b[512];
				UINT br;
				while (f_read(&f, b, sizeof b, &br) == FR_OK && br) {
					for (UINT i = 0; i < br; i++)
						printf("%02x", b[i]);
				}
				f_close(&f);
			}
			printf("\n");
		}
		else if (s.rfind("STAT ", 0) == 0) {
			FILINFO fi;
			FRESULT r = f_stat(s.substr(5).c_str(), &fi);
			printf("T %d %u %u %u\n", r, (unsigned)fi.fsize, (unsigned)fi.fdate, (unsigned)fi.ftime);
		}
		printf("OK\n");
		fflush(stdout);
	}
	return 0;
}
