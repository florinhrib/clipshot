"""System-tray icon via the StatusNotifierItem (SNI) protocol.

GNOME 50 dropped the legacy XEmbed/AppIndicator tray, and `libappindicator`
is GTK3-only — loading its typelib into our GTK4 process would mix two
incompatible GTK majors in one address space and abort.  So instead of any
library we speak the wire protocol directly: we publish an
`org.kde.StatusNotifierItem` object plus its `com.canonical.dbusmenu` on the
session bus with raw ``Gio.DBusConnection.register_object``, and ask the
running ``org.kde.StatusNotifierWatcher`` to adopt it.  The already-installed
``appindicatorsupport@rgcjonas.gmail.com`` GNOME extension is the host that
renders the icon in the top bar.

Everything here is best-effort: the daemon must keep running even if no tray
host is present, so all registration is wrapped and failures only log.
"""
from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

WATCHER_BUS = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"

SNI_IFACE = "org.kde.StatusNotifierItem"
SNI_PATH = "/StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"

ICON_NAME = "camera-photo-symbolic"

# Menu definition: (label, action) pairs; None == separator.  Order matters.
MENU_ITEMS: list[tuple[str, str] | None] = [
    ("Capture Region", "capture-region"),
    ("Capture Fullscreen", "capture-fullscreen"),
    ("Capture Window", "capture-window"),
    ("Extract Text (OCR)", "capture-ocr"),
    ("Repeat Last", "capture-previous"),
    None,
    ("History", "show-history"),
    ("Settings", "show-settings"),
    ("About", "about"),
    None,
    ("Quit", "quit"),
]

_SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="updatesNeeded" type="ai" direction="out"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg name="id" type="i"/>
      <arg name="timestamp" type="u"/>
    </signal>
  </interface>
</node>
"""


class Tray:
    """Publish a StatusNotifierItem + dbusmenu for the ClipShot daemon.

    Construction never raises: any failure during setup is logged to stderr and
    leaves the tray inert, so the daemon keeps running headless.
    """

    def __init__(self, app):
        self.app = app
        self._conn: Gio.DBusConnection | None = None
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._owner_id = 0
        self._sni_reg = 0
        self._menu_reg = 0
        self._revision = 1
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._sni_info = Gio.DBusNodeInfo.new_for_xml(_SNI_XML).interfaces[0]
            self._menu_info = Gio.DBusNodeInfo.new_for_xml(_MENU_XML).interfaces[0]
            self._owner_id = Gio.bus_own_name_on_connection(
                self._conn,
                self._bus_name,
                Gio.BusNameOwnerFlags.NONE,
                self._on_name_acquired,
                self._on_name_lost,
            )
        except Exception as exc:  # noqa: BLE001 — best effort
            print(f"[clipshot] tray init failed: {exc}", file=sys.stderr)

    # --- name lifecycle --------------------------------------------------
    def _on_name_acquired(self, _conn, name):
        try:
            self._export_objects()
            self._register_with_watcher()
        except Exception as exc:  # noqa: BLE001
            print(f"[clipshot] tray export failed: {exc}", file=sys.stderr)

    def _on_name_lost(self, _conn, name):
        print(f"[clipshot] tray lost bus name {name}", file=sys.stderr)

    def _export_objects(self):
        assert self._conn is not None
        self._sni_reg = self._conn.register_object(
            SNI_PATH, self._sni_info, self._sni_method, self._sni_get, None,
        )
        self._menu_reg = self._conn.register_object(
            MENU_PATH, self._menu_info, self._menu_method, self._menu_get, None,
        )

    def _register_with_watcher(self):
        assert self._conn is not None
        # The rgcjonas/KDE watcher accepts a bus name as the service string and
        # pairs it with the conventional /StatusNotifierItem object path.
        self._conn.call(
            WATCHER_BUS, WATCHER_PATH, WATCHER_IFACE,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self._bus_name,)),
            None, Gio.DBusCallFlags.NONE, 5000, None,
            self._on_registered,
        )

    def _on_registered(self, conn, res):
        try:
            conn.call_finish(res)
            print(f"[clipshot] tray registered as {self._bus_name}", file=sys.stderr)
        except GLib.Error as exc:
            print(f"[clipshot] tray RegisterStatusNotifierItem failed: {exc.message}",
                  file=sys.stderr)

    # --- StatusNotifierItem ---------------------------------------------
    def _sni_get(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "clipshot"),
            "Title": GLib.Variant("s", "ClipShot"),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", ICON_NAME),
            "IconThemePath": GLib.Variant("s", ""),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (ICON_NAME, [], "ClipShot", "")),
            "ItemIsMenu": GLib.Variant("b", True),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(prop)

    def _sni_method(self, _conn, _sender, _path, _iface, method, params,
                    invocation):
        # ItemIsMenu=True means clicks should pop the menu (handled by the host),
        # but honour an explicit Activate as a region capture for convenience.
        if method == "Activate":
            self._activate("capture-region")
            invocation.return_value(None)
        elif method in ("ContextMenu", "SecondaryActivate", "Scroll"):
            invocation.return_value(None)
        else:
            invocation.return_value(None)

    # --- com.canonical.dbusmenu -----------------------------------------
    def _menu_get(self, _conn, _sender, _path, _iface, prop):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(prop)

    def _item_props(self, index: int) -> dict:
        """Plain {str: GLib.Variant} property map for a menu item (1-based id).

        Returned unwrapped so it can be fed straight into an ``a{sv}`` slot of a
        larger ``GLib.Variant`` (passing an already-built ``a{sv}`` Variant there
        makes PyGObject try to re-parse it as a dict and fail).
        """
        item = MENU_ITEMS[index - 1]
        if item is None:
            return {"type": GLib.Variant("s", "separator")}
        label, _action = item
        return {
            "label": GLib.Variant("s", label),
            "enabled": GLib.Variant("b", True),
            "visible": GLib.Variant("b", True),
        }

    def _layout(self) -> GLib.Variant:
        """Root layout node: id 0 with one child per menu item.

        Returns the bare ``(ia{sv}av)`` value; callers wrap it for GetLayout.
        """
        children = []
        for i in range(1, len(MENU_ITEMS) + 1):
            child = GLib.Variant("(ia{sv}av)", (i, self._item_props(i), []))
            children.append(GLib.Variant("v", child))
        root_props = {"children-display": GLib.Variant("s", "submenu")}
        return GLib.Variant("(ia{sv}av)", (0, root_props, children))

    def _menu_method(self, _conn, _sender, _path, _iface, method, params,
                     invocation):
        if method == "GetLayout":
            # out: (u(ia{sv}av))
            reply = GLib.Variant.new_tuple(
                GLib.Variant("u", self._revision), self._layout())
            invocation.return_value(reply)
        elif method == "GetGroupProperties":
            ids, _names = params.unpack()
            entries = []  # list of plain (id, {str: Variant}) tuples
            for item_id in ids:
                if item_id == 0:
                    props = {"children-display": GLib.Variant("s", "submenu")}
                elif 1 <= item_id <= len(MENU_ITEMS):
                    props = self._item_props(item_id)
                else:
                    continue
                entries.append((item_id, props))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (entries,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            props = (self._item_props(item_id)
                     if 1 <= item_id <= len(MENU_ITEMS) else {})
            value = props.get(name) or GLib.Variant("s", "")
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("v", value)))
        elif method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id == "clicked" and 1 <= item_id <= len(MENU_ITEMS):
                item = MENU_ITEMS[item_id - 1]
                if item is not None:
                    self._activate(item[1])
            invocation.return_value(None)
        elif method == "EventGroup":
            (events,) = params.unpack()
            for item_id, event_id, _data, _ts in events:
                if event_id == "clicked" and 1 <= item_id <= len(MENU_ITEMS):
                    item = MENU_ITEMS[item_id - 1]
                    if item is not None:
                        self._activate(item[1])
            invocation.return_value(
                GLib.Variant.new_tuple(GLib.Variant("ai", [])))
        elif method == "AboutToShow":
            invocation.return_value(
                GLib.Variant.new_tuple(GLib.Variant("b", False)))
        elif method == "AboutToShowGroup":
            (ids,) = params.unpack()
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    # --- helpers ---------------------------------------------------------
    def _activate(self, action: str):
        try:
            self.app.activate_action(action, None)
        except Exception as exc:  # noqa: BLE001
            print(f"[clipshot] tray action {action} failed: {exc}", file=sys.stderr)

    def emit_layout_updated(self):
        """Signal the host that the menu changed (bump revision + LayoutUpdated)."""
        if self._conn is None:
            return
        self._revision += 1
        try:
            self._conn.emit_signal(
                None, MENU_PATH, MENU_IFACE, "LayoutUpdated",
                GLib.Variant("(ui)", (self._revision, 0)))
        except Exception as exc:  # noqa: BLE001
            print(f"[clipshot] tray LayoutUpdated failed: {exc}", file=sys.stderr)

    def set_status(self, status: str):
        """Update SNI Status (Active/Passive/NeedsAttention) and notify the host."""
        if self._conn is None:
            return
        self._status = status
        try:
            self._conn.emit_signal(
                None, SNI_PATH, SNI_IFACE, "NewStatus",
                GLib.Variant("(s)", (status,)))
        except Exception as exc:  # noqa: BLE001
            print(f"[clipshot] tray NewStatus failed: {exc}", file=sys.stderr)

    def emit_new_icon(self):
        """Notify the host that the icon changed."""
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(None, SNI_PATH, SNI_IFACE, "NewIcon", None)
        except Exception as exc:  # noqa: BLE001
            print(f"[clipshot] tray NewIcon failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    # Standalone smoke test: publish the tray against a fake app and confirm the
    # watcher adopts it.  Run from the repo root:  python3 -m clipshot.tray
    class _FakeApp:
        def activate_action(self, name, _param):
            print(f"[fake-app] activate_action({name!r})")

    loop = GLib.MainLoop()
    tray = Tray(_FakeApp())

    def _check():
        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        try:
            reply = conn.call_sync(
                WATCHER_BUS, WATCHER_PATH, "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", (WATCHER_IFACE, "RegisteredStatusNotifierItems")),
                GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
            (items,) = reply.unpack()
            mine = any(tray._bus_name in str(i) for i in items)
            print(f"[clipshot] watcher RegisteredStatusNotifierItems: {list(items)}")
            print(f"[clipshot] our item registered: {mine}")
        except GLib.Error as exc:
            print(f"[clipshot] could not query watcher: {exc.message}")
        return False

    GLib.timeout_add(1500, _check)
    GLib.timeout_add_seconds(5, lambda: (loop.quit(), False)[1])
    print("[clipshot] tray smoke test running for ~5s…")
    loop.run()
