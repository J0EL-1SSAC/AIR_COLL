import React from 'react';
import {DISTANCE_FORMAT} from '../config';

const number = (value, digits=0) => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString(undefined,{maximumFractionDigits:digits,minimumFractionDigits:digits});
const row = (label,value) => <React.Fragment key={label}><dt>{label}</dt><dd>{value}</dd></React.Fragment>;

export default function DetailsPanel({aircraft, prediction, event, runway}) {
  if (event) return <aside className="side-panel right event-details" aria-label="Alert event details">
    <h2 className="panel-heading">Event details</h2>
    <strong className="alert-title">POTENTIAL AIRCRAFT CONFLICT</strong>
    <div className="event-json-label">Full stored event record · {event.mode} · {event.risk_profile}</div>
    <pre className="event-json">{JSON.stringify(event,null,2)}</pre>
  </aside>;
  if (runway) return <aside className="side-panel right runway-details" aria-label="Selected runway details">
    <h2 className="panel-heading">Runway details</h2>
    <div className="details-call">{runway.pair_identifier}</div>
    <dl className="details-list">
      {row('Length',`${number(runway.length_m,0)} m · ${number(runway.length_m/DISTANCE_FORMAT.metersPerNm,2)} NM`)}
      {row('Width',`${number(runway.width_m,1)} m`)}
      {row('Surface',runway.surface||'Not listed')}
      {row('Lighting',runway.lighted==null?'Not listed':runway.lighted?'Yes':'No')}
      {row('Status',runway.closed?'Closed':'Open in source data')}
      {[runway.end_a,runway.end_b].map(end=><React.Fragment key={end.identifier}>
        {row(`End ${end.identifier}`,`${number(end.latitude,6)}°, ${number(end.longitude,6)}°`)}
        {row(`${end.identifier} true heading`,`${number(end.true_heading_deg,1)}°`)}
        {row(`${end.identifier} computed magnetic heading`,`${number(end.magnetic_heading_deg,1)}°`)}
        {row(`${end.identifier} magnetic designator`,end.magnetic_designator_heading_deg==null?'—':`${number(end.magnetic_designator_heading_deg,0)}°`)}
        {row(`${end.identifier} displaced threshold`,`${number(end.displaced_threshold_m,1)} m`)}
      </React.Fragment>)}
    </dl>
    <p className="panel-note">Open dataset values need verification against the official AIP / aerodrome chart.</p>
  </aside>;
  if (!aircraft) return <aside className="side-panel right"><h2 className="panel-heading">Aircraft details</h2><div className="empty-state">Select an aircraft on the map.</div></aside>;
  const altitude = aircraft.geo_altitude_m ?? aircraft.baro_altitude_m;
  const altitudeBasis = aircraft.geo_altitude_m != null ? 'Geometric' : aircraft.baro_altitude_m != null ? 'Barometric' : 'Unavailable';
  const stale = aircraft.quality_flags?.includes('LOW_QUALITY') || aircraft.quality_flags?.includes('stale_position');
  return <aside className="side-panel right" aria-label="Selected aircraft details">
    <h2 className="panel-heading">Aircraft details</h2>
    <div className="details-call">{aircraft.callsign || 'Unknown callsign'}</div>
    <div className="details-icao">ICAO24 · <span className="mono">{aircraft.icao24}</span></div>
    {stale && <p className="quality-tag">! STALE / LOW QUALITY</p>}
    <dl className="details-list">
      {row('Altitude',`${number(altitude == null ? null : altitude / DISTANCE_FORMAT.metersPerFoot)} ft · ${number(altitude,0)} m`)}
      {row('Ground speed',`${number(aircraft.velocity_mps == null ? null : aircraft.velocity_mps * DISTANCE_FORMAT.metersPerSecondToKnots,1)} kt`)}
      {row('Track',`${number(aircraft.track_deg,1)}° true`)}
      {row('Vertical rate',`${number(aircraft.vertical_rate_mps == null ? null : aircraft.vertical_rate_mps * 60 / DISTANCE_FORMAT.metersPerFoot)} ft/min`)}
      {row('On ground',aircraft.on_ground == null ? 'Unknown' : aircraft.on_ground ? 'Yes' : 'No')}
      {row('Data age',`${number(aircraft.age_s,1)} s`)}
      {row('Distance from airport',`${number(aircraft.distance_nm,1)} NM`)}
      {row('Altitude basis',altitudeBasis)}
      {row('Data quality',aircraft.quality_flags?.length ? <span className="quality-flags">{aircraft.quality_flags.map(flag=><span className="quality-tag" key={flag}>{flag}</span>)}</span> : 'Reported fields present')}
      {row('Prediction',prediction?.status==='SKIPPED'?`Skipped · ${prediction.reason_code}`:prediction?.status==='PREDICTED'?'Available':'Unavailable')}
      {prediction?.status==='PREDICTED' && row('End uncertainty',`${number(prediction.points.at(-1)?.uncertainty_radius_m,0)} m`)}
    </dl>
  </aside>;
}
