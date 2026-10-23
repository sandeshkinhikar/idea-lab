(() => {
  'use strict';
  function rgb(value) {
    const text=String(value||'').trim();
    const hex=text.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if(hex){let raw=hex[1];if(raw.length===3)raw=raw.split('').map(x=>x+x).join('');return {r:parseInt(raw.slice(0,2),16),g:parseInt(raw.slice(2,4),16),b:parseInt(raw.slice(4,6),16),a:1};}
    const m=text.match(/rgba?\(([^)]+)\)/i); if(!m)return null;
    const parts=m[1].split(',').map(x=>parseFloat(x)); if(parts.length<3)return null;
    return {r:parts[0],g:parts[1],b:parts[2],a:parts.length>3?parts[3]:1};
  }
  function luminance(color) {
    const c=rgb(color);if(!c)return null;
    const channel=v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;};
    return .2126*channel(c.r)+.7152*channel(c.g)+.0722*channel(c.b);
  }
  function runContrastAudit() {
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
    console.info(result);window.lastContrastAuditResult=result;const target=document.querySelector('[data-contrast-result]');if(target)target.textContent=result;
    if(window.toast)window.toast(result,failures?'error':'success');return failures;
  }
  window.runContrastAudit=runContrastAudit;
})();
