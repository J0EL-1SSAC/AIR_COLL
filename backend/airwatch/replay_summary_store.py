"""Rebuildable per-cycle derived summaries for recorded-feed replay only."""
import sqlite3


class ReplaySummaryStore:
    def __init__(self,path): self.path=path
    def initialize(self):
        with sqlite3.connect(self.path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS replay_summary_jobs (
                session_id TEXT PRIMARY KEY, start_ts REAL NOT NULL, end_ts REAL NOT NULL,
                total_cycles INTEGER NOT NULL, completed_cycles INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, updated_ts REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS replay_cycle_summaries (
                mode TEXT NOT NULL CHECK(mode='REPLAY'), session_id TEXT NOT NULL,
                cycle_time REAL NOT NULL, aircraft_count INTEGER NOT NULL, low_altitude_count INTEGER NOT NULL,
                on_ground_count INTEGER NOT NULL, pair_count INTEGER NOT NULL, alerts_opened INTEGER NOT NULL,
                PRIMARY KEY(session_id,cycle_time))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_replay_cycles_session_time ON replay_cycle_summaries(session_id,cycle_time)")
    def begin(self,session_id,start,end,total,now):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM replay_cycle_summaries WHERE session_id=?",(session_id,))
            db.execute("INSERT OR REPLACE INTO replay_summary_jobs(session_id,start_ts,end_ts,total_cycles,completed_cycles,status,updated_ts) VALUES(?,?,?,?,0,'RUNNING',?)",
                       (session_id,start,end,total,now))
    def record(self,session_id,cycle_time,aircraft,low,ground,pairs,opened,now):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO replay_cycle_summaries VALUES('REPLAY',?,?,?,?,?,?,?)",
                       (session_id,cycle_time,aircraft,low,ground,pairs,opened))
            db.execute("UPDATE replay_summary_jobs SET completed_cycles=(SELECT COUNT(*) FROM replay_cycle_summaries WHERE session_id=?),updated_ts=? WHERE session_id=?",
                       (session_id,now,session_id))
    def finish(self,session_id,status,now):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE replay_summary_jobs SET status=?,updated_ts=? WHERE session_id=?",(status,now,session_id))
    def job(self,session_id):
        with sqlite3.connect(self.path) as db:
            row=db.execute("SELECT session_id,start_ts,end_ts,total_cycles,completed_cycles,status,updated_ts FROM replay_summary_jobs WHERE session_id=?",(session_id,)).fetchone()
        return None if row is None else dict(zip(("session_id","start_time","end_time","total_cycles","completed_cycles","status","updated_ts"),row))
    def query(self,start,end,limit,offset):
        with sqlite3.connect(self.path) as db:
            total=db.execute("SELECT COUNT(*) FROM replay_cycle_summaries WHERE cycle_time>=? AND cycle_time<=?",(start,end)).fetchone()[0]
            rows=db.execute("SELECT cycle_time,aircraft_count,low_altitude_count,on_ground_count,pair_count,alerts_opened FROM replay_cycle_summaries WHERE cycle_time>=? AND cycle_time<=? ORDER BY cycle_time LIMIT ? OFFSET ?",(start,end,limit,offset)).fetchall()
        return [{"time":r[0],"aircraft":r[1],"low_altitude":r[2],"on_ground":r[3],"pairs":r[4],"alerts_opened":r[5]} for r in rows],total
