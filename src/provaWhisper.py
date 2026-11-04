import time
import sys
import warnings
import os

warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"] = "ignore"

from transcriptorWhisper import TranscriptorWhisper

def main():
    print("[*] Carregant teloAuto amb VAD Intel·ligent (Silero)...")
    
    transcriptor = TranscriptorWhisper(config_path="configTranscriptorWhisper.yaml")
    transcriptor.iniciar_captura_continua()
    
    print("\n[*] Escoltant activament... Parla frases completes (Ctrl+C per sortir):\n")

    al_lucinacions = ["gràcies.", "gràcies", "fins demà!", "bona nit.", "subtítols", "subtítols realitzats"]

    try:
        while True:
            # S'espera a tenir una frase completa tallada per silenci natural
            raw_pcm = transcriptor.extreure_frase_vad()
            
            if raw_pcm:
                text, _ = transcriptor.processar_amb_alternatives(raw_pcm)
                txt_clean = text.strip()
                
                if txt_clean and txt_clean.lower() not in al_lucinacions:
                    print(f"👉 {txt_clean}")

    except KeyboardInterrupt:
        print("\n[*] Aturant teloAuto...")
        transcriptor.aturar_captura()
        sys.exit(0)

if __name__ == "__main__":
    main()
