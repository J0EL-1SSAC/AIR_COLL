import React,{useEffect,useState} from 'react';

const statusLabel = status => ({OK:'LIVE DATA OK', DEGRADED:'DEGRADED', NO_DATA:'NO DATA', DISCONNECTED:'DISCONNECTED', CONNECTING:'CONNECTING'}[status] || status);

export default function DashboardHeader({airport, mode, status, message, utcNow, lastUpdateAge, onToggleLeft, onToggleRight, alertCounts, riskProfile, weather}) {
  const replay = mode === 'REPLAY';
  const [ist,setIst]=useState('--:--:--');
  const [runwayUse,setRunwayUse]=useState('unknown');
  const metar=weather?.metar?.decoded;
  const weatherLabel=mode==='REPLAY'?'Weather unavailable for replay':weather?.metar?.status==='AVAILABLE'||weather?.metar?.status==='STALE'
    ? `${metar?.wind_direction_variable?'VRB':metar?.wind_direction_true_deg==null?'Wind —':`${String(metar.wind_direction_true_deg).padStart(3,'0')}°`} ${metar?.wind_speed_kt??'—'} kt · Vis ${metar?.visibility_sm??'—'} SM · QNH ${metar?.qnh_hpa==null?'—':Math.round(metar.qnh_hpa)} hPa · ${metar?.flight_category||'Category —'} · METAR ${metar?.age_min==null?'—':Math.floor(metar.age_min)} min`
    : `Weather ${weather?.metar?.status==='STALE'?'STALE':'UNAVAILABLE'} · ${weather?.message||'No current VOMM METAR available.'}`;
  useEffect(()=>{const tick=()=>setIst(new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()));tick();const id=setInterval(tick,1000);return()=>clearInterval(id);},[]);
  useEffect(()=>{const load=()=>fetch(`${import.meta.env.VITE_API_BASE||'http://localhost:8000'}/api/runway-status`).then(r=>r.ok?r.json():null).then(data=>setRunwayUse(data?.runway_in_use?.estimate||'unknown')).catch(()=>setRunwayUse('unknown'));load();const id=setInterval(load,10000);return()=>clearInterval(id);},[mode]);
  return <>
    <header className="topbar">
      <div className="brand-block">
        <h1 className="brand-line">AIR_COL <span className="badge">RESEARCH</span></h1>
        <div className="brand-subtitle">{airport.icao} · {airport.radius_nm} NM surveillance area</div>
      </div>
      <div className="header-metrics">
        <span className={`badge ${replay ? 'mode-replay' : 'mode-live'}`}>{replay ? 'REPLAY MODE' : 'LIVE MODE'}</span>
        <span className={`status-chip status-${status}`} title={message}><i className="status-dot"/>{statusLabel(status)}</span>
        <span className="badge alert-count-chip" aria-label="Active alert counts">Alerts {Object.entries(alertCounts||{}).map(([level,count])=>`${level[0]} ${count}`).join(' · ')||'0'}</span>
        <span className="badge profile-header-chip">{riskProfile||'RISK —'}</span>
        <span className="badge">Runway in use · {runwayUse}</span>
        <button type="button" className={`weather-chip weather-${weather?.metar?.status||'checking'}`} title={weatherLabel} aria-label={`Airport weather: ${weatherLabel}`} onClick={()=>window.dispatchEvent(new CustomEvent('aircol-open-tab',{detail:'Weather'}))}>↗ {weatherLabel}</button>
        <div className="header-info">UTC <strong>{utcNow}</strong> · IST <strong>{ist}</strong></div>
        <div className="header-info">Updated <strong>{lastUpdateAge == null ? '—' : `${lastUpdateAge} s ago`}</strong></div>
      </div>
      <div className="header-actions">
        <button className="icon-button" type="button" onClick={onToggleLeft} aria-label="Collapse or expand side panel">Panel</button>
      </div>
    </header>
    {replay && <div className="replay-banner" role="status"><strong>REPLAY</strong><span>Recorded live-feed data is displayed. Live collection continues in the background.</span></div>}
    {riskProfile==='sensitive_test'&&<div className="test-profile-banner header-test-banner" role="status">Test profile active: alerts use loose thresholds and are not research results.</div>}
  </>;
}
