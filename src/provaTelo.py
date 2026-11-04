#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import yaml

def carregar_configuracio(config_path="configTranscriptorTelo.yaml"):
    """
    Carrega la configuració del fitxer YAML.
    """
    if not os.path.exists(config_path):
        print(f"[!] Error: El fitxer de configuració '{config_path}' no existeix.")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        try:
            return yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"[!] Error en llegir el fitxer YAML: {e}")
            sys.exit(1)

def instanciar_transcriptor(config_path="configTranscriptorTelo.yaml"):
    config = carregar_configuracio(config_path)
    engine_type = config.get('engine', {}).get('engine_type', 'faster-whisper').lower()

    print(f"[*] Carregant motor de transcripció: '{engine_type}'...")

    if engine_type == 'mlx':
        from transcriptorMLX import TranscriptorMLX
        return TranscriptorMLX(config_path)
    
    elif engine_type in ['faster-whisper', 'faster_whisper']:
        from transcriptorFasterWhisper import TranscriptorFasterWhisper
        return TranscriptorFasterWhisper(config_path)

    elif engine_type in ['transformers', 'hf']:
        from transcriptorTransformers import TranscriptorTransformers
        return TranscriptorTransformers(config_path)
    
    else:
        print(f"[!] Engine no suportat a la configuració: '{engine_type}'")
        sys.exit(1)

def mostrar_resultat(resultat):
    """
    Callback que rep el resultat de la transcripció i mostra
    el text, les candidates i la probabilitat de cada paraula.
    """
    sys.stdout.write("\r" + " " * 60 + "\r")
    sys.stdout.flush()

    if isinstance(resultat, dict):
        text_principal = resultat.get('text', '')
        candidates = resultat.get('candidates', [])
        words = resultat.get('words', [])
        metrics = resultat.get('metrics', {})
        language = resultat.get('language', 'ca')

        print("\n" + "=" * 60)
        print(f"🎙️  TRANSCRIPCIÓ PRINCIPAL [{language.upper()}]:")
        print(f"    ➜  \"{text_principal}\"")

        # Mostrem les paraules amb la seva probabilitat individual
        if words:
            formatted_words = [f"{w['word']}({w['probability']*100:.0f}%)" for w in words]
            print(f"    🔍 Probabilitats: {' '.join(formatted_words)}")

        # Mostrem les candidates alternatives del Beam Search
        if len(candidates) > 1:
            print("\n📋 CANDIDATES ALTERNATIVES (Diverse Beam Search):")
            for idx, cand in enumerate(candidates):
                marca = "⭐" if idx == 0 else "  "
                print(f"    {marca} [{idx + 1}] \"{cand}\"")

        t_proc = metrics.get('processing_time_sec', 0.0)
        rms = metrics.get('rms_signal', 0.0)
        print(f"\n⏱️  Temps: {t_proc:.3f}s | RMS senyal: {rms:.4f}")
        print("=" * 60 + "\n")

    else:
        print(f"\n💬 \"{resultat}\"")
def main():
    config_file = "configTranscriptorTelo.yaml"
    
    # Permet passar un fitxer YAML diferent per línia d'ordres si es vol
    if len(sys.argv) > 1:
        config_file = sys.argv[1]

    print("==================================================")
    print("        teloAuto - Sistema de Transcripció        ")
    print("==================================================")

    # Instanciem el transcriptor (MLX o faster-whisper segons YAML)
    transcriptor = instanciar_transcriptor(config_file)

    try:
        # Iniciem el bucle d'escolta i li passem la funció de callback
        transcriptor.iniciar_escolta(mostrar_resultat)
    
    except KeyboardInterrupt:
        print("\n\n[*] Aturant el transcriptor...")
        transcriptor.aturar_escolta()
        print("[*] Transcriptor aturat. Adéu!\n")

if __name__ == "__main__":
    main()
