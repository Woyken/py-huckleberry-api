package dev.local.huckleberry.a11yprobe;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import org.json.JSONObject;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

public final class ProbeProvider extends ContentProvider {
    @Override public boolean onCreate() { return true; }
    @Override public Bundle call(String method, String argument, Bundle extras) {
        if (Binder.getCallingUid() != 2000) throw new SecurityException("Android shell only");
        if (argument != null) throw new IllegalArgumentException("Unexpected argument");
        Set<String> keys;
        switch (method) {
            case "status": keys = new HashSet<>(); break;
            case "claim": keys = new HashSet<>(Arrays.asList("session", "user", "child")); break;
            case "release": keys = new HashSet<>(Arrays.asList("session")); break;
            case "action-status": keys = new HashSet<>(Arrays.asList("session", "action_id")); break;
            case "tree": keys = new HashSet<>(Arrays.asList("session", "user", "child", "task")); break;
            case "action": keys = new HashSet<>(Arrays.asList("session", "user", "child", "task", "action_id",
                "path", "id", "text", "action", "expect_id", "expect_text", "value")); break;
            case "navigate": keys = new HashSet<>(Arrays.asList("session", "user", "child", "task", "action_id", "from", "to")); break;
            default: throw new IllegalArgumentException("Unsupported method");
        }
        final Bundle request = extras == null ? new Bundle() : new Bundle(extras);
        if (!keys.equals(request.keySet())) throw new IllegalArgumentException("Unexpected or missing fields");
        final long deadline = SystemClock.uptimeMillis() + 5000;
        AtomicBoolean cancelled = new AtomicBoolean(false);
        AtomicReference<String> result = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        new Handler(Looper.getMainLooper()).post(() -> {
            try {
                JSONObject reply;
                if (cancelled.get() || SystemClock.uptimeMillis() >= deadline) reply = BridgeState.reply("request_expired");
                else {
                    BridgeState state = new BridgeState(getContext());
                    String owner = request.getString("session");
                    if ("status".equals(method)) reply = state.status();
                    else if ("claim".equals(method)) reply = state.claim(owner, request.getInt("user", -1), request.getString("child"));
                    else if ("release".equals(method)) reply = state.release(owner);
                    else if ("action-status".equals(method)) {
                        state.requireOwner(owner);
                        reply = BridgeState.reply("ok").put("consumed", state.consumed(request.getString("action_id")))
                            .put("meaning", "Consumed means dispatched-or-uncertain, not UI success");
                    }
                    else {
                        JSONObject enrollment = state.requireOwner(owner);
                        if (enrollment.getInt("user") != request.getInt("user", -1)
                                || !enrollment.getString("child").equals(request.getString("child"))) throw new IllegalStateException("target_changed");
                        ProbeService service = ProbeService.instance;
                        if (service == null) reply = BridgeState.reply("service_not_enabled");
                        else if ("tree".equals(method)) reply = service.snapshot(request.getInt("task", -1), enrollment.getString("child"));
                        else if ("action".equals(method)) reply = service.action(request.getInt("task", -1), enrollment.getString("child"),
                            request.getString("action_id"), request.getString("path"), request.getString("id"), request.getString("text"),
                            request.getString("action"), request.getString("expect_id"), request.getString("expect_text"),
                            request.getString("value"), state, deadline);
                        else reply = service.navigate(request.getInt("task", -1), enrollment.getString("child"),
                            request.getString("action_id"), request.getString("from"), request.getString("to"), state, deadline);
                    }
                }
                result.set(reply.toString());
            } catch (Exception error) {
                try { result.set(BridgeState.reply(error instanceof IllegalArgumentException || error instanceof IllegalStateException
                    ? error.getMessage() : "bridge_error").toString()); }
                catch (Exception ignored) { result.set("{\"protocol\":2,\"status\":\"bridge_error\"}"); }
            } finally { done.countDown(); }
        });
        try {
            if (!done.await(10, TimeUnit.SECONDS)) {
                cancelled.set(true);
                result.compareAndSet(null, "{\"protocol\":2,\"status\":\"timeout_uncertain\"}");
            }
        } catch (InterruptedException error) {
            cancelled.set(true);
            Thread.currentThread().interrupt();
            result.compareAndSet(null, "{\"protocol\":2,\"status\":\"interrupted_uncertain\"}");
        }
        Bundle response = new Bundle();
        response.putString("result", result.get());
        return response;
    }
    @Override public Cursor query(Uri uri, String[] columns, String selection, String[] args, String sort) { throw new UnsupportedOperationException(); }
    @Override public String getType(Uri uri) { return "application/json"; }
    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
