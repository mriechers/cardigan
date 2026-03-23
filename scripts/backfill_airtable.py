#!/usr/bin/env python3
"""
Backfill Airtable links for existing jobs.

Finds jobs missing airtable_record_id and attempts to link them
by looking up the Media ID in the SST table.

Usage (from project root):
    python -m scripts.backfill_airtable
"""

import asyncio

from api.services import database
from api.services.airtable import AirtableClient
from api.services.utils import extract_media_id


async def backfill_airtable_links():
    """Backfill Airtable links for all jobs missing them."""

    print("Initializing database...")
    await database.init_db()

    print("Initializing Airtable client...")
    try:
        client = AirtableClient()
        print(f"  API key loaded: {client.api_key[:10]}...")
    except ValueError as e:
        print(f"ERROR: {e}")
        return

    print("\nFetching jobs without Airtable links...")

    # Get all jobs
    all_jobs = await database.list_jobs(limit=1000)

    # Filter to jobs without airtable_record_id
    jobs_to_update = [j for j in all_jobs if not j.airtable_record_id]

    print(f"  Found {len(jobs_to_update)} jobs to process")

    if not jobs_to_update:
        print("\nAll jobs already have Airtable links!")
        return

    linked = 0
    not_found = 0
    errors = 0

    for job in jobs_to_update:
        job_id = job.id
        transcript_file = job.transcript_file

        # Extract media ID
        media_id = extract_media_id(transcript_file)

        print(f"\nJob {job_id}: {transcript_file}")
        print(f"  Media ID: {media_id}")

        try:
            # Search Airtable for matching record
            record = await client.search_sst_by_media_id(media_id)

            if record:
                record_id = record["id"]
                airtable_url = client.get_sst_url(record_id)

                # Update job with Airtable link
                from api.models.job import JobUpdate

                update = JobUpdate(
                    airtable_record_id=record_id,
                    airtable_url=airtable_url,
                    media_id=media_id,
                )
                await database.update_job(job_id, update)

                print(f"  LINKED: {record_id}")
                linked += 1
            else:
                # No match - just update media_id
                from api.models.job import JobUpdate

                update = JobUpdate(media_id=media_id)
                await database.update_job(job_id, update)

                print("  NOT FOUND in SST")
                not_found += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

    print(f"\n{'='*50}")
    print("BACKFILL COMPLETE")
    print(f"  Linked:    {linked}")
    print(f"  Not found: {not_found}")
    print(f"  Errors:    {errors}")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(backfill_airtable_links())
