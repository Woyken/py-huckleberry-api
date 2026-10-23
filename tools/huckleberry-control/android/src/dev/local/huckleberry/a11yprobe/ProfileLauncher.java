package dev.local.huckleberry.a11yprobe;

import java.lang.reflect.Method;

/** Shell-only launcher API entry point; no ActivityManager cross-user workaround. */
public final class ProfileLauncher {
    public static void main(String[] args) throws Exception {
        if (android.os.Process.myUid() != 2000) throw new SecurityException("Android shell only");
        if (args.length != 1) throw new IllegalArgumentException("Explicit Android user required");
        int user = Integer.parseInt(args[0]);
        if (user < 0) throw new IllegalArgumentException("Invalid Android user");
        Class<?> componentClass = Class.forName("android.content.ComponentName");
        Object component = componentClass.getConstructor(String.class, String.class)
            .newInstance("com.huckleberry_labs.app", "com.huckleberry_labs.app.MainActivity");
        Object handle = Class.forName("android.os.UserHandle").getMethod("of", int.class).invoke(null, user);
        Object binder = Class.forName("android.os.ServiceManager").getMethod("getService", String.class).invoke(null, "launcherapps");
        Object launcher = Class.forName("android.content.pm.ILauncherApps$Stub")
            .getMethod("asInterface", Class.forName("android.os.IBinder")).invoke(null, binder);
        for (Method method : launcher.getClass().getMethods()) {
            if (!method.getName().equals("startActivityAsUser")) continue;
            Object[] values = new Object[method.getParameterCount()];
            boolean packageSet = false;
            for (int index = 0; index < values.length; index++) {
                Class<?> type = method.getParameterTypes()[index];
                if (type == componentClass) values[index] = component;
                else if (type.getName().equals("android.os.UserHandle")) values[index] = handle;
                else if (type == String.class && !packageSet) { values[index] = "com.android.shell"; packageSet = true; }
            }
            method.invoke(launcher, values);
            System.out.println("Launcher API completed");
            return;
        }
        throw new IllegalStateException("Unsupported launcher API; no fallback");
    }
}
