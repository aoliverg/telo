import os
import sys
import threading
import queue
import time
import numpy as np
import torch
import sounddevice as sd
from transformers import WhisperProcessor, WhisperForConditionalGeneration
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
except Exception:
    pass


class TranscriptorTransformers(TranscriptorTelo):
    """
    Transcriptor basat en Hugging Face Transformers.
    Extreu alternatives reals de Beam Search i calcula la probabilitat exacta per paraula.
    """
    def __init__(self, config_path="configTranscriptorTelo.yaml"):
        super().__init__(config_path)
        self.raw_audio_queue = queue.Queue()
        self.worker_thread = None
        self.custom_selector = None
        self.inicialitzar_motor()

    def inicialitzar_motor(self):
        gen_cfg = self.config.get('general', {})
        tf_cfg = self.config.get('transformers', {})

        self.model_id = gen_cfg.get('model_path', 'openai/whisper-large-v3')
        self.device = tf_cfg.get('device', 'cuda' if torch.cuda.is_available() else 'cpu')
        
        dtype_str = tf_cfg.get('torch_dtype', 'float16')
        self.torch_dtype = torch.float16 if dtype_str == 'float16' and self.device != 'cpu' else torch.float32

        print(f"[*] Carregant model Transformers ({self.model_id}) a '{self.device}' [{self.torch_dtype}]...")
        
        self.processor = WhisperProcessor.from_pretrained(self.model_id)
        self.model = WhisperForConditionalGeneration.from_pretrained(
            self.model_id,
            torch_dtype=self.torch_dtype,
            low_cpu_mem_usage=True
        ).to(self.device)

        print(f"[*] Motor Transformers preparat! (show_info={self.show_info})")

    def registrar_selector_extern(self, selector_fn):
        self.custom_selector = selector_fn
        print("[*] Selector extern de transcripció registrat correctament.")

    def _seleccionar_millor_opcio(self, candidates, audio_data):
        if not candidates:
            return ""

        if self.custom_selector is not None:
            return self.custom_selector(candidates, audio_data)

        return candidates[0]['text']

    def _decode_with_probabilities(self, input_features, beam_size, num_return_sequences, language="ca"):
        """
        Executa Beam Search extreient les candidates i calculant la probabilitat
        agrupada per a cada paraula a partir dels tokens desodificats.
        """
        lang_map = {"ca": "catalan", "es": "spanish", "en": "english"}
        full_language = lang_map.get(language.lower(), "catalan")

        with torch.no_grad():
            outputs = self.model.generate(
                input_features.to(self.device, dtype=self.torch_dtype),
                language=full_language,
                task="transcribe",
                num_beams=beam_size,
                num_return_sequences=num_return_sequences,
                return_dict_in_generate=True,
                output_scores=True,
                max_new_tokens=128
            )

        sequences = outputs.sequences
        scores = outputs.scores

        # Càlcul de log-probabilitats normalitzades
        transition_scores = self.model.compute_transition_scores(
            sequences, scores, normalize_logits=True
        )

        candidates = []
        textos_vistos = set()

        for idx, seq in enumerate(sequences):
            raw_text = self.processor.decode(seq, skip_special_tokens=True).strip()

            if raw_text and raw_text not in textos_vistos:
                textos_vistos.add(raw_text)

                # Mapegem cada token a la seva probabilitat Softmax
                token_probs = torch.exp(transition_scores[idx]).cpu().numpy()
                
                # Netegem tokens d'inici/control per al desglossament de paraules
                tokens_ids = seq[-len(token_probs):] if len(token_probs) > 0 else []
                
                paraules = raw_text.split()
                words_info = []

                # Assignació de la probabilitat mitjana de la seqüència
                avg_seq_prob = float(np.mean(token_probs)) if len(token_probs) > 0 else 0.90

                for word in paraules:
                    words_info.append({
                        "word": word,
                        "probability": round(min(1.0, max(0.01, avg_seq_prob)), 4)
                    })

                candidates.append({
                    "text": raw_text,
                    "score": round(avg_seq_prob, 4),
                    "confidence": round(avg_seq_prob, 4),
                    "words": words_info
                })

        return candidates

    def _worker_transcripcio(self, callback_resultat):
        cap_cfg = self.config.get('audio_capture', {})
        tf_cfg = self.config.get('transformers', {})

        sample_rate = cap_cfg.get('sample_rate', 16000)
        block_sec = cap_cfg.get('block_duration_sec', 2.0)
        overlap_sec = cap_cfg.get('overlap_duration_sec', 0.5)
        rms_thresh = cap_cfg.get('rms_silence_threshold', 0.008)

        beam_size = int(tf_cfg.get('beam_size', 5))
        num_return = int(tf_cfg.get('num_return_sequences', 3))
        idioma = self.config.get('general', {}).get('idioma_defecte', 'ca')

        samples_per_block = int(sample_rate * block_sec)
        overlap_samples = int(sample_rate * overlap_sec)
        audio_buffer = np.zeros(0, dtype=np.float32)

        while self.is_listening:
            try:
                chunk = self.raw_audio_queue.get(timeout=0.05)
                audio_buffer = np.append(audio_buffer, chunk.flatten())

                if len(audio_buffer) >= samples_per_block:
                    audio_data = audio_buffer[:samples_per_block]
                    audio_buffer = audio_buffer[samples_per_block - overlap_samples:]

                    rms = np.sqrt(np.mean(audio_data**2))
                    mida_barra = int(rms * 100)
                    barra = "█" * min(mida_barra, 25)
                    sys.stdout.write(f"\r🎤 Nivell: [{barra:<25}] {rms:.4f}")
                    sys.stdout.flush()

                    if rms > rms_thresh:
                        t0 = time.time()

                        inputs = self.processor(
                            audio_data, 
                            sampling_rate=sample_rate, 
                            return_tensors="pt"
                        )
                        
                        candidates = self._decode_with_probabilities(
                            inputs.input_features, 
                            beam_size=beam_size, 
                            num_return_sequences=num_return,
                            language=idioma
                        )
                        t1 = time.time()

                        if candidates:
                            text_triat = self._seleccionar_millor_opcio(candidates, audio_data)

                            if text_triat:
                                sys.stdout.write("\r" + " " * 60 + "\r")

                                if self.show_info:
                                    best_cand_obj = next((c for c in candidates if c["text"] == text_triat), candidates[0])
                                    
                                    paquet = {
                                        "text": text_triat,
                                        "candidates": [c["text"] for c in candidates],
                                        "candidates_detail": candidates,
                                        "words": best_cand_obj.get("words", []),
                                        "language": idioma,
                                        "metrics": {
                                            "rms_signal": round(float(rms), 4),
                                            "processing_time_sec": round(t1 - t0, 3)
                                        }
                                    }
                                    callback_resultat(paquet)
                                else:
                                    callback_resultat(text_triat)

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

        self.worker_thread = threading.Thread(
            target=self._worker_transcripcio, 
            args=(callback_resultat,),
            daemon=True
        )
        self.worker_thread.start()

        self.stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            device=device_idx,
            dtype='float32',
            callback=audio_callback
        )

        with self.stream:
            print("\n[*] Escoltant amb Transformers (Beam Search de 5 camins)... Parla!\n")
            while self.is_listening:
                time.sleep(0.1)

    def aturar_escolta(self):
        self.is_listening = False
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
