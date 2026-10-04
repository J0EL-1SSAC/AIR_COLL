import React from 'react';
import {DISTANCE_FORMAT} from '../config';
import {localDateTimeInput,validateDateRange} from '../dateRange';

const ft = metres => metres == null ? '—' : `${Math.round(metres / DISTANCE_FORMAT.metersPerFoot).toLocaleString()} ft`;
const nm = metres => metres == null ? '—' : `${(metres / DISTANCE_FORMAT.metersPerNm).toFixed(2)} NM`;
const textCall = aircraft => aircraft?.callsign?.trim() || aircraft?.icao24 || 'Unknown';

function AlertCard({event, onClick, history=false}) {
  const a = textCall(event.aircraft_1), b = textCall(event.aircraft_2);
  return <button type="button" className={`alert-card risk-${event.peak_risk.toLowerCase()}`} onClick={()=>onClick(event)}>
    <div className="alert-card-top"><span className={`risk-badge risk-${event.current_risk.toLowerCase()}`}><i aria-hidden="true">◆</i>{event.current_risk}</span>
      <span className="profile-chip">{event.risk_profile}</span><span className="alert-status">{event.status}</span></div>
    <strong className="alert-title">POTENTIAL AIRCRAFT CONFLICT{event.alert_kind==='WATCH'?' · WATCH':''}</strong>
    <div className="alert-callsigns">{a} <span>↔</span> {b}</div>
    <div className="alert-metrics"><span>CPA {Math.round(event.time_to_cpa_s ?? event.min_time_to_cpa_s ?? 0)} s</span>
      <span>H {nm(history?event.min_h_sep_cpa_m:event.h_sep_cpa_m)}</span>
      <span>V {ft(history?event.min_v_sep_cpa_m:event.v_sep_cpa_m)}</span></div>
    <div className="alert-meta"><span>{history?`Duration ${Math.round(event.duration_s)} s`:`Age ${Math.round(event.duration_s)} s`}</span>
      <span>{event.confidence} confidence</span>
      <span>Data {Math.max(event.data_age_a_s||0,event.data_age_b_s||0).toFixed(0)} s</span></div>
    {history&&<div className="alert-resolution">Peak {event.peak_risk} · {event.resolution_reason||'Unresolved'}</div>}
    <div className="alert-disclaimer">Research estimate from public ADS-B data. Not an ATC or collision-avoidance alert.</div>
  </button>;
}

export default function AlertsPanel({active, history, profile, mode, filters, historyMessage, historyTotal=0, dateBounds, onFilterChange, onFocus, onInspect, loading}) {
  const dateZone=filters.dateZone||'IST';
  const riskOrder={CRITICAL:0,HIGH:1,MEDIUM:2,LOW:3};
  const activeItems=active.filter(item=>!filters.risk||item.current_risk===filters.risk||item.peak_risk===filters.risk)
    .sort((a,b)=>(riskOrder[a.current_risk]??4)-(riskOrder[b.current_risk]??4)||(a.time_to_cpa_s??Infinity)-(b.time_to_cpa_s??Infinity));
  const visibleHistory=history.filter(item=>!filters.risk||item.current_risk===filters.risk||item.peak_risk===filters.risk)
    .filter(item=>!filters.status||item.status===filters.status);
  const setRange=range=>{
    onFilterChange('range',range);onFilterChange('start','');onFilterChange('end','');
  };
  const rangeError=filters.range==='custom'?validateDateRange(filters.start,filters.end,dateBounds?.now_ts??Date.now()/1000,dateBounds?.earliest_ts,dateZone==='UTC'?'UTC':'Asia/Kolkata'):null;
  const dateMin=dateBounds?.earliest_ts?localDateTimeInput(dateBounds.earliest_ts,dateZone==='UTC'?'UTC':'Asia/Kolkata'):undefined;
  const dateMax=localDateTimeInput(dateBounds?.now_ts??Date.now()/1000,dateZone==='UTC'?'UTC':'Asia/Kolkata');
  return <div className="alerts-panel">
    <div className="alert-profile-row"><span>Active profile <strong className="profile-chip">{profile||'Loading…'}</strong></span>
      {profile==='sensitive_test'&&<strong className="test-profile-banner">Test profile active: alerts use loose thresholds and are not research results.</strong>}</div>
    <div className="alert-filters">
      <label>Risk<select value={filters.risk} onChange={e=>onFilterChange('risk',e.target.value)}><option value="">All</option><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option></select></label>
      <label>Status<select value={filters.status} onChange={e=>onFilterChange('status',e.target.value)}><option value="">All history</option><option>RESOLVED</option><option>ACTIVE</option><option>ESCALATED</option><option>CANDIDATE</option></select></label>
      <label>Mode<select value={filters.mode} onChange={e=>onFilterChange('mode',e.target.value)}><option value="CURRENT">Current ({mode})</option><option value="LIVE">LIVE</option><option value="REPLAY">REPLAY</option><option value="ALL">All modes</option></select></label>
    </div>
    <div className="range-presets segmented-control" aria-label="History time range">
      {[['1h','Last hour'],['6h','Last 6 h'],['24h','Last 24 h'],['all','All']].map(([value,label])=><button type="button" className="control-button" aria-pressed={filters.range===value} key={value} onClick={()=>setRange(value)}>{label}</button>)}
    </div>
    <details className="custom-range"><summary>Custom time range · {dateZone}</summary><button type="button" className="control-button" onClick={()=>onFilterChange('dateZone',dateZone==='IST'?'UTC':'IST')}>Show dates in {dateZone==='IST'?'UTC':'IST'}</button><div><label>From<input type="datetime-local" min={dateMin} max={dateMax} value={filters.start} onChange={e=>{onFilterChange('range','custom');onFilterChange('start',e.target.value);}}/></label><label>To<input type="datetime-local" min={dateMin} max={dateMax} value={filters.end} onChange={e=>{onFilterChange('range','custom');onFilterChange('end',e.target.value);}}/></label></div>{rangeError&&<p className="field-error" role="alert">{rangeError}</p>}</details>
    <div className="risk-legend" aria-label="Risk level legend">Risk levels: <span className="risk-low">◆ LOW</span><span className="risk-medium">◆ MEDIUM</span><span className="risk-high">◆ HIGH</span><span className="risk-critical">◆ CRITICAL</span></div>
    <h3 className="alert-section-title">Active alerts · {activeItems.length}</h3>
    {loading&&<div className="empty-state">Loading alert records…</div>}
    {!loading&&activeItems.length===0&&<div className="empty-state">No active Potential Aircraft Conflict alerts for this mode and filter.</div>}
    <div className="alert-card-list">{activeItems.map(event=><AlertCard key={event.event_id} event={event} onClick={onFocus}/>)}</div>
    <h3 className="alert-section-title">Event history · {visibleHistory.length}</h3>
    <p className="history-count">Showing {visibleHistory.length} of {historyTotal} {filters.mode==='CURRENT'?mode:filters.mode} events matching these filters.</p>
    {visibleHistory.length===0&&<div className="empty-state compact">{historyMessage||`0 ${filters.mode==='CURRENT'?mode:filters.mode} events match these history filters.`}</div>}
    <div className="alert-card-list history-list">{visibleHistory.map(event=><AlertCard key={event.event_id} event={event} history onClick={onInspect}/>)}</div>
    <button type="button" className="control-button history-more" onClick={()=>onFilterChange('reload',true)}>Refresh history</button>
  </div>;
}
