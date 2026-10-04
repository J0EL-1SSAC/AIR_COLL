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

export default function FiltersPanel({airport,counts, predictionCounts, mapLayers, onLayerChange, labelMode, onLabelMode, basemap, onBasemap, groundOnly, onGroundOnly, runwayMessage,weather,frequencyData}) {
  const layers = [
    ['trails','Trails'], ['predictions','Predictions'], ['uncertainty','Uncertainty circles'],
    ['closestApproaches','Closest-approach lines'], ['radius','Monitoring radius'], ['estimatedRoute','Selected aircraft estimated route'],
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
            <option value="light">Light · CARTO Voyager</option><option value="osm">OpenStreetMap</option><option value="dark">Dark · CARTO</option>
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
    <section className="panel-section"><h3 className="panel-heading">Live aviation weather · {airport.icao}</h3>
      <WeatherAndFrequencies weather={weather} frequencyData={frequencyData}/>
    </section>
    <section className="panel-section"><h3 className="panel-heading">Legend</h3><Legend airport={airport}/></section>
    {runwayMessage&&<p className="panel-note runway-unavailable" role="status">{runwayMessage}</p>}
    <p className="panel-note">Map tiles require internet access. Check CARTO and OpenStreetMap tile usage terms before sustained use.</p>
  </aside>;
}

function WeatherAndFrequencies({weather,frequencyData}) {
  const metar=weather?.metar||{}, taf=weather?.taf||{};
  const fields=(report)=>[
    ['Temperature',report?.temp==null?null:`${report.temp} °C`],
    ['Dew point',report?.dewp==null?null:`${report.dewp} °C`],
    ['Wind',report?.wdir==null?null:`${report.wdir}°${report.wspd==null?'':` · ${report.wspd} kt`}`],
    ['Visibility',report?.visib==null?null:`${report.visib} statute mi`],
    ['Altimeter setting',report?.altim==null?null:`${report.altim} inHg`],
    ['Weather',report?.wxString||null],
    ['Clouds',Array.isArray(report?.clouds)&&report.clouds.length?report.clouds.map(c=>`${c.cover||''}${c.base==null?'':` ${c.base} ft`}`).join(', '):null],
  ].filter(([,value])=>value!=null);
  const time=value=>value?new Date(value).toLocaleString():'Not available';
  return <div className="weather-content">
    <div className={`weather-status weather-${weather?.status||'CHECKING'}`} role="status">{weather?.status||'CHECKING'} · live provider</div>
    <h4>Current report (METAR)</h4>
    {metar.report?.rawOb?<>
      <p className="weather-raw">{metar.report.rawOb}</p>
      <p className="weather-meta">Observed {time(metar.observation_time_utc)} · retrieved {time(metar.fetched_at_utc)} · {metar.age_s==null?'age unavailable':`${Math.floor(metar.age_s)} s old`} · {metar.status}</p>
      <dl className="weather-fields">{fields(metar.report).map(([label,value])=><React.Fragment key={label}><dt>{label}</dt><dd>{value}</dd></React.Fragment>)}</dl>
    </>:<p className="empty-state">Current METAR: Not available{metar.error?` · ${metar.error}`:''}</p>}
    <h4>Terminal forecast (TAF)</h4>
    {taf.report?.rawTAF?<><p className="weather-raw">{taf.report.rawTAF}</p><p className="weather-meta">Issued {time(taf.issue_time_utc)} · valid {time(taf.report.validTimeFrom?new Date(taf.report.validTimeFrom*1000).toISOString():null)} to {time(taf.report.validTimeTo?new Date(taf.report.validTimeTo*1000).toISOString():null)} · {taf.status}</p></>:<p className="empty-state">Current TAF: Not available{taf.error?` · ${taf.error}`:''}</p>}
    <p className="panel-note">{weather?.message||'Weather comes from AviationWeather.gov; no local estimate or fallback is used.'}</p>
    <h4>Airport radio frequencies · reference data</h4>
    {frequencyData?.facilities?Object.entries(frequencyData.facilities).map(([type,rows])=><div className="frequency-row" key={type}><b>{type}</b><span>{rows.map(row=>`${row.frequency_mhz??'—'} MHz${row.description?` · ${row.description}`:''}`).join(' / ')||'Not listed'}</span></div>):<p className="empty-state">Frequency reference not available{frequencyData?.error?` · ${frequencyData.error}`:''}</p>}
    <p className="panel-note">{frequencyData?.verification||'Verify all frequency values against the official AIP.'}</p>
  </div>;
}
