import os
import yaml
import warnings
import torch
import numpy as np
import requests

# Desactivació de warnings i logs sorollosos de HuggingFace
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

from transcriptorTelo import TranscriptorTelo
from transformers import WhisperProcessor, WhisperForConditionalGeneration, logging as hf_logging

hf_logging.set_verbosity_error()

try:
    import faster_whisper
    CTRANSLATE_DISPONIBLE = True
except ImportError:
    CTRANSLATE_DISPONIBLE = False


class TranscriptorWhisper(TranscriptorTelo):
    def __init__(self, model=None, config=None, config_path="configTranscriptorWhisper.yaml"):
        # 1. Carreguem la configuració YAML
        loaded_config = self._carregar_yaml(config_path) if os.path.exists(config_path) else {}
        if config:
            loaded_config.update(config)
            
        gen_cfg = loaded_config.get('general', {})
        model_final = model or gen_cfg.get('model_path', 'openai/whisper-large-v3')

        super().__init__(model_path=model_final, config=loaded_config)
        
        # 2. Gestió del dispositiu (auto / mps / cuda / cpu)
        dev_override = gen_cfg.get('device_override', 'auto').lower()
        if dev_override != 'auto':
            self.device = dev_override
        else:
            if torch.cuda.is_available():
                self.device = "cuda"
            elif torch.backends.mps.is_available():
                self.device = "mps"
            else:
                self.device = "cpu"

        # 3. Configuració del motor
        engine_cfg = self.config.get('engine', {})
        self.engine_type = engine_cfg.get('engine_type', 'transformers')
        self.vllm_server_url = self.config.get('vllm', {}).get('server_url', 'http://localhost:8000/v1/audio/transcriptions')

        self.hf_processor = None
        self.hf_model = None
        self.ct2_model = None

        # Carreguem el model i les estructures
        self.carregar_estructures_ia(self.model_path, self.engine_type)

    def _carregar_yaml(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception:
            return {}

    def carregar_estructures_ia(self, ruta, tipus):
        """Carrega el motor de transcripció i informa de quin dispositiu físic s'està utilitzant."""
        self.engine_type = tipus.lower()
        
        try:
            if self.engine_type in ["ctranslate2", "simulstreaming"]:
                if not CTRANSLATE_DISPONIBLE:
                    raise RuntimeError("La llibreria 'faster-whisper' o 'ctranslate2' no està instal·lada.")
                
                ct2_cfg = self.config.get('ctranslate2', {})
                compute_type = ct2_cfg.get('compute_type', 'default')
                
                # CTranslate2 s'executa en GPU (CUDA) o CPU (ARM NEON en Mac)
                if self.device == "cuda":
                    ct2_device = "cuda"
                    if compute_type == 'default':
                        compute_type = "float16"
                    desc_dispositiu = "GPU NVIDIA (CUDA)"
                else:
                    ct2_device = "cpu"
                    if compute_type == 'default':
                        compute_type = "int8"
                    desc_dispositiu = "CPU (Optimitzat ARM NEON)"
                    if self.device == "mps":
                        print("    [i] NOTA: CTranslate2 no suporta Metal/MPS. Executant en CPU d'alt rendiment.")

                print(f"[*] [Whisper Engine] Carregant CTRANSLATE2 -> {ruta}")
                print(f"[*] [Dispositiu Actiu]: {desc_dispositiu} | Precision: {compute_type}")

                self.ct2_model = faster_whisper.WhisperModel(
                    ruta, 
                    device=ct2_device, 
                    compute_type=compute_type
                )

            elif self.engine_type == "vllm_client":
                print(f"[*] [Whisper Engine] Mode vLLM actiu.")
                print(f"[*] [Dispositiu Actiu]: Servidor Remot REST -> {self.vllm_server_url}")

            elif self.engine_type == "transformers":
                if self.device == "cuda":
                    torch_dtype = torch.float16
                    desc_dispositiu = "GPU NVIDIA (CUDA)"
                elif self.device == "mps":
                    torch_dtype = torch.float32
                    desc_dispositiu = "GPU Mac Apple Silicon (MPS Metal)"
                else:
                    torch_dtype = torch.float32
                    desc_dispositiu = "CPU del sistema"

                print(f"[*] [Whisper Engine] Carregant TRANSFORMERS -> {ruta}")
                print(f"[*] [Dispositiu Actiu]: {desc_dispositiu}")

                self.hf_processor = WhisperProcessor.from_pretrained(ruta)
                self.hf_model = WhisperForConditionalGeneration.from_pretrained(
                    ruta, torch_dtype=torch_dtype, low_cpu_mem_usage=True
                ).to(self.device)

            else:
                raise ValueError(f"Motor no reconegut: {self.engine_type}")

            self.model_path = ruta
            return True

        except Exception as e:
            print(f"[!] Error carregant el model d'IA: {e}")
            return False

    def processar_amb_alternatives(self, raw_data, idioma=None):
        if not raw_data:
            return "", []

        idioma_actiu = idioma or self.config.get('general', {}).get('idioma_defecte', 'ca')
        gen_cfg = self.config.get('generation', {})
        audio_np = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0

        # Filtre per evitar processar blocs d'àudio pràcticament buits (soroll de fons)
        rms = np.sqrt(np.mean(audio_np**2))
        if rms < 0.008:
            return "", []

        if self.engine_type == "transformers":
            inputs = self.hf_processor(audio_np, sampling_rate=16000, return_tensors="pt")
            input_features = inputs.input_features.to(self.device)
            
            if self.device == "cuda":
                input_features = input_features.to(torch.float16)
            else:
                input_features = input_features.to(torch.float32)

            outputs = self.hf_model.generate(
                input_features, 
                language=idioma_actiu, 
                max_new_tokens=gen_cfg.get('max_new_tokens', 64),
                max_length=None,
                num_beams=gen_cfg.get('beam_size', 1),
                temperature=0.0,
                do_sample=False
            )
            transcripcio = self.hf_processor.batch_decode(outputs, skip_special_tokens=True)[0]
            return transcripcio, []

        elif self.engine_type in ["ctranslate2", "simulstreaming"]:
            ct2_cfg = self.config.get('ctranslate2', {})
            
            # En mode simulstreaming desactivem word_timestamps i forcem beam_size=1 per a màxima velocitat
            es_simul = (self.engine_type == "simulstreaming")
            beam_sz = 1 if es_simul else gen_cfg.get('beam_size', 1)
            w_timestamps = False if es_simul else ct2_cfg.get('word_timestamps', True)

            segments, _ = self.ct2_model.transcribe(
                audio_np, 
                language=idioma_actiu, 
                beam_size=beam_sz,
                word_timestamps=w_timestamps,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=200),
                no_speech_threshold=0.5,
                condition_on_previous_text=False
            )
            transcripcio = " ".join([s.text for s in segments])
            return transcripcio, []

        return "", []
