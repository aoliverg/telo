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
    per comunicar-se amb un servidor WhisperLiveKit (ASR + Traducció opcional).
    """

    def __init__(self, config_path="configTeloAuto.yaml"):
        super().__init__(config_path)

        # Configuració del WebSocket
        ws_cfg = self.config.get("whisper_live", {})
        self.host = ws_cfg.get("host", "localhost")
        self.port = ws_cfg.get("port", 8000)
        self.endpoint = ws_cfg.get("endpoint", "/asr")

        # Paràmetres de traducció des del YAML
        translation_cfg = self.config.get("translation", {})
        self.translation_enabled = translation_cfg.get("enabled", True)
        self.target_language = translation_cfg.get("target_language", "es")

        # Construcció de la URL: NOmés afegim target_language si translation està activat (enabled: true)
        params = {}
        if self.translation_enabled and self.target_language:
            params["target_language"] = self.target_language

        query_string = f"?{urllib.parse.urlencode(params)}" if params else ""
        self.url = f"ws://{self.host}:{self.port}{self.endpoint}{query_string}"

        # Configuració d'àudio
        audio_cfg = self.config.get("audio", {})
        self.sample_rate = audio_cfg.get("samplerate", 16000)
        self.block_ms = audio_cfg.get("block_ms", 100)
        self.block_size = self.sample_rate * self.block_ms // 1000
        self.device = audio_cfg.get("device", None)

        # Gestió de fils i bucle
        self._loop = None
        self._thread = None
        self._cua = None
        self._ws = None
        self._callback_resultat = None

    def inicialitzar_motor(self):
        """Comprova que els paràmetres d'entrada d'àudio siguin vàlids."""
        try:
            if self.show_info:
                print(f"[INFO] Validant dispositiu d'àudio per a {self.url}...")

            sd.check_input_settings(
                device=self.device,
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
            )

            if self.show_info:
                print("[INFO] Configuració d'àudio validada correctament.")
        except Exception as e:
            raise RuntimeError(f"Error en la configuració d'àudio de sounddevice: {e}")

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
        while True:
            dades = await self._cua.get()
            if dades is None:
                break
            await ws.send(dades)

        try:
            await ws.send(b"")
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _rebre(self, ws):
        async for missatge in ws:
            try:
                data = json.loads(missatge)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "ready_to_stop":
                break

            if "lines" not in data:
                continue

            linies = data["lines"]

            text_original = " ".join(l.get("text", "") for l in linies if l.get("text")).strip()
            
            # Només extreurem la traducció si està activada
            text_traduccio = ""
            if self.translation_enabled:
                text_traduccio = " ".join(l.get("translation", "") for l in linies if l.get("translation")).strip()

            buffer = data.get("buffer_transcription", "").strip()
            if buffer:
                text_original = f"{text_original} {buffer}".strip()

            resultat_dual = {
                "original": text_original,
                "translation": text_traduccio if self.translation_enabled else ""
            }

            if self._callback_resultat and self.is_listening:
                self._callback_resultat(resultat_dual)

    async def _async_main(self):
        self._cua = asyncio.Queue()

        def audio_callback(indata, frames, time_info, status):
            if status and self.show_info:
                print(f"[ALERTA Àudio]: {status}", flush=True)

            if self.is_listening and self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._cua.put_nowait, bytes(indata))

        try:
            async with websockets.connect(self.url, max_size=None) as ws:
                self._ws = ws
                if self.show_info:
                    print(f"[INFO] Connectat amb èxit a {self.url}. Escoltant...")

                with sd.RawInputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=self.block_size,
                    device=self.device,
                    callback=audio_callback,
                ):
                    tasca_enviar = asyncio.create_task(self._enviar(ws))
                    tasca_rebre = asyncio.create_task(self._rebre(ws))

                    await asyncio.gather(tasca_enviar, tasca_rebre)

        except (websockets.exceptions.WebSocketException, ConnectionRefusedError, OSError) as e:
            if self.show_info:
                print(f"[ERROR WebSocket]: No s'ha pogut connectar o la connexió s'ha interromput: {e}")
        finally:
            self.is_listening = False

    def aturar_escolta(self):
        if not self.is_listening:
            return

        super().aturar_escolta()

        if self._loop and self._loop.is_running() and self._cua:
            self._loop.call_soon_threadsafe(self._cua.put_nowait, None)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            if self.show_info:
                print("[INFO] Fil de transcripció finalitzat correctament.")
