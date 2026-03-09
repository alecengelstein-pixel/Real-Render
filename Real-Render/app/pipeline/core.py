from __future__ import annotations

import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .. import db
from ..config import settings
from ..services.cost_tracker import can_afford
from ..providers.base import ProviderResult
from ..providers.luma import LumaProvider
from ..providers.veo import VeoProvider
from ..services.media.qc import run_qc
from ..services.cloud.storage import s3_configured, upload_job_outputs
from ..services.media.video import assess_video_quality, extract_keyframe, ffmpeg_available

logger = logging.getLogger(__name__)

DEFAULT_OPTIONS: dict[str, Any] = {
    "furnishing": "as_is",
    "lighting": "natural",
    "deliverables": {
        "walkthrough_video": True,
    },
}


def ensure_dirs(job_id: str) -> tuple[str, str]:
    job = db.get_job(job_id)
    if not job:
        raise ValueError(f"Unknown job_id: {job_id}")
    Path(job.input_dir).mkdir(parents=True, exist_ok=True)
    Path(job.outputs_dir).mkdir(parents=True, exist_ok=True)
    return job.input_dir, job.outputs_dir


def _update_phase(provider_state: dict[str, Any], job_id: str, phase: str) -> None:
    """Update the current pipeline phase in the DB so the progress endpoint can report it."""
    provider_state["current_phase"] = phase
    db.update_job(job_id, provider=provider_state)


def _run_luma(job_id: str, input_dir: str, outputs_dir: str, options: dict[str, Any]) -> ProviderResult:
    luma = LumaProvider()
    return luma.make_walkthrough(
        job_id=job_id,
        input_dir=input_dir,
        outputs_dir=outputs_dir,
        options=options,
        reconstruction=None,
    )


def _run_veo(job_id: str, input_dir: str, outputs_dir: str, options: dict[str, Any]) -> ProviderResult:
    veo = VeoProvider()
    return veo.make_walkthrough(
        job_id=job_id,
        input_dir=input_dir,
        outputs_dir=outputs_dir,
        options=options,
        reconstruction=None,
    )


def _package_settings(package: str | None) -> tuple[str, str, int]:
    """Return (strategy, furnishing, max_refine_rounds) for a package tier."""
    if package == "essential":
        return "luma_only", "as_is", 1
    elif package == "premium":
        return "compete", "staged", 3
    elif package == "signature":
        return "compete", "staged", 2
    else:
        # Legacy jobs without a package — use global settings
        return settings.agent_strategy, "as_is", settings.agent_max_rounds


def process_job(job_id: str) -> None:
    job = db.get_job(job_id)
    if not job:
        raise ValueError(f"Unknown job_id: {job_id}")

    db.update_job(job_id, status="processing", error=None)
    input_dir, outputs_dir = ensure_dirs(job_id)

    qc = run_qc(input_dir)
    db.update_job(job_id, qc=qc)

    # Derive pipeline behaviour from package tier
    strategy, furnishing, max_rounds = _package_settings(job.package)

    options = {**DEFAULT_OPTIONS, **job.options}
    options["furnishing"] = furnishing

    if not options.get("deliverables", {}).get("walkthrough_video", True):
        db.update_job(job_id, status="done", provider={})
        return

    provider_state: dict[str, Any] = {"steps": [], "strategy": strategy, "package": job.package}

    # ------------------------------------------------------------------
    # Phase 1: COMPETE — run providers in parallel (or single if configured)
    # ------------------------------------------------------------------
    _update_phase(provider_state, job_id, "compete")

    results: dict[str, ProviderResult] = {}
    total_cost = 0.0

    if strategy == "luma_only":
        res = _run_luma(job_id, input_dir, outputs_dir, options)
        results["luma"] = res
        total_cost += res.cost_usd
        provider_state["steps"].append({"provider": "luma", "phase": "compete", "ok": res.ok, "result": res.data})

    elif strategy == "veo_only":
        res = _run_veo(job_id, input_dir, outputs_dir, options)
        results["veo"] = res
        total_cost += res.cost_usd
        provider_state["steps"].append({"provider": "veo", "phase": "compete", "ok": res.ok, "result": res.data})

    else:
        # Default: compete — run both in parallel
        with ThreadPoolExecutor(max_workers=2) as pool:
            luma_future = pool.submit(_run_luma, job_id, input_dir, outputs_dir, options)
            veo_future = pool.submit(_run_veo, job_id, input_dir, outputs_dir, options)

            luma_res = luma_future.result()
            veo_res = veo_future.result()

        results["luma"] = luma_res
        results["veo"] = veo_res
        total_cost += luma_res.cost_usd + veo_res.cost_usd

        provider_state["steps"].append({"provider": "luma", "phase": "compete", "ok": luma_res.ok, "result": luma_res.data})
        provider_state["steps"].append({"provider": "veo", "phase": "compete", "ok": veo_res.ok, "result": veo_res.data})

    # Check if any provider succeeded
    ok_results = {k: v for k, v in results.items() if v.ok}
    if not ok_results:
        provider_state["total_cost_usd"] = total_cost
        errors = "; ".join(f"{k}: {v.data.get('error', 'unknown')}" for k, v in results.items())
        db.update_job(job_id, status="error", provider=provider_state, error=errors)
        return

    # If only one provider, skip evaluation
    if len(ok_results) == 1:
        winner_name = next(iter(ok_results))
        provider_state["winner"] = winner_name
        provider_state["scores"] = {}
    else:
        # ------------------------------------------------------------------
        # Phase 2: EVALUATE — score both videos
        # ------------------------------------------------------------------
        _update_phase(provider_state, job_id, "evaluate")

        scores: dict[str, Any] = {}
        for name, res in ok_results.items():
            mp4 = res.data.get("artifacts", {}).get("walkthrough_mp4", "")
            if mp4:
                q = assess_video_quality(mp4, name)
                scores[name] = {
                    "score": q.score,
                    "width": q.width,
                    "height": q.height,
                    "duration_secs": q.duration_secs,
                    "file_size_bytes": q.file_size_bytes,
                }
            else:
                scores[name] = {"score": 0}

        provider_state["scores"] = scores
        winner_name = max(scores, key=lambda k: scores[k]["score"])
        loser_name = "veo" if winner_name == "luma" else "luma"
        provider_state["winner"] = winner_name
        logger.info("Phase 2 winner: %s (score %.1f vs %.1f)",
                     winner_name, scores[winner_name]["score"], scores[loser_name]["score"])

        # ------------------------------------------------------------------
        # Phase 3: REFINE — extract keyframe from winner, re-run loser
        #   Loop up to (max_rounds - 1) refinement iterations
        # ------------------------------------------------------------------
        refine_iterations = max_rounds - 1  # round 1 = compete, rest = refine
        for refine_round in range(1, refine_iterations + 1):
            if not (ffmpeg_available() and can_afford(
                settings.cost_per_luma_generation if loser_name == "luma" else settings.cost_per_veo_generation
            )):
                break

            _update_phase(provider_state, job_id, f"refine_{refine_round}")

            current_winner = provider_state["winner"]
            # Get the best mp4 so far
            if current_winner in ok_results:
                winner_mp4 = ok_results[current_winner].data.get("artifacts", {}).get("walkthrough_mp4", "")
            else:
                break

            keyframe_path = str(Path(outputs_dir) / f"{job_id}_keyframe_r{refine_round}.jpg")
            extracted = extract_keyframe(winner_mp4, keyframe_path)
            if not extracted:
                break

            ref_dir = str(Path(outputs_dir) / f"refine_ref_r{refine_round}")
            Path(ref_dir).mkdir(parents=True, exist_ok=True)
            shutil.copy2(keyframe_path, Path(ref_dir) / "keyframe.jpg")

            if loser_name == "luma":
                refined_res = _run_luma(job_id, ref_dir, outputs_dir, options)
            else:
                refined_res = _run_veo(job_id, ref_dir, outputs_dir, options)

            total_cost += refined_res.cost_usd
            provider_state["steps"].append({
                "provider": loser_name, "phase": f"refine_{refine_round}",
                "ok": refined_res.ok, "result": refined_res.data,
            })

            if refined_res.ok:
                refined_mp4 = refined_res.data.get("artifacts", {}).get("walkthrough_mp4", "")
                if refined_mp4:
                    q_refined = assess_video_quality(refined_mp4, f"{loser_name}_r{refine_round}")
                    refined_key = f"{loser_name}_r{refine_round}"
                    scores[refined_key] = {
                        "score": q_refined.score,
                        "width": q_refined.width,
                        "height": q_refined.height,
                        "duration_secs": q_refined.duration_secs,
                        "file_size_bytes": q_refined.file_size_bytes,
                    }
                    provider_state["scores"] = scores

                    if q_refined.score > scores[current_winner]["score"]:
                        winner_name = refined_key
                        provider_state["winner"] = refined_key
                        ok_results[refined_key] = refined_res
                        # Swap: the new winner's opponent becomes the loser for next round
                        loser_name = "veo" if "luma" in refined_key else "luma"
                        logger.info("Refine round %d: %s beats previous winner → new winner: %s",
                                     refine_round, loser_name, refined_key)

    # ------------------------------------------------------------------
    # Phase 4: FINALIZE — copy best to canonical path, upload to S3
    # ------------------------------------------------------------------
    _update_phase(provider_state, job_id, "finalize")

    final_winner = provider_state.get("winner", "")
    best_res = ok_results.get(final_winner)
    if not best_res and final_winner:
        # Try stripping refinement suffix to find base provider result
        base_name = final_winner.split("_r")[0].replace("_refined", "")
        best_res = ok_results.get(base_name)

    if best_res:
        src_mp4 = best_res.data.get("artifacts", {}).get("walkthrough_mp4", "")
        if src_mp4 and Path(src_mp4).exists():
            canonical = Path(outputs_dir) / f"{job_id}_walkthrough.mp4"
            shutil.copy2(src_mp4, canonical)
            logger.info("Canonical output → %s (from %s)", canonical, final_winner)

    provider_state["total_cost_usd"] = total_cost

    # Upload all artifacts to S3/R2 if configured
    if s3_configured():
        try:
            s3_keys = upload_job_outputs(job_id, outputs_dir)
            provider_state["s3_keys"] = s3_keys
        except Exception:
            logger.exception("S3 upload failed for job %s; local files still available", job_id)

    db.update_job(job_id, status="done", provider=provider_state)

    # Send completion email
    try:
        from ..services.cloud.email import send_completion_email
        send_completion_email(job_id)
    except Exception:
        logger.exception("Failed to send completion email for job %s", job_id)
