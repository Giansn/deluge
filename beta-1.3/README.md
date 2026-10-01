# The community firmware's beta (v1.3): research and tests

This branch, `nightly`, holds the work on the community firmware's daily build, the v1.3 beta ("Derbyshire", upstream
`SynthstromAudible/DelugeFirmware` branch `main`, tag `beta`). It is kept apart from mastertune 1.2.1:

- **mastertune 1.2.1** (the firmware that is released and used) lives on the branch `claude/wizardly-brahmagupta-nnrk07`
  in the folder `mastertune-1.2.1/`.
- **The beta** lives here, on `nightly`, in the folder `beta-1.3/`. Nothing from here is merged into the 1.2.1 branch.
  The other way round is fine: the 1.2.1 branch is merged into `nightly` when the shared emulator rig changes.
- **The community repository is read-only** (see `CLAUDE.md`): it is cloned and fetched to read and build, never
  committed or pushed to, and no pull requests, issues or comments are made there.

| File | What |
|---|---|
| `NIGHTLY.md` | Why the beta crashes, what its own documentation says, the emulator runs, what it means for mastertune |
| `tests/emu13.py`, `tests/rig13.py` | mastertune's emulator rig (`mastertune-1.2.1/tests/song`, `tests/stress/ui`) adapted to v1.3 |
| `tests/fuzz_ui.py` | a random user on the real firmware in the emulator, for v1.3 or mastertune with the same inputs |

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
