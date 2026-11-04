import sounddevice as sd
import numpy as np
import sys
import queue
import mlx_whisper
from transcriptorTelo import TranscriptorTelo

class TranscriptorMLX(TranscriptorTelo):
    """
    Transcriptor MLX simplificat per a la màxima velocitat i continuïtat en macOS.
    """
    def __init__(self, config_path="configTranscriptorTelo.yaml"):
        super().__init__(config_path)
        self.audio_queue = queue.Queue()
        self.inicialitzar_motor()

    def inicialitzar_motor(self):
        gen_cfg = self.config.get('general', {})
        self.model_path = gen_cfg.get('model_path', 'mlx-community/whisper-large-v3-mlx')
        print(f"[*] Carregant mlx-whisper a la GPU ({self.model_path})...")

    def iniciar_escolta(self, callback_resultat):
        self.is_listening = True
        
        cap_cfg = self.config.get('audio_capture', {})
        mlx_cfg = self.config.get('mlx', {})

        sample_rate = cap_cfg.get('sample_rate', 16000)
        device_idx = cap_cfg.get('input_device_index', None)
        block_sec = cap_cfg.get('block_duration_sec', 1.5)
        overlap_sec = cap_cfg.get('overlap_duration_sec', 0.4)
        rms_thresh = cap_cfg.get('rms_silence_threshold', 0.003)
        temp_val = float(mlx_cfg.get('temperature', 0.0))

        samples_per_block = int(sample_rate * block_sec)
        overlap_samples = int(sample_rate * overlap_sec)
        
        buffer = np.zeros((0, 1), dtype=np.float32)

        def audio_callback(indata, frames, time_info, status):
            if self.is_listening:
                self.audio_queue.put(indata.copy())

        self.stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            device=device_idx,
            dtype='float32',
            callback=audio_callback
        )

        with self.stream:
            print(f"\n[*] Transcriptor a punt. Escoltant... (Ctrl+C per sortir)\n")
            
            while self.is_listening:
                try:
                    chunk = self.audio_queue.get(timeout=0.05)
                    buffer = np.vstack((buffer, chunk))

                    rms = np.sqrt(np.mean(chunk**2))
                    mida_barra = int(rms * 100)
                    barra = "█" * min(mida_barra, 25)
                    sys.stdout.write(f"\r🎤 Nivell: [{barra:<25}] {rms:.4f}")
                    sys.stdout.flush()

                    # Transcripció directa quan s'omple el bloc d'àudio
                    if len(buffer) >= samples_per_block:
                        audio_data = buffer[:samples_per_block].flatten()
                        # Fem espai al buffer conservant el solapament
                        buffer = buffer[samples_per_block - overlap_samples:]

                        # Només enviem a la GPU si hi ha un mínim de senyal
                        if np.sqrt(np.mean(audio_data**2)) > rms_thresh:
                            res = mlx_whisper.transcribe(
                                audio_data,
                                path_or_hf_repo=self.model_path,
                                language=self.config.get('general', {}).get('idioma_defecte', 'ca'),
                                temperature=temp_val
                            )
                            
                            text_principal = res.get("text", "").strip()

                            if text_principal:
                                sys.stdout.write("\r" + " " * 50 + "\r")
                                callback_resultat(text_principal)

                except queue.Empty:
                    continue

    def aturar_escolta(self):
        self.is_listening = False
