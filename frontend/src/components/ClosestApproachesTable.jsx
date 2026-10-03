import React from 'react';
import {DISTANCE_FORMAT} from '../config';

const display = (value,digits=1)=>value==null?'—':Number(value).toLocaleString(undefined,{maximumFractionDigits:digits,minimumFractionDigits:digits});

export default function ClosestApproachesTable({pairs,thresholds,onFocus}) {
  const converging = pairs.filter(pair=>pair.converging&&!pair.cpa_in_past)
    .sort((a,b)=>a.h_sep_cpa_m-b.h_sep_cpa_m);
  if (!converging.length) return <div className="empty-state">No converging pairs within the lookahead window.</div>;
  return <>
    <div className="pair-table-wrap"><table className="pair-table">
      <thead><tr><th>Aircraft</th><th>Time to CPA</th><th>Horizontal at CPA</th><th>Vertical at CPA</th><th>Closing speed</th><th>Altitude basis</th><th>Data age</th><th>Spacing band</th></tr></thead>
      <tbody>{converging.map(pair=>{
        const horizontalNm=pair.h_sep_cpa_m/DISTANCE_FORMAT.metersPerNm;
        const band=horizontalNm<=thresholds.near_nm?'near':horizontalNm<=thresholds.amber_nm?'amber':'neutral';
        const verticalFeet=pair.v_sep_cpa_m==null?null:pair.v_sep_cpa_m/DISTANCE_FORMAT.metersPerFoot;
        const closingKt=pair.closing_speed_mps*DISTANCE_FORMAT.metersPerSecondToKnots;
        return <tr key={pair.pair_key} tabIndex={0} onClick={()=>onFocus(pair)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' ')onFocus(pair);}}>
          <td className="callsigns">{pair.aircraft_a.callsign||pair.aircraft_a.icao24} / {pair.aircraft_b.callsign||pair.aircraft_b.icao24}</td>
          <td>{display(pair.t_cpa_s,0)} s</td><td>{display(horizontalNm,2)} NM</td>
          <td>{verticalFeet==null?'—':`${display(verticalFeet,0)} ft`}</td><td>{display(closingKt,1)} kt</td>
          <td>{pair.altitude_basis}</td><td>{display(pair.data_age_a_s,1)} / {display(pair.data_age_b_s,1)} s</td>
          <td><span className={`pair-state pair-state-${band}`}>{band==='near'?'Small separation':band==='amber'?'Closer estimate':'Closest approach'}</span></td>
        </tr>;
      })}</tbody>
    </table></div>
    <div className="pair-note">Research estimate. Not an official separation determination.</div>
  </>;
}
