import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

ADB = os.environ.get(
    "HUCK_ADB", str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
)
SERIAL = os.environ.get("HUCK_SERIAL", "emulator-5554")
PORT = int(os.environ.get("HUCK_CDP_PORT", "9222"))
PKG = os.environ.get("HUCK_PACKAGE", "com.huckleberry_labs.app")
REPO = Path(__file__).resolve().parents[3]
OUTDIR = Path(os.environ.get("HUCK_OUT", REPO / ".parity-out"))
OUTDIR.mkdir(parents=True, exist_ok=True)


def adb(*args, check=True, text=True):
    cmd = [ADB, "-s", SERIAL, *args]
    return subprocess.run(cmd, check=check, text=text, capture_output=True)


def discover_socket():
    out = adb("shell", "cat", "/proc/net/unix").stdout
    matches = re.findall(r"@?(webview_devtools_remote_\d+)", out)
    if not matches:
        raise RuntimeError("No webview_devtools_remote socket found")
    # Usually the last belongs to the foreground app after launches/reloads.
    return matches[-1]


def forward(port=PORT):
    sock = discover_socket()
    adb("forward", f"tcp:{port}", f"localabstract:{sock}")
    return sock


def targets(port=PORT):
    forward(port)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def page_ws(port=PORT):
    pages = [t for t in targets(port) if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    if not pages:
        raise RuntimeError("No debuggable page target found")
    # Prefer the app page over chrome/devtools artifacts.
    pages.sort(key=lambda t: ("localhost" not in t.get("url", ""), t.get("id", "")))
    return pages[0]["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, port=PORT):
        self.port = port
        self.ws = None
        self.next_id = 0

    async def __aenter__(self):
        self.ws = await websockets.connect(page_ws(self.port), max_size=10_000_000)
        await self.call("Runtime.enable")
        await self.call("Page.enable")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.ws.close()

    async def call(self, method, params=None):
        self.next_id += 1
        ident = self.next_id
        await self.ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def eval(self, js, await_promise=True, return_by_value=True):
        res = await self.call("Runtime.evaluate", {
            "expression": js,
            "awaitPromise": await_promise,
            "returnByValue": return_by_value,
            "userGesture": True,
        })
        if "exceptionDetails" in res:
            raise RuntimeError(json.dumps(res["exceptionDetails"], ensure_ascii=False))
        ro = res.get("result", {})
        return ro.get("value") if return_by_value else ro

    async def wait_for(self, selector, timeout=30):
        js = f"""
        new Promise((resolve) => {{
          const sel = {json.dumps(selector)};
          const visible = (el) => {{
            if (!el) return false;
            const r = el.getBoundingClientRect();
            const st = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
          }};
          const find = () => {{
            let el = document.querySelector(sel);
            if (!el) {{
              for (const host of document.querySelectorAll('ion-input,ion-textarea')) {{
                if (host.matches(sel)) {{ el = host.shadowRoot && host.shadowRoot.querySelector('input,textarea'); break; }}
              }}
            }}
            if (visible(el)) resolve(true);
            else if (Date.now() > deadline) resolve(false);
            else setTimeout(find, 200);
          }};
          const deadline = Date.now() + {int(timeout*1000)};
          find();
        }})
        """
        ok = await self.eval(js)
        if not ok:
            raise TimeoutError(f"Timed out waiting for {selector}")
        return True

    async def click(self, selector, real_touch=False):
        if real_touch:
            info = await self.eval(_RECT_JS(selector))
            if not info:
                raise RuntimeError(f"No rect for {selector}")
            x = int(info["x"]); y = int(info["y"])
            adb("shell", "input", "tap", str(x), str(y))
            return {"tap": [x, y]}
        js = f"""
        (() => {{
          const sel = {json.dumps(selector)};
          let el = document.querySelector(sel);
          if (!el) throw new Error('not found: '+sel);
          el.scrollIntoView({{block:'center', inline:'center'}});
          el.click();
          return true;
        }})()
        """
        return await self.eval(js)

    async def type(self, selector, text):
        js = f"""
        (() => {{
          const sel = {json.dumps(selector)};
          const val = {json.dumps(text)};
          let host = document.querySelector(sel);
          if (!host) throw new Error('not found: '+sel);
          let el = host;
          if (host.tagName && host.tagName.toLowerCase().startsWith('ion-')) {{
            el = (host.shadowRoot && host.shadowRoot.querySelector('input,textarea')) || host.querySelector('input,textarea') || host;
          }}
          const proto = Object.getPrototypeOf(el);
          const desc = Object.getOwnPropertyDescriptor(proto, 'value');
          if (desc && desc.set) desc.set.call(el, val); else el.value = val;
          el.dispatchEvent(new Event('input', {{bubbles:true, composed:true}}));
          el.dispatchEvent(new Event('change', {{bubbles:true, composed:true}}));
          host.dispatchEvent(new Event('ionInput', {{bubbles:true, composed:true}}));
          host.dispatchEvent(new Event('ionChange', {{bubbles:true, composed:true}}));
          return true;
        }})()
        """
        return await self.eval(js)

    async def dump(self):
        return await self.eval(DUMP_JS)

    async def screenshot(self, name=None):
        if not name:
            name = "screen-" + time.strftime("%Y%m%d-%H%M%S") + ".png"
        path = OUTDIR / name
        p = subprocess.run([ADB, "-s", SERIAL, "exec-out", "screencap", "-p"], check=True, capture_output=True)
        path.write_bytes(p.stdout)
        return str(path)


def _RECT_JS(selector):
    return f"""
    (() => {{
      const el = document.querySelector({json.dumps(selector)});
      if (!el) return null;
      el.scrollIntoView({{block:'center', inline:'center'}});
      const r = el.getBoundingClientRect();
      const vv = window.visualViewport || {{offsetLeft:0, offsetTop:0, scale:1}};
      return {{x:(r.left+r.width/2+vv.offsetLeft)*devicePixelRatio, y:(r.top+r.height/2+vv.offsetTop)*devicePixelRatio,
              rect:[r.left,r.top,r.width,r.height], dpr:devicePixelRatio}};
    }})()
    """

DUMP_JS = r"""
(() => {
  const out = [];
  const seen = new Set();
  const textOf = el => (el.innerText || el.textContent || el.getAttribute('aria-label') || '').replace(/\s+/g,' ').trim().slice(0,100);
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const add = el => {
    if (!el || seen.has(el) || !visible(el)) return;
    seen.add(el);
    const r = el.getBoundingClientRect();
    const qa = el.getAttribute('data-qa');
    const id = el.id || '';
    const aria = el.getAttribute('aria-label') || '';
    const role = el.getAttribute('role') || '';
    const tag = el.tagName.toLowerCase();
    const txt = textOf(el);
    if (qa || id || aria || role || /button|input|select|textarea|a|ion-/.test(tag) || el.onclick || txt) {
      out.push({tag, id, qa, aria, role, text: txt, bounds: [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]});
    }
  };
  const walk = root => {
    root.querySelectorAll('button,input,textarea,select,a,[role],[data-qa],[id],ion-button,ion-input,ion-item,ion-card,ion-label').forEach(el => {
      add(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    });
  };
  walk(document);
  return out.slice(0, 250);
})()
"""

RECORDER_JS = r"""
(() => {
  window.__fsWrites = window.__fsWrites || [];
  window.__installFsRecorder = function(plugin) {
    if (!plugin || plugin.__fsRecorderInstalled) return false;
    const clone = (x) => { try { return JSON.parse(JSON.stringify(x)); } catch(e) { return String(x); } };
    const wrap = (name, mapper) => {
      if (typeof plugin[name] !== 'function') return;
      const orig = plugin[name];
      plugin[name] = function(...args) {
        try { window.__fsWrites.push(Object.assign({method:name, ts: Date.now()}, mapper(args))); } catch(e) { window.__fsWrites.push({method:name, error:String(e), ts:Date.now()}); }
        return orig.apply(this, args);
      };
    };
    wrap('setDocumentInFirestoreCollection', a => ({docId:String(a[0]), data:clone(a[1]), collection:a[2], timestamp:!!a[3]}));
    wrap('mergeDocumentInFirestoreCollection', a => ({docId:String(a[0]), data:clone(a[1]), collection:a[2], merge:true}));
    wrap('updateDocumentInFirestoreCollection', a => ({docId:String(a[0]), data:clone(a[1]), collection:a[2], timestamp:!!a[3]}));
    wrap('addDocumentToFirestoreCollection', a => ({data:clone(a[0]), collection:a[1], timestamp:!!a[2]}));
    wrap('deleteDocumentFromFirestoreCollection', a => ({docId:String(a[0]), collection:a[1]}));
    for (const k of Object.keys(plugin)) if (/batch|transaction/i.test(k) && typeof plugin[k] === 'function') wrap(k, a => ({args:clone(a)}));
    plugin.__fsRecorderInstalled = true;
    return true;
  };
  const tryInstall = () => window.__installFsRecorder(window.FirebasePlugin);
  tryInstall();
  if (!window.__fsRecorderPoll) window.__fsRecorderPoll = setInterval(tryInstall, 250);
  return true;
})()
"""

async def install_recorder(cdp):
    await cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": RECORDER_JS})
    return await cdp.eval(RECORDER_JS)

async def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("forward")
    sub.add_parser("targets")
    p = sub.add_parser("eval"); p.add_argument("js")
    p = sub.add_parser("wait"); p.add_argument("selector"); p.add_argument("--timeout", type=float, default=30)
    p = sub.add_parser("click"); p.add_argument("selector"); p.add_argument("--real-touch", action="store_true")
    p = sub.add_parser("type"); p.add_argument("selector"); p.add_argument("text")
    sub.add_parser("dump")
    p = sub.add_parser("screenshot"); p.add_argument("name", nargs="?")
    sub.add_parser("install-recorder")
    args = ap.parse_args()
    if args.cmd == "forward":
        print(json.dumps({"socket": forward()})); return
    if args.cmd == "targets":
        print(json.dumps(targets(), indent=2)); return
    async with CDP() as c:
        if args.cmd == "eval": res = await c.eval(args.js)
        elif args.cmd == "wait": res = await c.wait_for(args.selector, args.timeout)
        elif args.cmd == "click": res = await c.click(args.selector, args.real_touch)
        elif args.cmd == "type": res = await c.type(args.selector, args.text)
        elif args.cmd == "dump": res = await c.dump()
        elif args.cmd == "screenshot": res = await c.screenshot(args.name)
        elif args.cmd == "install-recorder": res = await install_recorder(c)
        print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
