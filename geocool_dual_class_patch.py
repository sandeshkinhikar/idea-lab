from pathlib import Path
root=Path('/home/ubuntu/geocool')
def replace(path,old,new,count=1):
    p=root/path;s=p.read_text();n=s.count(old)
    if n!=count:raise SystemExit(f'{path}: expected {count} anchor(s), found {n}: {old[:160]!r}')
    p.write_text(s.replace(old,new,count))

# API: keep the edge-level prediction explicitly absent while returning both the
# actual edge AHP class and the corresponding ward-level trained-model result.
replace('backend/routes/wards.py',
'''def _edge_explanation(ward_id: str, edge: dict) -> dict:
    limits = query_all''',
'''def _edge_explanation(ward_id: str, edge: dict, thresholds=None) -> dict:
    thresholds = thresholds or get_setting("thresholds", {"low_max": .25, "moderate_max": .5, "high_max": .75})
    limits = query_all''')
replace('backend/routes/wards.py',
'''    risk = float(edge.get("ahp_risk") or 0.0)
    parts = ", ".join''',
'''    risk = float(edge.get("ahp_risk") or 0.0)
    ward_scores = _ward_risk(ward_id, None)
    ward_ml_score = ward_scores.get("ml_score")
    parts = ", ".join''')
replace('backend/routes/wards.py',
'''        "ml_score": None,
        "ahp_score": round(risk, 4),
        "risk_class": edge.get("risk_class"),''',
'''        "ml_score": None,
        "ml_class": None,
        "ward_ml_score": round(float(ward_ml_score), 4) if ward_ml_score is not None else None,
        "ward_ml_class": _risk_class(ward_ml_score, thresholds),
        "ahp_score": round(risk, 4),
        "ahp_class": _risk_class(risk, thresholds),
        "risk_class": edge.get("risk_class") or _risk_class(risk, thresholds),''')
replace('backend/routes/wards.py',
'''        row["risk_class"] = _risk_class(float(row.get("ahp_risk") or 0), thresholds)
        row["demo"] = row.get("provenance") == "demo_synthetic"''',
'''        row["risk_class"] = _risk_class(float(row.get("ahp_risk") or 0), thresholds)
        row["ahp_class"] = row["risk_class"]
        row["ml_score"] = None
        row["ml_class"] = None
        row["demo"] = row.get("provenance") == "demo_synthetic"''')
replace('backend/routes/wards.py',
'''        row["risk_class"] = _risk_class(float(row.get("ahp_risk") or 0), thresholds)
        row["ml_score"] = None
        row["demo"] = row.get("provenance") == "demo_synthetic"
        row["explanation"] = _edge_explanation(ward_id, row)''',
'''        row["risk_class"] = _risk_class(float(row.get("ahp_risk") or 0), thresholds)
        row["ahp_class"] = row["risk_class"]
        row["ml_score"] = None
        row["ml_class"] = None
        row["demo"] = row.get("provenance") == "demo_synthetic"
        row["explanation"] = _edge_explanation(ward_id, row, thresholds)''')
replace('backend/routes/wards.py',
'''        return _edge_explanation(ward_id, edge)''',
'''        return _edge_explanation(ward_id, edge, thresholds)''')
replace('backend/routes/wards.py',
'''        "risk_class": _risk_class(class_score, thresholds),
        "score_basis": scores["score_basis"],''',
'''        "risk_class": _risk_class(class_score, thresholds),
        "ml_class": _risk_class(ml_score, thresholds),
        "ahp_class": _risk_class(ahp_score, thresholds),
        "score_basis": scores["score_basis"],''')

# UI: distinguish the two class systems in the ward register and detail cards.
replace('frontend/assets/js/ui.js',
'''<td>${riskBadge(w.risk_class)}</td><td>${showVal(w.ml_score)}</td><td>${showVal(w.ahp_score)}</td>''',
'''<td>${w.ml_class?riskBadge(w.ml_class):'—'}</td><td>${w.ahp_class?riskBadge(w.ahp_class):'—'}</td><td>${showVal(w.ml_score)}</td><td>${showVal(w.ahp_score)}</td>''')
replace('frontend/assets/js/ui.js',
'''table(['Ward','Risk class','ML risk','AHP risk','Mean risk','P90 risk','Edges',''],rows)''',
'''table(['Ward','ML class','AHP class','ML risk','AHP risk','Mean risk','P90 risk','Edges',''],rows)''')
replace('frontend/assets/js/ui.js',
'''card('Ward risk distribution',`<div class="risk-list">${byRisk}</div>`,`Classified from API output · ${state.demo?'DEMO DATA':''}`)''',
'''card('Primary risk-class distribution',`<div class="risk-list">${byRisk}</div>`,`Uses ML when available; AHP is the reference class · ${state.demo?'DEMO DATA':''}`)''')
replace('frontend/assets/js/ui.js',
'''<div class="content-grid-3">${[['ML risk score',ward.ml_score],['AHP risk (1 − suitability)',ward.ahp_score],['Risk class',ward.risk_class]].map(([label,val])=>`<div class="metric-card"><span class="metric-label">${label}</span><strong class="metric-value">${typeof val==='number'?num(val):esc(val||'—')}</strong>${wardIsDemo?demoBadge():''}</div>`).join('')}</div><div class="content-grid-2"''',
'''<div class="content-grid-4">${[['ML risk score',ward.ml_score],['ML class',ward.ml_class||'No ML prediction'],['AHP risk (1 − suitability)',ward.ahp_score],['AHP class',ward.ahp_class||'—']].map(([label,val])=>`<div class="metric-card"><span class="metric-label">${label}</span><strong class="metric-value">${typeof val==='number'?num(val):esc(val||'—')}</strong>${wardIsDemo?demoBadge():''}</div>`).join('')}</div><p class="form-help">Primary class basis: ${esc(ward.class_basis||'AHP-derived weak label')}. AHP class is displayed separately as the reference.</p><div class="content-grid-2"''')

# Map: make class meanings explicit in both ward and street popups.
replace('frontend/assets/js/map.js',
'''      <p>Ward ${id} · ${escapeHTML(record.risk_class || 'Unclassified')}</p>
      <p>ML risk: ${num(record.ml_score)} · AHP risk: ${num(record.ahp_score)}</p>''',
'''      <p>Ward ${id} · primary ML class: ${escapeHTML(record.ml_class || 'No ML prediction')}</p>
      <p>AHP reference class: ${escapeHTML(record.ahp_class || '—')} · AHP risk: ${num(record.ahp_score)}</p>
      <p>ML risk: ${num(record.ml_score)} · ${escapeHTML(record.score_basis || 'score basis unavailable')}</p>''')
replace('frontend/assets/js/map.js',
'''    const score = data?.risk_class ? `<p class="popup-risk-summary"><strong>${escapeHTML(data.risk_class)} risk</strong> · ML score ${num(data.ml_score)} · AHP risk ${num(data.ahp_score)}</p>` : '';''',
'''    const score = data?.edge_id
      ? `<p class="popup-risk-summary"><strong>Street AHP class: ${escapeHTML(data.ahp_class || data.risk_class || 'Unclassified')}</strong> · edge AHP risk ${num(data.ahp_score)} · no edge-level ML prediction is stored.</p><p>Ward ML class: ${escapeHTML(data.ward_ml_class || 'No ML prediction')} · ward ML score ${num(data.ward_ml_score)}</p>`
      : data?.risk_class ? `<p class="popup-risk-summary"><strong>Primary ML class: ${escapeHTML(data.ml_class || data.risk_class)}</strong> · ML score ${num(data.ml_score)} · AHP class: ${escapeHTML(data.ahp_class || '—')} · AHP risk ${num(data.ahp_score)}</p>` : '';''')
replace('frontend/assets/js/map.js',
'''          <p>Ward ${escapeHTML(edge.ward_id || '\\u2014')} \\u00b7 ${escapeHTML(edge.risk_class || 'Unclassified')} \\u00b7 AHP risk ${num(edge.ahp_risk)}</p>''',
'''          <p>Ward ${escapeHTML(edge.ward_id || '\\u2014')} \\u00b7 AHP class ${escapeHTML(edge.ahp_class || edge.risk_class || 'Unclassified')} \\u00b7 AHP risk ${num(edge.ahp_risk)}</p>''')

# Dashboard detail summary cards need a four-column desktop layout, collapsing
# to two columns on tablets and one on phones.
replace('frontend/assets/css/theme.css',
'''.content-grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }''',
'''.content-grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.content-grid-4 { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }''')
replace('frontend/assets/css/theme.css',
'''  .kpi-grid { grid-template-columns: repeat(3, minmax(150px, 1fr)); }
  .dashboard-grid''',
'''  .kpi-grid { grid-template-columns: repeat(3, minmax(150px, 1fr)); }
  .content-grid-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dashboard-grid''')
replace('frontend/assets/css/theme.css',
'''  .content-grid-2, .content-grid-3, .form-grid { grid-template-columns: 1fr; }''',
'''  .content-grid-2, .content-grid-3, .content-grid-4, .form-grid { grid-template-columns: 1fr; }''')
print('API, frontend class labels, ward/street popups, and responsive summary cards updated.')