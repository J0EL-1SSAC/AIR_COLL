import React from 'react';
function Legend({airport}) {
  const lowFeet=airport.low_altitude_ft;
  const mediumFeet=airport.altitude_bands_m.medium_max/0.3048;
  return <div className="legend-list" aria-label="Map legend">
    <div className="legend-row"><i className="legend-swatch band-ground"/>On ground · circle</div>
    <div className="legend-row"><i className="legend-swatch band-low"/>Below {Math.round(lowFeet).toLocaleString()} ft · triangle</div>
    <div className="legend-row"><i className="legend-swatch band-medium"/>{Math.round(lowFeet).toLocaleString()}–{Math.round(mediumFeet).toLocaleString()} ft · triangle</div>
    <div className="legend-row"><i className="legend-swatch band-high"/>Above {Math.round(mediumFeet).toLocaleString()} ft · triangle</div>
    <div className="legend-row"><i className="legend-line legend-trail"/>Observed trail</div>
    <div className="legend-row"><i className="legend-line dashed legend-prediction"/>Predicted path · dashed</div>
    <div className="legend-row"><i className="legend-line cpa-red-line"/>Small CPA separation · label shown</div>
    <div className="legend-row">! Reduced-quality or stale track</div>
  </div>;
}

export default function FiltersPanel({airport,counts, predictionCounts, mapLayers, onLayerChange, labelMode, onLabelMode, basemap, onBasemap, groundOnly, onGroundOnly, runwayMessage}) {
  const layers = [
    ['trails','Trails'], ['predictions','Predictions'], ['uncertainty','Uncertainty circles'],
    ['closestApproaches','Closest-approach lines'], ['radius','Monitoring radius'],
    ['runways','Runways'], ['runwayBuffer','Runway buffer'], ['approachCorridors','Approach corridors'],
  ];
  return <aside className="side-panel left" aria-label="Map layers and filters">
    <h2 className="panel-heading">Layers and filters</h2>
    <section className="panel-section">
      <div className="control-stack">
        {layers.map(([key,label])=><label className="check-row" key={key}><input type="checkbox" checked={mapLayers[key]} onChange={e=>onLayerChange(key,e.target.checked)}/><span>{label}</span></label>)}
        <label className="check-row"><input type="checkbox" checked={groundOnly} onChange={e=>onGroundOnly(e.target.checked)}/><span>On-ground only</span></label>
        <label className="field-label">Aircraft labels
          <select className="select-control" value={labelMode} onChange={e=>onLabelMode(e.target.value)}>
            <option value="off">Off</option><option value="callsign">Callsign</option><option value="detailed">Detailed</option>
          </select>
        </label>
        <label className="field-label">Basemap
          <select className="select-control" value={basemap} onChange={e=>onBasemap(e.target.value)}>
            <option value="dark">Dark · CARTO</option><option value="light">Light · OpenStreetMap</option>
          </select>
        </label>
      </div>
    </section>
    <section className="panel-section"><h3 className="panel-heading">Coverage</h3>
      <div className="coverage-grid">
        <div className="metric-tile"><strong className="metric-value">{counts.aircraft_count || 0}</strong><span className="metric-label">aircraft now</span></div>
        <div className="metric-tile"><strong className="metric-value">{counts.low_or_ground_count || 0}</strong><span className="metric-label">low / ground</span></div>
        {mapLayers.predictions && <div className="metric-tile"><strong className="metric-value">{predictionCounts.predicted}</strong><span className="metric-label">predicted paths</span></div>}
        {mapLayers.predictions && <div className="metric-tile"><strong className="metric-value">{predictionCounts.skipped}</strong><span className="metric-label">skipped by quality</span></div>}
      </div>
    </section>
    <section className="panel-section"><h3 className="panel-heading">Legend</h3><Legend airport={airport}/></section>
    {runwayMessage&&<p className="panel-note runway-unavailable" role="status">{runwayMessage}</p>}
    <p className="panel-note">Map tiles require internet access. Check CARTO and OpenStreetMap tile usage terms before sustained use.</p>
  </aside>;
}
