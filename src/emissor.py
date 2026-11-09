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
    def __init__(self, config_server, mode="AUTOMÀTIC", config_styles=None, config_full=None):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        template_folder = os.path.join(base_dir, '..', 'templates')

        self.app = Flask(__name__, template_folder=template_folder)
        self.app.secret_key = "einacat_2026_secret"
        
        # Mode threading per evitar conflictes amb PyQt6
        self.socketio = SocketIO(self.app, async_mode='threading', cors_allowed_origins="*")
        
        self.mode = mode
        self.host = config_server.get('host', '0.0.0.0')
        self.port = config_server.get('port', 5000)
        self.channels = config_server.get('channels', [])
        self.stats_audiencia = {ch['id']: 0 for ch in self.channels}

        # Resolució dels estils en memòria
        styles_cfg = config_styles
        if not styles_cfg and config_full:
            styles_cfg = config_full.get('common', {}).get('styles', {})
        styles_cfg = styles_cfg or {}

        self.estils_web = self._processar_estils_web(styles_cfg)

    def _processar_estils_web(self, styles_cfg):
        """Processa el diccionari d'estils passat pel client en memòria."""
        estils_defecte = {
            'normal': {
                'font_family': 'Arial, sans-serif',
                'font_size': '2.5rem',
                'font_color': '#FFFFFF',
                'background_color': '#000000',
                'alignment': 'center',
                'max_lines': 2
            },
            'ulleres': {
                'font_family': "'Courier New', monospace",
                'font_size': '2.8rem',
                'font_color': '#00FF66',
                'background_color': '#000000',
                'alignment': 'center',
                'max_lines': 1
            }
        }

        web_cfg = styles_cfg.get('web', {})
        glasses_cfg = styles_cfg.get('glasses', {})

        return {
            'normal': {
                'font_family': web_cfg.get('font_family', estils_defecte['normal']['font_family']),
                'font_size': web_cfg.get('font_size', estils_defecte['normal']['font_size']),
                'font_color': web_cfg.get('font_color', estils_defecte['normal']['font_color']),
                'background_color': web_cfg.get('background_color', estils_defecte['normal']['background_color']),
                'alignment': web_cfg.get('alignment', estils_defecte['normal']['alignment']),
                'max_lines': web_cfg.get('max_lines', estils_defecte['normal']['max_lines'])
            },
            'ulleres': {
                'font_family': glasses_cfg.get('font_family', estils_defecte['ulleres']['font_family']),
                'font_size': glasses_cfg.get('font_size', estils_defecte['ulleres']['font_size']),
                'font_color': glasses_cfg.get('font_color', estils_defecte['ulleres']['font_color']),
                'background_color': glasses_cfg.get('background_color', estils_defecte['ulleres']['background_color']),
                'alignment': glasses_cfg.get('alignment', estils_defecte['ulleres']['alignment']),
                'max_lines': glasses_cfg.get('max_lines', estils_defecte['ulleres']['max_lines'])
            }
        }

    def obtenir_ip_local(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def setup_routes(self):
        @self.app.route('/')
        def serve_lobby():
            return render_template('lobby.html', channels=self.channels)

        @self.app.route('/<channel_id>')
        def serve_channel(channel_id):
            ch_info = next((c for c in self.channels if c['id'] == channel_id), None)
            if not ch_info:
                return f"Canal '{channel_id}' no trobat", 404
            
            return render_template(
                'index.html', 
                lang=channel_id, 
                label=ch_info['label'],
                web_cfg=self.estils_web,
                is_glasses=False
            )

        @self.app.route('/ulleres/<channel_id>')
        def serve_glasses_channel(channel_id):
            ch_info = next((c for c in self.channels if c['id'] == channel_id), None)
            if not ch_info:
                return f"Canal '{channel_id}' no trobat", 404
            
            return render_template(
                'index.html', 
                lang=channel_id, 
                label=ch_info['label'],
                web_cfg=self.estils_web,
                is_glasses=True
            )

        @self.socketio.on('join')
        def on_join(data):
            room = data.get('lang')
            if room in self.stats_audiencia:
                join_room(room)
                self.stats_audiencia[room] += 1
                self.socketio.emit('status', {'msg': f'Connected to {room}'}, room=request.sid)

    def emetre(self, channel_id, text, perf=None):
        """Envia text de subtítol a les sales connectades."""
        self.socketio.emit('nou_subtitol', {'text': text, 'perf': perf}, room=channel_id)

    def iniciar(self):
        self.setup_routes()
        
        def _run():
            try:
                self.socketio.run(
                    self.app, 
                    host=self.host, 
                    port=self.port, 
                    debug=False, 
                    use_reloader=False, 
                    allow_unsafe_werkzeug=True
                )
            except Exception as e:
                print(f"⚠️ Error al servidor web: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        
    def actualitzar_canals(self, nous_canals):
        self.channels = nous_canals
        self.stats_audiencia = {ch['id']: 0 for ch in self.channels}
