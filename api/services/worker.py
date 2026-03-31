"""Job processing worker for Cardigan.

Polls the queue for pending jobs and processes them through agent phases.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.models.events import EventCreate, EventData, EventType
from api.models.job import JobStatus
from api.services.airtable import get_airtable_client
from api.services.database import (
    claim_next_job,
    log_event,
    update_job_heartbeat,
    update_job_phase,
    update_job_status,
)
from api.services.llm import (
    LLMResponse,
    end_run_tracking,
    get_llm_client,
    start_run_tracking,
)
from api.services.logging import get_logger, setup_logging
from api.services.utils import calculate_transcript_metrics

# Initialize logging for worker
setup_logging(log_file="worker.log")
logger = get_logger(__name__)

# Default paths
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "OUTPUT"))
TRANSCRIPTS_DIR = Path(os.getenv("TRANSCRIPTS_DIR", "transcripts"))
TRANSCRIPTS_ARCHIVE_DIR = TRANSCRIPTS_DIR / "archive"
AGENTS_DIR = Path(".claude/agents")


class WorkerConfig:
    """Configuration for the job worker."""

    def __init__(
        self,
        poll_interval: int = 30,
        heartbeat_interval: int = 60,
        max_retries: int = 3,
        max_concurrent_jobs: int = 1,
        worker_id: Optional[str] = None,
    ):
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.max_retries = max_retries
        self.max_concurrent_jobs = max_concurrent_jobs
        # Generate worker_id if not provided
        self.worker_id = worker_id or f"worker-{os.getpid()}"


class JobWorker:
    """Processes jobs from the queue through agent phases.

    Note: copy_editor is intentionally excluded from automatic processing.
    It's designed to be run interactively via Claude Desktop/MCP for
    human-in-the-loop editing workflow.

    The manager phase runs last as QA review of all outputs.
    """

    # Required phases that always run
    PHASES = ["analyst", "formatter", "seo", "manager"]

    # Optional phases with trigger conditions
    # timestamp: Runs automatically for 30+ minute content, or when requested
    OPTIONAL_PHASES = ["timestamp"]

    # Duration threshold (in minutes) for auto-triggering timestamp phase
    TIMESTAMP_AUTO_THRESHOLD_MINUTES = 30

    # Phases that always run on big-brain tier (not configurable)
    # - manager: QA oversight requires strong reasoning
    FORCE_BIG_BRAIN_PHASES = ["manager"]

    # Minimum tier for phases (0=cheapskate, 1=default, 2=big-brain)
    # These set a floor but can be overridden to higher tiers via UI/config
    # - timestamp: Chapter detection needs semantic understanding, skip cheapskate
    MINIMUM_TIER_PHASES = {
        "timestamp": 1,  # Default tier minimum, big-brain still selectable
    }

    def __init__(self, config: Optional[WorkerConfig] = None):
        self.config = config or WorkerConfig()
        self.llm = get_llm_client()
        self.running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._current_job_id: Optional[int] = None

    async def start(self):
        """Start the worker polling loop with concurrent job processing."""
        self.running = True
        worker_id = self.config.worker_id
        max_concurrent = self.config.max_concurrent_jobs

        logger.info(
            "Worker starting",
            extra={
                "worker_id": worker_id,
                "poll_interval": self.config.poll_interval,
                "max_concurrent": max_concurrent,
            },
        )

        # Track active job tasks and their job_ids for cleanup on failure
        active_tasks: set = set()
        task_to_job_id: Dict[asyncio.Task, int] = {}

        while self.running:
            try:
                # Clean up completed tasks
                done_tasks = {t for t in active_tasks if t.done()}
                for task in done_tasks:
                    job_id = task_to_job_id.pop(task, None)
                    # Check for exceptions
                    try:
                        task.result()
                    except Exception as e:
                        logger.error(
                            "Task error",
                            extra={"worker_id": worker_id, "job_id": job_id, "error": str(e)},
                            exc_info=True,
                        )
                        # Mark job as failed if we have job_id and it escaped normal handling
                        if job_id is not None:
                            try:
                                await update_job_status(job_id, JobStatus.failed)
                                await log_event(
                                    EventCreate(
                                        job_id=job_id,
                                        event_type=EventType.job_failed,
                                        data=EventData(
                                            extra={
                                                "error": f"Unhandled task exception: {str(e)}",
                                                "worker_id": worker_id,
                                            }
                                        ),
                                    )
                                )
                            except Exception as cleanup_error:
                                logger.error(
                                    "Failed to mark job as failed during cleanup",
                                    extra={"job_id": job_id, "error": str(cleanup_error)},
                                )
                active_tasks -= done_tasks

                # Claim more jobs if we have capacity
                while len(active_tasks) < max_concurrent:
                    job = await claim_next_job(worker_id=worker_id)
                    if job:
                        # Convert Job model to dict for processing
                        job_dict = job.model_dump() if hasattr(job, "model_dump") else dict(job)
                        # Start processing as a task
                        task = asyncio.create_task(self.process_job(job_dict))
                        active_tasks.add(task)
                        task_to_job_id[task] = job.id
                        logger.info(
                            "Job claimed",
                            extra={
                                "worker_id": worker_id,
                                "job_id": job.id,
                                "active_jobs": len(active_tasks),
                                "max_concurrent": max_concurrent,
                            },
                        )
                    else:
                        # No more pending jobs
                        break

                # Wait before next poll
                await asyncio.sleep(self.config.poll_interval)

            except Exception as e:
                logger.error(
                    "Error in polling loop",
                    extra={"worker_id": worker_id, "error": str(e)},
                    exc_info=True,
                )
                await asyncio.sleep(self.config.poll_interval)

        # Wait for active tasks on shutdown
        if active_tasks:
            logger.info(
                "Waiting for active jobs to complete on shutdown",
                extra={"worker_id": worker_id, "active_jobs": len(active_tasks)},
            )
            await asyncio.gather(*active_tasks, return_exceptions=True)

    async def stop(self):
        """Stop the worker."""
        self.running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

    async def retry_single_phase(
        self, job_id: int, phase_name: str, force_tier: Optional[int] = None
    ) -> Dict[str, Any]:
        """Retry a single phase for a completed job.

        This allows regenerating one output (e.g., timestamp) without
        re-running the entire pipeline.

        Args:
            job_id: The job ID to retry a phase for
            phase_name: The phase to retry (e.g., 'timestamp', 'seo', 'analyst')
            force_tier: Optional tier override (0=cheapskate, 1=default, 2=big-brain)

        Returns:
            Dict with status, phase result, and any errors
        """
        from api.services.database import get_job, update_job

        # Validate phase name
        valid_phases = set(self.PHASES + self.OPTIONAL_PHASES)
        if phase_name not in valid_phases:
            return {"success": False, "error": f"Invalid phase: {phase_name}. Valid: {valid_phases}"}

        # Load job
        job = await get_job(job_id)
        if not job:
            return {"success": False, "error": f"Job {job_id} not found"}

        job_dict = job.model_dump() if hasattr(job, "model_dump") else dict(job)

        # Get project path
        project_path = Path(job_dict.get("project_path", ""))
        if not project_path.exists():
            return {"success": False, "error": f"Project path not found: {project_path}"}

        # Build context from existing outputs
        # Note: Use `or 0` pattern because .get() returns None for NULL db values
        context = {
            "project_name": job_dict.get("project_name") or "Unknown",
            "transcript_file": job_dict.get("transcript_file", ""),
            "transcript_metrics": {
                "word_count": job_dict.get("word_count") or 0,
                "estimated_duration_minutes": job_dict.get("duration_minutes") or 0,
            },
        }

        # Load existing phase outputs for context
        for existing_phase in self.PHASES:
            output_file = project_path / f"{existing_phase}_output.md"
            if output_file.exists():
                context[f"{existing_phase}_output"] = output_file.read_text()

        # Load transcript
        try:
            transcript_content = self._load_transcript(job_dict)
            context["transcript"] = transcript_content
        except Exception as e:
            logger.warning(f"Could not load transcript for phase retry: {e}")

        # For timestamp phase, load SRT content
        if phase_name == "timestamp":
            srt_path = self._find_srt_file(job_dict)
            if srt_path and srt_path.exists():
                try:
                    context["srt_content"] = srt_path.read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(f"Could not load SRT for timestamp retry: {e}")

        # Fetch SST context if available
        sst_context = await self._fetch_sst_context(job_dict)
        if sst_context:
            context["sst_context"] = sst_context

        # Set forced tier in context if specified
        if force_tier is not None:
            context["_force_tier"] = force_tier
            tier_labels = {0: "cheapskate", 1: "default", 2: "big-brain"}
            logger.info(
                "Forcing tier override",
                extra={
                    "job_id": job_id,
                    "phase": phase_name,
                    "tier": force_tier,
                    "tier_label": tier_labels.get(force_tier, "unknown"),
                },
            )

        # Run the phase
        try:
            logger.info("Retrying single phase", extra={"job_id": job_id, "phase": phase_name})

            phase_result = await self._run_phase(
                job_id=job_id,
                phase_name=phase_name,
                context=context,
                project_path=project_path,
            )

            # Update phase status in job record
            phases = job_dict.get("phases", [])
            if isinstance(phases, str):
                phases = json.loads(phases)

            # Convert any datetime objects to ISO strings for JSON serialization
            for p in phases:
                for key in ["started_at", "completed_at"]:
                    if key in p and p[key] is not None:
                        if hasattr(p[key], "isoformat"):
                            p[key] = p[key].isoformat()

            # Find and update the phase, archiving the previous run
            phase_updated = False
            for p in phases:
                if p.get("name") == phase_name:
                    # Archive the current run before overwriting
                    if p.get("model") or p.get("tier") is not None:
                        prev_run = {
                            "tier": p.get("tier"),
                            "tier_label": p.get("tier_label"),
                            "model": p.get("model"),
                            "cost": p.get("cost", 0),
                            "tokens": p.get("tokens", 0),
                            "completed_at": p.get("completed_at"),
                        }
                        previous_runs = p.get("previous_runs") or []
                        previous_runs.append(prev_run)
                        p["previous_runs"] = previous_runs
                    p["retry_count"] = (p.get("retry_count") or 0) + 1

                    # Update with new results
                    p["status"] = phase_result.get("status", "completed")
                    p["cost"] = phase_result.get("cost", 0)
                    p["tokens"] = phase_result.get("tokens", 0)
                    p["model"] = phase_result.get("model")
                    p["tier"] = phase_result.get("tier")
                    p["tier_label"] = phase_result.get("tier_label")
                    p["tier_reason"] = phase_result.get("tier_reason")
                    p["attempts"] = phase_result.get("attempts", 1)
                    p["completed_at"] = datetime.now(timezone.utc).isoformat()
                    phase_updated = True
                    break

            if not phase_updated and phase_result.get("status") == "completed":
                # Add the phase if it didn't exist
                phases.append(
                    {
                        "name": phase_name,
                        "status": "completed",
                        "cost": phase_result.get("cost", 0),
                        "tokens": phase_result.get("tokens", 0),
                        "model": phase_result.get("model"),
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )

            # Save updated phases
            from api.models.job import JobUpdate

            await update_job(job_id, JobUpdate(phases=phases))

            return {
                "success": True,
                "phase": phase_name,
                "result": phase_result,
            }

        except Exception as e:
            logger.error(
                "Phase retry failed", extra={"job_id": job_id, "phase": phase_name, "error": str(e)}, exc_info=True
            )
            return {"success": False, "error": str(e)}

    async def process_job(self, job: Dict[str, Any]):
        """Process a single job through all phases."""
        job_id = job["id"]
        self._current_job_id = job_id
        project_name = job.get("project_name", "Unknown")

        logger.info("Processing job", extra={"job_id": job_id, "project_name": project_name})

        # Start cost tracking for this run
        tracker = start_run_tracking(job_id)

        # Start heartbeat
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(job_id))

        try:
            # Status already set to in_progress by claim_next_job()

            # Log job started event
            await log_event(
                EventCreate(
                    job_id=job_id,
                    event_type=EventType.job_started,
                    data=EventData(extra={"project_name": job.get("project_name")}),
                )
            )

            # Set up project directory
            project_path = self._setup_project_dir(job)
            await update_job_status(job_id, JobStatus.in_progress, project_path=str(project_path))

            # Check if all phases are already complete (recovery case)
            phases = job.get("phases") or []
            if isinstance(phases, str):
                phases = json.loads(phases)

            if self._all_phases_complete(phases, project_path):
                logger.info(
                    "All phases already complete, marking job done",
                    extra={"job_id": job_id, "project_name": project_name},
                )
                await update_job_status(
                    job_id,
                    JobStatus.completed,
                    actual_cost=job.get("actual_cost", 0),
                )
                return

            # Load transcript
            transcript_content = self._load_transcript(job)

            # Calculate transcript metrics for routing decisions
            routing_config = self.llm.config.get("routing", {})
            threshold_minutes = routing_config.get("long_form_threshold_minutes", 15)
            transcript_metrics = calculate_transcript_metrics(
                transcript_content, long_form_threshold_minutes=threshold_minutes
            )
            logger.info(
                "Transcript metrics calculated",
                extra={
                    "job_id": job_id,
                    "word_count": transcript_metrics["word_count"],
                    "estimated_duration_minutes": transcript_metrics["estimated_duration_minutes"],
                    "is_long_form": transcript_metrics["is_long_form"],
                },
            )

            # Persist metrics to database (backfills on retry if missing)
            if not job.get("word_count") or not job.get("duration_minutes"):
                try:
                    from api.models.job import JobUpdate
                    from api.services.database import update_job

                    await update_job(
                        job_id,
                        JobUpdate(
                            word_count=transcript_metrics["word_count"],
                            duration_minutes=transcript_metrics["estimated_duration_minutes"],
                        ),
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to persist transcript metrics (non-fatal)",
                        extra={"job_id": job_id, "error": str(e)},
                    )

            # Fetch SST context if linked (Task 6.2.1)
            sst_context = await self._fetch_sst_context(job)
            if sst_context:
                logger.info(
                    "SST context loaded", extra={"job_id": job_id, "sst_title": sst_context.get("title", "Unknown")}
                )

            # Get existing phases or initialize
            phases = job.get("phases") or []
            if isinstance(phases, str):
                phases = json.loads(phases)

            # Process each phase
            context = {
                "transcript": transcript_content,
                "transcript_file": job.get("transcript_file", ""),
                "project_path": project_path,
                "transcript_metrics": transcript_metrics,
                "sst_context": sst_context,  # Add SST context to processing context
            }

            truncation_paused = False

            for phase_name in self.PHASES:
                # Check if phase already completed
                existing_phase = next((p for p in phases if p["name"] == phase_name), None)
                if existing_phase and existing_phase.get("status") == "completed":
                    logger.debug("Skipping completed phase", extra={"job_id": job_id, "phase": phase_name})
                    # Load previous output for context
                    output_file = project_path / f"{phase_name}_output.md"
                    if output_file.exists():
                        context[f"{phase_name}_output"] = output_file.read_text()
                    continue

                # Update current phase
                await update_job_status(job_id, JobStatus.in_progress, current_phase=phase_name)

                # Check if this phase has a forced tier from escalation retry
                if existing_phase and (existing_phase.get("metadata") or {}).get("forced_tier") is not None:
                    context["_force_tier"] = existing_phase["metadata"]["forced_tier"]
                    logger.info(
                        "Using forced tier from escalation retry",
                        extra={
                            "job_id": job_id,
                            "phase": phase_name,
                            "forced_tier": existing_phase["metadata"]["forced_tier"],
                        },
                    )
                else:
                    context.pop("_force_tier", None)

                # Process phase
                logger.info("Running phase", extra={"job_id": job_id, "phase": phase_name})
                phase_result = await self._run_phase(job_id, phase_name, context, project_path)

                # Update phases list with model/tier info
                phase_data = {
                    "name": phase_name,
                    "status": "completed" if phase_result["success"] else "failed",
                    "cost": phase_result.get("cost", 0),
                    "tokens": phase_result.get("tokens", 0),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "model": phase_result.get("model"),
                    "tier": phase_result.get("tier"),
                    "tier_label": phase_result.get("tier_label"),
                    "tier_reason": phase_result.get("tier_reason"),
                    "attempts": phase_result.get("attempts", 1),
                }

                # Update or add phase, preserving retry history fields
                phase_updated = False
                for i, p in enumerate(phases):
                    if p["name"] == phase_name:
                        # Preserve retry history from previous runs
                        phase_data["retry_count"] = p.get("retry_count", 0)
                        phase_data["previous_runs"] = p.get("previous_runs")
                        phase_data["metadata"] = p.get("metadata")
                        phases[i] = phase_data
                        phase_updated = True
                        break
                if not phase_updated:
                    phases.append(phase_data)

                await update_job_phase(job_id, phases)

                if not phase_result["success"]:
                    raise Exception(f"Phase {phase_name} failed: {phase_result.get('error')}")

                # Add output to context for next phase
                context[f"{phase_name}_output"] = phase_result.get("output", "")

                # === Completeness check after formatter phase ===
                if phase_name == "formatter":
                    from api.services.completeness import check_completeness

                    completeness_config = self.llm.config.get("routing", {}).get("completeness", {})
                    if completeness_config.get("enabled", True):
                        formatter_output = phase_result.get("output", "")
                        transcript_file = job.get("transcript_file", "")
                        is_srt = transcript_file.lower().endswith(".srt")

                        completeness = check_completeness(
                            formatter_output=formatter_output,
                            source_transcript=transcript_content,
                            is_srt=is_srt,
                            duration_minutes=job.get("duration_minutes"),
                            threshold=completeness_config.get("coverage_threshold", 0.70),
                            min_source_words=completeness_config.get("min_source_words", 500),
                        )

                        # Store result in context for Manager phase
                        context["completeness_check"] = completeness.to_dict()

                        if not completeness.is_complete and not completeness.skipped:
                            logger.warning(
                                "Transcript truncation detected",
                                extra={
                                    "job_id": job_id,
                                    "coverage_ratio": completeness.coverage_ratio,
                                    "source_words": completeness.source_word_count,
                                    "output_words": completeness.output_word_count,
                                },
                            )

                            await log_event(
                                EventCreate(
                                    job_id=job_id,
                                    event_type=EventType.phase_failed,
                                    data=EventData(
                                        phase="completeness_check",
                                        extra=completeness.to_dict(),
                                    ),
                                )
                            )

                            if completeness_config.get("pause_on_truncation", True):
                                truncation_msg = (
                                    f"TRUNCATION DETECTED: Formatter output covers only "
                                    f"{completeness.coverage_ratio:.0%} of source transcript "
                                    f"({completeness.output_word_count:,} / "
                                    f"{completeness.source_word_count:,} words). "
                                    f"Retry to escalate to a more capable model."
                                )
                                await update_job_status(
                                    job_id,
                                    JobStatus.paused,
                                    error_message=truncation_msg,
                                )
                                truncation_paused = True
                                break  # Exit phase loop cleanly (no exception)

            # Handle truncation pause — exit before optional phases
            if truncation_paused:
                logger.info(
                    "Job paused due to truncation detection", extra={"job_id": job_id, "project_name": project_name}
                )
                run_summary = await end_run_tracking(job_id)
                if run_summary:
                    await update_job_status(
                        job_id,
                        JobStatus.paused,
                        actual_cost=run_summary["total_cost"],
                    )
                return

            # Process optional phases (timestamp) if conditions are met
            srt_path = self._find_srt_file(job)
            if self._should_run_timestamp_phase(job, transcript_metrics, srt_path):
                phase_name = "timestamp"

                # Check if phase already completed
                existing_phase = next((p for p in phases if p["name"] == phase_name), None)
                if not (existing_phase and existing_phase.get("status") == "completed"):
                    # Add SRT content to context
                    if srt_path:
                        try:
                            context["srt_content"] = srt_path.read_text(encoding="utf-8", errors="replace")
                            context["srt_path"] = str(srt_path)
                        except Exception as e:
                            logger.warning(
                                "Failed to read SRT file for timestamp phase", extra={"job_id": job_id, "error": str(e)}
                            )

                    # Update current phase
                    await update_job_status(job_id, JobStatus.in_progress, current_phase=phase_name)

                    # Process phase
                    logger.info("Running optional phase", extra={"job_id": job_id, "phase": phase_name})
                    phase_result = await self._run_phase(job_id, phase_name, context, project_path)

                    # Update phases list
                    phase_data = {
                        "name": phase_name,
                        "status": "completed" if phase_result["success"] else "failed",
                        "cost": phase_result.get("cost", 0),
                        "tokens": phase_result.get("tokens", 0),
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "model": phase_result.get("model"),
                        "tier": phase_result.get("tier"),
                        "tier_label": phase_result.get("tier_label"),
                        "tier_reason": phase_result.get("tier_reason"),
                        "attempts": phase_result.get("attempts", 1),
                        "optional": True,  # Mark as optional phase
                    }

                    # Update existing phase entry or append new one
                    opt_phase_updated = False
                    for i, p in enumerate(phases):
                        if p.get("name") == phase_name:
                            phase_data["retry_count"] = p.get("retry_count", 0)
                            phase_data["previous_runs"] = p.get("previous_runs")
                            phase_data["metadata"] = p.get("metadata")
                            phases[i] = phase_data
                            opt_phase_updated = True
                            break
                    if not opt_phase_updated:
                        phases.append(phase_data)
                    await update_job_phase(job_id, phases)

                    # Optional phase failure is logged but doesn't fail the job
                    if not phase_result["success"]:
                        logger.warning(
                            "Optional phase failed (non-fatal)",
                            extra={"job_id": job_id, "phase": phase_name, "error": phase_result.get("error")},
                        )

                    # Add output to context
                    context[f"{phase_name}_output"] = phase_result.get("output", "")

            # Create manifest
            await self._create_manifest(job, project_path, phases, tracker)

            # Mark job completed
            run_summary = await end_run_tracking(job_id)
            await update_job_status(
                job_id,
                JobStatus.completed,
                actual_cost=run_summary["total_cost"] if run_summary else 0,
            )

            # Archive the transcript file (non-fatal if this fails)
            try:
                self._archive_transcript(job)
            except Exception as archive_err:
                logger.warning(
                    "Failed to archive transcript (non-fatal)", extra={"job_id": job_id, "error": str(archive_err)}
                )

            logger.info(
                "Job completed successfully",
                extra={
                    "job_id": job_id,
                    "project_name": project_name,
                    "total_cost": run_summary["total_cost"] if run_summary else 0,
                },
            )

        except Exception as e:
            logger.error(
                "Job failed",
                extra={"job_id": job_id, "project_name": project_name, "error": str(e)},
                exc_info=True,
            )

            # End tracking for this attempt
            run_summary = await end_run_tracking(job_id)
            current_cost = run_summary["total_cost"] if run_summary else 0

            # Set status to investigating while manager analyzes the failure
            await update_job_status(
                job_id,
                JobStatus.investigating,
                error_message=str(e),
            )

            # Run manager to analyze and decide on recovery action
            recovery_result = await self._analyze_and_recover(
                job=job,
                project_path=project_path,
                phases=phases,
                context=context,
                error=str(e),
                current_cost=current_cost,
            )

            # If recovery was successful, job status already updated
            if recovery_result.get("recovered"):
                logger.info(
                    "Job recovered by manager",
                    extra={
                        "job_id": job_id,
                        "action": recovery_result.get("action"),
                        "total_cost": recovery_result.get("total_cost", 0),
                    },
                )
                return

            # Recovery failed - mark job as failed
            await update_job_status(
                job_id,
                JobStatus.failed,
                error_message=str(e),
                actual_cost=current_cost + recovery_result.get("cost", 0),
            )

            # Log error event with investigation summary
            await log_event(
                EventCreate(
                    job_id=job_id,
                    event_type=EventType.job_failed,
                    data=EventData(
                        extra={
                            "error": str(e),
                            "recovery_attempted": recovery_result.get("action", "none"),
                            "recovery_reason": recovery_result.get("reason", "Unknown"),
                        }
                    ),
                )
            )

        finally:
            # Stop heartbeat - properly await cancellation
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task  # Wait for cancellation to complete
                except asyncio.CancelledError:
                    pass  # Expected when cancelling
                except Exception as e:
                    logger.warning("Heartbeat cleanup error", extra={"job_id": job_id, "error": str(e)})
                self._heartbeat_task = None
            self._current_job_id = None

    async def _fetch_sst_context(self, job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch SST metadata from Airtable if job has linked record.

        Args:
            job: Job dict with potential airtable_record_id field

        Returns:
            Dict with SST fields if found, None if no SST link or error
        """
        airtable_record_id = job.get("airtable_record_id")
        if not airtable_record_id:
            return None

        try:
            client = get_airtable_client()
            record = await client.get_sst_record(airtable_record_id)

            if not record:
                logger.warning("SST record not found", extra={"job_id": job.get("id"), "record_id": airtable_record_id})
                return None

            # Extract relevant fields for agent context
            fields = record.get("fields", {})
            sst_context = {
                "title": fields.get("Title"),
                "short_description": fields.get("Short Description"),
                "long_description": fields.get("Long Description"),
                "keywords": fields.get("Keywords"),
                "tags": fields.get("Tags"),
                "host": fields.get("Host"),
                "presenter": fields.get("Presenter"),
                "program": fields.get("Program"),
                "media_id": fields.get("Media ID"),
            }

            # Remove None values
            sst_context = {k: v for k, v in sst_context.items() if v is not None}

            return sst_context if sst_context else None

        except Exception as e:
            logger.warning("Failed to fetch SST context (non-fatal)", extra={"job_id": job.get("id"), "error": str(e)})
            return None

    async def _heartbeat_loop(self, job_id: int):
        """Send periodic heartbeats while processing."""
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                await update_job_heartbeat(job_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Heartbeat error", extra={"job_id": job_id, "error": str(e)})

    def _all_phases_complete(self, phases: List[Dict[str, Any]], project_path: Path) -> bool:
        """Check if all required phases are complete and output files exist.

        This is a recovery mechanism for jobs that failed after all phases
        completed (e.g., due to archiving errors). Verifies both phase status
        and actual output file existence.
        """
        if not phases:
            return False

        # Check all required phases are in the list and completed
        completed_phases = set()
        for phase in phases:
            if phase.get("status") == "completed":
                completed_phases.add(phase.get("name"))

        required_phases = set(self.PHASES)  # analyst, formatter, seo
        if not required_phases.issubset(completed_phases):
            return False

        # Verify output files actually exist
        for phase_name in required_phases:
            output_file = project_path / f"{phase_name}_output.md"
            if not output_file.exists():
                return False

        return True

    def _setup_project_dir(self, job: Dict[str, Any]) -> Path:
        """Create and return the project output directory."""
        project_name = job.get("project_name", f"job_{job['id']}")
        # Sanitize project name for filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)

        project_path = OUTPUT_DIR / safe_name
        project_path.mkdir(parents=True, exist_ok=True)

        return project_path

    def _load_transcript(self, job: Dict[str, Any]) -> str:
        """Load the transcript file content.

        Handles various encodings (UTF-8, ISO-8859, etc.) gracefully.
        Also checks the archive folder as a fallback for re-processed jobs.
        """
        transcript_file = job.get("transcript_file", "")

        # Try various paths, including archive folder as fallback
        paths_to_try = [
            Path(transcript_file),
            TRANSCRIPTS_DIR / transcript_file,
            TRANSCRIPTS_DIR / Path(transcript_file).name,
            # Archive folder fallback for re-processed jobs
            TRANSCRIPTS_ARCHIVE_DIR / transcript_file,
            TRANSCRIPTS_ARCHIVE_DIR / Path(transcript_file).name,
        ]

        for path in paths_to_try:
            if path.exists():
                # Log if we're using archive fallback
                if TRANSCRIPTS_ARCHIVE_DIR in path.parents or path.parent == TRANSCRIPTS_ARCHIVE_DIR:
                    logger.info(
                        "Using archived transcript (re-processing)",
                        extra={"job_id": job.get("id"), "source": str(path)},
                    )
                # Try different encodings
                for encoding in ["utf-8", "iso-8859-1", "cp1252", "latin-1"]:
                    try:
                        return path.read_text(encoding=encoding)
                    except UnicodeDecodeError:
                        continue
                # Last resort: read with errors='replace'
                return path.read_text(encoding="utf-8", errors="replace")

        raise FileNotFoundError(f"Transcript not found: {transcript_file}")

    def _archive_transcript(self, job: Dict[str, Any]) -> None:
        """Move completed transcript to archive folder.

        Archives the original transcript file to transcripts/archive/ after
        successful job completion. This keeps the main transcripts folder
        clean and shows only unprocessed files.
        """
        transcript_file = job.get("transcript_file", "")
        if not transcript_file:
            return

        # Find the source file
        source_paths = [
            Path(transcript_file),
            TRANSCRIPTS_DIR / transcript_file,
            TRANSCRIPTS_DIR / Path(transcript_file).name,
        ]

        source = None
        for path in source_paths:
            if path.exists():
                source = path
                break

        if not source:
            logger.warning("Transcript not found for archiving", extra={"source_file": transcript_file})
            return

        # Create archive directory if needed
        TRANSCRIPTS_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        # Move to archive
        dest = TRANSCRIPTS_ARCHIVE_DIR / source.name
        try:
            import shutil

            shutil.move(str(source), str(dest))
            logger.info("Archived transcript", extra={"source_file": source.name, "destination": str(dest)})
        except Exception as e:
            logger.error(
                "Failed to archive transcript",
                extra={"source_file": source.name, "error": str(e)},
                exc_info=True,
            )

    def _find_srt_file(self, job: Dict[str, Any]) -> Optional[Path]:
        """Find the SRT file associated with a transcript.

        Looks for an SRT file with the same base name as the transcript
        in both the transcripts folder and archive folder.

        Returns:
            Path to SRT file if found, None otherwise.
        """
        transcript_file = job.get("transcript_file", "")
        if not transcript_file:
            return None

        # Get the base name without extension
        from api.services.utils import extract_media_id

        media_id = extract_media_id(transcript_file)
        if not media_id:
            return None

        # Look for SRT file with same base name
        search_dirs = [TRANSCRIPTS_DIR, TRANSCRIPTS_ARCHIVE_DIR]

        for search_dir in search_dirs:
            if not search_dir.exists():
                continue

            # Try exact match first
            srt_path = search_dir / f"{media_id}.srt"
            if srt_path.exists():
                return srt_path

            # Try variations
            for srt_file in search_dir.glob("*.srt"):
                srt_media_id = extract_media_id(srt_file.name)
                if srt_media_id == media_id:
                    return srt_file

        return None

    def _get_content_duration_minutes(
        self, transcript_metrics: Dict[str, Any], srt_path: Optional[Path] = None
    ) -> float:
        """Get content duration in minutes from SRT or transcript metrics.

        Prefers SRT duration (more accurate) if available, falls back
        to estimated duration from transcript word count.

        Returns:
            Duration in minutes.
        """
        # Try to get duration from SRT file (most accurate)
        if srt_path and srt_path.exists():
            try:
                from api.services.utils import get_srt_duration, parse_srt

                srt_content = srt_path.read_text(encoding="utf-8", errors="replace")
                captions = parse_srt(srt_content)
                if captions:
                    duration_ms = get_srt_duration(captions)
                    return duration_ms / 60000  # Convert ms to minutes
            except Exception as e:
                logger.warning("Failed to parse SRT for duration", extra={"srt_file": str(srt_path), "error": str(e)})

        # Fall back to transcript metrics (estimated from word count)
        return transcript_metrics.get("estimated_duration_minutes", 0)

    def _should_run_timestamp_phase(
        self, job: Dict[str, Any], transcript_metrics: Dict[str, Any], srt_path: Optional[Path]
    ) -> bool:
        """Determine if the timestamp phase should run.

        Timestamp phase runs when:
        1. An SRT file exists AND
        2. Content is 30+ minutes OR job explicitly requests it

        Returns:
            True if timestamp phase should run, False otherwise.
        """
        # No SRT file = no timestamp phase
        if not srt_path or not srt_path.exists():
            logger.debug("Skipping timestamp phase: no SRT file", extra={"job_id": job.get("id")})
            return False

        # Check for explicit request in job
        include_timestamps = job.get("include_timestamps", False)
        if include_timestamps:
            logger.info("Timestamp phase enabled by request", extra={"job_id": job.get("id")})
            return True

        # Check duration threshold
        duration_minutes = self._get_content_duration_minutes(transcript_metrics, srt_path)
        if duration_minutes >= self.TIMESTAMP_AUTO_THRESHOLD_MINUTES:
            logger.info(
                "Timestamp phase auto-triggered for long content",
                extra={
                    "job_id": job.get("id"),
                    "duration_minutes": round(duration_minutes, 1),
                    "threshold": self.TIMESTAMP_AUTO_THRESHOLD_MINUTES,
                },
            )
            return True

        logger.debug(
            "Skipping timestamp phase: below duration threshold",
            extra={
                "job_id": job.get("id"),
                "duration_minutes": round(duration_minutes, 1),
                "threshold": self.TIMESTAMP_AUTO_THRESHOLD_MINUTES,
            },
        )
        return False

    async def _run_phase(
        self,
        job_id: int,
        phase_name: str,
        context: Dict[str, Any],
        project_path: Path,
    ) -> Dict[str, Any]:
        """Run a single agent phase with tiered escalation on failure.

        Attempts to run with the initial tier based on transcript duration.
        On failure or timeout, escalates to the next tier and retries.
        """
        # Check for chunked formatter processing
        if phase_name == "formatter":
            chunking_config = self.llm.config.get("routing", {}).get("chunking", {})
            if chunking_config.get("enabled", False):
                from api.services.chunking import split_transcript

                transcript_file = context.get("transcript_file", "")
                is_srt = transcript_file.lower().endswith(".srt")
                chunks = split_transcript(
                    context.get("transcript", ""),
                    is_srt=is_srt,
                    config=chunking_config,
                )
                if chunks is not None:
                    logger.info(
                        "Formatter using chunked processing",
                        extra={
                            "job_id": job_id,
                            "chunk_count": len(chunks),
                        },
                    )
                    return await self._run_formatter_chunked(
                        job_id=job_id,
                        chunks=chunks,
                        context=context,
                        project_path=project_path,
                        chunking_config=chunking_config,
                    )

        # Get escalation config
        escalation_config = self.llm.get_escalation_config()
        escalation_enabled = escalation_config.get("enabled", True)
        escalate_on_failure = escalation_config.get("on_failure", True)
        escalate_on_timeout = escalation_config.get("on_timeout", True)
        timeout_seconds = escalation_config.get("timeout_seconds", 120)

        # Check if tier is being forced (e.g., by manager escalation)
        forced_tier = context.get("_force_tier")

        # Force big-brain tier for QA phases (manager)
        if phase_name in self.FORCE_BIG_BRAIN_PHASES:
            initial_tier = 2  # big-brain tier
            initial_tier_reason = f"{phase_name} phase always uses big-brain tier for quality oversight"
        elif forced_tier is not None:
            initial_tier = forced_tier
            initial_tier_reason = f"Forced tier {forced_tier} by manager escalation"
        else:
            # Get initial tier based on context (duration thresholds)
            initial_tier, initial_tier_reason = self.llm.get_tier_for_phase_with_reason(phase_name, context)

            # Apply minimum tier floor for certain phases (e.g., timestamp needs at least default)
            min_tier = self.MINIMUM_TIER_PHASES.get(phase_name)
            if min_tier is not None and initial_tier < min_tier:
                initial_tier = min_tier
                initial_tier_reason = f"{phase_name} phase requires minimum tier {min_tier} (default tier)"

        current_tier = initial_tier
        tier_reason = initial_tier_reason
        routing_config = self.llm.config.get("routing", {})
        tier_labels = routing_config.get("tier_labels", ["cheapskate", "default", "big-brain"])

        # Load prompts once (don't reload on each retry)
        system_prompt = self._load_agent_prompt(phase_name)
        user_message = self._build_phase_prompt(phase_name, context)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        total_cost = 0.0
        total_tokens = 0
        last_error = None
        attempts = 0
        max_escalation_attempts = 10  # Safety guard against infinite loops

        while attempts < max_escalation_attempts:
            # Get backend for current tier
            backend = self.llm.get_backend_for_phase(phase_name, context, tier_override=current_tier)
            tier_label = tier_labels[current_tier] if current_tier < len(tier_labels) else f"tier-{current_tier}"
            logger.info(
                "Phase attempting with tier",
                extra={
                    "job_id": job_id,
                    "phase": phase_name,
                    "tier": current_tier,
                    "tier_label": tier_label,
                    "backend": backend,
                },
            )

            # Log phase started/retry
            await log_event(
                EventCreate(
                    job_id=job_id,
                    event_type=EventType.phase_started,
                    data=EventData(
                        phase=phase_name,
                        backend=backend,
                        extra={"tier": current_tier, "tier_label": tier_label, "attempt": attempts + 1},
                    ),
                )
            )

            try:
                # Use the backend's configured timeout, falling back to escalation config
                try:
                    backend_config = self.llm.get_backend_config(backend)
                    effective_timeout = backend_config.get("timeout", timeout_seconds)
                    if not isinstance(effective_timeout, (int, float)):
                        effective_timeout = timeout_seconds
                except Exception:
                    effective_timeout = timeout_seconds

                # Call LLM with timeout (include phase/tier for Langfuse tracing)
                response: LLMResponse = await asyncio.wait_for(
                    self.llm.chat(
                        messages=messages,
                        backend=backend,
                        job_id=job_id,
                        phase=phase_name,
                        tier=current_tier,
                        tier_label=tier_label,
                    ),
                    timeout=effective_timeout,
                )

                # Track costs across retries
                total_cost += response.cost
                total_tokens += response.total_tokens

                # Save output (preserving previous version if exists)
                output_file = project_path / f"{phase_name}_output.md"
                if output_file.exists():
                    # Preserve previous output with timestamp
                    prev_content = output_file.read_text()
                    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    prev_file = project_path / f"{phase_name}_output.{timestamp}.prev.md"
                    prev_file.write_text(prev_content)
                    logger.info(
                        "Preserved previous output",
                        extra={"job_id": job_id, "phase": phase_name, "preserved_as": prev_file.name},
                    )

                # Add provenance header to output
                provenance_header = f"<!-- model: {response.model} | tier: {tier_label} | cost: ${response.cost:.4f} | tokens: {response.total_tokens} -->\n"
                output_file.write_text(provenance_header + response.content)

                # Log phase completed
                await log_event(
                    EventCreate(
                        job_id=job_id,
                        event_type=EventType.phase_completed,
                        data=EventData(
                            phase=phase_name,
                            cost=response.cost,
                            tokens=response.total_tokens,
                            model=response.model,
                            extra={"tier": current_tier, "tier_label": tier_label, "total_attempts": attempts + 1},
                        ),
                    )
                )

                return {
                    "success": True,
                    "output": response.content,
                    "cost": total_cost,
                    "tokens": total_tokens,
                    "model": response.model,
                    "tier": current_tier,
                    "tier_label": tier_label,
                    "tier_reason": tier_reason,
                    "attempts": attempts + 1,
                }

            except asyncio.TimeoutError:
                last_error = f"Timeout after {effective_timeout}s"
                logger.warning(
                    "Phase timed out",
                    extra={
                        "job_id": job_id,
                        "phase": phase_name,
                        "tier_label": tier_label,
                        "timeout_seconds": effective_timeout,
                    },
                )

                # Check if we should escalate on timeout
                if not escalation_enabled or not escalate_on_timeout:
                    break

            except Exception as e:
                last_error = str(e)
                logger.error(
                    "Phase failed",
                    extra={
                        "job_id": job_id,
                        "phase": phase_name,
                        "tier_label": tier_label,
                        "error": str(e),
                    },
                    exc_info=True,
                )

                # Check if we should escalate on failure
                if not escalation_enabled or not escalate_on_failure:
                    break

            attempts += 1

            # Try to escalate to next tier
            next_tier = self.llm.get_next_tier(current_tier)
            if next_tier is None:
                logger.warning(
                    "Phase failed at max tier, no more escalation possible",
                    extra={
                        "job_id": job_id,
                        "phase": phase_name,
                        "final_tier": current_tier,
                    },
                )
                break

            # Log escalation
            next_label = tier_labels[next_tier] if next_tier < len(tier_labels) else f"tier-{next_tier}"
            logger.info(
                "Escalating phase to next tier",
                extra={
                    "job_id": job_id,
                    "phase": phase_name,
                    "from_tier": tier_label,
                    "to_tier": next_label,
                    "reason": last_error,
                },
            )

            # Update tier reason to reflect escalation
            tier_reason = f"escalated from {tier_label}: {last_error}"

            await log_event(
                EventCreate(
                    job_id=job_id,
                    event_type=EventType.phase_started,
                    data=EventData(
                        phase=phase_name,
                        extra={
                            "escalation": True,
                            "from_tier": current_tier,
                            "to_tier": next_tier,
                            "reason": last_error,
                        },
                    ),
                )
            )

            current_tier = next_tier

        # All attempts failed
        await log_event(
            EventCreate(
                job_id=job_id,
                event_type=EventType.phase_failed,
                data=EventData(
                    phase=phase_name, extra={"error": last_error, "attempts": attempts, "final_tier": current_tier}
                ),
            )
        )
        return {"success": False, "error": last_error, "attempts": attempts, "cost": total_cost}

    async def _run_formatter_chunked(
        self,
        job_id: int,
        chunks: list,
        context: Dict[str, Any],
        project_path: Path,
        chunking_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run formatter phase with chunked parallel processing.

        Splits long transcripts into chunks, processes them concurrently
        with a semaphore for backpressure, then merges results.
        """
        from api.services.chunking import TranscriptChunk, merge_formatter_chunks

        max_parallel = chunking_config.get("max_parallel", 3)
        semaphore = asyncio.Semaphore(max_parallel)

        # Load system prompt once
        system_prompt = self._load_agent_prompt("formatter")
        analysis = context.get("analyst_output", "")
        sst_context = context.get("sst_context")

        # Build SST section (reuse logic from _build_phase_prompt)
        sst_section = ""
        if sst_context:
            sst_section = "\n## Single Source of Truth (SST) Context\n\n"
            for key in [
                "title",
                "program",
                "short_description",
                "long_description",
                "host",
                "presenter",
                "keywords",
                "tags",
            ]:
                if sst_context.get(key):
                    sst_section += f"**{key.replace('_', ' ').title()}:** {sst_context[key]}\n"

        # Get tier/backend for this phase
        forced_tier = context.get("_force_tier")
        if forced_tier is not None:
            current_tier = forced_tier
            tier_reason = f"Forced tier {forced_tier} by manager escalation"
        else:
            current_tier, tier_reason = self.llm.get_tier_for_phase_with_reason("formatter", context)

        # Apply minimum tier for chunked formatting (cheapskate models condense too aggressively)
        min_tier = chunking_config.get("min_tier")
        if min_tier is not None and current_tier < min_tier:
            logger.info(
                "Applying chunked formatter min_tier",
                extra={"from_tier": current_tier, "min_tier": min_tier},
            )
            current_tier = min_tier
            tier_reason = f"Chunked formatter requires minimum tier {min_tier}"

        routing_config = self.llm.config.get("routing", {})
        tier_labels = routing_config.get("tier_labels", ["cheapskate", "default", "big-brain"])
        tier_label = tier_labels[current_tier] if current_tier < len(tier_labels) else f"tier-{current_tier}"
        backend = self.llm.get_backend_for_phase("formatter", context, tier_override=current_tier)

        # Get timeout from backend config
        escalation_config = self.llm.get_escalation_config()
        timeout_seconds = escalation_config.get("timeout_seconds", 120)
        try:
            backend_config = self.llm.get_backend_config(backend)
            effective_timeout = backend_config.get("timeout", timeout_seconds)
            if not isinstance(effective_timeout, (int, float)):
                effective_timeout = timeout_seconds
        except Exception:
            effective_timeout = timeout_seconds

        total_chunks = len(chunks)

        async def process_chunk(chunk: TranscriptChunk) -> str:
            """Process a single chunk through the formatter LLM."""
            async with semaphore:
                # Verbatim preservation instruction (applies to all chunks)
                verbatim_instruction = """CRITICAL: You MUST preserve ALL spoken dialogue. Do NOT summarize, condense, or paraphrase.
Every sentence spoken in the transcript must appear in your output. You may remove filler words
(um, uh) and fix grammar, but do NOT drop or merge sentences. Completeness is more important than brevity."""

                if chunk.index == 0:
                    # First chunk: normal formatter prompt
                    user_message = f"{verbatim_instruction}\n\n"
                    user_message += "Using the following analysis as guidance:\n\n"
                    if sst_section:
                        user_message += sst_section
                    user_message += f"---\n{analysis}\n---\n\n"
                    user_message += f"Please format this transcript:\n\n---\n{chunk.content}\n---"
                else:
                    # Continuation chunks: skip header, start with dialogue
                    user_message = f"""{verbatim_instruction}

IMPORTANT: This is section {chunk.index + 1} of {total_chunks} of a long transcript being processed in parts.
DO NOT generate the metadata header (Project, Program, Duration, Date).
DO NOT generate "# Formatted Transcript" heading.
Begin directly with speaker attribution and dialogue.
The previous section ended with:
---
{chunk.overlap_prefix}
---
Continue formatting from where the previous section left off. Do NOT repeat content from the overlap above.

Using the following analysis as guidance:
---
{analysis}
---

Please format this transcript section:

---
{chunk.content}
---"""

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ]

                response = await asyncio.wait_for(
                    self.llm.chat(
                        messages=messages,
                        backend=backend,
                        job_id=job_id,
                        phase="formatter",
                        tier=current_tier,
                        tier_label=tier_label,
                    ),
                    timeout=effective_timeout,
                )

                logger.info(
                    "Chunk processed",
                    extra={
                        "job_id": job_id,
                        "chunk_index": chunk.index,
                        "total_chunks": total_chunks,
                        "cost": response.cost,
                        "tokens": response.total_tokens,
                    },
                )

                return response.content

        # Log phase start
        await log_event(
            EventCreate(
                job_id=job_id,
                event_type=EventType.phase_started,
                data=EventData(
                    phase="formatter",
                    backend=backend,
                    extra={
                        "tier": current_tier,
                        "tier_label": tier_label,
                        "chunked": True,
                        "chunk_count": total_chunks,
                    },
                ),
            )
        )

        try:
            # Run all chunks concurrently (semaphore limits parallelism)
            chunk_results = await asyncio.gather(
                *(process_chunk(chunk) for chunk in chunks),
                return_exceptions=True,
            )

            # Check for failures
            for i, result in enumerate(chunk_results):
                if isinstance(result, Exception):
                    error_msg = f"Chunk {i} failed: {result}"
                    logger.error(error_msg, extra={"job_id": job_id, "chunk_index": i})
                    await log_event(
                        EventCreate(
                            job_id=job_id,
                            event_type=EventType.phase_failed,
                            data=EventData(
                                phase="formatter",
                                extra={"error": error_msg, "chunk_index": i},
                            ),
                        )
                    )
                    return {
                        "success": False,
                        "error": error_msg,
                        "attempts": 1,
                        "cost": 0,
                    }

            # Merge outputs
            merged = merge_formatter_chunks(chunk_results)

            # Calculate aggregate cost/tokens from run tracker
            # (RunCostTracker accumulates automatically via llm.chat)
            # We report 0 here since the tracker handles it
            total_cost = 0.0
            total_tokens = 0

            # Save merged output
            output_file = project_path / "formatter_output.md"
            if output_file.exists():
                prev_content = output_file.read_text()
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                prev_file = project_path / f"formatter_output.{timestamp}.prev.md"
                prev_file.write_text(prev_content)

            provenance_header = (
                f"<!-- model: chunked ({total_chunks} chunks) | " f"tier: {tier_label} | backend: {backend} -->\n"
            )
            output_file.write_text(provenance_header + merged)

            await log_event(
                EventCreate(
                    job_id=job_id,
                    event_type=EventType.phase_completed,
                    data=EventData(
                        phase="formatter",
                        cost=total_cost,
                        tokens=total_tokens,
                        extra={
                            "tier": current_tier,
                            "tier_label": tier_label,
                            "chunked": True,
                            "chunk_count": total_chunks,
                        },
                    ),
                )
            )

            return {
                "success": True,
                "output": merged,
                "cost": total_cost,
                "tokens": total_tokens,
                "model": f"chunked ({total_chunks} chunks via {backend})",
                "tier": current_tier,
                "tier_label": tier_label,
                "tier_reason": tier_reason,
                "attempts": 1,
            }

        except Exception as e:
            error_msg = f"Chunked formatter failed: {e}"
            logger.error(error_msg, extra={"job_id": job_id}, exc_info=True)
            await log_event(
                EventCreate(
                    job_id=job_id,
                    event_type=EventType.phase_failed,
                    data=EventData(
                        phase="formatter",
                        extra={"error": error_msg, "chunked": True},
                    ),
                )
            )
            return {
                "success": False,
                "error": error_msg,
                "attempts": 1,
                "cost": 0,
            }

    async def _analyze_and_recover(
        self,
        job: Dict[str, Any],
        project_path: Path,
        phases: List[Dict[str, Any]],
        context: Dict[str, Any],
        error: str,
        current_cost: float,
    ) -> Dict[str, Any]:
        """Run manager agent to analyze failure and attempt recovery.

        The manager decides on an action:
        - RETRY: Re-run the failed phase at the same tier
        - ESCALATE: Re-run with a higher tier model
        - FIX: Apply corrections and continue
        - FAIL: Mark as failed (truly unrecoverable)

        Note: There's a theoretical race condition if the worker crashes between
        updating phase status and completing phase execution. The phase would
        be marked "pending" but work may have been done. This is mitigated by:
        1. LLM calls being idempotent (re-running produces valid output)
        2. Output files being overwritten on each run
        3. Single-worker design (no concurrent access to same job)
        For multi-worker deployments, distributed locking would be needed.

        Returns:
            Dict with recovery results including whether job was recovered
        """
        job_id = job.get("id")
        project_name = job.get("project_name", "Unknown")

        logger.info("Manager analyzing failure", extra={"job_id": job_id, "error": error[:100]})

        try:
            # Find the failed phase
            failed_phase = None
            failed_phase_idx = -1
            for i, phase in enumerate(phases):
                if phase.get("status") == "failed":
                    failed_phase = phase
                    failed_phase_idx = i
                    break

            # Build context summary
            phases_summary = []
            for phase in phases:
                status = phase.get("status", "unknown")
                phase_name = phase.get("name", "unknown")
                phase_error = phase.get("error_message", "")
                tier_label = phase.get("tier_label", "unknown")
                tier = phase.get("tier", 0)
                phases_summary.append(
                    f"- {phase_name}: {status} (tier {tier}: {tier_label})"
                    f"{f' - Error: {phase_error}' if phase_error else ''}"
                )

            # Get partial outputs for context
            partial_outputs = []
            for phase_name in ["analyst", "formatter", "seo"]:
                output = context.get(f"{phase_name}_output", "")
                if output:
                    partial_outputs.append(f"## {phase_name.title()} Output:\n{output[:800]}...")

            # Build decision prompt
            decision_prompt = f"""## Failure Recovery Analysis

**Job ID:** {job_id}
**Project:** {project_name}
**Error:** {error}
**Failed Phase:** {failed_phase.get('name', 'unknown') if failed_phase else 'unknown'}
**Failed at Tier:** {failed_phase.get('tier', 0) if failed_phase else 0} ({failed_phase.get('tier_label', 'unknown') if failed_phase else 'unknown'})

## Phase Status:
{chr(10).join(phases_summary)}

## Available Outputs:
{chr(10).join(partial_outputs) if partial_outputs else "No outputs available yet"}

## Your Task:
Analyze this failure and decide on the BEST recovery action. You MUST respond with exactly ONE of these actions on the FIRST LINE of your response:

**ACTION: RETRY** - The failure is transient (API timeout, rate limit, temporary issue). Re-run at the same tier.

**ACTION: ESCALATE** - The task is too complex for the current tier. Re-run with a more capable model (tier {min((failed_phase.get('tier', 0) if failed_phase else 0) + 1, 2)}).

**ACTION: FIX** - The output has minor issues you can correct. Provide the corrected output after your analysis.

**ACTION: FAIL** - The failure is unrecoverable (missing transcript, invalid input, fundamental issue).

## Response Format:
ACTION: [RETRY|ESCALATE|FIX|FAIL]
REASON: [Brief explanation - 1-2 sentences]

[If ACTION is FIX, provide the corrected output below]
"""

            # Load manager system prompt
            system_prompt = self._load_agent_prompt("manager")

            # Use big-brain tier for recovery decisions
            routing_config = self.llm.config.get("routing", {})
            tier_backends = routing_config.get("tiers", ["openrouter-cheapskate", "openrouter", "openrouter-big-brain"])
            backend_name = tier_backends[2] if len(tier_backends) > 2 else tier_backends[-1]

            logger.info("Running recovery analysis", extra={"job_id": job_id, "backend": backend_name})

            # Run the analysis
            response = await self.llm.generate(
                system_prompt=system_prompt, user_prompt=decision_prompt, backend=backend_name, timeout=120
            )

            # Parse the decision - search full content for action pattern
            content = response.content.strip()
            # Normalize content for pattern matching (handles **ACTION:** markdown format)
            content_upper = content.upper().replace("**", "")

            action = "FAIL"  # Default to fail if we can't parse
            if "ACTION: RETRY" in content_upper or "ACTION:RETRY" in content_upper:
                action = "RETRY"
            elif "ACTION: ESCALATE" in content_upper or "ACTION:ESCALATE" in content_upper:
                action = "ESCALATE"
            elif "ACTION: FIX" in content_upper or "ACTION:FIX" in content_upper:
                action = "FIX"
            elif "ACTION: FAIL" in content_upper or "ACTION:FAIL" in content_upper:
                action = "FAIL"

            # Extract reason - check multiple formats
            reason = "No reason provided"
            lines = content.split("\n")
            for i, line in enumerate(lines):
                line_upper = line.upper().strip()
                # Check for "REASON:" format
                if line_upper.startswith("REASON:"):
                    reason = line[line.find(":") + 1 :].strip()
                    break
                # Check for "### Rationale" section (manager prompt format)
                elif "RATIONALE" in line_upper and line_upper.startswith("#"):
                    # Get the next non-empty line as the reason
                    for j in range(i + 1, min(i + 5, len(lines))):
                        next_line = lines[j].strip()
                        if next_line and not next_line.startswith("#"):
                            reason = next_line
                            break
                    break

            logger.info(
                "Manager decision",
                extra={
                    "job_id": job_id,
                    "action": action,
                    "reason": reason[:100],
                    "analysis_cost": response.cost,
                },
            )

            # Save the analysis report
            report_file = project_path / "recovery_analysis.md"
            report_file.write_text(f"""# Recovery Analysis Report
**Job ID:** {job_id}
**Project:** {project_name}
**Error:** {error}
**Analysis Time:** {datetime.now(timezone.utc).isoformat()}

## Decision
**Action:** {action}
**Reason:** {reason}

## Full Analysis
{response.content}

---
**Analysis Cost:** ${response.cost:.4f}
**Model:** {response.model}
""")

            total_cost = current_cost + response.cost

            # Execute the recovery action
            if action == "FAIL":
                return {
                    "recovered": False,
                    "action": action,
                    "reason": reason,
                    "cost": response.cost,
                }

            elif action == "RETRY":
                # Re-run the failed phase at the same tier
                # Validate index is within bounds (phases may have changed via API)
                if failed_phase and 0 <= failed_phase_idx < len(phases):
                    logger.info("Retrying failed phase", extra={"job_id": job_id, "phase": failed_phase.get("name")})

                    # Reset phase status
                    phases[failed_phase_idx]["status"] = "pending"
                    phases[failed_phase_idx]["error_message"] = None
                    await update_job_phase(job_id, phases)

                    # Re-run the phase
                    retry_result = await self._run_phase(
                        job_id=job_id,
                        phase_name=failed_phase.get("name"),
                        context=context,
                        project_path=project_path,
                    )

                    if retry_result["success"]:
                        # Continue with remaining phases
                        context[f"{failed_phase.get('name')}_output"] = retry_result.get("output", "")
                        return await self._complete_remaining_phases(
                            job=job,
                            phases=phases,
                            context=context,
                            project_path=project_path,
                            start_from=failed_phase_idx + 1,
                            total_cost=total_cost + retry_result.get("cost", 0),
                        )

                return {"recovered": False, "action": action, "reason": "Retry failed", "cost": response.cost}

            elif action == "ESCALATE":
                # Re-run with a higher tier
                # Validate index is within bounds (phases may have changed via API)
                if failed_phase and 0 <= failed_phase_idx < len(phases):
                    current_tier = failed_phase.get("tier", 0)
                    next_tier = min(current_tier + 1, 2)

                    if next_tier > current_tier:
                        logger.info(
                            "Escalating to higher tier",
                            extra={
                                "job_id": job_id,
                                "phase": failed_phase.get("name"),
                                "from_tier": current_tier,
                                "to_tier": next_tier,
                            },
                        )

                        # Reset phase and force higher tier
                        phases[failed_phase_idx]["status"] = "pending"
                        phases[failed_phase_idx]["error_message"] = None
                        phases[failed_phase_idx]["tier"] = next_tier  # Force escalation
                        await update_job_phase(job_id, phases)

                        # Re-run with escalated tier
                        # Temporarily modify context to force tier
                        context["_force_tier"] = next_tier

                        retry_result = await self._run_phase(
                            job_id=job_id,
                            phase_name=failed_phase.get("name"),
                            context=context,
                            project_path=project_path,
                        )

                        # Clean up
                        context.pop("_force_tier", None)

                        if retry_result["success"]:
                            context[f"{failed_phase.get('name')}_output"] = retry_result.get("output", "")
                            return await self._complete_remaining_phases(
                                job=job,
                                phases=phases,
                                context=context,
                                project_path=project_path,
                                start_from=failed_phase_idx + 1,
                                total_cost=total_cost + retry_result.get("cost", 0),
                            )

                return {"recovered": False, "action": action, "reason": "Escalation failed", "cost": response.cost}

            elif action == "FIX":
                # Manager provided a fix - extract and save it
                # Validate index is within bounds (phases may have changed via API)
                if failed_phase and 0 <= failed_phase_idx < len(phases):
                    phase_name = failed_phase.get("name")

                    # The fix content is everything after the REASON line
                    fix_content = ""
                    found_reason = False
                    for line in content.split("\n"):
                        if found_reason:
                            fix_content += line + "\n"
                        elif line.upper().startswith("REASON:"):
                            found_reason = True

                    fix_content = fix_content.strip()

                    if fix_content:
                        logger.info(
                            "Applying manager fix",
                            extra={"job_id": job_id, "phase": phase_name, "fix_length": len(fix_content)},
                        )

                        # Save the fixed output
                        output_file = project_path / f"{phase_name}_output.md"
                        output_file.write_text(fix_content)

                        # Mark phase as completed
                        phases[failed_phase_idx]["status"] = "completed"
                        phases[failed_phase_idx]["error_message"] = None
                        phases[failed_phase_idx]["completed_at"] = datetime.now(timezone.utc).isoformat()
                        await update_job_phase(job_id, phases)

                        # Add to context and continue
                        context[f"{phase_name}_output"] = fix_content

                        return await self._complete_remaining_phases(
                            job=job,
                            phases=phases,
                            context=context,
                            project_path=project_path,
                            start_from=failed_phase_idx + 1,
                            total_cost=total_cost,
                        )

                return {
                    "recovered": False,
                    "action": action,
                    "reason": "Fix could not be applied",
                    "cost": response.cost,
                }

            return {"recovered": False, "action": action, "reason": reason, "cost": response.cost}

        except Exception as recovery_err:
            logger.warning("Recovery analysis failed", extra={"job_id": job_id, "error": str(recovery_err)})
            return {
                "recovered": False,
                "action": "FAIL",
                "reason": f"Recovery analysis failed: {str(recovery_err)[:100]}",
                "cost": 0,
            }

    async def _complete_remaining_phases(
        self,
        job: Dict[str, Any],
        phases: List[Dict[str, Any]],
        context: Dict[str, Any],
        project_path: Path,
        start_from: int,
        total_cost: float,
    ) -> Dict[str, Any]:
        """Complete remaining phases after recovery.

        Returns:
            Dict indicating if job was fully recovered
        """
        job_id = job.get("id")

        try:
            # Run remaining phases
            for i in range(start_from, len(phases)):
                phase = phases[i]
                phase_name = phase.get("name")

                if phase.get("status") == "completed":
                    continue

                # Update phase status
                phase["status"] = "in_progress"
                phase["started_at"] = datetime.now(timezone.utc).isoformat()
                await update_job_phase(job_id, phases)

                # Run the phase
                result = await self._run_phase(
                    job_id=job_id,
                    phase_name=phase_name,
                    context=context,
                    project_path=project_path,
                )

                if not result["success"]:
                    # Another failure - don't recurse infinitely
                    return {
                        "recovered": False,
                        "action": "FAIL",
                        "reason": f"Phase {phase_name} failed after recovery",
                        "cost": total_cost,
                    }

                # Update phase as completed
                phase["status"] = "completed"
                phase["completed_at"] = datetime.now(timezone.utc).isoformat()
                phase["cost"] = result.get("cost", 0)
                phase["tokens"] = result.get("tokens", 0)
                phase["model"] = result.get("model")
                phase["tier"] = result.get("tier")
                phase["tier_label"] = result.get("tier_label")
                await update_job_phase(job_id, phases)

                total_cost += result.get("cost", 0)
                context[f"{phase_name}_output"] = result.get("output", "")

            # All phases complete - create manifest and mark done
            from api.services.tracking import CostTracker

            tracker = CostTracker()
            tracker.total_cost = total_cost

            await self._create_manifest(job, project_path, phases, tracker)

            await update_job_status(
                job_id,
                JobStatus.completed,
                actual_cost=total_cost,
            )

            # Archive transcript
            try:
                self._archive_transcript(job)
            except Exception:
                pass  # Non-fatal

            return {
                "recovered": True,
                "action": "COMPLETED",
                "total_cost": total_cost,
            }

        except Exception as e:
            return {
                "recovered": False,
                "action": "FAIL",
                "reason": str(e),
                "cost": total_cost,
            }

    def _load_agent_prompt(self, phase_name: str) -> str:
        """Load the system prompt for an agent phase."""
        prompt_file = AGENTS_DIR / f"{phase_name}.md"

        if prompt_file.exists():
            return prompt_file.read_text()

        # Fallback prompts if files don't exist
        fallback_prompts = {
            "analyst": """You are a transcript analyst for PBS Wisconsin. Your role is to analyze raw video transcripts and identify:

1. Key topics and themes discussed
2. Speaker identification and roles
3. Important quotes and timestamps
4. Structural elements (segments, transitions)
5. Items that may need human review (unclear audio, names to verify)

Output a detailed analysis document in markdown format that will guide the formatting and SEO agents.""",
            "formatter": """You are a transcript formatter for PBS Wisconsin. Your role is to transform raw transcripts into clean, readable markdown documents.

CRITICAL: Preserve ALL spoken dialogue. Do NOT summarize or condense. Every sentence must appear in your output.

Guidelines:
- Use proper speaker attribution (SPEAKER NAME:)
- Create logical paragraph breaks
- Preserve important timestamps (if present)
- Fix obvious transcription errors
- Remove filler words (um, uh) but preserve all substantive content
- Maintain the original meaning and voice

Output a clean, well-formatted markdown transcript with COMPLETE content.""",
            "seo": """You are an SEO specialist for PBS Wisconsin streaming content. Your role is to generate search-optimized metadata for video content.

Generate:
1. Title (compelling, keyword-rich, under 60 chars)
2. Short description (1-2 sentences, 150 chars)
3. Long description (2-3 paragraphs, engaging)
4. Tags (10-15 relevant keywords)
5. Categories

Output as JSON with keys: title, short_description, long_description, tags, categories""",
            "copy_editor": """You are a copy editor for PBS Wisconsin. Your role is to review and refine formatted transcripts for broadcast quality.

Focus on:
- Grammar and punctuation
- Clarity and readability
- PBS style guidelines
- Consistency in formatting
- Preserving speaker voice while improving prose

Output the polished transcript with any notes on changes made.""",
            "manager": """You are the QA Manager for Cardigan. Review all pipeline outputs for quality.

Check:
1. Formatter: Speaker labels use first+last name only (no titles like Dr./Mr./Ms.), review notes only at top
2. SEO: Title <60 chars, descriptions are engaging, tags relevant
3. Analyst: Speakers identified, topics captured

Output a QA report with:
- Overall Status: APPROVED or NEEDS_REVISION
- Checklist of passes/fails
- Issues found (CRITICAL/MAJOR/MINOR)
- Recommendation""",
        }

        return fallback_prompts.get(
            phase_name, f"You are the {phase_name} agent. Process the input and provide appropriate output."
        )

    def _build_phase_prompt(self, phase_name: str, context: Dict[str, Any]) -> str:
        """Build the user prompt for a phase with relevant context."""
        transcript = context.get("transcript", "")
        sst_context = context.get("sst_context")

        # Build SST context section if available
        sst_section = ""
        if sst_context:
            sst_section = "\n## Single Source of Truth (SST) Context\n\n"
            sst_section += "The following metadata exists in the PBS Wisconsin Airtable SST for this project:\n\n"

            if sst_context.get("title"):
                sst_section += f"**Title:** {sst_context['title']}\n"
            if sst_context.get("program"):
                sst_section += f"**Program:** {sst_context['program']}\n"
            if sst_context.get("short_description"):
                sst_section += f"**Short Description:** {sst_context['short_description']}\n"
            if sst_context.get("long_description"):
                sst_section += f"**Long Description:** {sst_context['long_description']}\n"
            if sst_context.get("host"):
                sst_section += f"**Host:** {sst_context['host']}\n"
            if sst_context.get("presenter"):
                sst_section += f"**Presenter:** {sst_context['presenter']}\n"
            if sst_context.get("keywords"):
                sst_section += f"**Keywords:** {sst_context['keywords']}\n"
            if sst_context.get("tags"):
                sst_section += f"**Tags:** {sst_context['tags']}\n"

            sst_section += "\n*Use this context to align your analysis with existing metadata.*\n\n"

        if phase_name == "analyst":
            prompt = """Please analyze the following transcript:
"""
            if sst_section:
                prompt += sst_section
            prompt += f"""---
{transcript}
---

Provide a detailed analysis document."""
            return prompt

        elif phase_name == "formatter":
            analysis = context.get("analyst_output", "")

            # Add verbatim preservation instruction (matches chunked formatter behavior)
            verbatim_instruction = """CRITICAL: You MUST preserve ALL spoken dialogue. Do NOT summarize, condense, or paraphrase.
Every sentence spoken in the transcript must appear in your output. You may remove filler words
(um, uh) and fix grammar, but do NOT drop or merge sentences. Completeness is more important than brevity.

"""

            prompt = verbatim_instruction
            prompt += "Using the following analysis as guidance:\n\n"
            if sst_section:
                prompt += sst_section
            prompt += f"""---
{analysis}
---

Please format this transcript:

---
{transcript}
---"""
            return prompt

        elif phase_name == "seo":
            analysis = context.get("analyst_output", "")
            formatted = context.get("formatter_output", "")
            prompt = "Based on this analysis:\n\n"
            if sst_section:
                prompt += sst_section
            prompt += f"""---
{analysis}
---

And this formatted transcript:

---
{formatted[:2000]}...
---

Generate SEO metadata as a markdown report."""
            return prompt

        elif phase_name == "copy_editor":
            formatted = context.get("formatter_output", "")
            return f"""Please review and polish this formatted transcript:

---
{formatted}
---

Apply PBS style guidelines and improve readability while preserving speaker voice."""

        elif phase_name == "manager":
            analysis = context.get("analyst_output", "")
            formatted = context.get("formatter_output", "")
            seo = context.get("seo_output", "")
            prompt = "Please perform a QA review of the following pipeline outputs.\n\n"

            # Add completeness check results if available
            completeness = context.get("completeness_check")
            if completeness:
                status = "PASS" if completeness["is_complete"] else "FAIL - TRUNCATION DETECTED"
                prompt += f"""## Transcript Completeness Check (Automated)

The system performed an automated word-count completeness check on the formatter output:
- **Coverage Ratio:** {completeness['coverage_ratio']:.1%}
- **Source Word Count:** {completeness['source_word_count']:,}
- **Output Word Count:** {completeness['output_word_count']:,}
- **Threshold:** {completeness['threshold']:.0%}
- **Result:** {status}
- **Detail:** {completeness['reason']}

{"The automated check passed, but please independently verify the formatted transcript covers the full content and reaches a natural conclusion." if completeness['is_complete'] else "CRITICAL: The automated check detected possible truncation. Verify manually whether content is missing."}

"""

            # Add transcript metrics if available
            metrics = context.get("transcript_metrics")
            if metrics:
                prompt += f"""## Transcript Metrics
- **Estimated Duration:** {metrics.get('estimated_duration_minutes', 0):.1f} minutes
- **Word Count:** {metrics.get('word_count', 0):,}
- **Long-Form Content:** {"Yes" if metrics.get('is_long_form') else "No"}

"""

            if sst_section:
                prompt += sst_section
            prompt += f"""## Original Transcript (for reference):
---
{transcript[:3000]}{"..." if len(transcript) > 3000 else ""}
---

## Analyst Output:
---
{analysis}
---

## Formatted Transcript:
---
{formatted}
---

## SEO Metadata:
---
{seo}
---

Review all outputs against PBS Wisconsin quality standards and provide your QA report."""
            return prompt

        elif phase_name == "timestamp":
            srt_content = context.get("srt_content", "")
            formatted = context.get("formatter_output", "")
            analysis = context.get("analyst_output", "")
            transcript_metrics = context.get("transcript_metrics", {})
            duration = transcript_metrics.get("estimated_duration_minutes", 0)
            project_name = context.get("project_name", "Unknown")

            prompt = f"""Create a timestamp report with chapter markers for this video content.

## Project: {project_name}
"""
            if sst_section:
                prompt += sst_section
            prompt += f"""
**Estimated Duration:** {duration:.1f} minutes

## Original SRT Content (use these timecodes):
---
{srt_content[:15000]}{"..." if len(srt_content) > 15000 else ""}
---

## Analyst Output (use this to identify chapter boundaries):
---
{analysis[:4000]}{"..." if len(analysis) > 4000 else ""}
---

## Your Task

Identify 3-8 logical chapter breaks based on topic transitions, speaker changes, and segment markers in the content above.

Output a timestamp report with TWO sections:
1. **Media Manager Format** - Table with Title, Start Time (H:MM:SS.000), End Time (H:MM:SS.999)
2. **YouTube Format** - Simple list like "0:00 Introduction" for video descriptions

Follow the exact format specified in your system instructions."""
            return prompt

        return f"Process the following:\n\n{transcript}"

    async def _create_manifest(
        self,
        job: Dict[str, Any],
        project_path: Path,
        phases: List[Dict[str, Any]],
        tracker,
    ):
        """Create a manifest file for the completed project."""
        manifest = {
            "job_id": job["id"],
            "project_name": job.get("project_name"),
            "transcript_file": job.get("transcript_file"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "phases": phases,
            "total_cost": tracker.total_cost if tracker else 0,
            "total_tokens": tracker.total_tokens if tracker else 0,
            "outputs": {
                "analysis": "analyst_output.md",
                "formatted_transcript": "formatter_output.md",
                "seo_metadata": "seo_output.md",
                "qa_review": "manager_output.md",
                "copy_edited": "copy_editor_output.md",
            },
            # Airtable SST linking - enables MCP server to fetch live metadata
            "media_id": job.get("media_id"),
            "airtable_record_id": job.get("airtable_record_id"),
            "airtable_url": job.get("airtable_url"),
            "duration_minutes": job.get("duration_minutes"),
        }

        manifest_file = project_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2))


# CLI entry point
async def run_worker():
    """Run the worker as a standalone process."""
    worker = JobWorker()
    try:
        await worker.start()
    except KeyboardInterrupt:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(run_worker())
