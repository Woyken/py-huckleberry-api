import asyncio
import json
import os
import sys
import time
from pathlib import Path

from cdp import CDP, OUTDIR, REPO, SERIAL, adb

ENV = REPO / ".env"
OUT = OUTDIR

def load_env():
    vals = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals

async def click_first(c, selectors, timeout=5, real=False):
    last=None
    for s in selectors:
        try:
            await c.wait_for(s, timeout)
            return await c.click(s, real_touch=real)
        except Exception as e:
            last=e
    raise last or RuntimeError('no selectors')

async def handle_popups():
    # Best-effort native notification permission dismissal/allow; no secret output.
    for txt in ['Allow', 'While using the app', 'OK', 'Don’t allow', "Don't allow"]:
        try:
            import uiautomator2 as u2
            d = u2.connect_usb(SERIAL)
            obj = d(text=txt)
            if obj.exists(timeout=0.2):
                obj.click()
                return txt
        except Exception:
            pass
    return None

async def main():
    env=load_env()
    email=env['HUCKLEBERRY_EMAIL']; password=env['HUCKLEBERRY_PASSWORD']
    result={'started': time.time()}
    async with CDP() as c:
        dump = await c.dump()
        result['initial_text'] = ' '.join([x.get('text','') for x in dump[:4]])[:300]
        # Navigate to login if needed.
        if any(x.get('qa') == 'welcome_sign_in' or x.get('id') == 'welcome_sign_in' for x in dump):
            await click_first(c, ['#welcome_sign_in','[data-qa="welcome_sign_in"]'])
        await c.wait_for('[data-qa="login_email"]', 20)
        await c.type('hb-email ion-input', email)
        await c.type('hb-password ion-input', password)
        await click_first(c, ['[data-qa="login_submit"] button','[data-qa="login_submit"]'], real=False)
        result['clicked_login'] = True
    result['native_popup'] = await handle_popups()
    # Wait for post-login shell/home.
    deadline = time.time()+90
    last_dump=[]
    while time.time() < deadline:
        await asyncio.sleep(2)
        try:
            async with CDP() as c:
                last_dump = await c.dump()
                txt=' '.join([str(x.get('text','')) for x in last_dump])
                qas=[x.get('qa') for x in last_dump if x.get('qa')]
                if 'Welcome Back' not in txt and ('diaper' in txt.lower() or 'sleep' in txt.lower() or 'feed' in txt.lower() or 'today' in txt.lower() or any(q and 'tracker' in q.lower() for q in qas)):
                    result['home_detected']=True
                    result['visible_qas']=qas[:80]
                    result['dump']=last_dump[:80]
                    break
        except Exception as e:
            result['last_error']=str(e)
    else:
        result['home_detected']=False
        result['dump']=last_dump[:80]
    (OUT/'login_result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='dump'}, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    asyncio.run(main())
