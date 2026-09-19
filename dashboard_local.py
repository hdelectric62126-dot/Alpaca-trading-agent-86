"""Loopback-only dashboard. Credentials stay in a local server config, never HTML."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def make_handler(config, port):
    html=(Path(__file__).parent/'dashboard.html').read_bytes()
    remote=config['url'].rstrip('/')
    if not remote.startswith('https://'):
        raise ValueError('Dashboard feed must use HTTPS')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_GET(self):
            if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
                self.send_error(403)
                return
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
                self.send_error(403)
                return
            parsed=urlsplit(self.path)
            status=200
            if parsed.path=='/':
                body,content_type=html,'text/html; charset=utf-8'
            elif parsed.path=='/api/dashboard':
                days=parse_qs(parsed.query).get('days',['7'])[0]
                if days not in ('1','7','30'):
                    self.send_error(400)
                    return
                request=Request(remote+'/api/dashboard?days='+days,headers={'Authorization':'Bearer '+config['token']})
                try:
                    with urlopen(request,timeout=12) as response:
                        body=response.read(2_000_001)
                        if len(body)>2_000_000:
                            raise ValueError('Response too large')
                        json.loads(body)
                except (HTTPError,URLError,TimeoutError,ValueError):
                    body=json.dumps({'error':'Cannot reach the agent right now. Displayed data may be out of date.'}).encode()
                    status=503
                content_type='application/json'
            else:
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; frame-ancestors 'none'; base-uri 'none'")
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    server=ThreadingHTTPServer(('127.0.0.1',args.port),make_handler(config,args.port))
    print(f'Dashboard: http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()


if __name__=='__main__':
    main()
