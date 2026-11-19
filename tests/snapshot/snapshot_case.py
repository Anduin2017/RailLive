"""Choose an actually operating day for the retained integration scenarios."""
from contextlib import closing
from datetime import date
import json
import sqlite3
from train_relations import TIMETABLE, timetable


def case_day():
    runs,calendar,_=timetable()
    required=('G1211','G7','G817','G6605','G1284','Z164')
    days={n:set().union(*(calendar[v[1]] for v in runs.values() if v[0]==n)) for n in required}
    candidates=set.intersection(*days.values())
    candidates={d for d in candidates if {d-2,d-1}.issubset(days['Z164'])}
    if not candidates:
        raise AssertionError('快照缺少既有验收组合的共同开行日期，请核对开行日历')
    with closing(sqlite3.connect(TIMETABLE)) as db:
        version=json.loads(db.execute('SELECT json FROM metadata').fetchone()[0])['feed_version']
    target=date.fromisoformat(version).toordinal()+1
    return date.fromordinal(min(candidates,key=lambda d:(abs(d-target),d))).isoformat()
