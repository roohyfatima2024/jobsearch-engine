"""Poll approved jobs and generate truthful drafts. Never submit applications."""
from contextlib import closing
import os, time, traceback
from app.main import db, tailor

POLL = max(10, int(os.getenv('APPROVAL_POLL_SECONDS', '20')))

def claim():
    with closing(db()) as c, c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT id FROM jobs WHERE status='approved' AND classification='eligible' ORDER BY score DESC LIMIT 1").fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='tailoring' WHERE id=? AND status='approved'", (row[0],))
        return row[0]

def failed(job_id, error):
    with closing(db()) as c, c:
        c.execute("UPDATE jobs SET status='tailor_failed', reason=? WHERE id=? AND status='tailoring'", (str(error)[:300], job_id))

if __name__ == '__main__':
    print('Approval worker started. No application submission functionality.', flush=True)
    while True:
        try:
            job_id = claim()
            if job_id:
                print('Tailoring:', job_id, flush=True)
                try:
                    tailor(job_id)
                except BaseException as e:
                    failed(job_id, e)
                    print('Tailoring failed:', job_id, repr(e), flush=True)
                continue
        except Exception:
            traceback.print_exc()
        time.sleep(POLL)
