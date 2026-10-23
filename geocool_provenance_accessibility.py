from pathlib import Path
import re
root=Path('/home/ubuntu/geocool')

p=root/'frontend/assets/css/theme.css'; s=p.read_text()
s=s.replace('  --accent: #2563EB;','  --accent: #2563EB;\n  --focus-ring: #2563EB;\n  --focus-ring-on-nav: #FFFFFF;')
s=s.replace('  --text-primary: #F8FAFC;','  --text-primary: #F8FAFC;\n  --focus-ring: #93C5FD;')
s=s.replace('outline: 3px solid #F59E0B;', 'outline: 3px solid var(--focus-ring);')
s += r'''
:root[data-theme="dark"] .topbar :focus-visible { outline-color: var(--focus-ring-on-nav); }
.map-risk-popup :focus-visible { outline-color: #2563EB; }
'''
p.write_text(s)

p=root/'frontend/assets/js/contrast-audit.js'; s=p.read_text()
s=s.replace("""  function rgb(value) {
    const m=String(value||'').match(/rgba?\\(([^)]+)\\)/i); if(!m)return null;
    const parts=m[1].split(',').map(x=>parseFloat(x)); if(parts.length<3)return null;
    return {r:parts[0],g:parts[1],b:parts[2],a:parts.length>3?parts[3]:1};
  }
""","""  function rgb(value) {
    const text=String(value||'').trim();
    const hex=text.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if(hex){let raw=hex[1];if(raw.length===3)raw=raw.split('').map(x=>x+x).join('');return {r:parseInt(raw.slice(0,2),16),g:parseInt(raw.slice(2,4),16),b:parseInt(raw.slice(4,6),16),a:1};}
    const m=text.match(/rgba?\\(([^)]+)\\)/i); if(!m)return null;
    const parts=m[1].split(',').map(x=>parseFloat(x)); if(parts.length<3)return null;
    return {r:parts[0],g:parts[1],b:parts[2],a:parts.length>3?parts[3]:1};
  }
""")
# Replace the audit function body as one stable source region.
start=s.index('  function runContrastAudit() {')
end=s.index('\n  window.runContrastAudit=runContrastAudit;',start)
fn=r'''  function runContrastAudit() {
    let failures=0, checked=0, textFailures=0, focusFailures=0;
    for(const el of document.querySelectorAll('body *')) {
      const style=getComputedStyle(el);if(style.display==='none'||style.visibility==='hidden'||!el.textContent.trim())continue;
      const fg=luminance(style.color);let bg=style.backgroundColor, node=el;
      while((!rgb(bg)||rgb(bg).a===0)&&node&&node!==document.documentElement){node=node.parentElement;if(node)bg=getComputedStyle(node).backgroundColor;}
      const back=luminance(bg);if(fg===null||back===null)continue;
      const ratio=(Math.max(fg,back)+.05)/(Math.min(fg,back)+.05);const size=parseFloat(style.fontSize)||16;
      const threshold=(size>=24||(size>=18.67&&Number(style.fontWeight)>=700))?3:4.5;checked++;
      if(ratio<threshold){failures++;textFailures++;console.warn(`[Contrast audit] text ${ratio.toFixed(2)}:1 (required ${threshold}:1)`,el,{foreground:style.color,background:bg,text:el.textContent.trim().slice(0,100)});}
    }
    const root=getComputedStyle(document.documentElement);
    const nav=document.querySelector('.topbar');
    const card=document.querySelector('.card');
    const popup=document.querySelector('.map-risk-popup');
    const contrast=(a,b)=>{const x=luminance(a),y=luminance(b);return x===null||y===null?null:(Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
    const focusChecks=[
      ['--focus-ring',root.getPropertyValue('--focus-ring'),root.getPropertyValue('--bg')],
      ['--focus-ring',root.getPropertyValue('--focus-ring'),card?getComputedStyle(card).backgroundColor:root.getPropertyValue('--surface')],
      ['popup focus','#2563EB',popup?getComputedStyle(popup).backgroundColor:'#FFFFFF'],
      ['navigation focus',root.getPropertyValue('--focus-ring-on-nav'),nav?getComputedStyle(nav).backgroundColor:root.getPropertyValue('--nav-bg')]
    ];
    for(const [name,ring,bg] of focusChecks){const value=contrast(ring,bg);if(value===null)continue;if(value<3){failures++;focusFailures++;console.warn(`[Contrast audit] ${name} indicator ${value.toFixed(2)}:1 (required 3:1)`,{ring,background:bg});}}
    const result=`Contrast audit: ${failures} failure(s) — ${textFailures} text pair(s) below WCAG AA and ${focusFailures} focus indicator(s) below 3:1; checked ${checked} text elements.`;
    console.info(result);const target=document.querySelector('[data-contrast-result]');if(target)target.textContent=result;
    if(window.toast)window.toast(result,failures?'error':'success');return failures;
  }'''
s=s[:start]+fn+s[end:]
p.write_text(s)

p=root/'backend/routes/wards.py'; s=p.read_text()
s=s.replace('ward = query_one("SELECT ward_id FROM wards WHERE ward_id=?", (ward_id,))', 'ward = query_one("SELECT ward_id,provenance FROM wards WHERE ward_id=?", (ward_id,))',1)
old='''    method = "transparent_weighted_AHP_contributions_not_SHAP"
    try:
        from backend.ml.explain import explain_ward as explain_model
        candidate = explain_model(ward_id)
        if isinstance(candidate, dict) and candidate.get("available"):
            contributions = candidate.get("contributions", contributions)
            explanation = candidate.get("explanation", explanation)
            ml_score = candidate.get("predicted_risk", ml_score)
            method = candidate.get("method", "trained_model_explanation")
    except Exception:
        pass
'''
new='''    method = "transparent_weighted_AHP_contributions_not_SHAP"
    model_demo = False
    try:
        from backend.ml.explain import explain_ward as explain_model
        candidate = explain_model(ward_id)
        if isinstance(candidate, dict) and candidate.get("available"):
            contributions = candidate.get("contributions", contributions)
            explanation = candidate.get("explanation", explanation)
            ml_score = candidate.get("predicted_risk", ml_score)
            method = candidate.get("method", "trained_model_explanation")
            model_demo = bool(candidate.get("demo")) or "demo" in str(candidate.get("provenance", "")).lower() or "synthetic" in str(candidate.get("provenance", "")).lower()
    except Exception:
        pass
    demo_edges = query_one("SELECT COUNT(*) AS n FROM edges WHERE ward_id=? AND provenance='demo_synthetic'", (ward_id,)) or {"n": 0}
    is_demo = model_demo or ward.get("provenance") == "demo_synthetic" or int(demo_edges.get("n", 0) or 0) > 0
    data_provenance = "demo_synthetic" if is_demo else str(ward.get("provenance") or "source_configured")
'''
if s.count(old)!=1: raise SystemExit('explanation provenance block mismatch')
s=s.replace(old,new,1)
s=s.replace('''        "demo": True,
        "provenance": "demo_synthetic",
''','''        "demo": is_demo,
        "provenance": data_provenance,
''',1)
p.write_text(s)

p=root/'frontend/assets/js/ui.js'; s=p.read_text()
marker="  const noRows = (text='No records returned by the API.') => `<tr><td colspan=\"12\"><div class=\"empty-state\">${esc(text)}</div></td></tr>`;\n"
if marker not in s: raise SystemExit('UI helper anchor mismatch')
s=s.replace(marker, marker+'  const isDemoSource=value=>/demo|synthetic/i.test(String(value||\'\'));\n',1)
s=re.sub(r'\$\{([A-Za-z_$][A-Za-z0-9_$]*)\.provenance\?demoBadge\(\):\'\'\}',r"${isDemoSource(\1.provenance)?demoBadge():''}",s)
s=s.replace("const val=id=>$(id)?.value||'all', ward=val('#alert-ward-filter')", "const val=id=>$(id)?.value||'all', ward=val('#alert-ward-filter')")
s=s.replace("const rows=list.map(a=>`<tr><td>${esc(a.ts?new Date(a.ts).toLocaleString():'—')} ${a.provenance?demoBadge():''}</td>", "const rows=list.map(a=>`<tr><td>${esc(a.ts?new Date(a.ts).toLocaleString():'—')} ${isDemoSource(a.provenance)?demoBadge():''}</td>")
s=s.replace("<p class=\"card-subtitle\">${demoBadge()} · latest attempt</p>", "<p class=\"card-subtitle\">${isDemoSource(m.provenance)?demoBadge():''} · latest attempt</p>")
s=s.replace("if(!ward){app.innerHTML=", "if(!ward){app.innerHTML=",1)
anchor="    setMeta(`/ward/${id}`);const causes=explain?.explanation"
if anchor not in s: raise SystemExit('ward detail anchor mismatch')
s=s.replace(anchor,"    const wardIsDemo=ward.demo===true||isDemoSource(ward.provenance);setMeta(`/ward/${id}`);const causes=explain?.explanation",1)
s=s.replace("${state.demo?demoBadge():''}</strong>","${wardIsDemo?demoBadge():''}</strong>")
s=s.replace("${state.demo?demoBadge():''}</div>","${wardIsDemo?demoBadge():''}</div>")
s=s.replace("Top three signed contributions ${state.demo?'<span class=\"badge badge-demo\">DEMO DATA</span>':''}","Top three signed contributions ${wardIsDemo?'<span class=\"badge badge-demo\">DEMO DATA</span>':''}")
s=s.replace("${state.demo?'<span class=\"badge badge-demo\">DEMO DATA · SENSOR HISTORY</span>':''}","${history.some(item=>isDemoSource(item.provenance))?'<span class=\"badge badge-demo\">DEMO DATA · SENSOR HISTORY</span>':''}")
s=s.replace("${state.demo||/demo|synthetic/i.test(w.provenance||'')?demoBadge():''}","${isDemoSource(w.provenance)?demoBadge():''}")
p.write_text(s)

p=root/'TODO.md'; s=p.read_text()
old='- [ ] Use the system font stack; body ≥14px, dashboard labels 16px, map legend ≥12px, and no text below 12px. Dark mode swaps `--bg` to `#0F172A` and primary text to `#F8FAFC` with readable surfaces. Focus rings, loading/success/error states and contrast-safe disabled controls remain visible. The developer-only contrast-audit button scans the DOM and console-logs every pair below WCAG AA.'
new='- [ ] Use the system font stack; body ≥14px, dashboard labels 16px, map legend ≥12px, and no text below 12px. Dark mode swaps `--bg` to `#0F172A` and primary text to `#F8FAFC` with readable surfaces. Focus rings, loading/success/error states and contrast-safe disabled controls remain visible. The developer-only contrast-audit button scans DOM text pairs and focus-indicator tokens, console-logging every failure below WCAG AA / 3:1.'
if s.count(old)==1: s=s.replace(old,new,1)
p.write_text(s)
print('Updated non-text focus contrast audit, accurate per-record provenance, and TODO criterion.')