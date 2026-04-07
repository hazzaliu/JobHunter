"""
embedder.py
Vector-based job similarity scoring using sentence-transformers.

Embeds the candidate profile (CV + cover letter + strategy) and computes
cosine similarity against job descriptions for fast, objective relevance ranking.
"""

import hashlib
import json
import os
import pickle
import sys

import numpy as np
import pdfplumber
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE = 200  # words per chunk
CHUNK_OVERLAP = 50  # overlap words between chunks


def get_model():
    """Load the sentence-transformer model (cached after first call)."""
    return SentenceTransformer(MODEL_NAME)


def extract_pdf_text(path):
    """Extract all text from a PDF file."""
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split text into overlapping word-level chunks for embedding."""
    words = text.split()
    if len(words) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        start += chunk_size - overlap
    return chunks


def build_profile_text(private_docs_dir="private_docs", strategy_path="strategy.json"):
    """
    Combine CV, cover letter, and strategy.json into a single profile text.
    CV and cover letter are weighted more heavily (included fully).
    Strategy adds positioning, selling points, and differentiators.
    """
    cv_text = ""
    cl_text = ""

    if os.path.exists(private_docs_dir):
        for f in sorted(os.listdir(private_docs_dir)):
            path = os.path.join(private_docs_dir, f)
            if not f.lower().endswith(".pdf"):
                continue
            text = extract_pdf_text(path)
            if "resume" in f.lower() or "cv" in f.lower():
                cv_text = text
            elif "cover" in f.lower():
                cl_text = text

    # Load strategy for additional context
    strategy_text = ""
    if os.path.exists(strategy_path):
        with open(strategy_path, "r") as f:
            strategy = json.load(f)

        selling_points = " ".join(
            f"{sp['name']}: {sp['metric']}. {sp['story']}"
            for sp in strategy.get("selling_points", [])
        )
        differentiators = " ".join(
            strategy.get("competitive_edge", {}).get("key_differentiators", [])
        )
        energisers = " ".join(strategy.get("energisers", []))

        strategy_text = f"""
Positioning: {strategy.get('positioning_statement', '')}
Target roles: {', '.join(strategy.get('target_titles', []))}
Target functions: {', '.join(strategy.get('target_functions', []))}
Target industries: {', '.join(strategy.get('target_industries', []))}
Competitive edge: {strategy.get('competitive_edge', {}).get('summary', '')}
Differentiators: {differentiators}
Selling points: {selling_points}
Energisers: {energisers}
""".strip()

    # Combine: CV first (most important), then cover letter, then strategy
    parts = [p for p in [cv_text, cl_text, strategy_text] if p.strip()]
    profile = "\n\n".join(parts)

    if not profile.strip():
        raise ValueError(
            "No profile text found. Ensure CV/resume PDFs are in private_docs/ "
            "and/or strategy.json exists."
        )

    return profile


def generate_embedding(text, model=None):
    """
    Generate a single embedding for a (potentially long) text.
    Chunks the text, embeds each chunk, and returns the normalized mean.
    """
    if model is None:
        model = get_model()

    chunks = chunk_text(text)
    embeddings = model.encode(chunks, normalize_embeddings=True)

    # Mean pooling across chunks, then re-normalize
    mean_embedding = np.mean(embeddings, axis=0)
    mean_embedding = mean_embedding / np.linalg.norm(mean_embedding)

    return mean_embedding


def _compute_source_hash(private_docs_dir="private_docs", strategy_path="strategy.json"):
    """Compute a hash of all source files that feed the profile embedding."""
    h = hashlib.sha256()
    # Hash strategy.json
    if os.path.exists(strategy_path):
        h.update(open(strategy_path, "rb").read())
    # Hash all PDFs in private_docs
    if os.path.exists(private_docs_dir):
        for f in sorted(os.listdir(private_docs_dir)):
            path = os.path.join(private_docs_dir, f)
            if f.lower().endswith(".pdf") and os.path.isfile(path):
                h.update(open(path, "rb").read())
    return h.hexdigest()


def load_or_create_profile_embedding(
    private_docs_dir="private_docs",
    strategy_path="strategy.json",
    embeddings_dir="embeddings",
    force_rebuild=False,
):
    """
    Load cached profile embedding or build from scratch.
    Auto-rebuilds if source files (CV, cover letter, strategy.json) have changed.
    Returns (embedding, profile_text).
    """
    cache_path = os.path.join(embeddings_dir, "profile_embedding.pkl")
    current_hash = _compute_source_hash(private_docs_dir, strategy_path)

    if not force_rebuild and os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            data = pickle.load(f)
        cached_hash = data.get("source_hash", "")
        if cached_hash == current_hash:
            print(f"[embedder] Loaded cached profile embedding ({len(data['profile_text'])} chars)")
            return data["embedding"], data["profile_text"]
        else:
            print("[embedder] Source files changed — rebuilding profile embedding...")

    print("[embedder] Building profile embedding from CV + cover letter + strategy...")
    model = get_model()
    profile_text = build_profile_text(private_docs_dir, strategy_path)
    embedding = generate_embedding(profile_text, model)

    os.makedirs(embeddings_dir, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump({
            "embedding": embedding,
            "profile_text": profile_text,
            "source_hash": current_hash,
        }, f)

    print(f"[embedder] Profile embedding cached ({len(profile_text)} chars, {len(embedding)} dims)")
    return embedding, profile_text


def compute_similarity(profile_embedding, text, model=None):
    """Compute cosine similarity between profile embedding and a text."""
    if model is None:
        model = get_model()
    text_embedding = generate_embedding(text, model)
    similarity = float(np.dot(profile_embedding, text_embedding))
    return similarity


def classify_similarity(score):
    """Map rescaled similarity score (0-100) to Safe/Stretch/Reach classification."""
    if score >= 60:
        return "Safe"
    elif score >= 40:
        return "Stretch"
    else:
        return "Reach"


def rescale_similarity(raw_sim):
    """
    Rescale raw cosine similarity to a 0-100 range that reflects actual relevance.

    With all-MiniLM-L6-v2, profile-to-job similarity practically ranges from
    ~0.25 (irrelevant) to ~0.75 (perfect match). Raw * 100 compresses scores
    into a misleading 25-75 band. This rescales so that:
      0.25 → 0,  0.50 → 50,  0.75 → 100
    """
    floor = 0.25  # Typical minimum for any Melbourne job
    ceiling = 0.75  # Practical maximum for a perfect-fit job
    rescaled = (raw_sim - floor) / (ceiling - floor)
    return round(max(0, min(1, rescaled)) * 100, 1)


def score_jobs_by_similarity(jobs, profile_embedding, model=None):
    """
    Score all jobs by cosine similarity to the profile embedding.
    Returns sorted list (highest similarity first) with scores and classifications.
    """
    if model is None:
        model = get_model()

    scored = []
    for job in jobs:
        # Combine all available job text for embedding
        job_text = " ".join(filter(None, [
            job.get("title", ""),
            job.get("company", ""),
            job.get("description", ""),
            job.get("requirements", ""),
        ]))

        if not job_text.strip():
            continue

        similarity = compute_similarity(profile_embedding, job_text, model)
        score_100 = rescale_similarity(similarity)
        classification = classify_similarity(score_100)

        scored_job = {
            "job_id": job.get("id") or job.get("url", ""),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "url": job.get("url", ""),
            "fit_score": score_100,
            "raw_similarity": round(similarity, 4),
            "classification": classification,
            "qualifies": score_100 >= 30,  # Lower threshold for vector scoring
            "job_data": job,
        }
        scored.append(scored_job)

    scored.sort(key=lambda x: x["fit_score"], reverse=True)

    print(f"[embedder] Scored {len(scored)} jobs by similarity | "
          f"Top score: {scored[0]['fit_score'] if scored else 0} | "
          f"Median: {scored[len(scored)//2]['fit_score'] if scored else 0}")

    return scored


def select_top_jobs(scored_jobs, top_n=5, threshold=30):
    """
    Select top N jobs above threshold.
    If fewer than top_n qualify, fill with best available (marked as near_relevant_fill).
    """
    qualifying = [j for j in scored_jobs if j["fit_score"] >= threshold]
    near_relevant = [j for j in scored_jobs if j["fit_score"] < threshold]

    top = qualifying[:top_n]

    # Fill if needed
    if len(top) < top_n:
        fill_count = top_n - len(top)
        for job in near_relevant[:fill_count]:
            job["near_relevant_fill"] = True
        top.extend(near_relevant[:fill_count])

    status = "ok"
    if len(qualifying) == 0:
        status = "no_qualifying"
    elif len(qualifying) < top_n:
        status = "partial_qualifying"

    return {
        "status": status,
        "threshold": threshold,
        "top_3": top,  # Top N for deep analysis (named top_3 for compat)
        "top_all": top,
        "near_relevant": near_relevant,
        "all_scored": scored_jobs,
    }


if __name__ == "__main__":
    # Build/rebuild profile embedding
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        embedding, text = load_or_create_profile_embedding(force_rebuild=True)
        print(f"\nProfile text preview:\n{text[:500]}...")
        print(f"\nEmbedding shape: {embedding.shape}")
        print(f"Embedding norm: {np.linalg.norm(embedding):.4f}")
    elif len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test with a sample job description
        embedding, _ = load_or_create_profile_embedding()
        model = get_model()
        test_jd = (
            "Senior Product Manager - AI Platform. "
            "Lead the development of AI-powered analytics products. "
            "Experience with LLMs, data pipelines, and agile delivery required. "
            "Melbourne based, hybrid work."
        )
        sim = compute_similarity(embedding, test_jd, model)
        print(f"Test similarity: {sim:.4f} ({sim*100:.1f}/100) — {classify_similarity(sim*100)}")
    else:
        print("Usage: python embedder.py [build|test]")
