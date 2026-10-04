import React,{useEffect,useState} from 'react';

const statusLabel = status => ({OK:'LIVE DATA OK', DEGRADED:'DEGRADED', NO_DATA:'NO DATA', DISCONNECTED:'DISCONNECTED', CONNECTING:'CONNECTING'}[status] || status);

export default function DashboardHeader({airport, mode, status, message, utcNow, lastUpdateAge, onToggleLeft, onToggleRight, alertCounts, riskProfile}) {
  const replay = mode === 'REPLAY';
  const [ist,setIst]=useState('--:--:--');
  const [runwayUse,setRunwayUse]=useState('unknown');
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
