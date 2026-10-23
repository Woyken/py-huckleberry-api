package dev.local.huckleberry.a11yprobe;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.provider.Settings;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/** A consent/setup screen for a developer tool, not a replacement Huckleberry app. */
public final class ProbeActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(32, 48, 32, 32);
        TextView text = new TextView(this);
        text.setText("Huckleberry developer debugging tool\n\n"
            + "This is not a Huckleberry product or API client. Enable this service only for an authorized debugging session.\n\n"
            + "Android shell may claim a session, read the enrolled Huckleberry task's accessibility nodes, "
            + "and perform semantic node actions (click, long-click, set text, scroll, focus, select, back) in the enrolled test app. "
            + "Actions can change app or backend state. It cannot take screenshots or inject coordinate gestures. "
            + "It has no network permission. Password fields are not exported.\n\n"
            + "Private session and consumed-action records persist to prevent accidental replay. "
            + "The host must verify the exact device, Android user and task. Disable this service when finished.");
        layout.addView(text);
        Button settings = new Button(this);
        settings.setText("Open Accessibility settings");
        settings.setOnClickListener(view -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        layout.addView(settings);
        setContentView(layout);
    }
}
