(() => {
  'use strict';
  const active = new Map();
  function warn(container, text = 'Chart.js did not load. The rest of the page remains available; charts are omitted.') {
    const node = typeof container === 'string' ? document.querySelector(container) : container;
    if (node) { const warning = document.createElement('p'); warning.className = 'badge badge-warning'; warning.textContent = text; node.append(warning); }
  }
  function draw(canvas, config) {
    const el = typeof canvas === 'string' ? document.querySelector(canvas) : canvas;
    if (!el) return;
    if (!window.Chart) { warn(el.parentElement); return; }
    if (active.has(el)) active.get(el).destroy();
    const chart = new Chart(el, config); active.set(el, chart); return chart;
  }
  window.GeoCharts = {
    available() { return !!window.Chart; },
    warn,
    contribution(canvas, items) {
      if (!Array.isArray(items) || !items.length) return;
      const top = [...items].sort((a,b) => Math.abs(Number(b.contribution ?? b.value) || 0) - Math.abs(Number(a.contribution ?? a.value) || 0)).slice(0,3);
      draw(canvas, { type:'bar', data:{ labels:top.map(x=>x.feature || x.name || 'Feature'), datasets:[{ label:'Signed contribution', data:top.map(x=>Number(x.contribution ?? x.value) || 0), backgroundColor:top.map(x=>(Number(x.contribution ?? x.value)||0)<0?'#0F766E':'#2563EB'), borderRadius:5 }] }, options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>` ${c.raw >= 0 ? '+' : ''}${Number(c.raw).toFixed(3)}`}}}, scales:{x:{grid:{color:'rgba(100,116,139,.2)'},ticks:{color:'#334155'}},y:{grid:{display:false},ticks:{color:'#0F172A'}}} } });
    },
    sparkline(canvas, points, field = 'ml_score') {
      if (!points?.length) return;
      const labels = points.map((p,i)=>p.ts ? new Date(p.ts).toLocaleDateString() : `${i+1}`);
      draw(canvas,{type:'line',data:{labels,datasets:[{label:'Trend',data:points.map(p=>Number(p[field] ?? p.temperature ?? p.value)),borderColor:'#2563EB',backgroundColor:'rgba(37,99,235,.13)',fill:true,tension:.3,pointRadius:2}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{display:false},y:{ticks:{color:'#334155'},grid:{color:'rgba(100,116,139,.2)'}}}}});
    },
    confusion(canvas, matrix, labels = []) {
      if (!Array.isArray(matrix) || !matrix.length) return;
      const names = labels.length ? labels : matrix.map((_,i)=>`Class ${i+1}`);
      const colors=['#DCFCE7','#FEF9C3','#FFEDD5','#FEE2E2','#BFDBFE','#DDD6FE'];
      const datasets=names.map((name,x)=>({label:`Predicted ${name}`,data:matrix.map(row=>Number(row[x])||0),backgroundColor:colors[x%colors.length],borderColor:'#0F172A',borderWidth:1}));
      draw(canvas,{type:'bar',data:{labels:names,datasets},options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{color:'#0F172A'}},tooltip:{callbacks:{label:c=>` ${c.dataset.label}: ${c.raw}`}}},scales:{x:{stacked:true,title:{display:true,text:'Number of records'},ticks:{color:'#0F172A'},grid:{color:'rgba(100,116,139,.2)'}},y:{stacked:true,title:{display:true,text:'Actual class'},ticks:{color:'#0F172A'},grid:{display:false}}}}});
    }
  };
})();
