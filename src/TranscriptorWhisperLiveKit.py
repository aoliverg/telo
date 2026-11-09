import asyncio
import json
import threading
import urllib.parse
import sounddevice as sd
import websockets

from TranscriptorTelo import TranscriptorTelo


class TranscriptorWhisperLiveKit(TranscriptorTelo):
    """
    Transcriptor en temps real basat en WebSocket i sounddevice
    per comunicar-se amb WhisperLiveKit (ASR + Traducció NLLB).
    """

    # Mapa de codis curts cap al format BCP-47 que requereix NLLB
    MAPA_NLLB = {
        'ca': 'cat_Latn',
        'es': 'spa_Latn',
        'fr': 'fra_Latn',
        'en': 'eng_Latn',
        'de': 'deu_Latn',
        'it': 'ita_Latn',
        'pt': 'por_Latn',
        'oc': 'oci_Latn'
    }

    def __init__(
        self,
        config=None,
        host=None,
        port=None,
        endpoint=None,
        language=None,
        translation_enabled=None,
        target_language=None,
        sample_rate=None,
        block_ms=None,
        device=None,
        show_info=None,
    ):
        try:
            super().__init__(config=config)
        except TypeError:
            super().__init__()

        cfg = config or {}

        ws_cfg = cfg.get("whisper_live", {})
        self.host = host or ws_cfg.get("host", "localhost")
        self.port = port or ws_cfg.get("port", 8000)
        self.endpoint = endpoint or ws_cfg.get("endpoint", "/asr")

        self.language = language or cfg.get("input_language", "fr")

        translation_cfg = cfg.get("translation", {})
        if translation_enabled is not None:
            self.translation_enabled = translation_enabled
        else:
            self.translation_enabled = translation_cfg.get("enabled", True)

        self.target_language = target_language or translation_cfg.get("target_language", "ca")

        audio_cfg = cfg.get("audio", {})
        self.sample_rate = sample_rate or audio_cfg.get("samplerate", 16000)
        self.block_ms = block_ms or audio_cfg.get("block_ms", 100)
        self.block_size = int(self.sample_rate * (self.block_ms / 1000.0))

        self.device = device if device is not None else audio_cfg.get("device", None)
        self.show_info = show_info if show_info is not None else getattr(self, "show_info", cfg.get("show_info", False))

        self._loop = None
        self._thread = None
        self._cua = None
        self._ws = None
        self._callback_resultat = None

    def obtenir_url(self):
        """Construeix la URL WebSocket forçant sempre ASR en l'idioma d'origen."""
        params = {
            "task": "transcribe"  # Evita que Whisper forci la seva traducció interna a anglès
        }
        
        if self.language and self.language != "auto":
            params["language"] = self.language

        if self.translation_enabled and self.target_language:
            t_raw = str(self.target_language).lower()
            params["target_language"] = self.MAPA_NLLB.get(t_raw, t_raw)

        query_string = f"?{urllib.parse.urlencode(params)}"
        return f"ws://{self.host}:{self.port}{self.endpoint}{query_string}"

    @property
    def url(self):
        return self.obtenir_url()

    def canviar_idioma(self, nou_idioma=None, nou_target=None):
        """Actualitza les propietats de llengua de la instància."""
        if nou_idioma:
            self.language = nou_idioma
        if nou_target:
            self.target_language = nou_target

    def inicialitzar_motor(self):
        try:
            sd.check_input_settings(
                device=self.device,
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
            )
        except Exception as e:
            raise RuntimeError(f"Error en la configuració d'àudio: {e}")

    def iniciar_escolta(self, callback_resultat):
        if self.is_listening:
            return

        self.is_listening = True
        self._callback_resultat = callback_resultat

        self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self._thread.start()

    def _run_async_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        finally:
            self._loop.close()

    async def _enviar(self, ws):
        try:
            while self.is_listening:
                try:
                    dades = await asyncio.wait_for(self._cua.get(), timeout=0.1)
                    if dades is None:
                        break
                    await ws.send(dades)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.show_info:
                print(f"[WARN] Error enviant àudio: {e}", flush=True)

    async def _rebre(self, ws):
        linies_consolidades_index = 0
        try:
            async for missatge in ws:
                if not self.is_listening:
                    break

                try:
                    data = json.loads(missatge)
                except json.JSONDecodeError:
                    continue

                if data.get("type") in ("config", "ready_to_stop"):
                    continue

                lines = data.get("lines") or []
                linies_reals = [
                    l for l in lines
                    if isinstance(l, dict) and l.get("speaker") != -2 and l.get("text", "").strip()
                ]

                if not linies_reals:
                    continue

                num_consolidades = max(0, len(linies_reals) - 1)
                es_nou_definitiu = num_consolidades > linies_consolidades_index

                if es_nou_definitiu:
                    linies_consolidades_index = num_consolidades

                consolidades = linies_reals[:num_consolidades]
                text_orig_definitiu = " ".join(l.get("text", "").strip() for l in consolidades).strip()
                text_trad_definitiu = " ".join(
                    str(l.get("translation", "")).strip() for l in consolidades if l.get("translation")
                ).strip()

                linia_viva = linies_reals[-1]
                buffer_orig = linia_viva.get("text", "").strip()

                buffer_trad = (
                    linia_viva.get("translation") or 
                    data.get("buffer_translation") or 
                    ""
                )
                buffer_trad = str(buffer_trad).strip() if self.translation_enabled else ""

                resultat = {
                    "original_definitiu": text_orig_definitiu,
                    "translation_definitiu": text_trad_definitiu,
                    "original_buffer": buffer_orig,
                    "translation_buffer": buffer_trad,
                    "es_consolidat": es_nou_definitiu,
                    "original": f"{text_orig_definitiu} {buffer_orig}".strip(),
                    "translation": f"{text_trad_definitiu} {buffer_trad}".strip(),
                }

                if self._callback_resultat and self.is_listening:
                    self._callback_resultat(resultat)

        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _async_main(self):
        self._cua = asyncio.Queue(maxsize=30)

        def audio_callback(indata, frames, time_info, status):
            if not (self.is_listening and self._loop and self._loop.is_running()):
                return
            try:
                self._cua.put_nowait(bytes(indata))
            except asyncio.QueueFull:
                try:
                    self._cua.get_nowait()
                    self._cua.put_nowait(bytes(indata))
                except Exception:
                    pass

        try:
            with sd.RawInputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self.block_size,
                device=self.device,
                callback=audio_callback,
            ):
                url_connexio = self.obtenir_url()
                if self.show_info:
                    print(f"[INFO] Connectant a: {url_connexio}", flush=True)

                async with websockets.connect(
                    url_connexio,
                    max_size=None,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    tasca_enviar = asyncio.create_task(self._enviar(ws))
                    tasca_rebre = asyncio.create_task(self._rebre(ws))

                    done, pending = await asyncio.wait(
                        [tasca_enviar, tasca_rebre],
                        return_when=asyncio.FIRST_EXCEPTION,
                    )
                    for task in pending:
                        task.cancel()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self.show_info:
                print(f"[ERROR Transcriptor]: {e}", flush=True)
        finally:
            self._ws = None
            self.is_listening = False

    def aturar_escolta(self):
        if not self.is_listening:
            return

        self.is_listening = False

        if self._loop and self._loop.is_running():
            async def _tancar():
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass

            asyncio.run_coroutine_threadsafe(_tancar(), self._loop)

            def _posar_centinella():
                if self._cua:
                    try:
                        self._cua.put_nowait(None)
                    except asyncio.QueueFull:
                        try:
                            self._cua.get_nowait()
                            self._cua.put_nowait(None)
                        except Exception:
                            pass

            self._loop.call_soon_threadsafe(_posar_centinella)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
