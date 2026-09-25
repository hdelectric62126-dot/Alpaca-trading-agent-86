"""Authenticated read-only dashboard feed. No trading client or write SQL."""
import hmac
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs


def strict_json(value):
    if isinstance(value, dict):
        return {k: strict_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [strict_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def dashboard_snapshot(path, days=7):
    if days not in (1, 7, 30):
        raise ValueError('Invalid period')
    now = datetime.now(timezone.utc)
    cutoff = (now-timedelta(days=days)).isoformat()
    root = Path(path)
    runtime = None
    try:
        runtime = json.loads(root.with_name('runtime_status.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    # mode=ro must not create or migrate a missing or old database.
    db = sqlite3.connect(root.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(sql, args=()):
            return [dict(r) for r in db.execute(sql, args)]
        columns = {r[1] for r in db.execute('PRAGMA table_info(paper_trades)')}
        verified = 'fill_verified' in columns
        closed = rows("SELECT symbol,score,quantity,entry_price,exit_price,realized_pnl,closed_at FROM paper_trades WHERE fill_verified=1 AND status='CLOSED' AND realized_pnl IS NOT NULL AND closed_at>=? ORDER BY closed_at,id", (cutoff,)) if verified else []
        open_trades = rows("SELECT symbol,quantity,entry_price,opened_at FROM paper_trades WHERE fill_verified=1 AND status='OPEN' ORDER BY opened_at DESC", ()) if verified else []
        legacy = db.execute('SELECT COUNT(*) FROM paper_trades'+(' WHERE fill_verified=0' if verified else '')).fetchone()[0]
        cycles = rows("SELECT timestamp,symbol,total_score,decision,rejection_reason,current_price FROM analysis_cycles WHERE timestamp>=? ORDER BY timestamp DESC,id DESC LIMIT 80", (cutoff,)) if 'analysis_cycles' in tables else []
        rejects = rows("SELECT rejection_reason AS reason,COUNT(*) AS count FROM analysis_cycles WHERE timestamp>=? AND decision='REJECT' GROUP BY rejection_reason ORDER BY count DESC LIMIT 8", (cutoff,)) if 'analysis_cycles' in tables else []
        intents = rows("SELECT symbol,side,status,filled_qty,filled_price,created_at,completed_at FROM order_intents ORDER BY created_at DESC LIMIT 30") if 'order_intents' in tables else []
        pending = db.execute("SELECT COUNT(*) FROM order_intents WHERE status='pending'").fetchone()[0] if 'order_intents' in tables else 0
        health = rows('SELECT category,failures,blocked_until,updated_at FROM guardian_health ORDER BY category') if 'guardian_health' in tables else []
        last_cycle = db.execute('SELECT MAX(timestamp) FROM analysis_cycles').fetchone()[0] if 'analysis_cycles' in tables else None
    finally:
        db.close()
    total = peak = drawdown = 0.0
    curve = []
    for trade in closed:
        total += float(trade['realized_pnl'])
        peak = max(peak,total)
        drawdown = max(drawdown,peak-total)
        curve.append({'time':trade['closed_at'],'value':round(total,6)})
    research = None
    try:
        report = json.loads(root.with_name('after_hours_recommendations.json').read_text(encoding='utf-8'))
        research = {k:report.get(k) for k in ('generated_at','recommendation','method')}
        research['studied_symbols'] = sum(s.get('status') == 'studied' for s in report.get('studies',[]))
    except (OSError, ValueError, TypeError):
        pass
    return strict_json(dict(generated_at=now.isoformat(), days=days, mode='PAPER', read_only=True,
        runtime=runtime, research=research, last_cycle=last_cycle,
        metrics=dict(realized_pnl=round(total,6) if closed else None, closed_trades=len(closed),
                     wins=sum(t['realized_pnl']>0 for t in closed),
                     win_rate=100*sum(t['realized_pnl']>0 for t in closed)/len(closed) if closed else None,
                     drawdown=round(drawdown,6) if closed else None,
                     legacy_excluded=legacy,pending_orders=pending),
        curve=curve, closed_trades=list(reversed(closed[-100:])), tracked_positions=open_trades,
        cycles=cycles,rejections=rejects,orders=intents,guardian=health))


def make_handler(path, token, ops_token=''):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, data):
            body=json.dumps(data,allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed=urlsplit(self.path)
            if parsed.path=='/health':
                return self.respond(200,{'status':'available'})
            supplied=self.headers.get('Authorization','')
            authorized=(bool(token) and hmac.compare_digest(supplied,'Bearer '+token)) or (bool(ops_token) and hmac.compare_digest(supplied,'Bearer '+ops_token))
            if not authorized:
                return self.respond(401,{'error':'Unauthorized'})
            if parsed.path!='/api/dashboard':
                return self.respond(404,{'error':'Not found'})
            try:
                days=int(parse_qs(parsed.query).get('days',['7'])[0])
                payload=dashboard_snapshot(path,days)
            except ValueError:
                return self.respond(400,{'error':'Invalid period'})
            except (OSError,sqlite3.Error):
                return self.respond(503,{'error':'Journal temporarily unavailable'})
            return self.respond(200,payload)

    return Handler


def start_dashboard_feed(path):
    token=os.getenv('DASHBOARD_READ_TOKEN','')
    ops_token=os.getenv('OPS_READ_TOKEN','')
    if len(token)<32 and len(ops_token)<32:
        print('[DASHBOARD] feed disabled; read token not configured')
        return None
    server=ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','8080'))),make_handler(path,token,ops_token))
    thread=threading.Thread(target=server.serve_forever,daemon=True,name='dashboard-read-only')
    thread.start()
    print('[DASHBOARD] authenticated read-only feed started')
    return server
