import numpy as np
import ctranslate2
from faster_whisper import WhisperModel
from faster_whisper.tokenizer import Tokenizer

def provar_candidates():
    model_path = "../faster-whisper-large-v3"
    print(f"[*] Carregant model des de {model_path}...")
    model = WhisperModel(model_path, device="cpu", compute_type="int8")

    # Àudio de prova de 1.5s
    t = np.linspace(0, 1.5, int(16000 * 1.5), False)
    audio_prova = np.sin(440 * t * 2 * np.pi).astype(np.float32)

    # 1. Extraiem característiques d'àudio i fem encode a CTranslate2
    ct2_model = model.model
    features = model.feature_extractor(audio_prova)
    features_view = ctranslate2.StorageView.from_array(np.expand_dims(features, 0))
    encoder_output = ct2_model.encode(features_view)

    # 2. Tokenizer i preparació de la seqüència inicial en català
    tokenizer = Tokenizer(
        model.hf_tokenizer,
        multilingual=True,
        task="transcribe",
        language="ca"
    )
    prompt_tokens = tokenizer.sot_sequence

    # 3. Sol·licitem les 3 millors hipòtesis del beam search a CTranslate2
    results = ct2_model.generate(
        encoder_output,
        [prompt_tokens],
        beam_size=5,
        num_hypotheses=3
    )

    resultat = results[0]

    # Fem servir sequences_ids per tenir els IDs numèrics directes
    ids_llista = getattr(resultat, "sequences_ids", [])
    
    print(f"\n✅ S'han trobat {len(ids_llista)} hipòtesis candidates a CTranslate2:\n")
    
    for idx, seq_ids in enumerate(ids_llista):
        # Decode dels IDs numèrics amb el tokenizer de HuggingFace/Rust
        text = model.hf_tokenizer.decode(seq_ids, skip_special_tokens=True)
        score = resultat.scores[idx] if hasattr(resultat, "scores") and len(resultat.scores) > idx else 0.0
        print(f"   Candidate [{idx + 1}] (Score: {score:.4f}): '{text.strip()}'")

if __name__ == "__main__":
    provar_candidates()
