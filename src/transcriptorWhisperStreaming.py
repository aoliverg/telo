import sys
from transcriptorTelo import TranscriptorTelo

class TranscriptorWhisperStreaming(TranscriptorTelo):
    def __init__(self, config_path="configTranscriptorTelo.yaml"):
        super().__init__(config_path)
        self.processor = None
        self.online_model = None
        self.inicialitzar_motor()

    def inicialitzar_motor(self):
        try:
            from whisper_online import faster_whisper_connect
        except ImportError:
            print("[!] Error: Cal instal·lar 'whisper-streaming'. Executa:")
            print("    pip install --no-deps git+https://github.com/ufal/whisper_streaming.git")
            sys.exit(1)

        gen_cfg = self.config.get('general', {})
        model_path = gen_cfg.get('model_path', 'Systran/faster-whisper-medium')
        idioma = gen_cfg.get('idioma_defecte', 'ca')

        print(f"[*] Carregant motor whisper-streaming amb el model: {model_path}...")
        
        # Connectem directament amb el backend de whisper-streaming
        self.online_model = faster_whisper_connect(
            model_size=model_path,
            language=idioma
        )

    def iniciar_escolta(self, callback_text):
        """Executa el bucle de la llibreria delegant el control d'àudio."""
        self.is_listening = True
        print("[*] Iniciant escolta en directe amb whisper-streaming...")
        
        # Aquí utilitzem el mecanisme d'àudio propi que integra la llibreria
        try:
            while self.is_listening:
                # La llibreria processa l'àudio en streaming i retorna fragments confirmats
                res = self.online_model.process_next()
                if res and res[2]: # Text confirmat pel Local Agreement
                    text_net = res[2].strip()
                    if text_net:
                        callback_text(text_net)
        except Exception as e:
            print(f"[!] Error durant la transcripció: {e}")

    def aturar_escolta(self):
        self.is_listening = False
        print("[*] Escolta aturada.")
