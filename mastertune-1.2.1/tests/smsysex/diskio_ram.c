// Host test: FatFS disk on a RAM image
#include "ff.h"
#include "diskio.h"
#include <stdlib.h>
#include <string.h>
unsigned char* ramDisk;
unsigned long ramDiskSectors = 131072; // 64 MB
uint8_t currentlyAccessingCard = 0;
DSTATUS disk_status(BYTE pdrv) { return 0; }
DSTATUS disk_initialize(BYTE pdrv) {
	if (!ramDisk) ramDisk = calloc(ramDiskSectors, 512);
	return 0;
}
DRESULT disk_read(BYTE pdrv, BYTE* buff, LBA_t sector, UINT count) {
	memcpy(buff, ramDisk + (size_t)sector * 512, (size_t)count * 512);
	return RES_OK;
}
DRESULT disk_write(BYTE pdrv, const BYTE* buff, LBA_t sector, UINT count) {
	memcpy(ramDisk + (size_t)sector * 512, buff, (size_t)count * 512);
	return RES_OK;
}
DRESULT disk_ioctl(BYTE pdrv, BYTE cmd, void* buff) {
	switch (cmd) {
	case CTRL_SYNC: return RES_OK;
	case GET_SECTOR_COUNT: *(LBA_t*)buff = ramDiskSectors; return RES_OK;
	case GET_SECTOR_SIZE: *(WORD*)buff = 512; return RES_OK;
	case GET_BLOCK_SIZE: *(DWORD*)buff = 1; return RES_OK;
	}
	return RES_PARERR;
}
DWORD get_fattime(void) { return ((DWORD)(2026 - 1980) << 25) | (9 << 21) | (26 << 16) | (12 << 11); }
int pendingGlobalMIDICommandNumClustersWritten;
