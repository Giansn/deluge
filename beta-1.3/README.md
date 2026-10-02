# The community firmware's beta (v1.3): research and tests

This branch, `nightly`, holds the work on the community firmware's daily build, the v1.3 beta ("Derbyshire", upstream
`SynthstromAudible/DelugeFirmware` branch `main`, tag `beta`). It is kept apart from mastertune 1.2.1:

- **mastertune 1.2.1** (the firmware that is released and used) lives on the branch `claude/wizardly-brahmagupta-nnrk07`
  in the folder `mastertune-1.2.1/`.
- **The beta** lives here, on `nightly`, in the folder `beta-1.3/`. Nothing from here is merged into the 1.2.1 branch.
  The other way round is fine: the 1.2.1 branch is merged into `nightly` when the shared emulator rig changes.
- **The community repository is read-only** (see `CLAUDE.md`): it is cloned and fetched to read and build, never
  committed or pushed to, and no pull requests, issues or comments are made there.

## The stabilised build

`deluge-1.3.0-beta-62a516c2-nightly-90a87481.bin` (1,739,528 bytes, SHA-256
`422de3d2f267d2853cd0171a949994a21bb166d5b3d62c5db195eb29776baaab`) is the beta `62a516c2` with the 30 patches
0001-0020 and 0101-0111 in `patches/` (0021 and 0022, found after it, come with the next build); what each one
fixes and how it was found is in `NIGHTLY.md` section 7. The Deluge shows it as
`1.3.0-beta-90a87481` (SETTINGS > FIRMWARE VERSION). Install it like any firmware: the .bin as the only .bin in the
card's root folder, then switch the Deluge on with SHIFT held. Back up the card first, as for any beta.

- **Tested in the emulator only, not yet on a Deluge.** On this very file every patch's test passes
  (`tests/repro/run.sh`, 27 tests, and the SM01 replay with `LONG=1`).
- **The fuzzer** ran about 12,300 random inputs without a problem in rounds 7-9 (`NIGHTLY.md` section 7; round 9 on
  the first delivered build). Rounds 10 and 11 then found E427 (0018) and a crash on a fast select turn in arranger
  view (0019); the audit after that, of every encoder handler, found seven more places of that kind (0020). The fuzz
  rounds on this file (round 12) are in `NIGHTLY.md`.
- **Built by `tools/setup_beta.sh`**: the tree `503cb5ce` (62a516c2 plus the patches), commit `90a87481`. The builds
  before it are in the git history: `...-582a21a1.bin` freezes with E427 on a ramp to the arrangement's end,
  `...-697ffb0f.bin` can crash on a fast select turn with a clip instance held in arranger view.

| File | What |
|---|---|
| `NIGHTLY.md` | Why the beta crashes, what its own documentation says, the emulator runs, our patches (section 7) |
| `patches/` | The fixes on 62a516c2: 0001-0099 this session's, 0101 and up the helper session's (`git am` in order) |
| `tools/setup_beta.sh` | The patched beta from this repository alone: source, patches, toolchain v22, build, blockcount.so |
| `tests/emu13.py`, `tests/rig13.py` | mastertune's emulator rig (`mastertune-1.2.1/tests/song`, `tests/stress/ui`) adapted to v1.3 |
| `tests/fuzz_ui.py` | a random user on the real firmware in the emulator, for v1.3 or mastertune with the same inputs |
| `tests/menu_walk_emu.py` | walks every menu of nine contexts (OLED or 7-segment, horizontal menus on or off) |
| `tests/repro/` | one test per patch (fails on 62a516c2, passes with the patches); `run.sh` runs them all |
| `reports/` | the helper session's write-ups of single findings |

## Setting it up in a new session

`beta-1.3/tools/setup_beta.sh` does it all from this repository: the community source at the beta `62a516c2` (cloned
read-only, pushing disabled), `beta-1.3/patches` applied on a local branch, the toolchain v22, a Release build and the
emulator's `blockcount.so`. Then `beta-1.3/tests/repro/run.sh` runs the fixes' tests (each fails on 62a516c2).

## Building the beta

The beta needs the dbt toolchain **v22** (GCC 14); mastertune's v16 (GCC 13) can't compile its `src/memmove.c`.

```sh
git clone --filter=blob:none https://github.com/SynthstromAudible/DelugeFirmware beta && cd beta
git remote set-url --push origin PUSH_DISABLED_community_repo_is_read_only
git checkout beta                       # the tag of the daily build
# toolchain v22: https://github.com/SynthstromAudible/dbt-toolchain/releases (linux-x86_64), as toolchain/v22
./dbt configure && ./dbt build release  # build/Release/deluge.elf; a sparse checkout without website/ and contrib/ works
```

## Running the fuzzer

The rig needs mastertune's test packages (`mastertune-1.2.1/tools/requirements-tests.txt`) and `blockcount.so`
(`mastertune-1.2.1/tools/setup_firmware.sh` builds it). Run python from any folder except the scratchpad:

```sh
python3 beta-1.3/tests/fuzz_ui.py <beta>/build/Release/deluge.elf --firmware v13 \
    --tools <toolchain v22>/arm-none-eabi-gcc/bin/arm-none-eabi- --build <blockcount dir> --out /tmp/fz13 --minutes 60
python3 beta-1.3/tests/fuzz_ui.py <mastertune tree>/build/Release/deluge.elf --firmware 121 \
    --tools <toolchain v16>/arm-none-eabi-gcc/bin/arm-none-eabi- --build <blockcount dir> --out /tmp/fz121 --minutes 60
```

`--7seg` runs the 7-segment display (v13 only), `--mode browser` only the song browser. Results go to
`<out>/fuzz.json`; NIGHTLY.md section 5 has the runs of 1 October 2026.

## How the beta handles sample clusters

A sample is read from the card in clusters of 32 KB (`Cluster::size`). Each cluster lives in the *stealable* part of
SDRAM and carries a count of *reasons* to stay loaded (`numReasonsToBeLoaded`):
- **Who claims reasons.** A sound's sample holder claims the first clusters from its start point
  (`clustersForStart`, 2) and, for a loop, from its loop start (`clustersForLoopStart`); a playing voice claims the
  clusters just ahead of its play position. A cluster with reasons can't be stolen.
- **Loading.** A cluster needed but not in RAM is queued in `ClusterPriorityQueue` (a `std::priority_queue`, the most
  urgent first) and loaded by the card routine. The beta rewrote this queue; 1.2.1's queue ordered by address instead
  of priority (mastertune fixed that separately).
- **Stealing.** When memory runs short, clusters without reasons are freed (stolen) from queues in a fixed order
  (`StealableQueue`): first data no song uses (samples, converted samples, wavetables, caches), then the current
  song's, its percussion cache last; within a queue, the one released longest ago first.
- **#4952 (30 September 2026)** meant to hold short samples whole (up to 2 + 8 clusters), so they never wait for the
  card. It had four faults: it counted samples instead of clusters (samples 2-6 times too long counted as short), it
  claimed only 2 clusters whatever it was asked, it then skipped a short sample's loop start (a sample up to ~7 s
  mono with a loop point read its loop start from the card at every pass), and it claimed forwards for a reversed
  sample. It also added an always-on freeze ("invalid") for a cluster pointer outside stealable memory.
- **Our patches:** 0009 corrects the caching (a 9-cluster sample now held whole instead of 4 clusters; a 19-cluster
  sample's loop start held again; test `tests/repro/cluster_cache_emu.py`); 0006 turns the "invalid" freeze into
  dropping the stale pointer and loading the cluster again.
- **RAM:** holding short samples whole pins up to 320 KB per sample. With the beta's ~6 MB less free SDRAM than
  1.2.1 (same song), out-of-memory is a real risk; 0006 lets `operator new` and the menus' vectors steal clusters
  before failing, instead of freezing.
