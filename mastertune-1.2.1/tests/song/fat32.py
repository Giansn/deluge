"""A FAT32 image as an SD card formatted by a computer: 512-byte sectors, 32 KB clusters (what SD cards of 4 GB and
more have), no partition table (FatFS takes the boot sector at sector 0), two FATs, 8.3 names only (upper case).

The image is a sparse file: 65,600 clusters make it 2.1 GB, of which only what's written takes space.
"""
import os
import struct

SECTOR = 512
CSIZE = 64  # Sectors per cluster: 32 KB
RESERVED = 32
NUM_FATS = 2
MIN_CLUSTERS = 65600  # More than 65,525, so FatFS sees FAT32
EOC = 0x0FFFFFFF


def short_name(name):
    base, _, ext = name.upper().partition(".")
    if not (1 <= len(base) <= 8 and len(ext) <= 3):
        raise ValueError(f"not an 8.3 name: {name}")
    return base.ljust(8).encode() + ext.ljust(3).encode()


def dir_entry(name11, attr, cluster, size):
    time_, date = (12 << 11), ((2026 - 1980) << 9) | (9 << 5) | 26
    return struct.pack("<11sBBBHHHHHHHI", name11, attr, 0, 0, time_, date, date, cluster >> 16, time_, date,
                       cluster & 0xFFFF, size)


def build(path, files):
    """files: {"SAMPLES/KICK.WAV": bytes, ...}"""
    cluster_bytes = CSIZE * SECTOR
    # Directory tree
    dirs = {"": []}
    for p in sorted(files):
        parts = p.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if d not in dirs:
                dirs[d] = []
                dirs["/".join(parts[:i - 1])].append(("dir", parts[i - 1], d))
        dirs["/".join(parts[:-1])].append(("file", parts[-1], p))

    # Clusters: directories first (one cluster each, 1024 entries), then files, contiguous
    next_cluster = 2
    dir_cluster = {}
    for d in sorted(dirs, key=lambda d: (d.count("/"), d)):
        dir_cluster[d] = next_cluster
        next_cluster += 1
    file_cluster = {}
    chains = []
    for p, data in files.items():
        n = max(1, -(-len(data) // cluster_bytes))
        file_cluster[p] = next_cluster
        chains.append((next_cluster, n))
        next_cluster += n
    for d in dirs:
        chains.append((dir_cluster[d], 1))
    num_clusters = max(MIN_CLUSTERS, next_cluster + 1024)
    fat_sectors = -(-((num_clusters + 2) * 4) // SECTOR)
    data_start = RESERVED + NUM_FATS * fat_sectors
    total_sectors = data_start + num_clusters * CSIZE

    fat = bytearray((next_cluster) * 4)
    struct.pack_into("<II", fat, 0, 0x0FFFFFF8, EOC)
    for first, n in chains:
        for i in range(n):
            struct.pack_into("<I", fat, (first + i) * 4, first + i + 1 if i < n - 1 else EOC)

    with open(path, "wb") as f:
        f.truncate(total_sectors * SECTOR)

        def put(sector, data):
            f.seek(sector * SECTOR)
            f.write(data)

        boot = bytearray(SECTOR)
        boot[0:3] = b"\xEB\x58\x90"
        boot[3:11] = b"MSWIN4.1"
        struct.pack_into("<HBHBHHBHHHII", boot, 11, SECTOR, CSIZE, RESERVED, NUM_FATS, 0, 0, 0xF8, 0, 63, 255, 0,
                         total_sectors)
        struct.pack_into("<IHHIHH", boot, 36, fat_sectors, 0, 0, 2, 1, 6)
        struct.pack_into("<BBBI11s8s", boot, 64, 0x80, 0, 0x29, 0x12345678, b"DELUGE     ", b"FAT32   ")
        boot[510:512] = b"\x55\xAA"
        fsinfo = bytearray(SECTOR)
        struct.pack_into("<I", fsinfo, 0, 0x41615252)
        struct.pack_into("<III", fsinfo, 484, 0x61417272, num_clusters - (next_cluster - 2), next_cluster)
        struct.pack_into("<I", fsinfo, 508, 0xAA550000)
        for s in (0, 6):
            put(s, boot)
            put(s + 1, fsinfo)
        for i in range(NUM_FATS):
            put(RESERVED + i * fat_sectors, fat)

        def cluster_sector(c):
            return data_start + (c - 2) * CSIZE

        for d, entries in dirs.items():
            out = bytearray()
            if d:
                parent = "/".join(d.split("/")[:-1])
                out += dir_entry(b".          ", 0x10, dir_cluster[d], 0)
                out += dir_entry(b"..         ", 0x10, dir_cluster[parent] if parent else 0, 0)
            else:
                out += dir_entry(b"DELUGE     ", 0x08, 0, 0)  # Volume label
            for kind, name, full in entries:
                if kind == "dir":
                    out += dir_entry(short_name(name), 0x10, dir_cluster[full], 0)
                else:
                    out += dir_entry(short_name(name), 0x20, file_cluster[full], len(files[full]))
            put(cluster_sector(dir_cluster[d]), bytes(out))
        for p, data in files.items():
            put(cluster_sector(file_cluster[p]), data)


def read_file(image, path):
    """Reads a file back out of an image (8.3 names; for checking what the firmware wrote)."""
    with open(image, "rb") as f:
        def sectors(s, n):
            f.seek(s * SECTOR)
            return f.read(n * SECTOR)
        boot = sectors(0, 1)
        csize = boot[13]
        reserved, = struct.unpack_from("<H", boot, 14)
        nfats = boot[16]
        fat_sectors, = struct.unpack_from("<I", boot, 36)
        root, = struct.unpack_from("<I", boot, 44)
        data_start = reserved + nfats * fat_sectors

        def chain(c):
            out = []
            while 2 <= c < 0x0FFFFFF8:
                out.append(c)
                f.seek(reserved * SECTOR + c * 4)
                c = struct.unpack("<I", f.read(4))[0] & 0x0FFFFFFF
            return out

        def read_chain(c):
            return b"".join(sectors(data_start + (x - 2) * csize, csize) for x in chain(c))

        cluster = root
        parts = path.upper().split("/")
        for i, part in enumerate(parts):
            data = read_chain(cluster)
            want = short_name(part)
            for off in range(0, len(data), 32):
                e = data[off:off + 32]
                if e[0] == 0:
                    raise FileNotFoundError(path)
                if e[0] == 0xE5 or e[11] == 0x0F:
                    continue
                if e[:11] == want:
                    hi, = struct.unpack_from("<H", e, 20)
                    lo, = struct.unpack_from("<H", e, 26)
                    size, = struct.unpack_from("<I", e, 28)
                    cluster = hi << 16 | lo
                    if i == len(parts) - 1:
                        return read_chain(cluster)[:size]
                    break
            else:
                raise FileNotFoundError(path)


def list_dir(image, path=""):
    """Names (8.3 and long) in a directory of the image."""
    with open(image, "rb") as f:
        def sectors(s, n):
            f.seek(s * SECTOR)
            return f.read(n * SECTOR)
        boot = sectors(0, 1)
        csize = boot[13]
        reserved, = struct.unpack_from("<H", boot, 14)
        fat_sectors, = struct.unpack_from("<I", boot, 36)
        root, = struct.unpack_from("<I", boot, 44)
        data_start = reserved + boot[16] * fat_sectors
        cluster = root
        for part in [p for p in path.upper().split("/") if p]:
            data = sectors(data_start + (cluster - 2) * csize, csize)
            for off in range(0, len(data), 32):
                e = data[off:off + 32]
                if e[:11] == short_name(part):
                    cluster = struct.unpack_from("<H", e, 20)[0] << 16 | struct.unpack_from("<H", e, 26)[0]
                    break
        data = sectors(data_start + (cluster - 2) * csize, csize)
        names = []
        lfn = ""
        for off in range(0, len(data), 32):
            e = data[off:off + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = ""
                continue
            if e[11] == 0x0F:
                chars = e[1:11] + e[14:26] + e[28:32]
                part = chars.decode("utf-16-le", "replace").split("\0")[0].rstrip("￿")
                lfn = part + lfn
                continue
            names.append((e[:11].decode("latin-1"), lfn))
            lfn = ""
        return names
