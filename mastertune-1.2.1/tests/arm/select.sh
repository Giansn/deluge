# Sourced by the tests' run.sh after setting FW and HERE: with ARM=1 in the environment, ARM_CXX builds for the
# Deluge's Cortex-A9 with the firmware's toolchain and flags, and ARM_RUN runs the result in the emulator. See README.
ARM_DIR=$(cd "$HERE/../arm" && pwd)
ARM_CXX="python3 $ARM_DIR/arm_build.py $FW"
ARM_RUN="env DELUGE_FIRMWARE=$FW python3 $ARM_DIR/arm_run.py"
