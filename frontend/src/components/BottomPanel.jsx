import React,{useState} from 'react';
import ClosestApproachesTable from './ClosestApproachesTable';
import ReplayControls from './ReplayControls';
import AlertsPanel from './AlertsPanel';

const TABS=['Closest Approaches','Recorded Data Replay','Alerts'];

export default function BottomPanel({pairs,thresholds,replaySettings,replay,onFocusPair,alerts,history,riskProfile,mode,alertFilters,onAlertFilterChange,onFocusAlert,onInspectEvent,alertsLoading}) {
  const [tab,setTab]=useState(TABS[0]);
  return <section className="bottom-panel" aria-label="Research details">
    <div className="bottom-tabs" role="tablist" aria-label="Bottom panel views">
      {TABS.map(label=><button key={label} type="button" role="tab" aria-selected={tab===label}
        className="tab-button" onClick={()=>setTab(label)}>{label}{label==='Closest Approaches'&&pairs.length>0?` (${pairs.length})`:''}</button>)}
    </div>
    <div className="tab-content" role="tabpanel">
      {tab===TABS[0]&&<ClosestApproachesTable pairs={pairs} thresholds={thresholds} onFocus={onFocusPair}/>}
      {tab===TABS[1]&&<ReplayControls {...replay} settings={replaySettings}/>}
      {tab===TABS[2]&&<AlertsPanel active={alerts} history={history} profile={riskProfile} mode={mode}
        filters={alertFilters} onFilterChange={onAlertFilterChange} onFocus={onFocusAlert} onInspect={onInspectEvent} loading={alertsLoading}/>}
    </div>
  </section>;
}
