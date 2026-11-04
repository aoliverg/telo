import sys
import time
from transcriptorWhisperLiveKit import TranscriptorWhisperLiveKit


def al_rebre_frase(text, confidence):
    """
    Callback que s'executa quan el motor confirma una frase sencera.
    Neteja la línia actual del terminal i mostra la transcripció neta.
    """
    sys.stdout.write("\r" + " " * 80 + "\r")
    print(f"🎭 [GUIÓ / ACTOR]: {text}")


def main():
    print("=" * 60)
    print("  TeloAuto - Prova de Transcripció amb WhisperLiveKit")
    print("=" * 60)

    # Instanciem el transcriptor carregant la configuració YAML
    transcriptor = TranscriptorWhisperLiveKit(config_path="configTranscriptorTelo.yaml")

    # 1. Engega el servidor 'wlk' en segon pla amb MLX i --pcm-input
    transcriptor.inicialitzar_motor()

    # 2. Obre la connexió WebSocket nàtiva i activa la captura del micròfon
    transcriptor.iniciar_escolta(callback_resultat=al_rebre_frase)

    print("\n[OK] Sistema a punt i escoltant. Parla pel micròfon!\n")

    try:
        # Mantenim el fil principal actiu
        while True:
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        print("\n\n[INFO] Aturant el sistema i tancant el servidor...")
        # Atura el micròfon, el WebSocket i mata el procés 'wlk' en segon pla
        transcriptor.aturar_escolta()
        print("[INFO] Sistema aturat correctament.")


if __name__ == "__main__":
    main()
