#!/usr/bin/env python3
"""Fast encoder turns in seven more places that assumed one detent per call, on the v1.3 beta (62a516c2). Since the
encoder overhaul (#4529) a handler gets the whole turn accumulated since the last read (+-2 or more when turned fast,
up to +-127 for the select encoder, x5 with SHIFT in the automation view):
  (a) a CV clip, select -2: CVInstrument::navigateChannels() wraps only a remainder of exactly -1, so CV1 (channel 0)
      turned -2 is channel -2, which CVInstrument::setChannel() takes (channel <= both): the clip's notes go to
      channel -2 (wild indices into the CV engine's channels), and the song saves it so
  (b) the keyboard view in scale mode, SCALE held + select -13: KeyboardScreen::selectEncoderAction() adds an octave
      before the offset only, so a turn of more than an octave down gives a negative root note (the song's key set to
      it, the note name read before its table)
  (c) a kit row dragged (its audition pad held, then its mute pad), the vertical encoder -2: the dragged NoteRow only
      swaps with its neighbour while the view scrolls by the whole turn, so the held pad ends up on another row, and
      its release ends that row's audition: the dragged drum stays auditioned (a stuck note)
  (d) the arranger's automation editor, SHIFT + select -8 (x5: -40): AutomationView::getNextSelectedParamArrayPosition()
      wraps a step past the start to numParams + offset, below 0 for a turn longer than the list (39 global
      parameters): globalParamsForAutomation[-1] is read and the position kept negative
  (e) the slicer's region mode, select -2 at 2 slices and +2 at 256: Slicer::selectEncoderAction() wraps only at
      exactly 1 or 257, which leaves 0 slices (a division by zero when slicing) or 258
  (f) the DX7 parameter menu, SHIFT + horizontal +2 on an operator parameter with the other operators switched off:
      DxParam::horizontalEncoderAction() steps by the whole offset over the six operators with wraps to 0 and 5, from
      operator 1 through 3, 5, 0, 2, 4, 0, ... and never meets a switched-on operator or operator 1 again: a hang
  (g) not tested here: SampleMarkerEditor::selectEncoderAction() refused a move onto another marker's column only, so
      a fast turn could carry a marker past its neighbour (the start past the end, the loop start past the loop end);
      the fix refuses a move across one. It needs the marker editor open on a sample, which this rig doesn't do.

One boot of the real firmware in the emulator (OLED), make_card's song (two synths, a kit, an audio track). (a) to
(d) through the UI: the first synth clip, CV, select -2; KEYBOARD, SCALE (scale mode on, if it isn't), SCALE held,
select -13, KEYBOARD; SONG, the kit clip, the audition pad and then the mute pad of a row with two rows below it held,
scrollY -2, both released; SONG, SONG (arranger view), CLIP, the song's LPF frequency pad, SHIFT held, select -8. (e) and (f) as
direct calls of the handlers: the slicer's region mode with 2 and 256 slices set; dxParam on a DX7 patch from
allocMaxSpeed() with only operator 1 on, SHIFT held, readValueAgain() skipped (it would take the patch of the sound
editor's sound). Checked: the CV channel within 0-2, the root note within 0-11, the dragged row under the held pad
and no drum left auditioned, the parameter position within the list, the slice count within 2-256, the operator step
returns on operator 1, and no freeze, crash, hang or access outside the mapped memory.

Usage: encoder_fast_turn_emu.py <deluge.elf> --tools PREFIX --build DIR --out DIR
Last line: PASS or FAIL; exit 0/1."""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kitrow_emu as kr  # noqa: E402  (its boot, Inputs and Song; puts the rig on the path)
import oom_robust_emu as oom  # noqa: E402  (call(): a direct call under the rig's watchdog)
import stress_ui_emu as su  # noqa: E402

SONG_LPF_PAD = (8, 7)  # UNPATCHED_LPF_FREQ in unpatchedGlobalParamShortcuts (modulation/params/param.h)
NUM_GLOBAL_PARAMS = 39  # kNumGlobalParamsForAutomation (automation_view.cpp)
OPERATOR = 1  # the DX7 operator left switched on, whose parameter is selected


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("elf")
    ap.add_argument("--tools", required=True)
    ap.add_argument("--build", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rig, inp = kr.boot(a, "boot")
    emu = rig.emu
    sym = emu.sym
    for name in ("automationView", "arrangerView", "instrumentClipView", "sessionView", "keyboardScreen"):
        rig.ui_names[sym[name]] = name
    song = kr.Song(rig)
    _, _, memory, count, msize, mstart, esize = rig.heap_offsets
    (channel_off, cv_type, root_off, scale_mode_off, note_rows_off, yscroll_off, drum_off, auditioned_off,
     position_off, num_clips_off, slicer_mode_off, param_off, patch_off, params_off, patch_size) = su.gdb_ints(emu, [
         "list Song::Song", "print (int)&((NonAudioInstrument*)0)->channel", "print (int)OutputType::CV",
         "print (int)&((Song*)0)->key.rootNote", "print (int)&((InstrumentClip*)0)->inScaleMode",
         "print (int)&((InstrumentClip*)0)->noteRows", "print (int)&((InstrumentClip*)0)->yScroll",
         "print (int)&((NoteRow*)0)->drum", "print (int)&((Drum*)0)->auditioned",
         "print (int)&((Song*)0)->lastSelectedParamArrayPosition", "print (int)&((Slicer*)0)->numClips",
         "print (int)&((Slicer*)0)->slicerMode",
         # gdb takes the menu item's namespace only quoted, by its variable
         "print (int)&'deluge::gui::menu_item::dxParam'.param - (int)&'deluge::gui::menu_item::dxParam'",
         "print (int)&'deluge::gui::menu_item::dxParam'.patch - (int)&'deluge::gui::menu_item::dxParam'",
         "print (int)&((DxPatch*)0)->params", "print (int)sizeof(DxPatch)"])

    def i32(address):
        return struct.unpack("<i", bytes(emu.uc.mem_read(address, 4)))[0]

    def press(name, what, wait=0.5):
        inp.button(name, True)
        rig.tm(0.05, f"{what} on")
        inp.button(name, False)
        rig.tm(wait, f"{what} off")
    res = {}
    try:
        # (a) the first synth clip made a CV clip, select -2
        res["a_entered"] = song.enter(inp, 0)
        press("CV", "CV (the clip to a CV instrument)", 1.0)
        output = emu.u32(song.current() + song.output_off)
        res["a_is_cv"] = emu.u8(output + song.otype_off) == cv_type
        res["a_channel_before"] = i32(output + channel_off)
        inp.turn("select", -2)
        rig.tm(1.0, "select -2 in the CV clip")
        output = emu.u32(song.current() + song.output_off)
        res["a_channel_after"] = i32(output + channel_off)

        # (b) the keyboard view in scale mode, SCALE held + select -13
        press("KEYBOARD", "KEYBOARD (the keyboard view)")
        res["b_view"] = rig.ui_name()
        if not emu.u8(song.current() + scale_mode_off):
            press("SCALE", "SCALE (scale mode on)")
        res["b_scale_mode"] = bool(emu.u8(song.current() + scale_mode_off))

        def root_note():
            return struct.unpack("<h", bytes(emu.uc.mem_read(rig.song() + root_off, 2)))[0]
        res["b_root_before"] = root_note()
        inp.button("SCALE", True)
        rig.tm(0.05, "SCALE held")
        inp.turn("select", -13)
        rig.tm(0.5, "select -13 with SCALE held")
        inp.button("SCALE", False)
        rig.tm(0.3, "SCALE released")
        res["b_root_after"] = root_note()
        press("KEYBOARD", "KEYBOARD (back to the clip view)")

        # (c) the kit clip: a row dragged (its audition pad held, then its mute pad), the vertical encoder +2
        press("SONG", "SONG (session view)")
        kit_index = next(i for i, c in enumerate(song.clips()) if song.is_kit(c))
        res["c_entered"] = song.enter(inp, kit_index) and song.is_kit(song.current())
        clip = song.current()

        def note_rows():
            mem, n, size, first, es = (emu.u32(clip + note_rows_off + o) for o in (memory, count, msize, mstart, esize))
            return [mem + ((first + i) % max(size, 1)) * es for i in range(n)]

        def drum_on_pad(y):
            rows, i = note_rows(), y + i32(clip + yscroll_off)
            return emu.u32(rows[i] + drum_off) if 0 <= i < len(rows) else None
        # a pad whose row has two rows below it, the view able to scroll two rows down (yScroll >= 1 - 8 after it)
        y_scroll = i32(clip + yscroll_off)
        y = max(0, 2 - y_scroll)
        res["c_rows"], res["c_scroll"], res["c_pad"] = len(note_rows()), y_scroll, y
        res["c_valid"] = y < 8 and 2 <= y + y_scroll < len(note_rows()) and y_scroll - 2 >= -7
        inp.pad(17, y, 100)
        rig.tm(0.1, f"audition pad 17,{y} held")
        inp.pad(16, y, 100)
        rig.tm(0.1, f"mute pad 16,{y}: the row dragged")
        dragged = drum_on_pad(y)
        inp.turn("scrollY", -2)
        rig.tm(0.3, "scrollY -2 dragging the row")
        res["c_dragged_under_pad"] = drum_on_pad(y) == dragged
        inp.pad(16, y, 0)
        rig.tm(0.1, "mute pad released")
        inp.pad(17, y, 0)
        rig.tm(0.5, "audition pad released")
        res["c_still_auditioned"] = [hex(r) for r in note_rows()
                                     if emu.u32(r + drum_off) and emu.u8(emu.u32(r + drum_off) + auditioned_off)]

        # (d) the arranger's automation editor, SHIFT + select -8 (x5)
        press("SONG", "SONG (session view)")
        res["d_session"] = rig.ui_name()
        press("SONG", "SONG (arranger view)")
        res["d_arranger"] = rig.ui_name()
        press("CLIP", "CLIP (the arranger's automation view)")
        res["d_view"] = rig.ui_name()
        inp.pad(*SONG_LPF_PAD, 100)
        rig.tm(0.05, "parameter pad on")
        inp.pad(*SONG_LPF_PAD, 0)
        rig.tm(0.3, "the parameter's automation editor")
        res["d_position_before"] = i32(rig.song() + position_off)
        inp.button("SHIFT", True)
        rig.tm(0.05, "SHIFT held")
        inp.turn("select", -8)
        rig.tm(0.5, "select -8 (x5) with SHIFT held")
        inp.button("SHIFT", False)
        rig.tm(0.3, "SHIFT released")
        res["d_position_after"] = i32(rig.song() + position_off)

        # (e) the slicer's region mode: select -2 at 2 slices, +2 at 256
        slicer = sym["slicer"]
        select_fn = sym.find("_ZN6Slicer19selectEncoderActionEa")
        counts = []
        for start, turn in ((2, -2), (256, 2)):
            emu.uc.mem_write(slicer + slicer_mode_off, struct.pack("<i", 0))  # SLICER_MODE_REGION
            emu.uc.mem_write(slicer + num_clips_off, struct.pack("<h", start))
            oom.call(rig, f"Slicer::selectEncoderAction({turn:+d}) at {start} slices", select_fn, (slicer, turn))
            counts.append((start, turn, struct.unpack("<h", bytes(emu.uc.mem_read(slicer + num_clips_off, 2)))[0]))
        res["e_slices"] = counts

        # (f) dxParam, SHIFT + horizontal +2 on operator 1's first parameter, the other operators off
        patch = oom.call(rig, "allocMaxSpeed", sym["_Z13allocMaxSpeedmPv"], (patch_size, 0))
        params = bytearray(156)
        params[155] = 1 << OPERATOR  # DxPatch::opSwitch(op): (params[155] >> op) & 1
        emu.uc.mem_write(patch + params_off, bytes(params))
        dx_param = sym["_ZN6deluge3gui9menu_item7dxParamE"]
        emu.uc.mem_write(dx_param + param_off, struct.pack("<i", 21 * OPERATOR))
        emu.uc.mem_write(dx_param + patch_off, struct.pack("<I", patch))
        emu.intercept(sym.find("_ZN6deluge3gui9menu_item7DxParam14readValueAgainEv"), lambda e: 0)
        emu.uc.ctl_flush_tb()
        inp.button("SHIFT", True)
        rig.tm(0.05, "SHIFT held")
        oom.call(rig, "DxParam::horizontalEncoderAction(+2) with SHIFT held",
                 sym.find("_ZN6deluge3gui9menu_item7DxParam23horizontalEncoderAction"), (dx_param, 2))
        res["f_param_after"] = i32(dx_param + param_off)
        inp.button("SHIFT", False)
        rig.tm(0.3, "SHIFT released")
        res["ui_after"] = rig.ui_name()
    except su.Stop:
        p = rig.problems[-1] if rig.problems else {}
        res["stopped"] = f"{p.get('kind')}: {p.get('detail')} during {p.get('what')}"
    res["invalid"] = [(hex(k[0]), k[1], k[2]) for k in rig.invalid]
    res["problems"] = rig.problems
    for k, v in res.items():
        print(f"  {k}: {v}")
    checks = {
        "a": res.get("a_entered") and res.get("a_is_cv") and res.get("a_channel_after") in (0, 1, 2)
        and res.get("a_channel_after") != res.get("a_channel_before"),
        "b": res.get("b_view") == "keyboardScreen" and res.get("b_scale_mode") and res.get("b_root_after") in range(12)
        and res.get("b_root_after") != res.get("b_root_before"),
        "c": res.get("c_entered") and res.get("c_valid") and res.get("c_dragged_under_pad")
        and res.get("c_still_auditioned") == [],
        "d": res.get("d_arranger") == "arrangerView" and res.get("d_view") == "automationView"
        and 0 <= res.get("d_position_after", -1) < NUM_GLOBAL_PARAMS,
        "e": len(res.get("e_slices", [])) == 2 and all(2 <= n <= 256 for _, _, n in res["e_slices"]),
        "f": res.get("f_param_after") == 21 * OPERATOR,
    }
    failed = [k for k, v in checks.items() if not v]
    if res["invalid"] or res["problems"]:
        failed.append("no problems")
    print(f"checks failed: {failed}" if failed else "all checks hold")
    print("PASS" if not failed else "FAIL")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
