#!/usr/bin/env python3
"""Stage 4: Full DeepSeek comparison"""
import json, time, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from summarizer import summarize_with_api

META = Path("output/pipeline/meta.json")
OUTPUT = Path("output/pipeline")

def build_text(key):
    meta = json.loads(META.read_bytes().decode("utf-8"))
    pages = meta[key]["pages"]
    sdir = Path(f"output/pipeline/summaries/{key}")
    lines = []
    for p in pages:
        pn = p["page"]
        sf = sdir / f"P{pn:03d}.json"
        if sf.exists():
            s = json.loads(sf.read_text(encoding="utf-8"))
            c = s.get("concepts","")[:120]
            d = s.get("depth","")
            m = s.get("method","")
            t = s.get("tools","")
            e = s.get("exam_relevance","")
            v = s.get("evaluation","")
            lines.append(f"P{pn}({p['duration']}s): c=[{c}] d=[{d}] m=[{m}] tools=[{t}] exam=[{e}] eval=[{v}]")
    return "\n".join(lines)

def main():
    text_a = build_text("A")
    text_b = build_text("B")

    prompt = f"""You are a math education evaluation expert. Compare two calculus video collections.

EVALUATION CRITERIA (1-10):
1. coverage: chapter coverage completeness
2. depth: formula derivation and intuition explanation
3. examples: quality and progression  
4. coherence: logical flow between parts
5. intuition: geometry/analogies usage
6. density: effective content per minute
7. exam_fit: how directly it targets exam questions

Output ONLY valid JSON (no markdown, no extra text, no backticks):
{{"dimension_scores":[
{{"key":"coverage","label":"Coverage","score_a":8,"score_b":6,"reason_a":"...","reason_b":"...","gap_analysis":"..."}},
{{"key":"depth","label":"Depth","score_a":7,"score_b":5,"reason_a":"...","reason_b":"...","gap_analysis":"..."}},
{{"key":"examples","label":"Examples","score_a":7,"score_b":6,"reason_a":"...","reason_b":"...","gap_analysis":"..."}},
{{"key":"coherence","label":"Coherence","score_a":9,"score_b":6,"reason_a":"...","reason_b":"...","gap_analysis":"..."}},
{{"key":"intuition","label":"Intuition","score_a":8,"score_b":4,"reason_a":"...","reason_b":"...","gap_analysis":"..."}},
{{"key":"density","label":"Density","score_a":6,"score_b":9,"reason_a":"...","reason_b":"...","gap_analysis":"..."}},
{{"key":"exam_fit","label":"Exam Fit","score_a":6,"score_b":9,"reason_a":"...","reason_b":"...","gap_analysis":"..."}}
],"summary":{{"total_score_a":56,"total_score_b":48,"recommendation":"...","best_for":{{"collection_a":"...","collection_b":"..."}}}},
"radar_data":{{"labels":["Coverage","Depth","Examples","Coherence","Intuition","Density","Exam Fit"],"scores_a":[8,7,6,9,8,6,6],"scores_b":[6,5,6,6,4,9,9]}}}}

=== COLLECTION A: Kuangkuang detailed course ===
35 lectures, ~18h. Detailed chapter-by-chapter teaching.
{text_a}

=== COLLECTION B: Yigaoshu speedrun ===
8 lectures, ~7.3h. Exam-oriented mind map review.
{text_b}

Give strict, evidence-based scores."""

    prompt_path = OUTPUT / "comparison_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    print(f"Prompt: {len(prompt)} chars ~{len(prompt)//4}tokens")

    print("Calling DeepSeek...")
    t0 = time.time()
    response = summarize_with_api(prompt, scene="general", max_tokens=8192)
    elapsed = time.time() - t0
    print(f"Response: {len(response)} chars, {elapsed:.1f}s")

    report_path = OUTPUT / "comparison_report.md"
    report_path.write_text(response, encoding="utf-8")
    print(f"Saved: {report_path}")

    try:
        parsed = json.loads(response.strip())
        scores = parsed.get("dimension_scores", [])
        summary = parsed.get("summary", {})
        print("\n=== SCORES ===")
        for d in scores:
            a = d.get("score_a","?")
            b = d.get("score_b","?")
            gap = ""
            try: gap = f"({'A-B: '+str(a-b)})"
            except: pass
            print(f"  {d.get('label','?'):15s}  A={a}/10  B={b}/10  {gap}")
        print(f"\n  TOTAL: A={summary.get('total_score_a','?')}/70  B={summary.get('total_score_b','?')}/70")
        print(f"  RECOMMENDATION: {summary.get('recommendation','?')[:300]}")
    except Exception as e:
        print(f"JSON parse failed: {e}")
        print("First 500 chars:", response[:500])

if __name__ == "__main__":
    main()
