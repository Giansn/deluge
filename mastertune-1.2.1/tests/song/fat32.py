"""A FAT32 image as an SD card formatted by a computer: 512-byte sectors, 32 KB clusters (what SD cards of 4 GB and
more have), no partition table (FatFS takes the boot sector at sector 0), two FATs.

The image is a sparse file: 65,600 clusters make it 2.1 GB, of which only what's written takes space.

build(path, files) as the song tests use it: upper-case 8.3 names, one cluster per directory, files contiguous, a
valid FSInfo. The same call gives the same image byte for byte as before the options below existed.

Options for big cards (tests/sdload):
- clusters: the card's size in clusters (1,048,576 = a 32 GB card, a FAT of 4 MB per copy; the FAT's unused part stays
  sparse).
- Long names: a name that isn't an upper-case 8.3 name gets long-name entries (UCS-2, checksum) before a generated
  short name (BASIS~N.EXT), as a computer writes them.
- Directories with more entries than one cluster holds (1,024) take as many clusters as they need, as a chain.
- A file's content is bytes, or (size, [(offset, bytes), ...]): only those parts are written, the rest reads as zeros
  (sparse), so a card can hold gigabytes of files without the disk space.
- fillers: [(path, size), ...]: files with directory entries and FAT chains but nothing written (a full card).
- fragment: (paths, k): those files' clusters interleaved in runs of k clusters (round-robin), as a card fills up
  after files were deleted and written again.
- fsinfo: "valid" (free count and next free cluster right), "invalid" (both 0xFFFFFFFF, as a card whose FSInfo was
  never updated), "stale" (next free cluster 2, the FAT's start: FatFS searches from there).
build() returns the layout (FAT and data area, the clusters of each directory and file, each directory's entries and
files, the first free cluster: all used clusters are contiguous from cluster 2), for telling the sectors read apart
(sdload_emu.py).
"""
import os
import struct

import numpy as np

SECTOR = 512
CSIZE = 64  # Sectors per cluster: 32 KB
RESERVED = 32
NUM_FATS = 2
MIN_CLUSTERS = 65600  # More than 65,525, so FatFS sees FAT32
EOC = 0x0FFFFFFF
LFN_CHARS = 13  # Per long-name entry


def is_short(name):
    base, dot, ext = name.partition(".")
    return (name == name.upper() and 1 <= len(base) <= 8 and len(ext) <= 3 and "." not in ext
            and all(c.isalnum() or c in "_-~!#$%&'()@^`{}" for c in base + ext) and (ext or not dot))


def short_name(name):
    base, _, ext = name.upper().partition(".")
    if not (1 <= len(base) <= 8 and len(ext) <= 3):
        raise ValueError(f"not an 8.3 name: {name}")
    return base.ljust(8).encode() + ext.ljust(3).encode()


def lfn_checksum(name11):
    s = 0
    for c in name11:
        s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
    return s


def generated_short_name(name, taken):
    """BASIS~N.EXT for a long name, unique among `taken` (a set of 11-byte names, updated)."""
    base, dot, ext = name.rpartition(".") if "." in name else (name, "", "")
    clean = lambda s: "".join(c for c in s.upper() if c.isalnum() or c == "_")  # noqa: E731
    base, ext = clean(base) or "X", clean(ext)[:3]
    for n in range(1, 1000000):
        tail = f"~{n}"
        name11 = (base[:8 - len(tail)] + tail).ljust(8).encode() + ext.ljust(3).encode()
        if name11 not in taken:
            taken.add(name11)
            return name11
    raise ValueError(name)


def lfn_entries(name, name11):
    """The long-name entries for name, in the order they're stored (last part first)."""
    chars = [ord(c) for c in name]
    if len(chars) % LFN_CHARS:
        chars += [0] + [0xFFFF] * (LFN_CHARS - 1 - len(chars) % LFN_CHARS)
    parts = [chars[i:i + LFN_CHARS] for i in range(0, len(chars), LFN_CHARS)]
    check = lfn_checksum(name11)
    out = []
    for i, part in enumerate(parts, 1):
        u = struct.pack(f"<{LFN_CHARS}H", *part)
        out.append(struct.pack("<B10sBBB12sH4s", i | (0x40 if i == len(parts) else 0), u[:10], 0x0F, 0, check,
                               u[10:22], 0, u[22:26]))
    return b"".join(reversed(out))


def dir_entry(name11, attr, cluster, size):
    time_, date = (12 << 11), ((2026 - 1980) << 9) | (9 << 5) | 26
    return struct.pack("<11sBBBHHHHHHHI", name11, attr, 0, 0, time_, date, date, cluster >> 16, time_, date,
                       cluster & 0xFFFF, size)


def content_size(data):
    return data[0] if isinstance(data, tuple) else len(data)


def build(path, files, clusters=None, fillers=(), fragment=None, fsinfo="valid"):
    """files: {"SAMPLES/KICK.WAV": bytes or (size, [(offset, bytes), ...]), ...}. See the module's docstring."""
    cluster_bytes = CSIZE * SECTOR
    sizes = {p: content_size(d) for p, d in files.items()}
    for p, size in fillers:
        if p in sizes:
            raise ValueError(f"filler {p} is also a file")
        sizes[p] = size
    # Directory tree
    dirs = {"": []}
    for p in sorted(sizes):
        parts = p.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i])
            if d not in dirs:
                dirs[d] = []
                dirs["/".join(parts[:i - 1])].append(("dir", parts[i - 1], d))
        dirs["/".join(parts[:-1])].append(("file", parts[-1], p))

    # Each directory's entries (without their clusters yet): long names first get their short names
    entry_counts = {}
    short_names = {}
    for d, entries in dirs.items():
        taken = {short_name(n) for _, n, _ in entries if is_short(n)}
        count = 2 if d else 1  # . and .., or the volume label
        for kind, name, full in entries:
            if is_short(name):
                short_names[full] = (short_name(name), None)
                count += 1
            else:
                name11 = generated_short_name(name, taken)
                short_names[full] = (name11, name)
                count += 1 + -(-len(name) // LFN_CHARS)
        entry_counts[d] = count

    # Clusters: directories first (as many as their entries need, 1024 per cluster), then files, contiguous, then
    # the fragmented files interleaved, then the fillers
    next_cluster = 2
    dir_cluster = {}
    runs = {}  # path -> [(first, n), ...]
    for d in sorted(dirs, key=lambda d: (d.count("/"), d)):
        n = max(1, -(-entry_counts[d] * 32 // cluster_bytes))
        dir_cluster[d] = next_cluster
        runs[d + "/"] = [(next_cluster, n)]
        next_cluster += n
    fragmented = set(fragment[0]) if fragment else set()
    for p in files:
        if p in fragmented:
            continue
        n = max(1, -(-sizes[p] // cluster_bytes))
        runs[p] = [(next_cluster, n)]
        next_cluster += n
    if fragmented:
        k = fragment[1]
        left = {p: max(1, -(-sizes[p] // cluster_bytes)) for p in files if p in fragmented}
        while left:
            for p in list(left):
                n = min(k, left[p])
                runs.setdefault(p, []).append((next_cluster, n))
                next_cluster += n
                left[p] -= n
                if not left[p]:
                    del left[p]
    for p, size in fillers:
        n = max(1, -(-size // cluster_bytes))
        runs[p] = [(next_cluster, n)]
        next_cluster += n
    num_clusters = clusters or max(MIN_CLUSTERS, next_cluster + 1024)
    if next_cluster + 1 > num_clusters:
        raise ValueError(f"{next_cluster - 2} clusters used, the card has {num_clusters}")
    fat_sectors = -(-((num_clusters + 2) * 4) // SECTOR)
    data_start = RESERVED + NUM_FATS * fat_sectors
    total_sectors = data_start + num_clusters * CSIZE

    fat = np.zeros(next_cluster, "<u4")
    fat[0], fat[1] = 0x0FFFFFF8, EOC
    for chain in runs.values():
        clusters_ = np.concatenate([np.arange(first, first + n, dtype="<u4") for first, n in chain])
        fat[clusters_[:-1]] = clusters_[1:]
        fat[clusters_[-1]] = EOC
    fat = fat.tobytes()

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
        free, next_free = num_clusters - (next_cluster - 2), next_cluster
        if fsinfo == "invalid":
            free = next_free = 0xFFFFFFFF
        elif fsinfo == "stale":
            next_free = 2
        elif fsinfo != "valid":
            raise ValueError(fsinfo)
        info = bytearray(SECTOR)
        struct.pack_into("<I", info, 0, 0x41615252)
        struct.pack_into("<III", info, 484, 0x61417272, free, next_free)
        struct.pack_into("<I", info, 508, 0xAA550000)
        for s in (0, 6):
            put(s, boot)
            put(s + 1, info)
        for i in range(NUM_FATS):
            put(RESERVED + i * fat_sectors, fat)

        def cluster_sector(c):
            return data_start + (c - 2) * CSIZE

        def first(p):
            return runs[p][0][0]

        for d, entries in dirs.items():
            out = bytearray()
            if d:
                parent = "/".join(d.split("/")[:-1])
                out += dir_entry(b".          ", 0x10, dir_cluster[d], 0)
                out += dir_entry(b"..         ", 0x10, dir_cluster[parent] if parent else 0, 0)
            else:
                out += dir_entry(b"DELUGE     ", 0x08, 0, 0)  # Volume label
            for kind, name, full in entries:
                name11, long_name = short_names[full]
                if long_name:
                    out += lfn_entries(long_name, name11)
                if kind == "dir":
                    out += dir_entry(name11, 0x10, dir_cluster[full], 0)
                else:
                    out += dir_entry(name11, 0x20, first(full), sizes[full])
            put(cluster_sector(dir_cluster[d]), bytes(out))  # The directory's clusters are contiguous

        def put_file(p, offset, data):
            """data at offset within the file p, through its runs of clusters."""
            pos = 0  # Offset of the current run within the file
            for start, n in runs[p]:
                run_bytes = n * cluster_bytes
                lo, hi = max(offset, pos), min(offset + len(data), pos + run_bytes)
                if lo < hi:
                    f.seek(cluster_sector(start) * SECTOR + lo - pos)
                    f.write(data[lo - offset:hi - offset])
                pos += run_bytes

        for p, data in files.items():
            if isinstance(data, tuple):
                for offset, part in data[1]:
                    put_file(p, offset, part)
            elif p in fragmented:
                put_file(p, 0, data)
            else:
                put(cluster_sector(first(p)), data)
    return dict(sector=SECTOR, csize=CSIZE, fat_start=RESERVED, fat_sectors=fat_sectors, num_fats=NUM_FATS,
                data_start=data_start, num_clusters=num_clusters, used_clusters=next_cluster - 2,
                total_sectors=total_sectors,
                dirs={d: runs[d + "/"] for d in dirs},
                dir_entries=entry_counts, dir_files={d: sum(k == "file" for k, _, _ in e) for d, e in dirs.items()},
                first_free_cluster=next_cluster,
                files={p: runs[p] for p in files},
                fillers=len(fillers), filler_clusters=sum(runs[p][0][1] for p, _ in fillers))


def _open(image):
    f = open(image, "rb")

    def sectors(s, n):
        f.seek(s * SECTOR)
        return f.read(n * SECTOR)
    boot = sectors(0, 1)
    csize = boot[13]
    reserved, = struct.unpack_from("<H", boot, 14)
    fat_sectors, = struct.unpack_from("<I", boot, 36)
    root, = struct.unpack_from("<I", boot, 44)
    data_start = reserved + boot[16] * fat_sectors

    def chain(c):
        out = []
        while 2 <= c < 0x0FFFFFF8:
            out.append(c)
            f.seek(reserved * SECTOR + c * 4)
            c = struct.unpack("<I", f.read(4))[0] & 0x0FFFFFFF
        return out

    def read_chain(c):
        return b"".join(sectors(data_start + (x - 2) * csize, csize) for x in chain(c))
    return f, root, read_chain


def _entries(data):
    """(short name 11 bytes, long name or "", attr, cluster, size) of a directory's entries."""
    lfn = ""
    for off in range(0, len(data), 32):
        e = data[off:off + 32]
        if e[0] == 0:
            return
        if e[0] == 0xE5:
            lfn = ""
            continue
        if e[11] == 0x0F:
            chars = e[1:11] + e[14:26] + e[28:32]
            part = chars.decode("utf-16-le", "replace").split("\0")[0].rstrip("￿")
            lfn = part + lfn
            continue
        hi, = struct.unpack_from("<H", e, 20)
        lo, = struct.unpack_from("<H", e, 26)
        size, = struct.unpack_from("<I", e, 28)
        yield e[:11], lfn, e[11], hi << 16 | lo, size
        lfn = ""


def _find(read_chain, cluster, part):
    want = short_name(part) if is_short(part.upper()) else None
    for name11, lfn, _, c, size in _entries(read_chain(cluster)):
        if name11 == want or (lfn and lfn.upper() == part.upper()):
            return c, size
    return None


def read_file(image, path):
    """Reads a file back out of an image (8.3 or long names; for checking what the firmware wrote)."""
    f, cluster, read_chain = _open(image)
    with f:
        parts = path.split("/")
        for i, part in enumerate(parts):
            found = _find(read_chain, cluster, part)
            if not found:
                raise FileNotFoundError(path)
            cluster, size = found
            if i == len(parts) - 1:
                return read_chain(cluster)[:size]


def list_dir(image, path=""):
    """Names (8.3 and long) in a directory of the image."""
    f, cluster, read_chain = _open(image)
    with f:
        for part in [p for p in path.split("/") if p]:
            found = _find(read_chain, cluster, part)
            if not found:
                raise FileNotFoundError(path)
            cluster = found[0]
        return [(name11.decode("latin-1"), lfn) for name11, lfn, _, _, _ in _entries(read_chain(cluster))]
