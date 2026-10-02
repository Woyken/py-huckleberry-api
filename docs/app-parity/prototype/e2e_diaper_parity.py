import asyncio
import json
import os
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

# Import repo package without changing repo files.
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from huckleberry_api.api import HuckleberryAPI  # noqa: E402
from cdp import CDP, OUTDIR, install_recorder, adb  # noqa: E402

OUT = OUTDIR
ENV = REPO / ".env"
CID = None


def load_env():
    vals = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def ser(x):
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, dict):
        return {str(k): ser(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [ser(v) for v in x]
    if hasattr(x, "timestamp"):
        try:
            return {"__datetime__": x.isoformat(), "seconds": x.timestamp()}
        except Exception:
            pass
    return repr(x)


async def snapshot(client, cid, label):
    root_ref = client.collection("diaper").document(cid)
    root = await root_ref.get()
    q = root_ref.collection("intervals").order_by("start", direction="DESCENDING").limit(5)
    intervals = []
    async for doc in q.stream():
        intervals.append({"id": doc.id, "data": ser(doc.to_dict() or {})})
    snap = {"label": label, "ts": time.time(), "root": ser(root.to_dict() or {}), "intervals": intervals}
    (OUT / f"snapshot_{label}.json").write_text(json.dumps(snap, indent=2, ensure_ascii=False), encoding="utf-8")
    return snap


def interval_ids(snap):
    return {x["id"] for x in snap["intervals"]}


def flatten(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten(v, p))
    else:
        out[prefix] = d
    return out


def type_name(v):
    if isinstance(v, dict) and "__datetime__" in v:
        return "timestamp"
    return type(v).__name__


def compare_docs(app_doc, wrapper_doc):
    af = flatten(app_doc or {})
    wf = flatten(wrapper_doc or {})
    rows = []
    for k in sorted(set(af) | set(wf)):
        av = af.get(k, "<missing>")
        wv = wf.get(k, "<missing>")
        status = "same" if av == wv else ("type" if k in af and k in wf and type_name(av) != type_name(wv) else "diff")
        rows.append({"field": k, "app": av, "wrapper": wv, "status": status, "app_type": type_name(av) if k in af else "missing", "wrapper_type": type_name(wv) if k in wf else "missing"})
    return rows


def root_diff(before, after):
    bf = flatten(before or {})
    af = flatten(after or {})
    return [{"field": k, "before": bf.get(k, "<missing>"), "after": af.get(k, "<missing>")} for k in sorted(set(bf)|set(af)) if bf.get(k) != af.get(k)]


async def ensure_diaper_form(c):
    dump = await c.dump()
    qas = {x.get("qa") for x in dump if x.get("qa")}
    if "diaperPotty_modeSelector_both" not in qas:
        # close any modal if necessary, then open diaper card from home.
        if "home_diaper" not in qas:
            try:
                await c.click('[data-qa="diaper_close"]')
                await asyncio.sleep(1)
            except Exception:
                pass
            try:
                await c.click('[data-qa="mainTabs_home"]')
                await asyncio.sleep(1)
            except Exception:
                pass
        await c.wait_for('[data-qa="home_diaper"]', 20)
        await c.click('[data-qa="home_diaper"]')
        await c.wait_for('[data-qa="diaperPotty_modeSelector_both"]', 20)
    try:
        await c.wait_for('#diaper_save_button', 1)
        return
    except Exception:
        pass
    # The Ionic list item has Angular gesture handlers; DOM .click() did not always fire.
    await c.click('[data-qa="diaperPotty_modeSelector_both"]', real_touch=True)
    await c.wait_for('#diaper_save_button', 10)


async def run_app_action():
    async with CDP() as c:
        await install_recorder(c)
        await c.eval("window.__fsWrites = []")
        await ensure_diaper_form(c)
        start_text = await c.eval("document.querySelector('#diaper_setStartTime')?.innerText || ''")
        await c.screenshot("before_app_diaper_save.png")
        # ADB tap hit the sticky footer visually but did not trigger Ionic reliably; dispatch full events.
        await c.eval("""(() => {
          const e=document.querySelector('#diaper_save_button');
          ['pointerdown','mousedown','touchstart','pointerup','mouseup','touchend','click'].forEach(t =>
            e.dispatchEvent(new Event(t,{bubbles:true,cancelable:true,composed:true})));
          return true;
        })()""")
        writes = []
        for _ in range(20):
            await asyncio.sleep(0.75)
            writes = await c.eval("window.__fsWrites || []")
            if any(w.get("method") == "setDocumentInFirestoreCollection" and "/intervals" in str(w.get("collection", "")) for w in writes):
                break
        dump = await c.dump()
        await c.screenshot("after_app_diaper_save.png")
        (OUT / "app_fs_writes.json").write_text(json.dumps(writes, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / "after_app_dump.json").write_text(json.dumps(dump[:120], indent=2, ensure_ascii=False), encoding="utf-8")
        return {"start_text": start_text, "writes": writes}


async def main():
    env = load_env()
    result = {"started": time.time(), "created_interval_ids": [], "deleted_interval_ids": []}
    async with aiohttp.ClientSession() as session:
        api = HuckleberryAPI(env["HUCKLEBERRY_EMAIL"], env["HUCKLEBERRY_PASSWORD"], env.get("HUCKLEBERRY_TIMEZONE", "UTC"), session)
        await api.authenticate()
        user = await api.get_user()
        cid = user.childList[0].cid
        result["child"] = {"cid": cid, "nickname": user.childList[0].nickname}
        client = await api._get_firestore_client()

        before = await snapshot(client, cid, "before_app")
        app_action = await run_app_action()
        result["app_action"] = {"start_text": app_action["start_text"], "write_count": len(app_action["writes"])}
        after_app = await snapshot(client, cid, "after_app")
        new_app_ids = list(interval_ids(after_app) - interval_ids(before))
        if not new_app_ids:
            # Prefer the recorder's document id over guessing from history.
            for w in app_action["writes"]:
                if w.get("method") == "setDocumentInFirestoreCollection" and w.get("collection") == f"diaper/{cid}/intervals":
                    new_app_ids = [w.get("docId")]
                    break
        result["app_interval_id"] = new_app_ids[0] if new_app_ids else None
        if result["app_interval_id"]:
            result["created_interval_ids"].append(result["app_interval_id"])

        exact = {}
        if result.get("app_interval_id"):
            app_doc = await client.collection("diaper").document(cid).collection("intervals").document(result["app_interval_id"]).get()
            exact["app"] = {"id": result["app_interval_id"], "data": ser(app_doc.to_dict() or {})}
        app_start = exact.get("app", {}).get("data", {}).get("start")
        wrapper_start = datetime.fromtimestamp(float(app_start), timezone.utc) if app_start else datetime.now(timezone.utc)
        await api.log_diaper(cid, start_time=wrapper_start, mode="both")
        await asyncio.sleep(2)
        after_wrapper = await snapshot(client, cid, "after_wrapper")
        new_wrapper_ids = list(interval_ids(after_wrapper) - interval_ids(after_app))
        result["wrapper_interval_id"] = new_wrapper_ids[0] if new_wrapper_ids else (after_wrapper["intervals"][0]["id"] if after_wrapper["intervals"] else None)
        if result["wrapper_interval_id"]:
            result["created_interval_ids"].append(result["wrapper_interval_id"])

        # Fetch exact created docs (not just top 5) for report.
        for side, doc_id in [("app", result.get("app_interval_id")), ("wrapper", result.get("wrapper_interval_id"))]:
            if doc_id:
                doc = await client.collection("diaper").document(cid).collection("intervals").document(doc_id).get()
                exact[side] = {"id": doc_id, "data": ser(doc.to_dict() or {})}
        result["exact_docs"] = exact
        result["interval_compare"] = compare_docs(exact.get("app", {}).get("data"), exact.get("wrapper", {}).get("data"))
        result["root_diff_app"] = root_diff(before["root"], after_app["root"])
        result["root_diff_wrapper"] = root_diff(after_app["root"], after_wrapper["root"])

        # Compare JS payloads to landed app doc.
        result["js_vs_landed"] = []
        app_doc_id = result.get("app_interval_id")
        for w in app_action["writes"]:
            if w.get("collection") == f"diaper/{cid}/intervals" or w.get("collection") == f"diaper/{cid}/intervals/":
                row = {"method": w.get("method"), "docId": w.get("docId"), "timestampFlag": w.get("timestamp"), "compare": compare_docs(w.get("data"), exact.get("app", {}).get("data"))}
                result["js_vs_landed"].append(row)
            elif str(w.get("collection", "")).startswith(f"diaper/{cid}") or w.get("collection") == "diaper":
                result.setdefault("captured_relevant_writes", []).append(w)

        # Clean up only created interval docs.
        for doc_id in list(dict.fromkeys(result["created_interval_ids"])):
            try:
                await client.collection("diaper").document(cid).collection("intervals").document(doc_id).delete()
                result["deleted_interval_ids"].append(doc_id)
            except Exception as e:
                result.setdefault("cleanup_errors", []).append({"doc_id": doc_id, "error": str(e)})

    (OUT / "parity_report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "child": result.get("child"),
        "app_interval_id": result.get("app_interval_id"),
        "wrapper_interval_id": result.get("wrapper_interval_id"),
        "app_write_count": result.get("app_action", {}).get("write_count"),
        "deleted_interval_ids": result.get("deleted_interval_ids"),
        "report": str(OUT / "parity_report.json"),
    }, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
