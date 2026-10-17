package dev.local.huckleberry.a11yprobe;

import android.accessibilityservice.AccessibilityService;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class ProbeService extends AccessibilityService {
    static volatile ProbeService instance;
    private static final String PACKAGE = "com.huckleberry_labs.app";
    private static final Pattern TASK = Pattern.compile("\\btaskId=(-?\\d+)");
    @Override protected void onServiceConnected() { instance = this; }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) { /* Never log event contents. */ }
    @Override public void onInterrupt() { }
    @Override public void onDestroy() { if (instance == this) instance = null; super.onDestroy(); }

    private static int task(AccessibilityWindowInfo window) {
        Matcher match = TASK.matcher(window.toString());
        return match.find() ? Integer.parseInt(match.group(1)) : -1;
    }
    private AccessibilityWindowInfo window(int expectedTask) {
        if (expectedTask <= 0) throw new IllegalArgumentException("task_required");
        AccessibilityWindowInfo found = null;
        for (AccessibilityWindowInfo candidate : getWindows()) {
            if (candidate.isFocused() && candidate.isActive() && task(candidate) == expectedTask) {
                if (found != null) throw new IllegalStateException("ambiguous_window");
                found = candidate;
            }
        }
        if (found == null) throw new IllegalStateException("focused_task_not_found");
        return found;
    }
    private AccessibilityNodeInfo root(AccessibilityWindowInfo window) {
        AccessibilityNodeInfo root = window.getRoot();
        if (root == null) throw new IllegalStateException("null_root");
        if (!PACKAGE.contentEquals(root.getPackageName() == null ? "" : root.getPackageName())) {
            root.recycle(); throw new IllegalStateException("wrong_package");
        }
        return root;
    }
    private static String tab(String id) {
        if ("tab-button-home".equals(id)) return "home";
        if ("tab-button-stat-tabs".equals(id)) return "reports";
        return null;
    }
    private static String clipped(CharSequence text) {
        return text == null ? null : text.subSequence(0, Math.min(256, text.length())).toString();
    }
    private static final class Scan implements AutoCloseable {
        final ArrayList<JSONObject> content = new ArrayList<>(), navigation = new ArrayList<>();
        final ArrayList<AccessibilityNodeInfo> targets = new ArrayList<>();
        final Set<String> selectedTabs = new HashSet<>();
        int visited, exported, childMatches;
        boolean incomplete, form;
        String selectedTab() { return selectedTabs.size() == 1 ? selectedTabs.iterator().next() : null; }
        @Override public void close() { for (AccessibilityNodeInfo target : targets) target.recycle(); }
    }
    private void scan(AccessibilityNodeInfo node, String path, int depth, String ancestorTab,
                      boolean inNavigation, String child, String target, Scan result) throws Exception {
        if (result.visited >= 4000 || depth > 45) { result.incomplete = true; return; }
        result.visited++;
        if (!PACKAGE.contentEquals(node.getPackageName() == null ? "" : node.getPackageName())) return;
        String id = node.getViewIdResourceName();
        String currentTab = tab(id);
        if (currentTab == null) currentTab = ancestorTab;
        boolean navigation = inNavigation || "mainTabs".equals(id);
        boolean visible = node.isVisibleToUser();
        // Ionic retains entire hidden pages and off-screen list subtrees. This
        // tool inspects the currently exposed UI, never cached invisible pages.
        // If a provider hides an ancestor inconsistently, missing controls cause
        // refusal rather than a coordinate/first-match fallback.
        if (!visible) return;
        result.exported++;
        if (visible) {
            if (node.isSelected() && currentTab != null) result.selectedTabs.add(currentTab);
            if (child.contentEquals(node.getText() == null ? "" : node.getText())
                    && node.isClickable() && "android.widget.Button".contentEquals(node.getClassName())) result.childMatches++;
            if (node.isEditable() || "Save".equals(node.getText() == null ? "" : node.getText().toString().trim())) result.form = true;
            if (target != null && target.equals(tab(id)) && node.isEnabled() && node.isClickable()) {
                result.targets.add(AccessibilityNodeInfo.obtain(node));
            }
        }
        boolean redact = node.isPassword() || node.isEditable();
        JSONArray actions = new JSONArray();
        for (AccessibilityNodeInfo.AccessibilityAction action : node.getActionList()) actions.put(action.getId());
        JSONObject row = new JSONObject().put("path", path).put("className", String.valueOf(node.getClassName()))
            .put("id", id == null ? JSONObject.NULL : id).put("visible", visible).put("enabled", node.isEnabled())
            .put("clickable", node.isClickable()).put("selected", node.isSelected()).put("editable", node.isEditable())
            .put("text", redact || node.getText() == null ? JSONObject.NULL : clipped(node.getText()))
            .put("description", redact || node.getContentDescription() == null ? JSONObject.NULL : clipped(node.getContentDescription()))
            .put("actions", actions);
        // Keep bounded content but inspect all guard nodes; navigation is exported first.
        ArrayList<JSONObject> output = navigation ? result.navigation : result.content;
        if (output.size() < 600) output.add(row);
        for (int index = 0; index < node.getChildCount(); index++) {
            if (result.visited >= 4000) { result.incomplete = true; break; }
            AccessibilityNodeInfo next = node.getChild(index);
            if (next == null) continue;
            try { scan(next, path + "/" + index, depth + 1, currentTab, navigation, child, target, result); }
            finally { next.recycle(); }
        }
    }
    JSONObject snapshot(int expectedTask, String child) throws Exception {
        AccessibilityWindowInfo selected = window(expectedTask);
        AccessibilityNodeInfo root = root(selected);
        try (Scan result = new Scan()) {
            scan(root, "root", 0, null, false, child, null, result);
            if (window(expectedTask).getId() != selected.getId()) return BridgeState.reply("focus_changed");
            JSONArray nodes = new JSONArray();
            for (JSONObject row : result.navigation) if (nodes.length() < 600) nodes.put(row);
            for (JSONObject row : result.content) if (nodes.length() < 600) nodes.put(row);
            return BridgeState.reply("ok").put("task", expectedTask).put("window", selected.getId())
                .put("nodes", nodes).put("truncated", result.incomplete || result.exported > nodes.length())
                .put("guardComplete", !result.incomplete).put("childVerified", result.childMatches == 1)
                .put("selectedTab", result.selectedTab() == null ? JSONObject.NULL : result.selectedTab());
        } finally { root.recycle(); }
    }
    JSONObject navigate(int expectedTask, String child, String actionId, String from, String to,
                        BridgeState state, long deadline) throws Exception {
        BridgeState.id(actionId, 8);
        if (!("home".equals(from) || "reports".equals(from)) || !("home".equals(to) || "reports".equals(to)) || from.equals(to)) {
            return BridgeState.reply("navigation_not_allowed");
        }
        if (state.consumed(actionId)) return BridgeState.reply("duplicate_suppressed");
        AccessibilityWindowInfo selected = window(expectedTask);
        AccessibilityNodeInfo root = root(selected);
        try (Scan result = new Scan()) {
            scan(root, "root", 0, null, false, child, to, result);
            if (result.incomplete) return BridgeState.reply("incomplete_guard_tree");
            if (result.childMatches != 1 || !from.equals(result.selectedTab()) || result.form) return BridgeState.reply("precondition_failed");
            if (result.targets.size() != 1) return BridgeState.reply("target_not_unique");
            AccessibilityNodeInfo target = result.targets.get(0);
            if (!target.refresh() || !target.isVisibleToUser() || !target.isEnabled() || !target.isClickable()) return BridgeState.reply("target_changed");
            if (window(expectedTask).getId() != selected.getId()) return BridgeState.reply("focus_changed");
            if (SystemClock.uptimeMillis() >= deadline) return BridgeState.reply("request_expired");
            if (!state.consume(actionId)) return BridgeState.reply("duplicate_suppressed");
            if (SystemClock.uptimeMillis() >= deadline || window(expectedTask).getId() != selected.getId()
                    || !target.refresh() || !target.isVisibleToUser() || !target.isEnabled()) return BridgeState.reply("consumed_without_dispatch");
            // Final execution boundary: never retry this ID, even if Binder/host disappears.
            boolean accepted = target.performAction(AccessibilityNodeInfo.ACTION_CLICK);
            return BridgeState.reply(accepted ? "dispatched" : "uncertain").put("actionId", actionId).put("trackingWrites", false);
        } finally { root.recycle(); }
    }
}
