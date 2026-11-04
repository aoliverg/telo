import os
import sys
import threading
import sounddevice as sd
import numpy as np
import queue
import time
from faster_whisper import WhisperModel
from transcriptorTelo import TranscriptorTelo

# Patch de compatibilitat CUDA Linux/Mac
try:
    import nvidia.cublas.lib
    import nvidia.cudnn.lib

    def _get_pkg_path(pkg):
        if hasattr(pkg, "__file__") and pkg.__file__:
            return os.path.dirname(pkg.__file__)
        elif hasattr(pkg, "__path__") and pkg.__path__:
            return list(pkg.__path__)[0]
        return None

    cublas_path = _get_pkg_path(nvidia.cublas.lib)
    cudnn_path = _get_pkg_path(nvidia.cudnn.lib)

    if cublas_path and cudnn_path:
        current_ld = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = f"{cublas_path}:{cudnn_path}:{current_ld}"

        import ctypes
        for lib_dir in [cublas_path, cudnn_path]:
            for lib_name in ["libcublas.so.12", "libcudnn.so.9", "libcudnn.so.8"]:
                try:
                    ctypes.CDLL(os.path.join(lib_dir, lib_name))
                except OSError:
                    pass
        try:
            ctypes.CDLL(os.path.join(cudnn_path, "libcudnn.so.8"))
        except OSError:
            pass
except Exception:
    pass


class TranscriptorFasterWhisper(TranscriptorTelo):
    """
    Transcriptor asíncron multi-thread per a captura d'àudio contínua sense pèrdues.
    """
    def __init__(self, config_path="configTranscriptorTelo.yaml"):
        super().__init__(config_path)
        self.raw_audio_queue = queue.Queue()
        self.worker_thread = None
        self.inicialitzar_motor()

    def inicialitzar_motor(self):
        gen_cfg = self.config.get('general', {})
        fw_cfg = self.config.get('faster_whisper', {})

        self.model_path = gen_cfg.get('model_path', 'large-v3')
        device = fw_cfg.get('device', 'auto')
        compute_type = fw_cfg.get('compute_type', 'default')

        print(f"[*] Carregant faster-whisper ({self.model_path}) a '{device}'...")
        self.model = WhisperModel(
            self.model_path,
            device=device,
            compute_type=compute_type
        )
        print(f"[*] Motor preparat! (show_info={self.show_info})")

    def _worker_transcripcio(self, callback_resultat):
        """
        Fil independent que consumeix àudio continuat i fa la inferència
        sense bloquejar la gravació del micròfon.
        """
        cap_cfg = self.config.get('audio_capture', {})
        fw_cfg = self.config.get('faster_whisper', {})

        sample_rate = cap_cfg.get('sample_rate', 16000)
        block_sec = cap_cfg.get('block_duration_sec', 2.0)
        overlap_sec = cap_cfg.get('overlap_duration_sec', 0.5)
        rms_thresh = cap_cfg.get('rms_silence_threshold', 0.008)

        use_vad = bool(fw_cfg.get('use_vad', True))
        no_speech_thresh = float(fw_cfg.get('no_speech_threshold', 0.6))
        log_prob_thresh = float(fw_cfg.get('log_prob_threshold', -1.0))
        hallucination_thresh = float(fw_cfg.get('hallucination_silence_threshold', 2.0))

        samples_per_block = int(sample_rate * block_sec)
        overlap_samples = int(sample_rate * overlap_sec)
        audio_buffer = np.zeros(0, dtype=np.float32)

        while self.is_listening:
            try:
                # Obtenim blocs d'àudio de la cua d'entrada
                chunk = self.raw_audio_queue.get(timeout=0.05)
                audio_buffer = np.append(audio_buffer, chunk.flatten())

                # Quan tenim prou àudio acumulat
                if len(audio_buffer) >= samples_per_block:
                    audio_data = audio_buffer[:samples_per_block]
                    
                    # Guardem la cua d'overlap per a la frontera de la frase
                    audio_buffer = audio_buffer[samples_per_block - overlap_samples:]

                    rms = np.sqrt(np.mean(audio_data**2))
                    mida_barra = int(rms * 100)
                    barra = "█" * min(mida_barra, 25)
                    sys.stdout.write(f"\r🎤 Nivell: [{barra:<25}] {rms:.4f}")
                    sys.stdout.flush()

                    if rms > rms_thresh:
                        t0 = time.time()
                        
                        segments, info = self.model.transcribe(
                            audio_data,
                            language=self.config.get('general', {}).get('idioma_defecte', 'ca'),
                            word_timestamps=self.show_info,
                            vad_filter=use_vad,
                            vad_parameters=dict(min_silence_duration_ms=300),
                            no_speech_threshold=no_speech_thresh,
                            log_prob_threshold=log_prob_thresh,
                            hallucination_silence_threshold=hallucination_thresh,
                            condition_on_previous_text=False
                        )
                        
                        llista_segments = list(segments)
                        t1 = time.time()

                        text = "".join([s.text for s in llista_segments]).strip()

                        if text:
                            sys.stdout.write("\r" + " " * 60 + "\r")

                            if self.show_info:
                                paraules = []
                                for seg in llista_segments:
                                    if seg.words:
                                        for w in seg.words:
                                            paraules.append({
                                                "word": w.word.strip(),
                                                "probability": round(float(w.probability), 4)
                                            })

                                paquet = {
                                    "text": text,
                                    "language": info.language,
                                    "metrics": {
                                        "rms_signal": round(float(rms), 4),
                                        "processing_time_sec": round(t1 - t0, 3)
                                    },
                                    "words": paraules
                                }
                                callback_resultat(paquet)
                            else:
                                callback_resultat(text)

            except queue.Empty:
                continue

    def iniciar_escolta(self, callback_resultat):
        self.is_listening = True
        
        cap_cfg = self.config.get('audio_capture', {})
        sample_rate = cap_cfg.get('sample_rate', 16000)
        device_idx = cap_cfg.get('input_device_index', None)

        def audio_callback(indata, frames, time_info, status):
            if self.is_listening:
                self.raw_audio_queue.put(indata.copy())

        # Arrancem el fil de transcripció asíncron
        self.worker_thread = threading.Thread(
            target=self._worker_transcripcio, 
            args=(callback_resultat,),
            daemon=True
        )
        self.worker_thread.start()

        # Arrancem la captura del micròfon
        self.stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            device=device_idx,
            dtype='float32',
            callback=audio_callback
        )

        with self.stream:
            print("\n[*] Escoltant en mode asíncron (sense pèrdua d'àudio)... Parla!\n")
            while self.is_listening:
                time.sleep(0.1)

    def aturar_escolta(self):
        self.is_listening = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
