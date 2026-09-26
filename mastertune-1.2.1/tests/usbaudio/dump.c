#include <stdio.h>
#include <stdint.h>
#include "io/usb/usb_audio.h"
extern uint8_t g_midi_configuration[];
int main(void) {
	usbAudioSetEnabled(1);
	printf("enabled %d\n", usbAudioEnabled);
	uint8_t* d = usbAudioDeviceDescriptor();
	printf("device");
	for (int i = 0; i < 18; i++) printf(" %02x", d[i]);
	printf("\nmidiconfig");
	int mt = g_midi_configuration[2] | (g_midi_configuration[3] << 8);
	for (int i = 0; i < mt; i++) printf(" %02x", g_midi_configuration[i]);
	uint8_t* c = usbAudioConfigurationDescriptor();
	int t = c[2] | (c[3] << 8);
	printf("\nconfig");
	for (int i = 0; i < t; i++) printf(" %02x", c[i]);
	uint16_t* p = usbAudioPipeTable();
	printf("\npipes");
	for (int i = 0; i < 3 * 6 + 1; i++) printf(" %04x", p[i]);
	printf("\n");
	return 0;
}
