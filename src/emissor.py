
import os
import socket
import threading
import sys
import logging
from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

class EmissorWeb:
    def __init__(self, config_server, mode="AUTOMÀTIC"):
        # Calculem la ruta absoluta del directori on està aquest fitxer (src/)
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Si la carpeta 'templates' està al directori principal (forad de src/):
        template_folder = os.path.join(base_dir, '..', 'templates')
        # Si la carpeta 'templates' està DINS de src/ (src/templates/), fes servir:
        # template_folder = os.path.join(base_dir, 'templates')

        self.app = Flask(__name__, template_folder=template_folder)
        self.app.secret_key = "einacat_2026_secret"
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")
        
        self.mode = mode
        self.host = config_server.get('host', '0.0.0.0')
        self.port = config_server.get('port', 5000)
        self.channels = config_server.get('channels', [])
        self.stats_audiencia = {ch['id']: 0 for ch in self.channels}

    def obtenir_ip_local(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def mostrar_benvinguda(self):
        """Imprimeix el banner quan tot el sistema està llest."""
        
        pass
        '''
        ip = self.obtenir_ip_local()
        print("\n" + "═"*65)
        print("  EinaCAT - SISTEMA DE SUBTITULACIÓ AR (V 2.0)")
        print("─"*65)
        print(f"  [*] SERVIDOR WEB:  http://{ip}:{self.port}")
        print(f"  [*] LOCALHOST:     http://localhost:{self.port}")
        print("─"*65)
        print("  [*] CANALS ACTIUS:")
        for ch in self.channels:
            print(f"      > {ch['id'].upper()}: {ch['label']}")
        print("─"*65)
        print("  [*] SISTEMA EN MARXA. Escoltant àudio...")
        print("═"*65 + "\n")
        '''

    def setup_routes(self):
        @self.app.route('/')
        def serve_lobby():
            return render_template('lobby.html', channels=self.channels)

        @self.app.route('/<channel_id>')
        def serve_channel(channel_id):
            ch_info = next((c for c in self.channels if c['id'] == channel_id), None)
            if not ch_info: return "Canal no trobat", 404
            return render_template('index.html', lang=channel_id, label=ch_info['label'])

        @self.socketio.on('join')
        def on_join(data):
            room = data.get('lang')
            if room in self.stats_audiencia:
                join_room(room)
                self.stats_audiencia[room] += 1
                self.socketio.emit('status', {'msg': f'Connected to {room}'}, room=request.sid)
                self.imprimir_terminal()

    def imprimir_terminal(self, perf=None):
        audiencia = " | ".join([f"{k.upper()}:{v}" for k, v in self.stats_audiencia.items()])
        cadencia = f" >> IA:{perf['dt']:.2f}s | WPM:{perf['wpm']}" if perf else ""
        sys.stdout.write(f"\r    [LIVE] {audiencia}{cadencia}              ")
        sys.stdout.flush()

    def emetre(self, channel_id, text, perf=None):
        self.socketio.emit('nou_subtitol', {'text': text, 'perf': perf}, room=channel_id)

    def iniciar(self):
        self.setup_routes()
        t = threading.Thread(
            target=self.socketio.run,
            args=(self.app,),
            kwargs={'host': self.host, 'port': self.port, 'debug': False, 'use_reloader': False, 'allow_unsafe_werkzeug': True},
            daemon=True
        )
        t.start()
        
    def actualitzar_canals(self, nous_canals):
        """Permet actualitzar dinàmicament els canals quan es carrega un SRT multilingüe"""
        self.channels = nous_canals
        self.stats_audiencia = {ch['id']: 0 for ch in self.channels}
