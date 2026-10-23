(() => {
  'use strict';

  const fills = {
    low: '#DCFCE7',
    moderate: '#FEF9C3',
    high: '#FFEDD5',
    critical: '#FEE2E2'
  };
  const orderedClasses = ['low', 'moderate', 'high', 'critical'];
  const osmFallback = {
    label: 'OpenStreetMap · CARTO key not configured',
    tile_url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>'
  };
  let map = null;
  let baseLayer = null;
  let wardsLayer = null;
  let streetsLayer = null;
  let currentWards = [];
  let sourceWards = [];
  let onSelect = () => {};
  let currentLayer = 'Risk Class';

  const riskClass = (ward) => String(ward?.risk_class || ward?.ml_class || 'low').toLowerCase();
  const centerOf = (ward) => {
    const center = ward?.center;
    return center
      ? [Number(center.lat ?? center[1]), Number(center.lon ?? center.lng ?? center[0])]
      : [21.1458, 79.0882];
  };

  function featureOf(record) {
    let geometry = record?.geom || record?.geometry;
    if (typeof geometry === 'string') {
      try { geometry = JSON.parse(geometry); } catch (_) { return null; }
    }
    if (!geometry) return null;
    return {
      type: 'Feature',
      geometry: geometry.type === 'Feature' ? geometry.geometry : geometry,
      properties: { ...record }
    };
  }

  function polygonStyle(feature) {
    const properties = feature?.properties || {};
    return {
      color: '#0F172A',
      weight: 1,
      opacity: 1,
      fillColor: fills[riskClass(properties)] || '#CBD5E1',
      fillOpacity: 0.75
    };
  }

  function popup(record) {
    const demo = /demo|synthetic/i.test(String(record.provenance || ''))
      ? '<span class="badge badge-demo">DEMO DATA</span>' : '';
    const basis = record.score_basis || 'AHP-inverted reference until trained ML output is available.';
    const id = escapeHTML(record.ward_id || '');
    return `<div class="map-risk-popup">
      <strong>${escapeHTML(record.name || record.ward_id)}</strong>
      <p>Ward ${id} · primary ML class: ${escapeHTML(record.ml_class || 'No ML prediction')}</p>
      <p>AHP reference class: ${escapeHTML(record.ahp_class || '—')} · AHP risk: ${num(record.ahp_score)}</p>
      <p>ML risk: ${num(record.ml_score)} · ${escapeHTML(record.score_basis || 'score basis unavailable')}</p>
      <p>${escapeHTML(basis)}</p>${demo}
      <div class="popup-explanation" data-popup-explanation data-ward="${id}" aria-live="polite">Loading score breakdown…</div>
      <p><button class="btn btn-small btn-primary" data-map-open="${id}">Open details</button></p>
    </div>`;
  }

  function weightRows(weights) {
    const order = ['lst', 'ndvi', 'pm', 'population', 'slope'];
    return order.filter((key) => weights && weights[key] !== undefined)
      .map((key) => `<div class="popup-weight-row"><span>${escapeHTML(key.toUpperCase())}</span><strong>${num(weights[key], 2)}</strong></div>`).join('');
  }

  function contributionRows(contributions) {
    const top = (Array.isArray(contributions) ? contributions : [])
      .slice().sort((a, b) => Math.abs(Number(b.contribution) || 0) - Math.abs(Number(a.contribution) || 0)).slice(0, 3);
    if (!top.length) return '<p class="empty-state">No contribution values were returned; no breakdown is inferred.</p>';
    const max = Math.max(...top.map((item) => Math.abs(Number(item.contribution) || 0)), 0.000001);
    return top.map((item) => {
      const value = Number(item.contribution) || 0;
      const width = Math.max(4, Math.min(100, Math.round(Math.abs(value) / max * 100)));
      const sign = value > 0 ? '+' : '';
      return `<div class="popup-contribution-row"><span class="popup-feature">${escapeHTML(item.feature || 'Feature')}</span><span class="popup-contribution-track"><span class="popup-contribution-bar ${value < 0 ? 'negative' : ''}" style="width:${width}%"></span></span><strong>${sign}${num(value, 3)}</strong></div>`;
    }).join('');
  }

  function explanationPanel(data, record) {
    const constant = data?.constant_inputs?.pm;
    const showConstant = constant && constant.constant === true;
    const isDemo = data?.demo === true || /demo|synthetic/i.test(String(record?.provenance || data?.provenance || ''));
    const weakText = data?.weak_label || 'Labels derive from AHP risk thresholds, not field ground truth.';
    const chips = `<div class="popup-chips"><span class="badge badge-warning">WEAK-LABEL</span>${showConstant ? `<span class="badge badge-warning">CONSTANT INPUT \u00b7 PM ${num(constant.value, 1)} placeholder</span>` : ''}${isDemo ? '<span class="badge badge-demo">DEMO DATA</span>' : ''}</div>`;
    const score = data?.edge_id
      ? `<p class="popup-risk-summary"><strong>Street AHP class: ${escapeHTML(data.ahp_class || data.risk_class || 'Unclassified')}</strong> \u00b7 edge AHP risk ${num(data.ahp_score)} \u00b7 no edge-level ML prediction is stored.</p><p>Ward ML class: ${escapeHTML(data.ward_ml_class || 'No ML prediction')} \u00b7 ward ML score ${num(data.ward_ml_score)}</p>`
      : data?.risk_class ? `<p class="popup-risk-summary"><strong>Primary ML class: ${escapeHTML(data.ml_class || data.risk_class)}</strong> \u00b7 ML score ${num(data.ml_score)} \u00b7 AHP class: ${escapeHTML(data.ahp_class || '—')} \u00b7 AHP risk ${num(data.ahp_score)}</p>` : '';
    const why = data?.explanation ? `<p class="popup-why"><strong>Why this risk?</strong> ${escapeHTML(data.explanation)}</p>` : '<p class="data-notice">No explanation sentence was returned for this score.</p>';
    const weights = weightRows(data?.weights);
    return `${chips}${score}${why}<div class="popup-evidence"><div><h4>Top 3 signed contributions</h4>${contributionRows(data?.contributions)}</div><div><h4>AHP weights \u00b7 reference</h4><div class="popup-weight-grid">${weights || '<span>Weights unavailable</span>'}</div><p class="form-help">Higher AHP suitability is better/cooler; risk is inverted.</p></div></div><p class="form-help">${escapeHTML(weakText)}</p>`;
  }

  function loadPopupExplanation(event, record, edgeId = null) {
    const target = event.popup?.getElement()?.querySelector('[data-popup-explanation]');
    if (!target || target.dataset.loaded === 'true' || target.dataset.loading === 'true') return;
    target.dataset.loading = 'true';
    const wardId = String(record.ward_id || '');
    const edgeQuery = edgeId ? `?edge_id=${encodeURIComponent(edgeId)}` : '';
    window.GeoAPI.get(`/api/explain/${encodeURIComponent(wardId)}${edgeQuery}`)
      .then((data) => {
        target.innerHTML = explanationPanel(data, record);
        target.dataset.loaded = 'true';
      })
      .catch((error) => {
        target.innerHTML = `<div class="data-notice" role="alert">Explanation unavailable: ${escapeHTML(error.message)}. Open details to retry.</div>`;
      })
      .finally(() => { delete target.dataset.loading; });
  }

  function reportMissing(host) {
    if (!host || host.querySelector('[data-map-error]')) return;
    const notice = document.createElement('div');
    notice.className = 'empty-state';
    notice.dataset.mapError = '';
    notice.textContent = 'Map library did not load. Ward table and all other controls remain available.';
    host.append(notice);
  }

  function init(container, wards, select = () => {}) {
    const element = typeof container === 'string' ? document.querySelector(container) : container;
    if (!element) return;
    currentWards = wards || [];
    sourceWards = currentWards;
    onSelect = select;
    currentLayer = 'Risk Class';
    if (!window.L) {
      reportMissing(element);
      return;
    }
    if (map) {
      map.remove();
      map = null;
    }
    wardsLayer = null;
    streetsLayer = null;
    baseLayer = null;
    map = L.map(element, { scrollWheelZoom: false }).setView([21.1458, 79.0882], 11);
    const activeMap = map;
    const providerLabel = document.querySelector('[data-basemap-provider]');
    baseLayer = L.tileLayer(osmFallback.tile_url, { attribution: osmFallback.attribution, maxZoom: 19 }).addTo(activeMap);
    if (providerLabel) providerLabel.textContent = osmFallback.label;
    window.GeoAPI.get('/api/map-config').then((config) => {
      if (map !== activeMap || !config?.tile_url) return;
      if (config.provider === 'osm') {
        if (providerLabel) providerLabel.textContent = config.label || osmFallback.label;
        return;
      }
      const nextLayer = L.tileLayer(config.tile_url, {
        attribution: config.attribution || osmFallback.attribution,
        ...(config.subdomains ? { subdomains: config.subdomains } : {}),
        maxZoom: config.provider === 'carto' ? 20 : 19
      });
      let tileErrors = 0;
      nextLayer.on('tileerror', () => {
        tileErrors += 1;
        if (config.provider !== 'carto' || tileErrors < 3 || map !== activeMap || baseLayer !== nextLayer) return;
        activeMap.removeLayer(nextLayer);
        baseLayer = L.tileLayer(osmFallback.tile_url, { attribution: osmFallback.attribution, maxZoom: 19 }).addTo(activeMap);
        if (providerLabel) providerLabel.textContent = 'OpenStreetMap fallback · CARTO tiles unavailable';
        console.warn('[GeoCool map] CARTO tiles failed; using the labeled OpenStreetMap fallback.');
      });
      if (baseLayer && activeMap.hasLayer(baseLayer)) activeMap.removeLayer(baseLayer);
      baseLayer = nextLayer;
      nextLayer.addTo(activeMap);
      if (providerLabel) providerLabel.textContent = config.label || 'Configured basemap';
    }).catch((error) => {
      console.warn('[GeoCool map] Basemap configuration unavailable; using OpenStreetMap fallback.', error);
    });

    const features = currentWards.map(featureOf).filter(Boolean);
    if (features.length) {
      wardsLayer = L.geoJSON({ type: 'FeatureCollection', features }, {
        style: polygonStyle,
        onEachFeature: (feature, layer) => {
          const ward = currentWards.find((item) => String(item.ward_id) === String(feature.properties.ward_id)) || feature.properties;
          layer.bindPopup(popup(ward), { maxWidth: 460, maxHeight: 540 });
          layer.on('popupopen', (event) => loadPopupExplanation(event, ward));
        }
      }).addTo(map);
      if (wardsLayer.getBounds().isValid()) map.fitBounds(wardsLayer.getBounds(), { padding: [18, 18], maxZoom: 13 });
    } else if (currentWards.length) {
      map.setView(centerOf(currentWards[0]), 12);
    }
    setTimeout(() => map?.invalidateSize(), 50);
  }

  function zoomWard(id) {
    const ward = sourceWards.find((item) => String(item.ward_id) === String(id));
    if (!ward || !map) return false;
    const feature = featureOf(ward);
    if (feature) {
      const layer = L.geoJSON(feature);
      if (layer.getBounds().isValid()) map.fitBounds(layer.getBounds(), { padding: [30, 30], maxZoom: 15 });
    } else {
      map.setView(centerOf(ward), 15);
    }
    return true;
  }

  function geometryFromEdge(edge) {
    let geometry = edge?.geom || edge?.geometry;
    if (typeof geometry === 'string') {
      try { geometry = JSON.parse(geometry); } catch (_) { geometry = null; }
    }
    if (!geometry && edge?.geometry_wkt) {
      const coordinates = edge.geometry_wkt.match(/[-+]?\d+(?:\.\d+)?\s+[-+]?\d+(?:\.\d+)?/g) || [];
      geometry = {
        type: 'LineString',
        coordinates: coordinates.map((pair) => pair.trim().split(/\s+/).map(Number))
      };
    }
    return geometry?.type === 'Feature' ? geometry.geometry : geometry;
  }

  function showStreets(edges) {
    if (!map || !window.L) return;
    if (streetsLayer) map.removeLayer(streetsLayer);
    if (wardsLayer && map.hasLayer(wardsLayer)) map.removeLayer(wardsLayer);
    const features = (edges || []).map((edge) => {
      const geometry = geometryFromEdge(edge);
      return geometry ? { type: 'Feature', geometry, properties: edge } : null;
    }).filter(Boolean);
    streetsLayer = L.geoJSON({ type: 'FeatureCollection', features }, {
      style: { color: '#2563EB', weight: 3, opacity: 0.9 },
      onEachFeature: (feature, layer) => {
        const edge = feature.properties || {};
        const demo = /demo|synthetic/i.test(String(edge.provenance || ''))
          ? '<span class="badge badge-demo">DEMO DATA</span>' : '';
        layer.bindPopup(`<div class="map-risk-popup"><strong>${escapeHTML(edge.edge_id || 'Street edge')}</strong>
          <p>Ward ${escapeHTML(edge.ward_id || '\u2014')} \u00b7 AHP class ${escapeHTML(edge.ahp_class || edge.risk_class || 'Unclassified')} \u00b7 AHP risk ${num(edge.ahp_risk)}</p>
          <p>LST ${num(edge.lst, 1)} \u00b7 NDVI ${num(edge.ndvi, 2)} \u00b7 PM ${num(edge.pm, 1)}</p>
          ${demo}<div class="popup-explanation" data-popup-explanation data-ward="${escapeHTML(edge.ward_id || '')}" aria-live="polite">Loading score breakdown…</div>
          <p><button class="btn btn-small btn-primary" data-map-open="${escapeHTML(edge.ward_id || '')}">Open ward details</button></p></div>`, { maxWidth: 460, maxHeight: 540 });
        layer.on('popupopen', (event) => loadPopupExplanation(event, edge, edge.edge_id));
      }
    }).addTo(map);
    if (features.length && streetsLayer.getBounds().isValid()) map.fitBounds(streetsLayer.getBounds(), { padding: [25, 25], maxZoom: 15 });
  }

  function numericLayerClass(name, featureProperties) {
    const field = {
      'LST': 'mean_lst',
      'NDVI': 'mean_ndvi',
      'Slope': 'mean_slope',
      'Population': 'mean_population',
      'AHP Suitability': 'ahp_suitability'
    }[name];
    if (!field) return null;
    const values = sourceWards.map((ward) => Number(ward[field])).filter(Number.isFinite);
    const value = Number(featureProperties[field]);
    if (!Number.isFinite(value) || !values.length) return null;
    const min = Math.min(...values);
    const max = Math.max(...values);
    let position = max === min ? 0.5 : (value - min) / (max - min);
    if (name === 'NDVI' || name === 'AHP Suitability') position = 1 - position;
    const index = Math.min(3, Math.floor(Math.max(0, Math.min(0.999999, position)) * 4));
    return orderedClasses[index];
  }

  async function setLayer(name) {
    if (!map) return;
    currentLayer = name;
    if (name === 'Street-level') {
      if (!streetsLayer) {
        const result = await window.GeoAPI.get('/api/edges?limit=5000');
        showStreets(result.edges || []);
      } else {
        if (wardsLayer && map.hasLayer(wardsLayer)) map.removeLayer(wardsLayer);
        if (!map.hasLayer(streetsLayer)) streetsLayer.addTo(map);
      }
      return;
    }
    if (streetsLayer && map.hasLayer(streetsLayer)) map.removeLayer(streetsLayer);
    if (!wardsLayer) return;
    if (!map.hasLayer(wardsLayer)) wardsLayer.addTo(map);
    if (name === 'Risk Class') {
      wardsLayer.setStyle(polygonStyle);
      return;
    }
    wardsLayer.setStyle((feature) => {
      const properties = feature?.properties || {};
      const thematicClass = numericLayerClass(name, properties);
      return thematicClass ? { ...polygonStyle(feature), fillColor: fills[thematicClass] } : polygonStyle(feature);
    });
  }

  function filterRisk(level) {
    const source = sourceWards;
    const visible = level === 'all' ? source : source.filter((ward) => riskClass(ward) === level);
    const host = document.querySelector('#risk-map');
    if (host) {
      init(host, visible, onSelect);
      sourceWards = source;
    }
  }

  window.GeoMap = {
    init,
    zoomWard,
    showStreets,
    filterRisk,
    setLayer,
    selectWard: (id) => onSelect(sourceWards.find((ward) => String(ward.ward_id) === String(id)))
  };
})();
