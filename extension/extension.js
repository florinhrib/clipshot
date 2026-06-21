// ClipShot Helper — GNOME Shell extension (UUID clipshot@florinlab.uk)
//
// WHY: ClipShot's Python app captures via the XDG desktop portal, which works
// everywhere but (a) cannot keep an open menu/popup on screen and (b) gives no
// per-window geometry. This extension is an OPTIONAL fidelity layer: it owns the
// session bus name `uk.florinlab.ClipShot` and exposes the Shell's *internal*
// screenshot API (Shell.Screenshot) so the app can grab the whole screen, the
// active window (with its frame rect), or an arbitrary area at compositor level.
//
// It must never crash the shell: every method is wrapped, async screenshot calls
// return their D-Bus reply only once the PNG is on disk, and enable()/disable()
// own/unown the name and export/unexport the object with no leaks.

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'uk.florinlab.ClipShot';
const OBJECT_PATH = '/uk/florinlab/ClipShot';

// The D-Bus interface. Signatures match clipshot/capture.py exactly:
//   CaptureScreen()       -> (s pngPath)
//   CaptureActiveWindow() -> (s path, i x, i y, i w, i h)
//   SelectArea()          -> (i x, i y, i w, i h)
const IFACE_XML = `
<node>
  <interface name="uk.florinlab.ClipShot">
    <method name="CaptureScreen">
      <arg type="s" direction="out" name="pngPath"/>
    </method>
    <method name="CaptureActiveWindow">
      <arg type="s" direction="out" name="path"/>
      <arg type="i" direction="out" name="x"/>
      <arg type="i" direction="out" name="y"/>
      <arg type="i" direction="out" name="w"/>
      <arg type="i" direction="out" name="h"/>
    </method>
    <method name="SelectArea">
      <arg type="i" direction="out" name="x"/>
      <arg type="i" direction="out" name="y"/>
      <arg type="i" direction="out" name="w"/>
      <arg type="i" direction="out" name="h"/>
    </method>
  </interface>
</node>`;

// Build a fresh temp PNG path under the runtime/tmp dir. The Python side treats
// these as transients and cleans them up (see capture.cleanup_capture).
function _newTmpPath() {
    const dir = GLib.get_tmp_dir();
    const name = `clipshot-shell-${GLib.get_monotonic_time()}.png`;
    return GLib.build_filenamev([dir, name]);
}

// Open a writable PNG stream at path, replacing any existing file.
function _openStream(path) {
    const file = Gio.File.new_for_path(path);
    // (etag, make_backup, flags, cancellable)
    return file.replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
}

export default class ClipShotHelperExtension extends Extension {
    enable() {
        this._screenshot = new Shell.Screenshot();
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._dbusImpl.export(Gio.DBus.session, OBJECT_PATH);

        this._ownerId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            null, // bus acquired — object already exported above
            null, // name acquired
            null  // name lost
        );
    }

    disable() {
        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = 0;
        }
        if (this._dbusImpl) {
            try {
                this._dbusImpl.unexport();
            } catch (e) {
                // best effort — never throw out of disable()
            }
            this._dbusImpl = null;
        }
        this._screenshot = null;
    }

    // --- D-Bus methods (async variants so we reply only after the PNG lands) ---
    //
    // wrapJSObject dispatches to `<Method>Async(params, invocation)` when present,
    // letting us defer invocation.return_value(...) until the async capture is done.

    CaptureScreenAsync(_params, invocation) {
        let path, stream;
        try {
            path = _newTmpPath();
            stream = _openStream(path);
        } catch (e) {
            invocation.return_error_literal(
                Gio.DBusError, Gio.DBusError.FAILED,
                `CaptureScreen: could not open output file: ${e}`);
            return;
        }

        // screenshot(include_cursor, stream, callback) -> screenshot_finish(res)
        this._screenshot.screenshot(false, stream, (obj, res) => {
            try {
                obj.screenshot_finish(res);
                stream.close(null);
                invocation.return_value(new GLib.Variant('(s)', [path]));
            } catch (e) {
                try { stream.close(null); } catch (_e) {}
                invocation.return_error_literal(
                    Gio.DBusError, Gio.DBusError.FAILED,
                    `CaptureScreen failed: ${e}`);
            }
        });
    }

    CaptureActiveWindowAsync(_params, invocation) {
        const win = global.display.get_focus_window();
        if (!win) {
            invocation.return_error_literal(
                Gio.DBusError, Gio.DBusError.FAILED,
                'CaptureActiveWindow: no focused window');
            return;
        }

        const rect = win.get_frame_rect();
        let path, stream;
        try {
            path = _newTmpPath();
            stream = _openStream(path);
        } catch (e) {
            invocation.return_error_literal(
                Gio.DBusError, Gio.DBusError.FAILED,
                `CaptureActiveWindow: could not open output file: ${e}`);
            return;
        }

        // screenshot_window(include_frame, include_cursor, stream, callback)
        this._screenshot.screenshot_window(true, false, stream, (obj, res) => {
            try {
                obj.screenshot_window_finish(res);
                stream.close(null);
                invocation.return_value(new GLib.Variant(
                    '(siiii)', [path, rect.x, rect.y, rect.width, rect.height]));
            } catch (e) {
                try { stream.close(null); } catch (_e) {}
                invocation.return_error_literal(
                    Gio.DBusError, Gio.DBusError.FAILED,
                    `CaptureActiveWindow failed: ${e}`);
            }
        });
    }

    SelectAreaAsync(_params, invocation) {
        // Shell.Screenshot has an interactive area picker, but its UI plumbing is
        // version-fragile and the Python app already ships a full region selector
        // over a frozen frame. Returning a zero rect tells the caller "no shell
        // selection happened" so it falls back to its own selector — which is the
        // intended behaviour. Do not block on this.
        // TODO: optionally wire up Main.screenshotUI's area selection to return a
        // real rect here if a compositor-level crosshair is ever wanted.
        invocation.return_value(new GLib.Variant('(iiii)', [0, 0, 0, 0]));
    }
}
