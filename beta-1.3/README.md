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

`deluge-1.3.0-beta-62a516c2-nightly-426a0ab4.bin` (1,740,048 bytes, SHA-256
`bceb0397e8f274a39f11a95b589cfc271701624c52e366595beb04042fb97ada`) is the beta `62a516c2` with the 43 patches in
`patches/` (0001-0026 and 0101-0118 without the withdrawn 0104, in the order of `patches/series`); what each one fixes
and how it was found is in `NIGHTLY.md` section 7. The Deluge shows it as `1.3.0-beta-426a0ab4` (SETTINGS > FIRMWARE
VERSION).
Install it like any firmware: the .bin as the only .bin in the card's root folder, then switch the Deluge on
with SHIFT held. Back up the card first, as for any beta.

- **Stable by the stop run criterion, in the emulator.** The helper session's stop run 12 on these 43 patches: the
  full 2 hours of `--mode deep`, OLED and 7-segment, 12 seeds, 13,100 inputs, no problem (no crash, freeze or hang, no
  access outside RAM, no write through null; only the test card's popups). Stop runs 1-12: 102,304 inputs, 10
  problems, 9 fixed by patches and one a fuzzer artefact (`reports/2026-10-02-stop-runs.md`).
- **Tested in the emulator only, not yet on a Deluge.** On this very file every patch's test passes
  (`tests/repro/run.sh`, 39 tests, and the SM01 and seed 152 replays with `LONG=1`).
- **The fuzzer** ran about 12,300 random inputs without a problem in rounds 7-9 (`NIGHTLY.md` section 7; round 9 on
  the first delivered build). Rounds 10-12 on the delivered builds then found E427 (0018), a crash on a fast select
  turn in arranger view (0019) and one on KEYBOARD in an audio Clip's automation view (0023: one problem in 11,823
  inputs); the helper session's stop runs found E170 at a song swap (0112), a crash or E369 on undo in the arranger's
  automation view (0113), a crash in a kit row's sample browser with Affect Entire on (0114) and E411 after CV in the
  synth browser over a clip's automation view (0115), E242 at a stem export while the output was being resampled
  (0116), E058 or i009 at a preset change with every preset in the song already (0117) and a crash on undo into a
  sound editor that was closed (0118). Audits of each fault's
  kind found 0020 (seven more fast-turn places), 0021 (MIDI follow), 0022 and 0024 (the automation view and an audio
  Clip). Round 13 on the fifth build: 8,453 inputs, no problem. MIDI input, which the fuzzer now sends too (`--midi`,
  with a running clock since round 3): 13,852 inputs in three rounds, one problem, E427 recording a parameter right
  after a continue whose clock ticks arrived together (0025); round 4 on the sixth build found a crash on BACK in the
  performance view after a song load in its editing mode (0026). Round 16 and MIDI round 6 on the eighth build:
  11,163 inputs and 29,271 MIDI messages, no problem. The fuzz rounds are in `NIGHTLY.md`.
- **Built by `tools/setup_beta.sh`**: the tree `87da6143` (62a516c2 plus the patches), commit `426a0ab4`. The builds
  before it are in the git history: `...-582a21a1.bin` freezes with E427 on a ramp to the arrangement's end,
  `...-697ffb0f.bin` can crash on a fast select turn with a clip instance held in arranger view, `...-90a87481.bin` on
  KEYBOARD in an audio Clip's automation view, `...-8f34a7c4.bin` freezes with E427 when a parameter is recorded right
  after a MIDI continue whose clock ticks arrive together, and can crash in a kit row's sample browser with Affect
  Entire on, `...-1ff34bd0.bin` can crash on BACK in the performance view after a song load in its editing mode, and
  freezes with E411 after CV in the synth browser over a clip's automation view, `...-dd64dbbe.bin` freezes with
  E242 at a stem export while the output is being resampled, and with E058 (or i009) at a preset change with every
  preset in the song already, `...-3b916052.bin` can crash on undo after the sound editor it was made in was
  closed.

| File | What |
|---|---|
| `NIGHTLY.md` | Why the beta crashes, what its own documentation says, the emulator runs, our patches (section 7) |
| `patches/` | The fixes on 62a516c2: 0001-0099 this session's, 0101 and up the helper session's; `patches/series` is the order (`git am`), new ones not listed go after it in name order |
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
