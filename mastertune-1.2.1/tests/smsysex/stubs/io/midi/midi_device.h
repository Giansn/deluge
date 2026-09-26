// Host test stub: the parts of MIDIDevice the SysEx file access uses
#pragma once
#include <cstdint>
class MIDIDevice {
public:
	virtual ~MIDIDevice() = default;
	virtual void sendSysex(const uint8_t* data, int32_t len) = 0;
	virtual int32_t sendBufferSpace() = 0;
};
