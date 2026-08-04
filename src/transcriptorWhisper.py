import torch
import numpy as np
import speech_recognition as sr
from transformers import WhisperProcessor, WhisperForConditionalGeneration

# Intentem importar ctranslate2 de manera opcional
try:
    import ctranslate2
    import faster_whisper
    CTRANSLATE_DISPONIBLE = True
except ImportError:
    CTRANSLATE_DISPONIBLE = False

class TranscriptorWhisper:
    def __init__(self, model="openai/whisper-large-v3", config=None):
        self.config = config
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.engine_type = config['whisper'].get('engine_type', 'transformers') if config else 'transformers'
        self.model_path = model
        
        # Referències als instàncies dels motors
        self.hf_processor = None
        self.hf_model = None
        self.ct2_model = None
        
        # Inicialitzar el motor seleccionat
        self.carregar_estructures_ia(self.model_path, self.engine_type)

        # CONFIGURACIÓ DEL SISTEMA DE CAPTURA (SpeechRecognition)
        self.recorder = sr.Recognizer()
        idx = config['whisper'].get('input_device_index', None) if config else None
        threshold = config['whisper'].get('energy_threshold', 1000) if config else 1000
        
        self.source = sr.Microphone(sample_rate=44100, device_index=idx)
        self.recorder.energy_threshold = threshold
        self.recorder.dynamic_energy_threshold = False


    def carregar_estructures_ia(self, ruta, tipus):
        """Munta les llibreries i models a la VRAM segons el motor triat"""
        print(f"[*] Inicialitzant motor d'intel·ligència artificial: {tipus.upper()} -> {ruta}")
        try:
            if tipus == "ctranslate2":
                if not CTRANSLATE_DISPONIBLE:
                    raise RuntimeError("La llibreria 'ctranslate2' o 'faster-whisper' no està instal·lada.")
                # Carreguem el model utilitzant faster-whisper per gestionar els formats convertits CT2
                self.ct2_model = faster_whisper.WhisperModel(ruta, device=self.device, compute_type="float16")
                self.hf_processor = WhisperProcessor.from_pretrained("openai/whisper-large-v3") # Tokenitzador base de referència
                self.engine_type = "ctranslate2"
            else:
                # Transformers estàndard
                torch_dtype = torch.float16 if self.device == "cuda" else torch.float32
                self.hf_processor = WhisperProcessor.from_pretrained(ruta)
                self.hf_model = WhisperForConditionalGeneration.from_pretrained(
                    ruta, torch_dtype=torch_dtype, low_cpu_mem_usage=True
                ).to(self.device)
                self.engine_type = "transformers"
            self.model_path = ruta
            return True
        except Exception as e:
            print(f"[!] Error greu carregant el model de IA: {e}")
            return False

    def mutar_model_instanciat(self, ruta_model, tipus_motor):
        """Allibera la memòria de la GPU i commuta el model en calent de forma thread-safe"""
        # Neteja de memòria cau de PyTorch per evitar OutOfMemory a la GPU
        self.hf_model = None
        self.ct2_model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        return self.carregar_estructures_ia(ruta_model, tipus_motor)

    def actualitzar_dispositiu_entrada(self, device_idx):
        """Re-injecta un nou micròfon físic al sistema de captura"""
        self.source = sr.Microphone(sample_rate=16000, device_index=device_idx)

    def calibrar(self, durada=2):
        if self.source:
            try:
                print(f"[*] Calibrant soroll ambient ({durada}s)...")
                with self.source as src:
                    self.recorder.adjust_for_ambient_noise(src, duration=durada)
            except Exception as e:
                print(f"[!] Avís: No s'ha pogut calibrar el soroll ambient automàtic (el maquinari està ocupat o rate invàlid): {e}")
                print("[*] Es continuarà amb el llindar fix per defecte.")

    def escoltar_continu(self, callback):
        if self.source:
            return self.recorder.listen_in_background(self.source, callback, phrase_time_limit=10)
        return None

    def processar_amb_alternatives(self, raw_data, idioma="ca"):
        """Interfície polimòrfica. Processa l'àudio independentment del motor actiu"""
        # Convertim el bloc d'àudio PCM de 16 bits a array de flotants normalitzat
        audio_np = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0

        if self.engine_type == "ctranslate2":
            # Transcripció optimitzada per a CTranslate2
            segments, info = self.ct2_model.transcribe(audio_np, language=idioma, beam_size=3)
            segments = list(segments)
            transcripcio = " ".join([seg.text for seg in segments])
            
            # Simulació de matriu de probabilitats bàsica per al sincronitzador laxe de teatre
            detalls = []
            for seg in segments:
                for paraula in seg.words if hasattr(seg, 'words') and seg.words else []:
                    detalls.append([(paraula.word, paraula.probability * 100)])
            return transcripcio, detalls
            
        else:
            # Procés estàndard utilitzant HuggingFace Transformers
            inputs = self.hf_processor(audio_np, sampling_rate=16000, return_tensors="pt")
            input_features = inputs.input_features.to(self.device)
            if self.device == "cuda":
                input_features = input_features.to(torch.float16)

            outputs = self.model.generate(
                input_features,
                language=idioma,
                return_dict_in_generate=True,
                output_scores=True,
                max_new_tokens=128,
                do_sample=False,
                num_beams=3
            )

            transcripcio = self.hf_processor.batch_decode(outputs.sequences, skip_special_tokens=True)[0]
            
            # Generació de la llista d'alternatives (logits step-by-step)
            detalls = []
            for step_logits in outputs.scores:
                probs = torch.softmax(step_logits[0], dim=-1)
                top_k = torch.topk(probs, 3)
                opcions = []
                for j in range(3):
                    token_id = top_k.indices[j].item()
                    token_txt = self.hf_processor.tokenizer.decode([token_id]).strip()
                    p = top_k.values[j].item() * 100
                    if token_txt:
                        opcions.append((token_txt, p))
                if opcions:
                    detalls.append(opcions)

            return transcripcio, detalls

    @property
    def model(self):
        """Retorna l'instància activa de transformers si està operativa"""
        return self.hf_model
