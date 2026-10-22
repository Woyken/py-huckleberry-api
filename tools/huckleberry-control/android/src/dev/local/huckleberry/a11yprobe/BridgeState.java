package dev.local.huckleberry.a11yprobe;

import android.content.Context;
import android.system.Os;
import android.system.OsConstants;
import android.util.AtomicFile;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/** Accessed only on the app main thread. No TTL, PID-based stealing or automatic recovery. */
final class BridgeState {
    private final File directory;
    private final AtomicFile session;
    BridgeState(Context context) {
        directory = new File(context.getFilesDir(), "bridge-protocol2");
        if (!directory.isDirectory() && !directory.mkdir()) throw new IllegalStateException("state_unavailable");
        session = new AtomicFile(new File(directory, "session.json"));
    }
    static JSONObject reply(String status) throws Exception {
        return new JSONObject().put("protocol", 2).put("status", status);
    }
    static String id(String value, int minimum) {
        if (value == null || value.length() < minimum || value.length() > 80
                || !value.matches("[A-Za-z0-9][A-Za-z0-9_-]*")) throw new IllegalArgumentException("invalid_identifier");
        return value;
    }
    private JSONObject owner() throws Exception {
        if (!session.getBaseFile().exists()) return null;
        JSONObject value = new JSONObject(new String(session.readFully(), StandardCharsets.UTF_8));
        id(value.getString("session"), 3);
        if (value.getInt("user") < 0 || value.getString("child").isEmpty()) throw new IllegalStateException("corrupt_session");
        return value;
    }
    JSONObject status() throws Exception {
        JSONObject owner = owner();
        return reply("ok").put("serviceConnected", ProbeService.instance != null)
            .put("owner", owner == null ? JSONObject.NULL : owner.getString("session"))
            .put("capabilities", new JSONObject().put("inspect", true).put("semanticActions", true)
                .put("actions", new JSONArray().put("click").put("long-click").put("set-text")
                    .put("scroll-forward").put("scroll-backward").put("focus").put("select").put("back"))
                .put("setText", true).put("trackingWrites", false).put("screenshots", false).put("gestures", false));
    }
    JSONObject claim(String name, int user, String child) throws Exception {
        id(name, 3);
        if (user < 0 || child == null || child.trim().isEmpty() || !child.equals(child.trim())
                || child.length() > 128 || child.matches("(?s).*[\\x00-\\x1f\\x7f].*")) throw new IllegalArgumentException("invalid_target");
        JSONObject current = owner();
        if (current != null) {
            if (!name.equals(current.getString("session"))) return reply("claimed_by_another_session");
            if (user != current.getInt("user") || !child.equals(current.getString("child"))) return reply("target_changed");
            return reply("ok").put("owner", name);
        }
        byte[] bytes = new JSONObject().put("session", name).put("user", user).put("child", child)
            .toString().getBytes(StandardCharsets.UTF_8);
        FileOutputStream stream = session.startWrite();
        try { stream.write(bytes); session.finishWrite(stream); }
        catch (Exception error) { session.failWrite(stream); throw error; }
        syncDirectory();
        return reply("ok").put("owner", name);
    }
    JSONObject requireOwner(String name) throws Exception {
        id(name, 3);
        JSONObject current = owner();
        if (current == null || !name.equals(current.getString("session"))) throw new IllegalStateException("session_not_owned");
        return current;
    }
    JSONObject release(String name) throws Exception {
        requireOwner(name);
        session.delete();
        syncDirectory();
        return reply("ok").put("released", name).put("appLeftRunning", true);
    }
    boolean consumed(String actionId) throws Exception {
        // Preserve suppression across the protocol-1 upgrade and the earlier
        // local navigation probe. A new state directory must not make old IDs reusable.
        id(actionId, 8);
        File legacy = new File(directory.getParentFile(), "bridge-protocol1");
        return actionFile(actionId).exists() || actionFile(legacy, actionId).exists()
            || new File(directory.getParentFile(), "navigation-journal/" + actionId).exists();
    }
    boolean consume(String actionId) throws Exception {
        File file = actionFile(actionId);
        if (!file.createNewFile()) return false;
        try (FileOutputStream stream = new FileOutputStream(file)) {
            stream.write("dispatching-or-uncertain\n".getBytes(StandardCharsets.UTF_8));
            stream.getFD().sync();
        }
        syncDirectory();
        return true;
    }
    private File actionFile(String actionId) throws Exception {
        return actionFile(directory, actionId);
    }
    private File actionFile(File root, String actionId) throws Exception {
        id(actionId, 8);
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(actionId.getBytes(StandardCharsets.UTF_8));
        StringBuilder hex = new StringBuilder();
        for (byte value : digest) hex.append(String.format("%02x", value & 255));
        return new File(root, "action-" + hex);
    }
    private void syncDirectory() throws Exception {
        FileDescriptor descriptor = Os.open(directory.getPath(), OsConstants.O_RDONLY, 0);
        try { Os.fsync(descriptor); } finally { Os.close(descriptor); }
    }
}
