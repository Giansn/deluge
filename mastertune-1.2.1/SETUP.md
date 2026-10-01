# Setting up the build and the tests (for a new session)

A cloud session's container is new every time: nothing outside this repository survives. This file and
`tools/setup_firmware.sh` set up, from the repository alone, what is needed to build mastertune and run its tests.

## What happens on its own

`.claude/hooks/session-start.sh` (registered in `.claude/settings.json`) runs at the start of every Claude Code on the
web session. It installs the Python packages of `tools/requirements-tests.txt` (unicorn 2.1.4, numpy, scipy,
soundfile, pillow, pymupdf, pyflakes) if they are missing. It does nothing else, so the session starts quickly.

## The firmware: one command

```sh
mastertune-1.2.1/tools/setup_firmware.sh            # v19.0.3; or v19.0.2, v19.0.1, v19.0, v18.4
```

In about 4 minutes (first time: plus the downloads) it:

1. clones the community firmware (`SynthstromAudible/DelugeFirmware`, blob-less) to `/home/user/work/DelugeFirmware-121`
   and fetches the tag `release_1_2_1`;
2. makes the firmware tree `/home/user/work/mastertune-<version>` (a git worktree at `release_1_2_1`) and applies
   `patches/0001`–`0122` and `l2test/0001`–`0003` with `git am`; for v19.0.3 it checks that the source tree is the
   release's (`520ba462…`);
3. downloads the dbt toolchain v16 and keeps only what the build needs (the compiler with the Cortex-A9 libraries,
   cmake, ninja: 506 MB) in `/home/user/work/dbt/toolchain/v16/linux-x86_64`;
4. clones the library argon at v0.1.0 (the build would download it as an archive, which the session's proxy refuses);
5. configures and builds Release, with the release's commit hash built in, and checks that `deluge.bin` is bit for bit
   the released `.bin` in this folder;
6. builds `blockcount.so` (the emulator's instruction counter) in `/home/user/work/blockcount`.

Disk: about 1.3 GB. If something is already there, it is kept. `NO_BUILD=1` stops after step 3. The paths can be
changed with `WORK`, `UPSTREAM`, `TREE` and `TOOLCHAIN` (see the script's header).

Checked on 1 October 2026: a new tree from the patches built `b6f0bf18…48397743`, the released v19.0.3, and
`tests/songchange` scenario `countin` passed on it.

## Running the tests

```sh
export BLOCKCOUNT_DIR=/home/user/work/blockcount
ELF=/home/user/work/mastertune-v19.0.3/build/Release/deluge.elf
TOOLS=/home/user/work/dbt/toolchain/v16/linux-x86_64/arm-none-eabi-gcc/bin/arm-none-eabi-
python3 mastertune-1.2.1/tests/songchange/songchange_emu.py $ELF --tools $TOOLS --build $BLOCKCOUNT_DIR --out /tmp/sc
python3 mastertune-1.2.1/tests/dj/dj_emu.py $ELF --tools $TOOLS --out /tmp/dj
python3 mastertune-1.2.1/tests/scan/scan_view_emu.py $ELF --tools $TOOLS --out /tmp/scan
EMU_OPTS="--init-sounds --seed 1" mastertune-1.2.1/tests/song/run.sh /home/user/work/mastertune-v19.0.3 /tmp/song
```

Each test's own docstring or `run.sh` says what it checks. Run python with any working directory except a folder that
has files named like the test modules. The arp test (`tests/arp/arp_roll_test.py`) writes `out/` into the current
directory: run it from a scratch folder.

For a new firmware version: commit in the tree, `git format-patch -1 --start-number <next>` into `patches/`, and check
the tree as step 2 does (a temporary index with `git read-tree release_1_2_1`, every patch with `git apply --cached`,
`git write-tree` equal to `HEAD^{tree}`).

## Notes

- The disk allowance of a cloud session is small (a few GB): build artifacts of old trees and big test outputs fill it.
  `df -h /` shows what is left.
- Behind the session's proxy, the upstream clone and the toolchain download work; GitHub's archive downloads
  (`/archive/…tar.gz`) are refused with 403.
- The community firmware's own beta (upstream `main`, v1.3) needs the toolchain v22 (GCC 14); see the branch
  `nightly-research`.
