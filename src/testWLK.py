import asyncio
import json
import websockets
import sounddevice as sd

WS_URL = "ws://localhost:8000/v1/listen?encoding=linear16&sample_rate=16000&channels=1"
SAMPLE_RATE = 16000

class AcumuladorFrase:
    def __init__(self, callback_final, silence_timeout=0.8):
        self.callback_final = callback_final
        self.silence_timeout = silence_timeout
        self.buffer = []
        self.timer_task = None

    def afegir_fragment(self, text):
        text = text.strip()
        if not text:
            return

        if not self.buffer or self.buffer[-1] != text:
            self.buffer.append(text)

        if self.timer_task and not self.timer_task.done():
            self.timer_task.cancel()

        self.timer_task = asyncio.create_task(self._esperar_silenci())

    async def _esperar_silenci(self):
        await asyncio.sleep(self.silence_timeout)
        if self.buffer:
            frase_completa = " ".join(self.buffer)
            self.buffer.clear()
            self.callback_final(frase_completa)


async def enviar_audio(websocket, queue):
    while True:
        data = await queue.get()
        try:
            await websocket.send(data)
        except websockets.exceptions.ConnectionClosed:
            break
        finally:
            queue.task_done()


async def rebre_transcripcio(websocket, acumulador):
    while True:
        try:
            missatge = await websocket.recv()
            if isinstance(missatge, str):
                data = json.loads(missatge)
                channel = data.get("channel", {})
                alternatives = channel.get("alternatives", [])

                if alternatives:
                    transcript = alternatives[0].get("transcript", "").strip()
                    is_final = data.get("is_final", False)

                    if transcript and is_final:
                        acumulador.afegir_fragment(transcript)

        except websockets.exceptions.ConnectionClosed:
            break


def al_completar_frase(text_complet):
    print(f"🎭 [ACTOR / GUIÓ]: {text_complet}")


async def main():
    print(f"Connectant a {WS_URL}...")
    loop = asyncio.get_running_loop()
    audio_queue = asyncio.Queue()
    
    # Acumulador amb llindar de silenci de 0.8s
    acumulador = AcumuladorFrase(callback_final=al_completar_frase, silence_timeout=0.8)

    def audio_callback(indata, frames, time_info, status):
        loop.call_soon_threadsafe(audio_queue.put_nowait, indata.tobytes())

    try:
        async with websockets.connect(WS_URL, ping_interval=None) as ws:
            print("Connexió establerta amb buffer d'acumulació de frases!\n")

            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='int16', callback=audio_callback, blocksize=2048):
                tasca_enviar = asyncio.create_task(enviar_audio(ws, audio_queue))
                tasca_rebre = asyncio.create_task(rebre_transcripcio(ws, acumulador))

                await asyncio.gather(tasca_enviar, tasca_rebre)

    except Exception as e:
        print(f"\n[ERROR]: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nPrograma aturat.")
