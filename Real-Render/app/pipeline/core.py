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
from ..services.remotion import remotion_available, render_branded_video, render_instagram_carousel
from ..services.enhancement import enhance_all_photos
from ..services.staging import staging_available, stage_all_rooms
from ..services.reconstruction import reconstruction_available, reconstruct_from_video, extract_frames
from ..services.model_viewer import build_model_viewer
from ..services.tour_builder import build_tour
from ..services.mls_formatter import format_all_for_mls

logger = logging.getLogger(__name__)

DEFAULT_OPTIONS: dict[str, Any] = {
    "furnishing": "as_is",
    "lighting": "natural",
    "deliverables": {
        "walkthrough_video": True,
    },
}

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


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
    elif package == "signature":
        return "compete", "staged", 2
    elif package == "premium":
        return "compete", "staged", 3
    else:
        return settings.agent_strategy, "as_is", settings.agent_max_rounds


def _find_input_video(input_dir: str) -> str | None:
    """Find a video file in the input directory (for Signature video or Premium LiDAR)."""
    for p in Path(input_dir).iterdir():
        if p.suffix.lower() in _VIDEO_EXTS and p.is_file():
            return str(p)
    return None


def process_job(job_id: str) -> None:  # noqa: C901
    job = db.get_job(job_id)
    if not job:
        raise ValueError(f"Unknown job_id: {job_id}")

    db.update_job(job_id, status="processing", error=None)
    input_dir, outputs_dir = ensure_dirs(job_id)
    job_addons = job.addons if hasattr(job, "addons") else []
    package = job.package or "essential"

    qc = run_qc(input_dir)
    db.update_job(job_id, qc=qc)

    strategy, furnishing, max_rounds = _package_settings(package)

    options = {**DEFAULT_OPTIONS, **job.options}
    options["furnishing"] = furnishing

    provider_state: dict[str, Any] = {
        "steps": [],
        "strategy": strategy,
        "package": package,
    }
    total_cost = 0.0

    # Determine which input dir to use for video generation
    # This gets updated as enhancement/staging/reconstruction runs
    effective_input_dir = input_dir

    # ==================================================================
    # PHASE 0a: ENHANCE — improve photo quality (all tiers)
    # ==================================================================
    _update_phase(provider_state, job_id, "enhance")

    enhanced_dir = str(Path(outputs_dir) / "enhanced")
    try:
        enhance_result = enhance_all_photos(input_dir, enhanced_dir)
        provider_state["enhancement"] = {
            "ok": enhance_result["ok"],
            "count": enhance_result.get("enhanced_count", 0),
        }
        if enhance_result["ok"]:
            effective_input_dir = enhanced_dir
            logger.info("Photo enhancement complete: %d photos", enhance_result["enhanced_count"])
    except Exception:
        provider_state["enhancement"] = {"ok": False, "reason": "exception"}
        logger.exception("Photo enhancement failed; continuing with originals")

    # ==================================================================
    # PHASE 0b: RECONSTRUCT — 3D model from LiDAR video (Premium only)
    # ==================================================================
    reconstruction_data: dict[str, Any] = {}

    if package == "premium":
        input_video = _find_input_video(input_dir)

        if input_video and reconstruction_available():
            _update_phase(provider_state, job_id, "reconstruct")
            try:
                recon_result = reconstruct_from_video(
                    video_path=input_video,
                    output_dir=str(Path(outputs_dir) / "reconstruction"),
                    job_id=job_id,
                )
                reconstruction_data = recon_result
                total_cost += recon_result.get("cost_usd", 0.0)
                provider_state["reconstruction"] = {
                    "ok": recon_result["ok"],
                    "glb_path": recon_result.get("glb_path"),
                    "reason": recon_result.get("reason"),
                }

                # Use extracted frames as additional input for video generation
                if recon_result["ok"] and recon_result.get("frames_dir"):
                    effective_input_dir = recon_result["frames_dir"]
                    logger.info("3D reconstruction complete: %s", recon_result.get("glb_path"))

            except Exception:
                provider_state["reconstruction"] = {"ok": False, "reason": "exception"}
                logger.exception("3D reconstruction failed; continuing without 3D model")

        elif input_video and not reconstruction_available():
            # Extract frames from video even without reconstruction
            _update_phase(provider_state, job_id, "frame_extract")
            frames_dir = str(Path(outputs_dir) / "video_frames")
            frames = extract_frames(input_video, frames_dir, fps=1.0, max_frames=30)
            if frames:
                effective_input_dir = frames_dir
                logger.info("Extracted %d frames from video for processing", len(frames))

    elif package == "signature":
        # Signature can optionally upload video — extract frames for better input
        input_video = _find_input_video(input_dir)
        if input_video:
            _update_phase(provider_state, job_id, "frame_extract")
            frames_dir = str(Path(outputs_dir) / "video_frames")
            frames = extract_frames(input_video, frames_dir, fps=1.0, max_frames=20)
            if frames:
                # Merge: use both original photos and extracted frames
                logger.info("Extracted %d frames from Signature video input", len(frames))

    # ==================================================================
    # PHASE 0c: STAGE — virtual staging (Signature + Premium, or add-on)
    # ==================================================================
    staged_dir = str(Path(outputs_dir) / "staged")
    do_staging = package in ("signature", "premium") or "custom_staging" in job_addons

    if do_staging and staging_available():
        _update_phase(provider_state, job_id, "stage")

        staging_style = options.get("staging_style", "modern")
        if "custom_staging" in job_addons and options.get("staging_style"):
            staging_style = options["staging_style"]

        try:
            staging_result = stage_all_rooms(
                input_dir=effective_input_dir,
                output_dir=staged_dir,
                style=staging_style,
            )
            total_cost += staging_result.get("cost_usd", 0.0)
            provider_state["staging"] = {
                "ok": staging_result["ok"],
                "style": staging_style,
                "count": staging_result.get("staged_count", 0),
            }
            if staging_result["ok"]:
                # Use staged photos for video generation
                effective_input_dir = staged_dir
                logger.info("Virtual staging complete: %d photos in '%s' style",
                            staging_result["staged_count"], staging_style)
        except Exception:
            provider_state["staging"] = {"ok": False, "reason": "exception"}
            logger.exception("Virtual staging failed; continuing with unstaged photos")
    elif do_staging and not staging_available():
        provider_state["staging"] = {"ok": False, "reason": "api_not_configured"}

    # ==================================================================
    # PHASE 1: COMPETE — run video providers
    # ==================================================================
    _update_phase(provider_state, job_id, "compete")

    results: dict[str, ProviderResult] = {}

    if strategy == "luma_only":
        res = _run_luma(job_id, effective_input_dir, outputs_dir, options)
        results["luma"] = res
        total_cost += res.cost_usd
        provider_state["steps"].append({"provider": "luma", "phase": "compete", "ok": res.ok, "result": res.data})

    elif strategy == "veo_only":
        res = _run_veo(job_id, effective_input_dir, outputs_dir, options)
        results["veo"] = res
        total_cost += res.cost_usd
        provider_state["steps"].append({"provider": "veo", "phase": "compete", "ok": res.ok, "result": res.data})

    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            luma_future = pool.submit(_run_luma, job_id, effective_input_dir, outputs_dir, options)
            veo_future = pool.submit(_run_veo, job_id, effective_input_dir, outputs_dir, options)
            luma_res = luma_future.result()
            veo_res = veo_future.result()

        results["luma"] = luma_res
        results["veo"] = veo_res
        total_cost += luma_res.cost_usd + veo_res.cost_usd

        provider_state["steps"].append({"provider": "luma", "phase": "compete", "ok": luma_res.ok, "result": luma_res.data})
        provider_state["steps"].append({"provider": "veo", "phase": "compete", "ok": veo_res.ok, "result": veo_res.data})

    ok_results = {k: v for k, v in results.items() if v.ok}
    if not ok_results:
        provider_state["total_cost_usd"] = total_cost
        errors = "; ".join(f"{k}: {v.data.get('error', 'unknown')}" for k, v in results.items())
        db.update_job(job_id, status="error", provider=provider_state, error=errors)
        return

    # ==================================================================
    # PHASE 2: EVALUATE — score videos
    # ==================================================================
    if len(ok_results) == 1:
        winner_name = next(iter(ok_results))
        provider_state["winner"] = winner_name
        provider_state["scores"] = {}
    else:
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
        logger.info("Evaluate winner: %s (%.1f vs %.1f)",
                     winner_name, scores[winner_name]["score"], scores[loser_name]["score"])

        # ==============================================================
        # PHASE 3: REFINE — extract keyframe, re-run loser
        # ==============================================================
        refine_iterations = max_rounds - 1
        for refine_round in range(1, refine_iterations + 1):
            if not (ffmpeg_available() and can_afford(
                settings.cost_per_luma_generation if loser_name == "luma" else settings.cost_per_veo_generation
            )):
                break

            _update_phase(provider_state, job_id, f"refine_{refine_round}")

            current_winner = provider_state["winner"]
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
                        loser_name = "veo" if "luma" in refined_key else "luma"
                        logger.info("Refine round %d: new winner %s", refine_round, refined_key)

    # ==================================================================
    # PHASE 4: FINALIZE — copy best video, build deliverables
    # ==================================================================
    _update_phase(provider_state, job_id, "finalize")

    final_winner = provider_state.get("winner", "")
    best_res = ok_results.get(final_winner)
    if not best_res and final_winner:
        base_name = final_winner.split("_r")[0].replace("_refined", "")
        best_res = ok_results.get(base_name)

    canonical = Path(outputs_dir) / f"{job_id}_walkthrough.mp4"
    if best_res:
        src_mp4 = best_res.data.get("artifacts", {}).get("walkthrough_mp4", "")
        if src_mp4 and Path(src_mp4).exists():
            shutil.copy2(src_mp4, canonical)
            logger.info("Canonical output → %s (from %s)", canonical, final_winner)

    provider_state["total_cost_usd"] = total_cost

    # ------------------------------------------------------------------
    # Phase 4b: POLISH — Remotion branding + Instagram carousel
    # ------------------------------------------------------------------
    address = options.get("property_address", job.options.get("property_address", ""))
    agent_name = options.get("agent_name", job.options.get("agent_name", ""))

    if canonical.exists() and remotion_available():
        _update_phase(provider_state, job_id, "polish")

        polished_mp4 = Path(outputs_dir) / f"{job_id}_walkthrough_branded.mp4"
        try:
            branded_ok = render_branded_video(
                raw_mp4=str(canonical),
                output_mp4=str(polished_mp4),
                address=address,
                agent_name=agent_name,
            )
            if branded_ok and polished_mp4.exists():
                shutil.copy2(polished_mp4, canonical)
                provider_state["polish"] = {"branded": True, "branded_path": str(polished_mp4)}
                logger.info("Branded video applied")
            else:
                provider_state["polish"] = {"branded": False, "reason": "render_failed"}
        except Exception:
            provider_state["polish"] = {"branded": False, "reason": "exception"}
            logger.exception("Remotion branded render failed; using raw video")

        if "instagram_carousel" in job_addons:
            carousel_dir = str(Path(outputs_dir) / f"{job_id}_carousel")
            try:
                carousel_clips = render_instagram_carousel(
                    raw_mp4=str(canonical),
                    output_dir=carousel_dir,
                    address=address,
                )
                if carousel_clips:
                    provider_state.setdefault("polish", {})["carousel"] = True
                    provider_state["polish"]["carousel_clips"] = carousel_clips
                    logger.info("Carousel rendered: %d clips", len(carousel_clips))
                else:
                    provider_state.setdefault("polish", {})["carousel"] = False
            except Exception:
                provider_state.setdefault("polish", {})["carousel"] = False
                logger.exception("Carousel render failed; skipping")

        db.update_job(job_id, provider=provider_state)

    # ------------------------------------------------------------------
    # Phase 4c: 3D MODEL VIEWER (Premium only)
    # ------------------------------------------------------------------
    if package == "premium" and reconstruction_data.get("ok"):
        _update_phase(provider_state, job_id, "model_viewer")
        glb_path = reconstruction_data.get("glb_path", "")
        if glb_path and Path(glb_path).exists():
            viewer_html = str(Path(outputs_dir) / f"{job_id}_3d_tour.html")
            try:
                ok = build_model_viewer(
                    glb_path=glb_path,
                    output_html=viewer_html,
                    property_info={"address": address, "agent_name": agent_name},
                )
                provider_state["model_viewer"] = {"ok": ok, "html_path": viewer_html if ok else None}
                if ok:
                    logger.info("3D model viewer generated: %s", viewer_html)
            except Exception:
                provider_state["model_viewer"] = {"ok": False, "reason": "exception"}
                logger.exception("3D model viewer generation failed")

    # ------------------------------------------------------------------
    # Phase 4d: INTERACTIVE TOUR (Signature = photo tour, Premium = 3D + photo)
    # ------------------------------------------------------------------
    if package in ("signature", "premium"):
        _update_phase(provider_state, job_id, "tour")

        # Use staged photos if available, otherwise enhanced, otherwise originals
        tour_photos_dir = staged_dir if Path(staged_dir).exists() and any(Path(staged_dir).iterdir()) else enhanced_dir
        if not Path(tour_photos_dir).exists() or not any(Path(tour_photos_dir).iterdir()):
            tour_photos_dir = input_dir

        tour_html = str(Path(outputs_dir) / f"{job_id}_virtual_tour.html")

        # Link to 3D model viewer if available (Premium)
        model_viewer_url = None
        if package == "premium" and provider_state.get("model_viewer", {}).get("ok"):
            model_viewer_url = f"{job_id}_3d_tour.html"

        try:
            tour_ok = build_tour(
                image_dir=tour_photos_dir,
                output_html=tour_html,
                property_info={"address": address, "agent_name": agent_name},
                model_viewer_url=model_viewer_url,
            )
            provider_state["tour"] = {"ok": tour_ok, "html_path": tour_html if tour_ok else None}
            if tour_ok:
                logger.info("Interactive tour generated: %s", tour_html)
        except Exception:
            provider_state["tour"] = {"ok": False, "reason": "exception"}
            logger.exception("Tour generation failed")

    # ------------------------------------------------------------------
    # Phase 4e: MLS FORMATTING (Premium only)
    # ------------------------------------------------------------------
    if package == "premium":
        _update_phase(provider_state, job_id, "mls_format")

        mls_photos_dir = staged_dir if Path(staged_dir).exists() and any(Path(staged_dir).iterdir()) else enhanced_dir
        if not Path(mls_photos_dir).exists() or not any(Path(mls_photos_dir).iterdir()):
            mls_photos_dir = input_dir

        mls_output_dir = str(Path(outputs_dir) / "mls")
        walkthrough_path = str(canonical) if canonical.exists() else None

        try:
            mls_result = format_all_for_mls(
                photos_dir=mls_photos_dir,
                video_path=walkthrough_path,
                output_dir=mls_output_dir,
            )
            provider_state["mls"] = {
                "ok": mls_result["ok"],
                "photo_count": mls_result.get("photo_count", 0),
                "video": mls_result.get("video") is not None,
            }
            if mls_result["ok"]:
                logger.info("MLS formatting complete: %d photos", mls_result["photo_count"])
        except Exception:
            provider_state["mls"] = {"ok": False, "reason": "exception"}
            logger.exception("MLS formatting failed")

    # ------------------------------------------------------------------
    # Upload all artifacts to S3/R2
    # ------------------------------------------------------------------
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
