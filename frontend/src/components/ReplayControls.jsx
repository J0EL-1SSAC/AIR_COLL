import React from 'react';

export default function ReplayControls({settings,speed,onSpeed,onStart,onStop,busy,mode,message}) {
  const options=[...new Set([...settings.speed_steps,settings.default_speed,settings.min_speed,settings.max_speed])]
    .filter(value=>value>=settings.min_speed&&value<=settings.max_speed).sort((a,b)=>a-b);
  return <div className="replay-controls">
    <label className="field-label">Playback speed
      <select className="select-control" value={speed} onChange={event=>onSpeed(Number(event.target.value))}>
        {options.map(value=><option value={value} key={value}>{value}×</option>)}
      </select>
    </label>
    <button className="control-button primary" type="button" disabled={busy||mode==='REPLAY'} onClick={onStart}>Replay recorded feed</button>
    <button className="control-button" type="button" disabled={busy||mode!=='REPLAY'} onClick={onStop}>Stop replay</button>
    <span className="replay-message" role="status">{message}</span>
  </div>;
}
