"""Fixed read-only private-X role metadata; never request names, images or input."""

import argparse
import ctypes
import json

try:
    from tools import qq_application_observation as application
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_application_observation as application
    import qq_ephemeral_host_entry as control

# Standard X11 predefined atom IDs and fixed budgets, not caller-selected targets.
XA_ATOM, XA_WINDOW, XA_STRING = 4, 33, 31
MAX_ATOMS, MAX_CLASS_BYTES, MAX_CHILDREN = 128, 128, 32


class WindowAttributes(ctypes.Structure):
    """Match public Xlib XWindowAttributes ABI solely to extract map_state."""

    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int),
                ("height", ctypes.c_int), ("border_width", ctypes.c_int),
                ("depth", ctypes.c_int), ("visual", ctypes.c_void_p),
                ("root", ctypes.c_ulong), ("window_class", ctypes.c_int),
                ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
                ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
                ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
                ("colormap", ctypes.c_ulong), ("map_installed", ctypes.c_int),
                ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
                ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p)]


def class_flags(value):
    """Immediately discard bounded class bytes, exposing three fixed predicates only."""
    if not isinstance(value, bytes) or len(value) > MAX_CLASS_BYTES:
        raise ValueError("class_bound_invalid")
    lowered = value.lower()
    return {"class_contains_error": b"error" in lowered,
            "class_contains_dialog": b"dialog" in lowered,
            "class_contains_qq": b"qq" in lowered}


def verified_main(identity):
    """Require the actual jailed QQ main PID/start identity and inherited safety facts."""
    return (isinstance(identity, dict) and type(identity.get("pid")) is int
            and identity.get("exe") == application.QQ_EXE
            and type(identity.get("start_ticks")) is int
            and identity.get("uid") == [65534] * 4
            and identity.get("cap_eff_zero") is True
            and identity.get("no_new_privileges") is True
            and identity.get("user_namespace_matches_supervisor") is True)


class PrivateWindowRoles(application.PrivateXIdentity):
    """Extend the existing authenticated private :99 reader, without another loader."""

    def __init__(self):
        super().__init__()
        self.lib.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                 ctypes.POINTER(WindowAttributes)]
        self.lib.XGetWindowAttributes.restype = ctypes.c_int
        # Only fixed atom names are interned; atom values remain integers internally.
        self.role_atoms = {name: self.lib.XInternAtom(self.display, name.encode("ascii"), 1)
                           for name in ("_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DIALOG",
                                        "_NET_WM_WINDOW_TYPE_NORMAL", "_NET_WM_WINDOW_TYPE_UTILITY",
                                        "WM_TRANSIENT_FOR", "WM_CLASS")}

    def children(self, window):
        """Supply bounded child IDs to inherited traversal, reading <=32 per child."""
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        success = self.lib.XQueryTree(self.display, window, ctypes.byref(root), ctypes.byref(parent),
                                      ctypes.byref(children), ctypes.byref(count))
        try:
            maximum = application.WINDOW_LIMIT if window == self.root else MAX_CHILDREN
            return [int(children[index]) for index in range(min(count.value, maximum))] if success else []
        finally:
            if children:
                self.lib.XFree(children)

    def property_value(self, window, atom, expected_type, expected_format, maximum):
        """Request one bounded prefix, validate type/format/count and always XFree."""
        if not atom:
            return None, False
        actual_type, count, remaining = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
        actual_format, data = ctypes.c_int(), ctypes.c_void_p()
        # XGetWindowProperty lengths are in four-byte units, even for format 8.
        length = (maximum + 3) // 4 if expected_format == 8 else maximum
        result = self.lib.XGetWindowProperty(
            self.display, window, atom, 0, length, 0, expected_type,
            ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(count),
            ctypes.byref(remaining), ctypes.byref(data))
        try:
            truncated = bool(remaining.value or count.value > maximum)
            if (result != 0 or actual_type.value != expected_type
                    or actual_format.value != expected_format or count.value > maximum
                    or (count.value and not data.value)):
                return None, truncated
            if expected_format == 8:
                return ctypes.string_at(data, count.value) if count.value else b"", truncated
            values = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            return [int(values[index]) for index in range(count.value)], truncated
        finally:
            if data.value:
                self.lib.XFree(data)

    def window_roles(self, window, main_windows):
        """Read allowlisted role properties for an already PID-correlated window only."""
        attributes = WindowAttributes()
        mapped = self.lib.XGetWindowAttributes(self.display, window, ctypes.byref(attributes))
        kinds, kind_truncated = self.property_value(
            window, self.role_atoms["_NET_WM_WINDOW_TYPE"], XA_ATOM, 32, MAX_ATOMS)
        transient, transient_truncated = self.property_value(
            window, self.role_atoms["WM_TRANSIENT_FOR"], XA_WINDOW, 32, 1)
        raw_class, class_truncated = self.property_value(
            window, self.role_atoms["WM_CLASS"], XA_STRING, 8, MAX_CLASS_BYTES)
        kind_set = set(kinds or [])
        known = {self.role_atoms["_NET_WM_WINDOW_TYPE_" + label]
                 for label in ("DIALOG", "NORMAL", "UTILITY")} - {0}
        # Empty or malformed properties remain unknown; no title-based inference.
        result = {"map_state": ({0: "unmapped", 1: "unviewable", 2: "viewable"}.get(
                      attributes.map_state, "unknown") if mapped else "unknown"),
                  "type_dialog": bool(self.role_atoms["_NET_WM_WINDOW_TYPE_DIALOG"] in kind_set),
                  "type_normal": bool(self.role_atoms["_NET_WM_WINDOW_TYPE_NORMAL"] in kind_set),
                  "type_utility": bool(self.role_atoms["_NET_WM_WINDOW_TYPE_UTILITY"] in kind_set),
                  "type_unknown": kinds is None or not kind_set or bool(kind_set - known),
                  "transient_for_present": bool(transient and transient[0]),
                  "transient_for_main_correlated_window": bool(transient and transient[0] in main_windows),
                  "class_property_valid": raw_class is not None,
                  "property_truncated": {"window_type": kind_truncated,
                                         "transient_for": transient_truncated, "class": class_truncated}}
        result.update(class_flags(raw_class or b""))
        return result

    def role_snapshot(self, main_identity):
        """Reuse snapshot IDs internally and publish counts/booleans, never IDs or strings."""
        if not verified_main(main_identity):
            raise ValueError("main_identity_invalid")
        if application.process_identity(main_identity["pid"]) != main_identity:
            raise ValueError("main_identity_changed")
        snapshot = self.snapshot()
        matching = [record for record in snapshot["qq_pid_properties"]
                    if record["identity"] == main_identity]
        windows = {record["window_id"] for record in matching}
        roles = [self.window_roles(window, windows) for window in sorted(windows)]
        if application.process_identity(main_identity["pid"]) != main_identity:
            raise ValueError("main_identity_changed")
        return {"root_children": snapshot["root_children"], "visited_windows": snapshot["visited_windows"],
                "main_pid_correlated_windows": len(roles), "roles_queried": True,
                "roles": roles, "main_owned_dialog_observed": any(
                    role["type_dialog"] or role["transient_for_main_correlated_window"] for role in roles),
                "modal_state_proven": False, "root_cause_proven": False,
                "pid_attribution": "client_supplied_pid_correlated_with_actual_main_pid_and_start_ticks"}


def sample_roles(main_identity):
    """Use the existing UID65534 callback context and fixed display, closing every sample."""
    if not verified_main(main_identity):
        raise ValueError("main_identity_invalid")
    reader = PrivateWindowRoles()
    try:
        return reader.role_snapshot(main_identity)
    finally:
        reader.close()


def observe(qq_root, *, execute=False):
    """Select the fixed shared vendor bootstrap/waiter, not an arbitrary diagnostic."""
    return control.observe(qq_root, execute=execute, control_mode="vendor_window_roles")


def main(argv=None):
    """Accept only software root and explicit consent, never PID/display/input targets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = observe(args.qq_root, execute=args.execute)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
