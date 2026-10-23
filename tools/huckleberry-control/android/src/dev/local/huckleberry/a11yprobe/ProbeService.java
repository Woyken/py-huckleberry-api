package dev.local.huckleberry.a11yprobe;

import android.accessibilityservice.AccessibilityService;
import android.graphics.Rect;
import android.os.Bundle;
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

/**
 * Developer-only semantic accessibility bridge.
 *
 * <p>It is intentionally not a general-purpose automation service: every request
 * carries the enrolled Android user and exact task; nodes are limited to
 * Huckleberry and to the focused window. Mutating actions require an atomically
 * consumed action id. There is no screenshot, gesture injection or network
 * access.</p>
 */
public final class ProbeService extends AccessibilityService {
    static volatile ProbeService instance;
    private static final String PACKAGE = "com.huckleberry_labs.app";
    private static final Pattern TASK = Pattern.compile("\\btaskId=(-?\\d+)");
    private static final Pattern PATH = Pattern.compile("root(?:/\\d{1,4}){0,60}");
    private static final int MAX_VISITED = 6000;
    private static final int MAX_EXPORTED = 3000;
    private static final int MAX_DEPTH = 60;

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
        if ("tab-button-child-info".equals(id)) return "child";
        return null;
    }
    private static String clipped(CharSequence text) {
        return text == null ? null : text.subSequence(0, Math.min(512, text.length())).toString();
    }
    private static Object json(String value) { return value == null ? JSONObject.NULL : value; }
    private static String text(AccessibilityNodeInfo node) {
        CharSequence value = node.getText();
        return value == null ? null : value.toString();
    }
    private static String description(AccessibilityNodeInfo node) {
        CharSequence value = node.getContentDescription();
        return value == null ? null : value.toString();
    }
    private static boolean hasAction(AccessibilityNodeInfo node, int id) {
        for (AccessibilityNodeInfo.AccessibilityAction action : node.getActionList()) {
            if (action.getId() == id) return true;
        }
        return false;
    }

    private static final class Scan implements AutoCloseable {
        final ArrayList<JSONObject> content = new ArrayList<>(), navigation = new ArrayList<>();
        final ArrayList<AccessibilityNodeInfo> targets = new ArrayList<>();
        final Set<String> selectedTabs = new HashSet<>();
        int visited, exported;
        boolean incomplete, form;
        String selectedTab() { return selectedTabs.size() == 1 ? selectedTabs.iterator().next() : null; }
        @Override public void close() { for (AccessibilityNodeInfo target : targets) target.recycle(); }
    }

    private void scan(AccessibilityNodeInfo node, String path, int depth, String ancestorTab,
                      boolean inNavigation, String target, Scan result) throws Exception {
        if (result.visited >= MAX_VISITED || depth > MAX_DEPTH) { result.incomplete = true; return; }
        result.visited++;
        if (!PACKAGE.contentEquals(node.getPackageName() == null ? "" : node.getPackageName())) return;
        String id = node.getViewIdResourceName();
        String currentTab = tab(id);
        if (currentTab == null) currentTab = ancestorTab;
        boolean navigation = inNavigation || "mainTabs".equals(id);
        boolean visible = node.isVisibleToUser();
        // Ionic retains entire hidden pages and off-screen list subtrees. This
        // tool inspects the currently exposed UI, never cached invisible pages.
        if (!visible) return;
        result.exported++;
        if (node.isSelected() && currentTab != null) result.selectedTabs.add(currentTab);
        if (node.isEditable() || "Save".equals(node.getText() == null ? "" : node.getText().toString().trim())) result.form = true;
        if (target != null && target.equals(tab(id)) && node.isEnabled() && node.isClickable()) {
            result.targets.add(AccessibilityNodeInfo.obtain(node));
        }
        Rect bounds = new Rect();
        node.getBoundsInScreen(bounds);
        JSONArray actions = new JSONArray();
        for (AccessibilityNodeInfo.AccessibilityAction action : node.getActionList()) actions.put(action.getId());
        boolean redact = node.isPassword();
        JSONObject row = new JSONObject().put("path", path).put("depth", depth)
            .put("className", String.valueOf(node.getClassName()))
            .put("id", id == null ? JSONObject.NULL : id)
            .put("visible", true).put("enabled", node.isEnabled())
            .put("clickable", node.isClickable()).put("longClickable", node.isLongClickable())
            .put("scrollable", node.isScrollable()).put("checkable", node.isCheckable())
            .put("checked", node.isChecked()).put("selected", node.isSelected())
            .put("editable", node.isEditable()).put("focusable", node.isFocusable())
            .put("focused", node.isFocused()).put("password", node.isPassword())
            .put("bounds", "[" + bounds.left + "," + bounds.top + "][" + bounds.right + "," + bounds.bottom + "]")
            .put("text", redact ? JSONObject.NULL : json(clipped(node.getText())))
            .put("description", redact ? JSONObject.NULL : json(clipped(node.getContentDescription())))
            .put("hint", redact ? JSONObject.NULL : json(clipped(node.getHintText())))
            .put("actions", actions);
        ArrayList<JSONObject> output = navigation ? result.navigation : result.content;
        if (result.exported <= MAX_EXPORTED) output.add(row);
        else result.incomplete = true;
        for (int index = 0; index < node.getChildCount(); index++) {
            if (result.visited >= MAX_VISITED) { result.incomplete = true; break; }
            AccessibilityNodeInfo next = node.getChild(index);
            if (next == null) continue;
            try { scan(next, path + "/" + index, depth + 1, currentTab, navigation, target, result); }
            finally { next.recycle(); }
        }
    }

    JSONObject snapshot(int expectedTask) throws Exception {
        AccessibilityWindowInfo selected = window(expectedTask);
        AccessibilityNodeInfo root = root(selected);
        try (Scan result = new Scan()) {
            scan(root, "root", 0, null, false, null, result);
            if (window(expectedTask).getId() != selected.getId()) return BridgeState.reply("focus_changed");
            JSONArray nodes = new JSONArray();
            for (JSONObject row : result.navigation) if (nodes.length() < MAX_EXPORTED) nodes.put(row);
            for (JSONObject row : result.content) if (nodes.length() < MAX_EXPORTED) nodes.put(row);
            return BridgeState.reply("ok").put("task", expectedTask).put("window", selected.getId())
                .put("nodes", nodes).put("exported", result.exported)
                .put("truncated", result.incomplete || result.exported > nodes.length())
                .put("guardComplete", !result.incomplete)
                .put("selectedTab", result.selectedTab() == null ? JSONObject.NULL : result.selectedTab());
        } finally { root.recycle(); }
    }

    private AccessibilityNodeInfo byPath(AccessibilityNodeInfo root, String path) {
        if (path == null || !PATH.matcher(path).matches()) throw new IllegalArgumentException("invalid_path");
        AccessibilityNodeInfo current = root;
        boolean owned = false;
        try {
            if ("root".equals(path)) return AccessibilityNodeInfo.obtain(root);
            for (String part : path.substring(5).split("/")) {
                int index = Integer.parseInt(part);
                AccessibilityNodeInfo next = current.getChild(index);
                if (next == null) throw new IllegalStateException("target_not_found");
                if (owned) current.recycle();
                current = next;
                owned = true;
            }
            AccessibilityNodeInfo result = AccessibilityNodeInfo.obtain(current);
            return result;
        } finally {
            if (owned) current.recycle();
        }
    }

    private static final class Match implements AutoCloseable {
        AccessibilityNodeInfo node;
        int count;
        @Override public void close() { if (node != null) node.recycle(); }
    }

    private static void collectMatch(AccessibilityNodeInfo node, String id, String text, int depth, Match match) {
        if (depth > MAX_DEPTH || match.count > 1) return;
        if (!PACKAGE.contentEquals(node.getPackageName() == null ? "" : node.getPackageName())) return;
        if (!node.isVisibleToUser()) return;
        boolean idMatch = id != null && !id.isEmpty() && id.equals(node.getViewIdResourceName());
        boolean textMatch = text != null && !text.isEmpty() && text.equals(node.getText() == null ? "" : node.getText().toString());
        if (idMatch || textMatch) {
            match.count++;
            if (match.count == 1) match.node = AccessibilityNodeInfo.obtain(node);
        }
        for (int index = 0; index < node.getChildCount() && match.count <= 1; index++) {
            AccessibilityNodeInfo next = node.getChild(index);
            if (next == null) continue;
            try { collectMatch(next, id, text, depth + 1, match); }
            finally { next.recycle(); }
        }
    }

    private static AccessibilityNodeInfo actionable(AccessibilityNodeInfo node, String action) {
        if (!("click".equals(action) || "long-click".equals(action))) return AccessibilityNodeInfo.obtain(node);
        AccessibilityNodeInfo current = node;
        boolean owned = false;
        try {
            for (int level = 0; level <= 10; level++) {
                if ("click".equals(action) ? (current.isClickable() || hasAction(current, AccessibilityNodeInfo.ACTION_CLICK))
                        : (current.isLongClickable() || hasAction(current, AccessibilityNodeInfo.ACTION_LONG_CLICK))) {
                    AccessibilityNodeInfo result = AccessibilityNodeInfo.obtain(current);
                    return result;
                }
                AccessibilityNodeInfo parent = current.getParent();
                if (parent == null) return null;
                if (owned) current.recycle();
                current = parent;
                owned = true;
            }
            return null;
        } finally {
            if (owned) current.recycle();
        }
    }

    private static boolean same(String expected, String actual) {
        return expected == null || expected.isEmpty() || expected.equals(actual == null ? "" : actual);
    }

    JSONObject action(int expectedTask, String actionId, String path, String id, String text,
                      String action, String expectId, String expectText, String value,
                      BridgeState state, long deadline) throws Exception {
        BridgeState.id(actionId, 8);
        if (!("click".equals(action) || "long-click".equals(action) || "set-text".equals(action)
                || "scroll-forward".equals(action) || "scroll-backward".equals(action)
                || "focus".equals(action) || "select".equals(action) || "back".equals(action))) {
            return BridgeState.reply("action_not_allowed");
        }
        if (state.consumed(actionId)) return BridgeState.reply("duplicate_suppressed");
        if (id == null) id = "";
        if (text == null) text = "";
        if (path == null) path = "";
        if (expectId == null) expectId = "";
        if (expectText == null) expectText = "";
        if (value == null) value = "";
        int selectors = (id.isEmpty() ? 0 : 1) + (text.isEmpty() ? 0 : 1) + (path.isEmpty() ? 0 : 1);
        if (selectors == 0 && !"back".equals(action)) return BridgeState.reply("target_required");
        if (selectors > 1) return BridgeState.reply("ambiguous_target_selector");
        if (value.length() > 4096) return BridgeState.reply("value_too_long");

        AccessibilityWindowInfo selected = window(expectedTask);
        AccessibilityNodeInfo root = root(selected);
        AccessibilityNodeInfo target = null;
        Match match = null;
        try {
            if ("back".equals(action)) {
                if (selectors != 0) return BridgeState.reply("back_takes_no_target");
            } else if (!id.isEmpty() || !text.isEmpty()) {
                match = new Match();
                collectMatch(root, id, text, 0, match);
                if (match.count == 0) return BridgeState.reply("target_not_found");
                if (match.count > 1) return BridgeState.reply("target_not_unique");
                target = match.node;
            } else {
                target = byPath(root, path);
            }
            if (target != null) {
                if (!target.refresh()) return BridgeState.reply("target_changed");
                if (!target.isVisibleToUser()) return BridgeState.reply("target_not_visible");
                String actualId = target.getViewIdResourceName();
                if (!same(expectId, actualId)) return BridgeState.reply("expectation_failed");
                if (!same(expectText, text(target))) return BridgeState.reply("expectation_failed");
                if (!"set-text".equals(action) && !target.isEnabled()) return BridgeState.reply("target_disabled");
                if (!id.isEmpty() || !text.isEmpty()) {
                    if (match != null) { match.node = null; match.close(); match = null; }
                    AccessibilityNodeInfo resolved = actionable(target, action);
                    target.recycle();
                    target = resolved;
                    if (target == null) return BridgeState.reply("target_not_actionable");
                }
                switch (action) {
                    case "click": if (!target.isClickable() && !hasAction(target, AccessibilityNodeInfo.ACTION_CLICK)) return BridgeState.reply("target_not_clickable"); break;
                    case "long-click": if (!target.isLongClickable() && !hasAction(target, AccessibilityNodeInfo.ACTION_LONG_CLICK)) return BridgeState.reply("target_not_long_clickable"); break;
                    case "set-text": if (!target.isEditable()) return BridgeState.reply("target_not_editable"); break;
                    case "scroll-forward": if (!hasAction(target, AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)) return BridgeState.reply("target_not_scrollable"); break;
                    case "scroll-backward": if (!hasAction(target, AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)) return BridgeState.reply("target_not_scrollable"); break;
                    case "focus": if (!target.isFocusable()) return BridgeState.reply("target_not_focusable"); break;
                    case "select": if (!target.isSelected() && !hasAction(target, AccessibilityNodeInfo.ACTION_SELECT)) return BridgeState.reply("target_not_selectable"); break;
                    default: break;
                }
            }
            if (window(expectedTask).getId() != selected.getId()) return BridgeState.reply("focus_changed");
            if (SystemClock.uptimeMillis() >= deadline) return BridgeState.reply("request_expired");
            if (!state.consume(actionId)) return BridgeState.reply("duplicate_suppressed");
            if (SystemClock.uptimeMillis() >= deadline || window(expectedTask).getId() != selected.getId()) {
                return BridgeState.reply("consumed_without_dispatch");
            }
            boolean accepted;
            if ("back".equals(action)) {
                accepted = performGlobalAction(GLOBAL_ACTION_BACK);
            } else if (target != null && !target.refresh()) {
                return BridgeState.reply("target_changed");
            } else {
                int nodeAction;
                switch (action) {
                    case "click": nodeAction = AccessibilityNodeInfo.ACTION_CLICK; break;
                    case "long-click": nodeAction = AccessibilityNodeInfo.ACTION_LONG_CLICK; break;
                    case "scroll-forward": nodeAction = AccessibilityNodeInfo.ACTION_SCROLL_FORWARD; break;
                    case "scroll-backward": nodeAction = AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD; break;
                    case "focus": nodeAction = AccessibilityNodeInfo.ACTION_FOCUS; break;
                    case "select": nodeAction = AccessibilityNodeInfo.ACTION_SELECT; break;
                    case "set-text":
                        Bundle arguments = new Bundle();
                        arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
                        accepted = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
                        return BridgeState.reply(accepted ? "dispatched" : "uncertain")
                            .put("actionId", actionId).put("action", action).put("mayMutate", true)
                            .put("targetId", target.getViewIdResourceName() == null ? JSONObject.NULL : target.getViewIdResourceName())
                            .put("targetText", json(clipped(target.getText())));
                    default: throw new IllegalStateException("unsupported_action");
                }
                accepted = target.performAction(nodeAction);
            }
            // Final execution boundary: never retry this ID, even if Binder/host disappears.
            return BridgeState.reply(accepted ? "dispatched" : "uncertain")
                .put("actionId", actionId).put("action", action)
                .put("mayMutate", !"focus".equals(action) && !"select".equals(action) && !"scroll-forward".equals(action) && !"scroll-backward".equals(action))
                .put("targetId", target == null || target.getViewIdResourceName() == null ? JSONObject.NULL : target.getViewIdResourceName())
                .put("targetText", target == null || clipped(target.getText()) == null ? JSONObject.NULL : clipped(target.getText()));
        } finally {
            if (match != null) match.close();
            else if (target != null) target.recycle();
            root.recycle();
        }
    }

    JSONObject navigate(int expectedTask, String actionId, String from, String to,
                        BridgeState state, long deadline) throws Exception {
        BridgeState.id(actionId, 8);
        if (!("home".equals(from) || "reports".equals(from)) || !("home".equals(to) || "reports".equals(to)) || from.equals(to)) {
            return BridgeState.reply("navigation_not_allowed");
        }
        if (state.consumed(actionId)) return BridgeState.reply("duplicate_suppressed");
        AccessibilityWindowInfo selected = window(expectedTask);
        AccessibilityNodeInfo root = root(selected);
        try (Scan result = new Scan()) {
            scan(root, "root", 0, null, false, to, result);
            if (result.incomplete) return BridgeState.reply("incomplete_guard_tree");
            if (!from.equals(result.selectedTab()) || result.form) return BridgeState.reply("precondition_failed");
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
