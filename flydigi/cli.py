"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import commands as C
from . import macros as MAC
from . import mapping as M
from .device import (FLYDIGI_VID, Controller, DeviceError, NotFound,
                     enumerate_interfaces)
from .input_monitor import InputMonitor
from .updates import UpdateCheckError, check_firmware


# ------------------------------------------------------------------ helpers

def _open(args) -> Controller:
    ctl = Controller()
    ctl.trace = getattr(args, "trace", False)
    return ctl.open()


def resolve_key(name: str) -> int:
    """Accept a button name ('a', 'M1', 'LB') or a raw id."""
    if name.isdigit():
        value = int(name)
        if value not in M.KEY_NAMES:
            raise SystemExit(f"error: {value} is not a known key id")
        return value
    lowered = name.strip().lower()
    for key_id, label in M.KEY_NAMES.items():
        if label.lower() == lowered or label.split(" ")[0].lower() == lowered:
            return key_id
    raise SystemExit(f"error: unknown button {name!r}. "
                     f"Try one of: {', '.join(M.KEY_NAMES[k] for k in M.PHYSICAL_KEYS)}")


def _profile_arg(args, ctl: Controller) -> int:
    if getattr(args, "profile", None) is None:
        return C.read_profile_versions(ctl).active
    index = args.profile - 1
    if not 0 <= index < C.PROFILE_COUNT:
        raise SystemExit(f"error: profile must be 1..{C.PROFILE_COUNT}")
    return index


# ------------------------------------------------------------- subcommands

def cmd_list(args) -> int:
    flydigi = [i for i in enumerate_interfaces() if i.vid == FLYDIGI_VID]
    if not flydigi:
        print("No Flydigi HID interfaces present.")
        return 1
    print(f"{'device':<14} {'vid:pid':<12} {'usage':<8} role")
    for i in flydigi:
        role = ("control" if i.is_control else
                "wired marker" if i.usage_page == 0xFFEF else
                "dongle marker" if i.usage_page == 0xFFEE else "gamepad input")
        up = f"{i.usage_page:#06x}" if i.usage_page is not None else "-"
        print(f"{i.path:<14} {i.vid:04X}:{i.pid:04X}   {up:<8} {role}")
    return 0


def cmd_info(args) -> int:
    with _open(args) as ctl:
        info = C.read_info(ctl)
        info.uid = C.read_uid(ctl)
        print(info.describe())
    return 0


def cmd_status(args) -> int:
    with _open(args) as ctl:
        print(C.read_data_report_status(ctl).describe())
        print()
        print(C.read_hardware_status(ctl).describe())
    return 0


def cmd_third_party(args) -> int:
    with _open(args) as ctl:
        if args.state is None:
            st = C.read_data_report_status(ctl)
            print("on" if st.third_party_control else "off")
            if st.controlled_by:
                print(f"currently held by {st.controlled_by!r}")
            return 0
        want = args.state == "on"
        if not C.set_third_party_control(ctl, want):
            print("Controller did not acknowledge the change.", file=sys.stderr)
            return 1
        got = C.read_data_report_status(ctl).third_party_control
        print(f"Third-party mapping takeover is now {'on' if got else 'off'}.")
        if got:
            print("Steam Input / reWASD can now drive the mapping; the "
                  "controller's onboard mapping is bypassed.")
        return 0 if got == want else 1


def cmd_profiles(args) -> int:
    with _open(args) as ctl:
        pv = C.read_profile_versions(ctl)
        blobs = C.read_all_profiles(ctl)
        for i in range(C.PROFILE_COUNT):
            blob = M.MappingBlob(blobs[i])
            marks = ["active"] if i == pv.active else []
            if pv.ns_profile == i:
                marks.append("Switch mode")
            tag = f"   <- {', '.join(marks)}" if marks else ""
            state = ("factory default" if pv.data_versions[i] == C.UNWRITTEN
                     else f"dataVersion {pv.data_versions[i]}")
            remapped = sum(1 for s in blob.key_table()
                           if s.slot in M.KEY_NAMES and not s.is_identity)
            print(f"  {i + 1}. {blob.title:<12} {state:<22}"
                  f"{remapped} remapped{tag}")
        if not pv.ns_reported:
            print()
            print("Nintendo Switch binding is not readable while the controller "
                  "is in XInput mode.")
    return 0


def cmd_activate(args) -> int:
    with _open(args) as ctl:
        index = args.profile - 1
        if not 0 <= index < C.PROFILE_COUNT:
            raise SystemExit(f"error: profile must be 1..{C.PROFILE_COUNT}")
        if not C.activate_profile(ctl, index):
            print("Controller did not acknowledge.", file=sys.stderr)
            return 1
        time.sleep(0.3)
        print(f"Profile {C.read_profile_versions(ctl).active + 1} is now active.")
    return 0


def cmd_keys(args) -> int:
    with _open(args) as ctl:
        index = _profile_arg(args, ctl)
        before = C.read_profile_versions(ctl).active
        blob = M.MappingBlob(C.read_mapping_blob(ctl, index))
        if C.read_profile_versions(ctl).active != before:
            C.activate_profile(ctl, before)
        print(f"Profile {index + 1}: {blob.title!r}")
        warning = blob.layout_warning()
        if warning:
            print(f"  WARNING: {warning}\n")
        text = blob.describe_keys(only_changed=not args.all)
        if text.count("\n") == 0:
            print("  (every button at its default)")
        else:
            print(text)
    return 0


def _edit_profile(args, edit) -> int:
    """Read a profile, apply `edit(blob)`, write it back, restore selection."""
    with _open(args) as ctl:
        index = _profile_arg(args, ctl)
        before = C.read_profile_versions(ctl).active
        blob = M.MappingBlob(C.read_mapping_blob(ctl, index))
        original = bytes(blob.data)
        summary = edit(blob)
        if bytes(blob.data) == original:
            print("Nothing to change.")
            if C.read_profile_versions(ctl).active != before:
                C.activate_profile(ctl, before)
            return 0
        if args.dry_run:
            print(f"[dry run] would write to profile {index + 1}: {summary}")
            if C.read_profile_versions(ctl).active != before:
                C.activate_profile(ctl, before)
            return 0
        dv = C.write_profile(ctl, index, bytes(blob.data))
        time.sleep(0.4)
        check = M.MappingBlob(C.read_mapping_blob(ctl, index))
        ok = bytes(check.data)[:M.OFF_DATA_VERSION] == bytes(blob.data)[:M.OFF_DATA_VERSION]
        if C.read_profile_versions(ctl).active != before:
            C.activate_profile(ctl, before)
        print(f"Profile {index + 1}: {summary}")
        print(f"  committed as dataVersion {dv}; verified: {ok}")
        return 0 if ok else 1


def cmd_remap(args) -> int:
    button = resolve_key(args.button)
    if button not in M.PHYSICAL_KEYS:
        print(f"warning: {M.key_name(button)} is not a physical button on this "
              f"controller", file=sys.stderr)
    if args.clear:
        return _edit_profile(args, lambda b: (
            b.remap(button, None),
            f"{M.key_name(button)} restored to default")[-1])
    if args.target is None:
        raise SystemExit("error: give a target button, or --clear")
    target = resolve_key(args.target)
    return _edit_profile(args, lambda b: (
        b.remap(button, target),
        f"{M.key_name(button)} -> {M.key_name(target)}")[-1])


def cmd_turbo(args) -> int:
    button = resolve_key(args.button)
    target = resolve_key(args.target) if args.target else button
    mode = {"press": M.TurboMode.PRESS, "click": M.TurboMode.CLICK}[args.mode]
    return _edit_profile(args, lambda b: (
        b.set_turbo(button, target, args.hz, mode),
        f"{M.key_name(button)} turbo {M.key_name(target)} @{args.hz} ({args.mode})")[-1])


def cmd_rename(args) -> int:
    return _edit_profile(args, lambda b: (
        setattr(b, "title", args.title),
        f"renamed to {args.title!r}")[-1])


def cmd_ns_bind(args) -> int:
    with _open(args) as ctl:
        index = args.profile - 1
        if not 0 <= index < C.PROFILE_COUNT:
            raise SystemExit(f"error: profile must be 1..{C.PROFILE_COUNT}")
        before = C.read_profile_versions(ctl).active
        blob = M.MappingBlob(C.read_mapping_blob(ctl, index))
        dv = blob.data_version
        C.activate_profile(ctl, index)
        time.sleep(0.3)
        result = C.save_as_ns_profile(ctl, index, data_version=dv)
        time.sleep(0.6)
        if before != index:
            C.activate_profile(ctl, before)
        if result is None:
            print("Controller did not acknowledge the binding.", file=sys.stderr)
            return 1
        print(f"Profile {index + 1} ({blob.title!r}) is now the Nintendo "
              f"Switch mode profile.")
        print("Switch the controller into NS mode to confirm; the binding "
              "cannot be read back over USB in XInput mode.")
    return 0


def cmd_sleep(args) -> int:
    with _open(args) as ctl:
        if args.value is None:
            print(C.sleep_label(C.read_hardware_status(ctl).sleep_minutes))
            return 0
        raw = args.value.strip().lower()
        if raw in ("never", "off", "0"):
            minutes = 0
        elif raw.endswith("h"):
            minutes = int(float(raw[:-1]) * 60)
        else:
            minutes = int(raw.rstrip("m"))
        if not C.set_sleep_time(ctl, minutes):
            print("Controller did not acknowledge.", file=sys.stderr)
            return 1
        time.sleep(0.3)
        now = C.read_hardware_status(ctl).sleep_minutes
        print(f"Sleep timeout is now {C.sleep_label(now)}.")
        return 0 if now == minutes else 1


def cmd_feature(args) -> int:
    with _open(args) as ctl:
        st = C.read_hardware_status(ctl)
        if args.name is None:
            print(st.describe())
            return 0
        wanted = args.name.strip().lower().replace("-", " ").replace("_", " ")
        match = None
        for label, sub, uidx, eidx, bit in C.FEATURE_TABLE:
            if label.lower().startswith(wanted) or wanted in label.lower():
                match = (label, sub)
                break
        if match is None:
            print(f"error: unknown feature {args.name!r}. Options:", file=sys.stderr)
            for label, *_ in C.FEATURE_TABLE:
                print(f"  {label}", file=sys.stderr)
            return 2
        label, sub = match
        usable, enabled = st.features[label]
        if args.state is None:
            print(f"{label}: {'on' if enabled else 'off'}"
                  f"{'' if usable else '  (not supported on this device)'}")
            return 0
        if not usable:
            print(f"error: {label} is not supported on this controller",
                  file=sys.stderr)
            return 1
        want = args.state == "on"
        if not C.set_feature(ctl, sub, want):
            print("Controller did not acknowledge.", file=sys.stderr)
            return 1
        time.sleep(0.3)
        _, now = C.read_hardware_status(ctl).features[label]
        print(f"{label} is now {'on' if now else 'off'}.")
        return 0 if now == want else 1


def cmd_macro_list(args) -> int:
    with _open(args) as ctl:
        index = _profile_arg(args, ctl)
        before = C.read_profile_versions(ctl).active
        blob = C.read_macro_blob(ctl, index)
        mapping = M.MappingBlob(C.read_mapping_blob(ctl, index))
        if C.read_profile_versions(ctl).active != before:
            C.activate_profile(ctl, before)
    print(f"Profile {index + 1}: {mapping.title}")
    print(MAC.describe(MAC.parse(blob)))
    bound = [s.slot for s in mapping.key_table()
             if s.keyid == M.KEY_MACRO and s.slot in M.KEY_NAMES]
    if bound:
        print("  buttons set to run a macro: "
              + ", ".join(M.key_name(b) for b in bound))
    return 0


def cmd_macro_record(args) -> int:
    button = resolve_key(args.button)
    if button not in M.PHYSICAL_KEYS:
        raise SystemExit(f"error: {M.key_name(button)} is not a physical button")
    enable = {"once": MAC.EnableType.ONCE,
              "held": MAC.EnableType.PRESS,
              "toggle": MAC.EnableType.CLICK}[args.type]
    print(f"Recording a macro for {M.key_name(button)}.")
    print("Press the buttons you want in the sequence, then press Enter here "
          "to finish.")
    print(f"({M.key_name(button)} itself is ignored while recording.)")
    print()
    import threading
    done = threading.Event()

    def waiter():
        try:
            input()
        except EOFError:
            pass
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    with _open(args) as ctl:
        index = _profile_arg(args, ctl)
        before = C.read_profile_versions(ctl).active
        with InputMonitor(ctl) as monitor:
            steps = MAC.record(
                monitor, done.is_set, ignore={button},
                on_event=lambda s: print(f"   {s.describe()}"))
        if not steps:
            print("Nothing recorded; leaving the controller alone.")
            return 1
        macro = MAC.Macro(button=button, name=args.name or "",
                          enable_type=enable,
                          interval_ms=args.interval, steps=steps)
        print()
        print(macro.describe())
        if args.dry_run:
            print("[dry run] not written")
            return 0
        dv = C.bind_macro(ctl, index, macro)
        check = MAC.parse(C.read_macro_blob(ctl, index))
        if C.read_profile_versions(ctl).active != before:
            C.activate_profile(ctl, before)
    ok = any(m.button == button and len(m.steps) == len(steps) for m in check)
    print(f"Written to profile {index + 1} (dataVersion {dv}); verified: {ok}")
    return 0 if ok else 1


def cmd_macro_clear(args) -> int:
    button = resolve_key(args.button)
    with _open(args) as ctl:
        index = _profile_arg(args, ctl)
        before = C.read_profile_versions(ctl).active
        if args.dry_run:
            print(f"[dry run] would remove the macro on {M.key_name(button)} "
                  f"in profile {index + 1}")
            return 0
        dv = C.unbind_macro(ctl, index, button)
        left = MAC.parse(C.read_macro_blob(ctl, index))
        if C.read_profile_versions(ctl).active != before:
            C.activate_profile(ctl, before)
    print(f"Removed the macro on {M.key_name(button)} from profile "
          f"{index + 1} (dataVersion {dv}); {len(left)} macro(s) left")
    return 0


def cmd_monitor(args) -> int:
    with _open(args) as ctl:
        with InputMonitor(ctl) as monitor:
            print("Reading live input; Ctrl-C to stop.")
            last = None
            try:
                while True:
                    st = monitor.read(timeout=0.2)
                    if st is None:
                        continue
                    line = ("  ".join(st.pressed_names()) or "-")
                    out = "  ".join(st.output_names()) or "-"
                    line = (f"L({st.left_x:+.2f},{st.left_y:+.2f}) "
                            f"R({st.right_x:+.2f},{st.right_y:+.2f}) "
                            f"LT{st.left_trigger:.2f} RT{st.right_trigger:.2f}  "
                            f"pressed:{line}  emits:{out}")
                    if line != last:
                        print("\r\033[K" + line, end="", flush=True)
                        last = line
            except KeyboardInterrupt:
                print()
    return 0


def cmd_updates(args) -> int:
    with _open(args) as ctl:
        info = C.read_info(ctl)
    print(f"Installed on {info.name}:")
    for label, value in (("Controller", info.firmware),
                         ("Switch", info.switch_version),
                         ("Dongle", info.dongle_version),
                         ("Trigger", info.trigger_version),
                         ("Screen", info.screen_version),
                         ("ADC", info.adc_version),
                         ("NearLink", info.nearlink_version)):
        if value:
            print(f"  {label:<12}{value}")
    if args.offline:
        return 0
    print()
    print("Checking Flydigi's API (sends device code, type and firmware "
          "versions; not your UID)...")
    try:
        updates = check_firmware(args.device_code, info.device_type, info)
    except UpdateCheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not updates:
        print("  No newer firmware reported.")
        return 0
    available = False
    for u in updates:
        if u.newer_available:
            available = True
            print(f"  {u.label:<12}{u.installed or '-':<10} -> {u.latest}"
                  f"   UPDATE AVAILABLE")
            if u.notes:
                print(f"      {u.notes}")
        else:
            print(f"  {u.label:<12}{u.installed or '-':<10} up to date")
    if available:
        print()
        print("This tool never flashes firmware. Use the Windows app (or a "
              "Windows VM with USB passthrough) to apply an update.")
    return 0


#: Where backups go when no directory is given. Anchored to the project
#: rather than the working directory, so running the tool from elsewhere
#: does not scatter backups around the filesystem.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BACKUP_DIR = os.path.join(PROJECT_DIR, "backups")


def cmd_backup(args) -> int:
    outdir = args.directory or os.path.join(
        DEFAULT_BACKUP_DIR, time.strftime("%Y%m%d-%H%M%S"))
    outdir = os.path.abspath(outdir)
    # Connect before creating anything, so a failed run leaves no empty
    # directory behind.
    with _open(args) as ctl:
        os.makedirs(outdir, exist_ok=True)
        info = C.read_info(ctl)
        info.uid = C.read_uid(ctl)
        pv = C.read_profile_versions(ctl)
        hw = C.read_hardware_status(ctl)
        st = C.read_data_report_status(ctl)
        blobs = C.read_all_profiles(ctl)
        meta = {
            "saved": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "device": info.name, "device_type": info.device_type,
            "uid": info.uid, "firmware": info.firmware,
            "active_profile": pv.active,
            "data_versions": pv.data_versions,
            "sleep_minutes": hw.sleep_minutes,
            "joystick_precision": hw.joystick_precision,
            "joystick_sensitivity": hw.joystick_sensitivity,
            "third_party_control": st.third_party_control,
            "features": {k: {"usable": u, "enabled": e}
                         for k, (u, e) in hw.features.items()},
            "profiles": [],
        }
        for idx, blob in blobs.items():
            name = f"profile{idx}.mapping.bin"
            with open(os.path.join(outdir, name), "wb") as fh:
                fh.write(blob)
            mb = M.MappingBlob(blob)
            entry = {"index": idx, "title": mb.title,
                     "data_version": mb.data_version, "file": name}
            try:
                led = C.read_blob(ctl, C.ProfileCmd.READ_LED, idx)
                led_name = f"profile{idx}.led.bin"
                with open(os.path.join(outdir, led_name), "wb") as fh:
                    fh.write(led)
                entry["led_file"] = led_name
            except DeviceError:
                pass
            meta["profiles"].append(entry)
    with open(os.path.join(outdir, "backup.json"), "w") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    print(f"Backed up {len(meta['profiles'])} profiles to {outdir}")
    print(f"Restore with:  {os.path.basename(sys.argv[0])} restore {outdir}")
    return 0


def cmd_restore(args) -> int:
    path = os.path.abspath(args.directory)
    meta_path = os.path.join(path, "backup.json")
    if not os.path.exists(meta_path):
        raise SystemExit(f"error: {meta_path} not found")
    with open(meta_path) as fh:
        meta = json.load(fh)
    only = None if args.profile is None else args.profile - 1
    with _open(args) as ctl:
        before = C.read_profile_versions(ctl).active
        done = []
        for entry in meta["profiles"]:
            idx = entry["index"]
            if only is not None and idx != only:
                continue
            with open(os.path.join(path, entry["file"]), "rb") as fh:
                blob = fh.read()
            if args.dry_run:
                print(f"[dry run] would restore profile {idx + 1} "
                      f"({entry['title']!r}, {len(blob)} bytes)")
                continue
            C.write_mapping_blob(ctl, idx, blob)
            C.save_config(ctl, entry.get("data_version", C.UNWRITTEN))
            time.sleep(0.3)
            back = C.read_mapping_blob(ctl, idx)
            done.append((idx, back == blob))
        if C.read_profile_versions(ctl).active != before:
            C.activate_profile(ctl, before)
    for idx, ok in done:
        print(f"Profile {idx + 1} restored, byte-identical: {ok}")
    return 0 if all(ok for _, ok in done) else 1


def cmd_verify(args) -> int:
    """Compare the controller against a backup, ignoring the revision tag."""
    path = os.path.abspath(args.directory)
    meta_path = os.path.join(path, "backup.json")
    if not os.path.exists(meta_path):
        raise SystemExit(f"error: {meta_path} not found")
    with open(meta_path) as fh:
        meta = json.load(fh)
    with _open(args) as ctl:
        blobs = C.read_all_profiles(ctl)
        pv = C.read_profile_versions(ctl)
    ok = True
    for entry in meta["profiles"]:
        idx = entry["index"]
        with open(os.path.join(path, entry["file"]), "rb") as fh:
            ref = fh.read()
        live = blobs.get(idx, b"")
        diff = [i for i in range(min(len(ref), len(live))) if ref[i] != live[i]]
        dv_only = bool(diff) and all(
            i in (M.OFF_DATA_VERSION, M.OFF_DATA_VERSION + 1) for i in diff)
        title = M.MappingBlob(live).title
        if not diff:
            print(f"  profile {idx + 1} ({title}): identical")
        elif dv_only:
            print(f"  profile {idx + 1} ({title}): content identical, only the "
                  f"revision tag differs "
                  f"({entry.get('data_version')} -> {pv.data_versions[idx]})")
        else:
            ok = False
            print(f"  profile {idx + 1} ({title}): DIFFERS at {len(diff)} bytes, "
                  f"offsets {diff[:8]}")
    print()
    if ok:
        print("All mappings match the backup.")
        print("The revision tag is bumped by the controller itself whenever a "
              "setting is changed on the device — for example the FN chords "
              "for vibration strength, fast profile swap or on-device macro "
              "recording. It does not affect your mappings.")
    else:
        print("Some content differs. `restore` will put it back.")
    return 0 if ok else 1


def cmd_buttons(args) -> int:
    print(f"{'name':<16}{'id':<6}physical")
    for key_id in sorted(M.KEY_NAMES):
        phys = "yes" if key_id in M.PHYSICAL_KEYS else ""
        print(f"  {M.KEY_NAMES[key_id]:<14}{key_id:<6}{phys}")
    return 0


# ---------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flydigi-cli",
        description="Manage Flydigi controllers on Linux.")
    p.add_argument("--trace", action="store_true",
                   help="print every HID frame sent and received")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_text, **kw):
        sp = sub.add_parser(name, help=help_text, **kw)
        sp.set_defaults(func=fn)
        return sp

    add("list", cmd_list, "show the controller's HID interfaces")
    add("info", cmd_info, "identity, battery and firmware versions")
    add("status", cmd_status, "mapping ownership and every hardware setting")
    add("buttons", cmd_buttons, "list button names and their ids")

    tp = add("third-party", cmd_third_party,
             "allow Steam Input / reWASD to take over mapping")
    tp.add_argument("state", nargs="?", choices=["on", "off"],
                    help="omit to read the current setting")

    add("profiles", cmd_profiles, "list the four onboard profiles")

    ap = add("activate", cmd_activate, "switch the active onboard profile")
    ap.add_argument("profile", type=int, help="profile number, 1-4")

    kp = add("keys", cmd_keys, "show a profile's button mapping")
    kp.add_argument("-p", "--profile", type=int, help="profile 1-4 (default: active)")
    kp.add_argument("-a", "--all", action="store_true",
                    help="include buttons left at their defaults")

    rp = add("remap", cmd_remap, "point a button at another button")
    rp.add_argument("button")
    rp.add_argument("target", nargs="?")
    rp.add_argument("--clear", action="store_true", help="restore the default")
    rp.add_argument("-p", "--profile", type=int)
    rp.add_argument("-n", "--dry-run", action="store_true")

    tb = add("turbo", cmd_turbo, "make a button fire repeatedly")
    tb.add_argument("button")
    tb.add_argument("target", nargs="?")
    tb.add_argument("--hz", type=int, required=True, help="repeat frequency")
    tb.add_argument("--mode", choices=["press", "click"], default="press")
    tb.add_argument("-p", "--profile", type=int)
    tb.add_argument("-n", "--dry-run", action="store_true")

    rn = add("rename", cmd_rename, "rename a profile")
    rn.add_argument("title")
    rn.add_argument("-p", "--profile", type=int)
    rn.add_argument("-n", "--dry-run", action="store_true")

    ns = add("ns-bind", cmd_ns_bind,
             "make a profile the one used in Nintendo Switch mode")
    ns.add_argument("profile", type=int, help="profile number, 1-4")

    sl = add("sleep", cmd_sleep, "read or set the auto-sleep timeout")
    sl.add_argument("value", nargs="?",
                    help="minutes, '2h', or 'never' (omit to read)")

    ft = add("feature", cmd_feature, "read or toggle a hardware feature")
    ft.add_argument("name", nargs="?", help="omit to list all features")
    ft.add_argument("state", nargs="?", choices=["on", "off"])

    mn = add("monitor", cmd_monitor,
             "show live button and stick input until Ctrl-C")

    ml = add("macro-list", cmd_macro_list, "show macros stored on a profile")
    ml.add_argument("-p", "--profile", type=int)

    mr = add("macro-record", cmd_macro_record,
             "record a macro and bind it to a button")
    mr.add_argument("button")
    mr.add_argument("--name", default="")
    mr.add_argument("--type", choices=["once", "held", "toggle"],
                    default="once",
                    help="once = play on each press, held = repeat while "
                         "held, toggle = start/stop on press")
    mr.add_argument("--interval", type=int, default=0,
                    help="milliseconds between repeats")
    mr.add_argument("-p", "--profile", type=int)
    mr.add_argument("-n", "--dry-run", action="store_true")

    mc = add("macro-clear", cmd_macro_clear, "remove a button's macro")
    mc.add_argument("button")
    mc.add_argument("-p", "--profile", type=int)
    mc.add_argument("-n", "--dry-run", action="store_true")

    up = add("updates", cmd_updates,
             "show installed firmware and check for newer versions")
    up.add_argument("--offline", action="store_true",
                    help="show installed versions only, no network request")
    up.add_argument("--device-code", default="f5",
                    help="Flydigi product code (default: f5)")

    bk = add("backup", cmd_backup, "save all profiles and settings to disk")
    bk.add_argument("directory", nargs="?",
                    help="where to write it (default: backups/ next to this "
                         "script, timestamped)")

    vf = add("verify", cmd_verify, "compare the controller against a backup")
    vf.add_argument("directory")

    rs = add("restore", cmd_restore, "write a backup back to the controller")
    rs.add_argument("directory")
    rs.add_argument("-p", "--profile", type=int, help="restore only this profile")
    rs.add_argument("-n", "--dry-run", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except NotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except DeviceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
