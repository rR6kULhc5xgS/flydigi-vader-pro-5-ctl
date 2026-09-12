"""Explanatory text for each setting.

Wording is taken verbatim from the Windows app's own English translation
(`locales/en/translation.json`) so the descriptions match what the vendor
documents the feature to do. The translation key is noted for each entry.
"""

from __future__ import annotations

#: feature label (as used in commands.FEATURE_TABLE) -> (summary, caveat)
FEATURE_HELP: dict[str, tuple[str, str]] = {
    "Fast swap config": (
        # setting_controller_config_quick_switch_desc / fast_switch_tip
        "The controller can carry four onboard configurations. With this on, "
        "press FN + A/B/X/Y to switch straight to configuration 1/2/3/4.",
        "",
    ),
    "Xbox Home button": (
        "Makes the Home button report as an Xbox Guide button, so software "
        "that expects an Xbox controller reacts to it.",
        "",
    ),
    "Motion debounce": (
        # motion_mapping_warning
        "Filters jitter out of the gyro/motion sensor readings.",
        "Gyro mapping reduces the controller's polling rate.",
    ),
    "Turbo function": (
        # turbo_mapping_title, EnableMappingSwitch
        "Master switch for the controller's own turbo (rapid-fire) mapping. "
        "Turbo assignments in a profile only take effect while this is on.",
        "",
    ),
    "Joystick debounce": (
        # setting_controller_joystick_debounce_tip
        "Anti-shake algorithm for the thumbsticks. Disabling it makes the "
        "sticks more responsive to subtle movements but significantly "
        "increases data jitter when they are at rest.",
        # disable_joy_debounce_desc
        "Turning this off also prevents automatic calibration from working — "
        "debounce must be back on for calibration to run.",
    ),
    "Joystick auto-calibration": (
        # setting_controller_joystick_automatic_calibration_tip
        "When at rest, the thumbstick automatically recalculates its centre "
        "point, improving centre-return accuracy.",
        "Requires joystick debounce to be enabled.",
    ),
    "Joystick rebound": (
        # setting_controller_joystick_rebound_algorithm_tip
        "Filters out reverse input caused by the physical inertia of the "
        "thumbstick when you let go of it.",
        "",
    ),
    "Screen status bar always on": (
        # screen_status_bar_display_desc
        "Keeps the controller screen's status bar visible.",
        "With this off you can still bring the status bar up briefly by "
        "lightly pressing the Home key.",
    ),
    "Off-screen": (
        # screen_switch_desc
        "Turns the controller's screen animation off. The screen settings "
        "themselves remain usable.",
        "",
    ),
    "Audio switch": (
        # setting_controller_audio
        "Enables the controller's audio output.",
        "",
    ),
}

#: Other settings, keyed by an internal name.
SETTING_HELP: dict[str, tuple[str, str]] = {
    "third_party": (
        # control_by_third_party_app_description
        "When this is on and a third-party application (such as Steam or "
        "reWASD) is open, that application takes over the controller "
        "mapping, and the controller's own settings stop applying.",
        "This applies even if Steam or reWASD is only running in the "
        "background. The controller also stops reporting its own XInput "
        "data while it is enabled. Needs firmware 7.1.4.1 or newer.",
    ),
    "sleep": (
        # setting_controller_sleep_desc
        "If the controller is not used for this long it enters sleep mode. "
        "Press the Home button to wake it.",
        "",
    ),
    "precision": (
        # setting_controller_joystick_accuracy_tip
        "Sets the minimum data step size for thumbstick movement. Higher "
        "precision gives a smaller step size.",
        "",
    ),
    "sensitivity": (
        # setting_controller_joystick_center_sensitivity_tip
        "Affects thumbstick sensitivity around the centre zone.",
        "",
    ),
    "report_rate": (
        # setting_controller_joystick_polling_rate_tip
        "How often the thumbsticks report their position. This does not "
        "affect battery life.",
        "",
    ),
    "ns_mode": (
        # apply_switch_config / loading_message_apply_switch_config
        "Makes this profile the one the controller uses when it is switched "
        "into Nintendo Switch mode.",
        "The binding cannot be read back over USB while the controller is in "
        "XInput mode, so verify it by switching the controller to NS mode.",
    ),
    "onboard_profile": (
        # replace_profile_tip / reset_mapping_config_desc
        "The controller stores four configurations on itself, so they work "
        "on any machine without software running.",
        "Writing a profile commits it to the controller's flash.",
    ),
    "turbo_key": (
        # mapping_config_key_map_type_continuous
        "Turbo repeatedly presses the chosen button while the physical "
        "button is held, or until pressed again in toggle mode.",
        "The Turbo Function switch in Settings must be on.",
    ),
    "macro": (
        # macro_mapping_title / macro_mapping_record_step1
        "A macro plays back a recorded sequence of button presses with their "
        "timings. Macros are stored on the controller and run on it, so "
        "nothing needs to run on this computer.",
        "Macros can only emit controller buttons. Keyboard and mouse output "
        "requires host software and is not supported here.",
    ),
    "keyboard_mapping": (
        "This button is mapped to a keyboard or mouse output. The controller "
        "stores only a marker for it; the actual keystroke is produced by "
        "software on the host.",
        "There is no host-side injector here, so this mapping is shown but "
        "cannot be edited or acted on.",
    ),
}


def feature(label: str) -> tuple[str, str]:
    return FEATURE_HELP.get(label, ("", ""))


def setting(name: str) -> tuple[str, str]:
    return SETTING_HELP.get(name, ("", ""))


def tooltip(summary: str, caveat: str = "") -> str:
    """Build a wrapped tooltip string."""
    parts = [summary]
    if caveat:
        parts.append(caveat)
    return "\n\n".join(p for p in parts if p)
