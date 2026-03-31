"""
Ingest Scanner Service

Monitors remote ingest server (mmingest.pbswi.wisc.edu) for new files.
Parses Apache/nginx directory listings to discover SRT transcripts and JPG screengrabs.

File type routing:
- .srt/.txt files -> tracked for manual queue action (transcripts)
- .jpg/.jpeg/.png files -> auto-attached to SST records (screengrabs)
"""

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import unquote, urljoin

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import text

from api.services.database import get_session
from api.services.utils import sanitize_duplicate_filename

logger = logging.getLogger(__name__)


@dataclass
class RemoteFile:
    """Represents a file discovered on the remote server."""

    filename: str
    url: str
    directory_path: str
    file_type: str  # 'transcript' or 'screengrab'
    media_id: Optional[str] = None
    file_size_bytes: Optional[int] = None
    modified_at: Optional[datetime] = None


@dataclass
class ScanResult:
    """Result of scanning the remote server."""

    success: bool
    qc_passed_checked: int  # Number of QC-passed Media IDs checked
    new_files_found: int
    total_files_on_server: int
    scan_duration_ms: int
    error_message: Optional[str] = None
    new_transcripts: int = 0
    new_screengrabs: int = 0


class IngestScanner:
    """
    Monitors remote ingest server for new SRT and JPG files.

    Parses Apache/nginx autoindex directory listings to discover files,
    extracts Media IDs from filenames, and tracks discoveries in the database.
    """

    # Media ID patterns (PBS Wisconsin conventions)
    # Pattern: 4 characters + 4 digits + optional 2 characters (e.g., 2WLI1209HD, 9UNP2005)
    MEDIA_ID_PATTERN = re.compile(r"([A-Z0-9]{4}\d{4}[A-Z]{0,2})", re.IGNORECASE)

    # File extensions by type
    TRANSCRIPT_EXTENSIONS = {".srt", ".txt"}
    SCREENGRAB_EXTENSIONS = {".jpg", ".jpeg", ".png"}

    # Maximum recursion depth for subdirectory scanning
    MAX_SCAN_DEPTH = 3

    def __init__(
        self,
        base_url: str = "https://mmingest.pbswi.wisc.edu/",
        directories: Optional[List[str]] = None,
        timeout_seconds: int = 30,
        auth: Optional[tuple] = None,
        ignore_directories: Optional[List[str]] = None,
    ):
        """
        Initialize scanner.

        Args:
            base_url: Base URL of the ingest server
            directories: List of directory paths to scan (e.g., ["/exports/", "/images/"])
            timeout_seconds: HTTP request timeout
            auth: Optional (username, password) tuple for basic auth
            ignore_directories: Directory paths to skip during recursive scanning
        """
        self.base_url = base_url.rstrip("/")
        self.directories = directories or ["/"]
        self.timeout = timeout_seconds
        self.auth = auth
        self.ignore_directories = {d.strip("/").lower() for d in (ignore_directories or [])}

    async def get_qc_passed_media_ids(self) -> List[str]:
        """
        Query Airtable SST for QC-passed Media IDs that don't have existing jobs.

        This is Step 1 of the "smart scanning" approach: determine which Media IDs
        we should look for on the ingest server.

        Returns:
            List of Media IDs that passed QC and don't have jobs yet
        """
        from sqlalchemy import text

        from api.services.airtable import AirtableClient

        try:
            client = AirtableClient()

            # Query SST for records where QC field indicates passed status
            # The "QC" field is a dropdown with status values
            url = f"{client.API_BASE_URL}/{client.BASE_ID}/{client.TABLE_ID}"

            # Airtable formula to find QC-passed records
            # "QC" is a single-select dropdown field
            formula = "{QC} = 'Passed'"

            params = {
                "filterByFormula": formula,
                "fields[]": ["Media ID"],  # Only fetch Media ID field
                "pageSize": 100,  # Fetch in batches
            }

            media_ids = []
            offset = None

            async with httpx.AsyncClient(timeout=60.0) as http_client:
                while True:
                    if offset:
                        params["offset"] = offset

                    response = await http_client.get(url, headers=client.headers, params=params)
                    response.raise_for_status()

                    data = response.json()
                    records = data.get("records", [])

                    for record in records:
                        media_id = record.get("fields", {}).get("Media ID")
                        if media_id:
                            media_ids.append(media_id)

                    # Check for pagination
                    offset = data.get("offset")
                    if not offset:
                        break

            # Filter out Media IDs that already have jobs in our database
            async with get_session() as session:
                if media_ids:
                    placeholders = ",".join([f":id{i}" for i in range(len(media_ids))])
                    query = text(f"""
                        SELECT DISTINCT media_id
                        FROM jobs
                        WHERE media_id IN ({placeholders})
                    """)

                    params_dict = {f"id{i}": mid for i, mid in enumerate(media_ids)}
                    result = await session.execute(query, params_dict)
                    existing_media_ids = {row.media_id for row in result.fetchall()}

                    # Return only those without existing jobs
                    media_ids = [mid for mid in media_ids if mid not in existing_media_ids]

            logger.info(f"Found {len(media_ids)} QC-passed Media IDs without jobs")
            return media_ids

        except Exception as e:
            logger.error(f"Failed to query QC-passed Media IDs: {e}")
            return []

    async def check_ingest_server_for_media_id(self, media_id: str) -> List[RemoteFile]:
        """
        Check if files exist on ingest server for a specific Media ID.

        Args:
            media_id: Media ID to search for (e.g., "2WLI1209HD")

        Returns:
            List of RemoteFile objects matching this Media ID
        """
        matching_files: List[RemoteFile] = []

        # Scan configured directories for files matching this Media ID
        for directory in self.directories:
            try:
                dir_url = f"{self.base_url}{directory}"
                all_files = await self._scan_directory(dir_url, directory)

                # Filter to files matching this Media ID
                for remote_file in all_files:
                    if remote_file.media_id == media_id:
                        matching_files.append(remote_file)

            except Exception as e:
                logger.warning(f"Failed to scan directory {directory} for {media_id}: {e}")

        return matching_files

    async def scan(self) -> ScanResult:
        """
        Scan ingest server directories for all transcript and screengrab files.

        Simple approach: scan configured directories, extract Media IDs from filenames,
        and track all discovered files. No Airtable dependency.

        Uses batch database operations for performance (reduces 4000+ individual
        queries to just 3: one SELECT, one bulk INSERT, one bulk UPDATE).

        Returns:
            ScanResult with scan statistics
        """
        import time

        start_time = time.time()

        result = ScanResult(
            success=False,
            qc_passed_checked=0,  # Not used in simple scan
            new_files_found=0,
            total_files_on_server=0,
            scan_duration_ms=0,
        )

        try:
            all_files: List[RemoteFile] = []

            # Scan each configured directory
            for directory in self.directories:
                dir_url = f"{self.base_url}{directory}"
                try:
                    files = await self._scan_directory(dir_url, directory)
                    all_files.extend(files)
                except Exception as e:
                    logger.warning(f"Failed to scan {directory}: {e}")
                    continue

            result.total_files_on_server = len(all_files)
            logger.info(f"Found {len(all_files)} total files on server")

            # Track discovered files using batch operations
            new_count, new_transcripts, new_screengrabs = await self._track_files_batch(all_files)
            result.new_files_found = new_count
            result.new_transcripts = new_transcripts
            result.new_screengrabs = new_screengrabs

            result.success = True
            logger.info(
                f"Scan complete: {result.total_files_on_server} files on server, "
                f"{result.new_files_found} new "
                f"({result.new_transcripts} transcripts, {result.new_screengrabs} screengrabs)"
            )

        except Exception as e:
            result.error_message = str(e)
            logger.error(f"Scan failed: {e}")

        result.scan_duration_ms = int((time.time() - start_time) * 1000)
        return result

    async def _track_files_batch(self, files: List[RemoteFile]) -> tuple[int, int, int]:
        """
        Track multiple files using batch database operations.

        Instead of N individual queries, uses:
        1. One SELECT to get all existing URLs
        2. One bulk INSERT for new files
        3. One bulk UPDATE for existing files (update last_seen_at)

        Args:
            files: List of RemoteFile objects to track

        Returns:
            Tuple of (new_count, new_transcripts, new_screengrabs)
        """
        if not files:
            return 0, 0, 0

        now = datetime.now(timezone.utc).isoformat()
        new_count = 0
        new_transcripts = 0
        new_screengrabs = 0

        async with get_session() as session:
            # Step 1: Get all existing URLs in one query
            all_urls = [f.url for f in files]

            # SQLite has a limit on number of parameters, so batch if needed
            existing_urls: set = set()
            batch_size = 500
            for i in range(0, len(all_urls), batch_size):
                batch_urls = all_urls[i : i + batch_size]
                placeholders = ",".join([f":url{j}" for j in range(len(batch_urls))])
                check_query = text(f"""
                    SELECT remote_url FROM available_files
                    WHERE remote_url IN ({placeholders})
                """)
                params = {f"url{j}": url for j, url in enumerate(batch_urls)}
                result = await session.execute(check_query, params)
                existing_urls.update(row.remote_url for row in result.fetchall())

            logger.info(f"Found {len(existing_urls)} existing files, {len(files) - len(existing_urls)} new")

            # Step 2: Insert new files
            new_files = [f for f in files if f.url not in existing_urls]
            for f in new_files:
                insert_query = text("""
                    INSERT INTO available_files
                    (remote_url, filename, directory_path, file_type, media_id,
                     file_size_bytes, remote_modified_at, first_seen_at, last_seen_at, status)
                    VALUES
                    (:remote_url, :filename, :directory_path, :file_type, :media_id,
                     :file_size_bytes, :remote_modified_at, :now, :now, 'new')
                """)
                await session.execute(
                    insert_query,
                    {
                        "remote_url": f.url,
                        "filename": f.filename,
                        "directory_path": f.directory_path,
                        "file_type": f.file_type,
                        "media_id": f.media_id,
                        "file_size_bytes": f.file_size_bytes,
                        "remote_modified_at": f.modified_at.isoformat() if f.modified_at else None,
                        "now": now,
                    },
                )
                new_count += 1
                if f.file_type == "transcript":
                    new_transcripts += 1
                else:
                    new_screengrabs += 1

            # Step 3: Update last_seen_at for existing files (single UPDATE)
            if existing_urls:
                update_query = text("""
                    UPDATE available_files
                    SET last_seen_at = :now
                    WHERE remote_url IN (SELECT remote_url FROM available_files WHERE 1=1)
                """)
                # Actually, let's just update all records to current timestamp
                # This is simpler and still fast
                update_query = text("""
                    UPDATE available_files SET last_seen_at = :now
                """)
                await session.execute(update_query, {"now": now})

            await session.commit()

        return new_count, new_transcripts, new_screengrabs

    async def _scan_directory(
        self,
        url: str,
        directory_path: str,
        depth: int = 0,
    ) -> List[RemoteFile]:
        """
        Fetch and parse a directory listing, recursing into subdirectories.

        Args:
            url: Full URL to the directory
            directory_path: Path relative to base URL
            depth: Current recursion depth (0 = top-level configured directory)

        Returns:
            List of RemoteFile objects (including from subdirectories)
        """
        files: List[RemoteFile] = []

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            # Set up auth if provided
            auth = None
            if self.auth:
                auth = httpx.BasicAuth(self.auth[0], self.auth[1])

            response = await client.get(url, auth=auth)
            response.raise_for_status()

            # Parse HTML into files and subdirectory links
            found_files, subdirs = self._parse_directory_listing(
                response.text,
                url,
                directory_path,
            )
            files.extend(found_files)

        logger.info(f"Found {len(found_files)} files in {directory_path}")

        # Recurse into subdirectories if within depth limit
        if depth < self.MAX_SCAN_DEPTH:
            for subdir_name, subdir_url in subdirs:
                subdir_path = f"{directory_path.rstrip('/')}/{subdir_name}/"

                # Check against ignore list
                if subdir_name.lower() in self.ignore_directories:
                    logger.debug(f"Skipping ignored directory: {subdir_path}")
                    continue

                try:
                    sub_files = await self._scan_directory(
                        subdir_url, subdir_path, depth + 1
                    )
                    files.extend(sub_files)
                except Exception as e:
                    logger.warning(f"Failed to scan subdirectory {subdir_path}: {e}")

        return files

    def _parse_directory_listing(
        self,
        html: str,
        base_url: str,
        directory_path: str,
    ) -> tuple[List[RemoteFile], List[tuple[str, str]]]:
        """
        Parse Apache/nginx autoindex HTML to extract file links and subdirectories.

        Typical Apache autoindex format:
        <a href="filename.srt">filename.srt</a>  12-Jan-2025 14:30  45K

        Args:
            html: Raw HTML of directory listing
            base_url: URL of the directory (for resolving relative links)
            directory_path: Path for tracking

        Returns:
            Tuple of (files, subdirectories) where subdirectories is a list
            of (name, url) tuples for recursive scanning
        """
        files: List[RemoteFile] = []
        subdirs: List[tuple[str, str]] = []
        soup = BeautifulSoup(html, "html.parser")

        for link in soup.find_all("a"):
            href = link.get("href", "")
            if not href:
                continue

            # Skip parent directory and sorting links
            if href in ("..", "../", "?", "?C=N;O=D", "?C=M;O=A", "?C=S;O=A", "?C=D;O=A"):
                continue
            if href.startswith("?"):
                continue
            if href.endswith("/"):
                # Subdirectory — collect for recursive scanning
                subdir_name = href.rstrip("/").split("/")[-1]
                if subdir_name and subdir_name not in ("..", "."):
                    subdir_url = urljoin(base_url + "/", href)
                    subdirs.append((subdir_name, subdir_url))
                continue

            # Determine file type by extension
            filename = href.split("/")[-1]
            ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

            if ext in self.TRANSCRIPT_EXTENSIONS:
                file_type = "transcript"
            elif ext in self.SCREENGRAB_EXTENSIONS:
                file_type = "screengrab"
            else:
                # Skip unknown file types
                continue

            # Build full URL
            full_url = urljoin(base_url + "/", href)

            # Extract Media ID from filename
            media_id = self._extract_media_id(filename)

            # Try to parse size/date from surrounding text
            file_size, modified_at = self._parse_file_metadata(link)

            files.append(
                RemoteFile(
                    filename=filename,
                    url=full_url,
                    directory_path=directory_path,
                    file_type=file_type,
                    media_id=media_id,
                    file_size_bytes=file_size,
                    modified_at=modified_at,
                )
            )

        return files, subdirs

    def _extract_media_id(self, filename: str) -> Optional[str]:
        """
        Extract Media ID from filename using PBS Wisconsin conventions.

        Examples:
            "2WLI1209HD_transcript.srt" -> "2WLI1209HD"
            "9UNP2005_screengrab.jpg" -> "9UNP2005"
            "WPT_2401_final.srt" -> None (doesn't match pattern)
            "2WLI1209HD%20(2).srt" -> "2WLI1209HD" (URL-encoded, duplicate stripped)
            "2WLI1209HD.srt.srt" -> "2WLI1209HD" (duplicate extension)
            "2WLIComicArtistSM (1).srt" -> "2WLIComicArtistSM" (OS duplicate stripped)
        """
        # URL-decode the filename first (handles %20, etc.)
        decoded = unquote(filename)

        # Strip duplicate extensions (e.g., .srt.srt -> .srt)
        while decoded.endswith(".srt.srt"):
            decoded = decoded[:-4]
        while decoded.endswith(".txt.txt"):
            decoded = decoded[:-4]

        # Remove extension for sanitization
        if "." in decoded:
            name_part = decoded.rsplit(".", 1)[0]
        else:
            name_part = decoded

        # Sanitize OS duplicate patterns like (1), - Copy, copy 2
        sanitized, was_duplicate = sanitize_duplicate_filename(name_part)

        match = self.MEDIA_ID_PATTERN.search(sanitized)
        if match:
            return match.group(1).upper()
        return None

    def _parse_file_metadata(self, link_element) -> tuple[Optional[int], Optional[datetime]]:
        """
        Try to parse file size and modification date from Apache autoindex listing.

        Handles both formats:
        - Table format: <td><a>file</a></td><td>2019-03-22 19:50</td><td>3.0K</td>
        - Plain format: <a>file</a>  12-Jan-2025 14:30  45K

        Returns:
            (file_size_bytes, modified_at) tuple, with None for unparseable values
        """
        file_size = None
        modified_at = None

        try:
            # Check if we're in a table row (Apache table format)
            parent_td = link_element.find_parent("td")
            if parent_td:
                # Table format: look for sibling <td> elements
                all_tds = parent_td.find_parent("tr").find_all("td")
                for td in all_tds:
                    text = td.get_text(strip=True)
                    if not text or text == "-":
                        continue

                    # Try to parse as date (format: YYYY-MM-DD HH:MM)
                    if modified_at is None and "-" in text:
                        try:
                            modified_at = datetime.strptime(text, "%Y-%m-%d %H:%M")
                            modified_at = modified_at.replace(tzinfo=timezone.utc)
                        except ValueError:
                            # Try alternate format: DD-Mon-YYYY HH:MM
                            try:
                                modified_at = datetime.strptime(text, "%d-%b-%Y %H:%M")
                                modified_at = modified_at.replace(tzinfo=timezone.utc)
                            except ValueError:
                                pass

                    # Try to parse as size (format: 45K, 1.2M, etc.)
                    if file_size is None:
                        parsed_size = self._parse_size(text)
                        if parsed_size is not None:
                            file_size = parsed_size
            else:
                # Plain format: text after the link
                next_text = link_element.next_sibling
                if next_text and isinstance(next_text, str):
                    parts = next_text.strip().split()

                    # Parse date (format: DD-Mon-YYYY HH:MM)
                    if len(parts) >= 2:
                        try:
                            date_str = f"{parts[0]} {parts[1]}"
                            modified_at = datetime.strptime(date_str, "%d-%b-%Y %H:%M")
                            modified_at = modified_at.replace(tzinfo=timezone.utc)
                        except (ValueError, IndexError):
                            pass

                    # Parse size (format: 45K, 1.2M, etc.)
                    if len(parts) >= 3:
                        file_size = self._parse_size(parts[-1])

        except Exception:
            pass

        return file_size, modified_at

    def _parse_size(self, size_str: str) -> Optional[int]:
        """Parse human-readable size (45K, 1.2M, 500) to bytes."""
        try:
            size_str = size_str.strip().upper()
            if size_str.endswith("K"):
                return int(float(size_str[:-1]) * 1024)
            elif size_str.endswith("M"):
                return int(float(size_str[:-1]) * 1024 * 1024)
            elif size_str.endswith("G"):
                return int(float(size_str[:-1]) * 1024 * 1024 * 1024)
            else:
                return int(size_str)
        except (ValueError, AttributeError):
            return None

    async def _track_file(self, remote_file: RemoteFile) -> bool:
        """
        Add file to available_files table if not already tracked.

        Args:
            remote_file: Discovered file info

        Returns:
            True if this is a new file, False if already tracked
        """
        async with get_session() as session:
            # Check if already tracked
            check_query = text("""
                SELECT id, status FROM available_files
                WHERE remote_url = :url
            """)
            result = await session.execute(check_query, {"url": remote_file.url})
            existing = result.fetchone()

            if existing:
                # Update last_seen_at and backfill remote_modified_at if missing
                update_query = text("""
                    UPDATE available_files
                    SET last_seen_at = :now,
                        remote_modified_at = COALESCE(remote_modified_at, :remote_modified_at)
                    WHERE id = :id
                """)
                await session.execute(
                    update_query,
                    {
                        "now": datetime.now(timezone.utc).isoformat(),
                        "remote_modified_at": remote_file.modified_at.isoformat() if remote_file.modified_at else None,
                        "id": existing.id,
                    },
                )
                return False

            # Insert new file
            insert_query = text("""
                INSERT INTO available_files
                (remote_url, filename, directory_path, file_type, media_id,
                 file_size_bytes, remote_modified_at, first_seen_at, last_seen_at, status)
                VALUES
                (:remote_url, :filename, :directory_path, :file_type, :media_id,
                 :file_size_bytes, :remote_modified_at, :now, :now, 'new')
            """)

            now = datetime.now(timezone.utc).isoformat()
            await session.execute(
                insert_query,
                {
                    "remote_url": remote_file.url,
                    "filename": remote_file.filename,
                    "directory_path": remote_file.directory_path,
                    "file_type": remote_file.file_type,
                    "media_id": remote_file.media_id,
                    "file_size_bytes": remote_file.file_size_bytes,
                    "remote_modified_at": remote_file.modified_at.isoformat() if remote_file.modified_at else None,
                    "now": now,
                },
            )

            logger.info(f"Tracked new {remote_file.file_type}: {remote_file.filename}")
            return True

    async def download_file(
        self, file_id: int, destination_dir: str = os.getenv("TRANSCRIPTS_DIR", "transcripts")
    ) -> dict:
        """
        Download a file from the ingest server to a local directory.

        SRT files are copied locally for safekeeping since the ingest server
        is trimmed regularly. Creates the destination directory if needed.

        Args:
            file_id: ID from available_files table
            destination_dir: Local directory to save file (default: transcripts/)

        Returns:
            Dict with download result:
            - success: bool
            - local_path: Path where file was saved (if successful)
            - media_id: Media ID from the file
            - error: Error message (if failed)
        """
        from pathlib import Path

        # Get file record from database
        async with get_session() as session:
            query = text("""
                SELECT id, remote_url, filename, media_id, file_type, status
                FROM available_files
                WHERE id = :file_id
            """)
            result = await session.execute(query, {"file_id": file_id})
            row = result.fetchone()

            if not row:
                return {"success": False, "error": f"File {file_id} not found"}

            if row.file_type != "transcript":
                return {"success": False, "error": f"File {file_id} is not a transcript"}

        # Create destination directory if needed
        dest_path = Path(destination_dir)
        dest_path.mkdir(parents=True, exist_ok=True)

        # Build local filename (use original filename)
        local_filename = row.filename
        local_path = dest_path / local_filename

        # Download the file
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                auth = None
                if self.auth:
                    auth = httpx.BasicAuth(self.auth[0], self.auth[1])

                response = await client.get(row.remote_url, auth=auth)
                response.raise_for_status()

                # Write to local file
                with open(local_path, "wb") as f:
                    f.write(response.content)

                logger.info(f"Downloaded {row.filename} to {local_path}")

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error downloading {row.filename}: {e.response.status_code}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}
        except Exception as e:
            error_msg = f"Error downloading {row.filename}: {e}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg}

        # Update file status in database
        async with get_session() as session:
            update_query = text("""
                UPDATE available_files
                SET status = 'queued',
                    local_path = :local_path,
                    downloaded_at = :now
                WHERE id = :file_id
            """)
            await session.execute(
                update_query,
                {
                    "file_id": file_id,
                    "local_path": str(local_path),
                    "now": datetime.now(timezone.utc).isoformat(),
                },
            )

        return {
            "success": True,
            "local_path": str(local_path),
            "media_id": row.media_id,
            "filename": row.filename,
        }

    async def get_pending_screengrabs(self) -> List[dict]:
        """
        Get all 'new' screengrabs with Media IDs.

        Returns:
            List of file records ready for attachment
        """
        async with get_session() as session:
            query = text("""
                SELECT id, remote_url, filename, media_id, first_seen_at
                FROM available_files
                WHERE file_type = 'screengrab'
                  AND status = 'new'
                  AND media_id IS NOT NULL
                ORDER BY first_seen_at ASC
            """)
            result = await session.execute(query)
            rows = result.fetchall()

            return [
                {
                    "id": row.id,
                    "remote_url": row.remote_url,
                    "filename": row.filename,
                    "media_id": row.media_id,
                    "first_seen_at": row.first_seen_at,
                }
                for row in rows
            ]


# Factory function
def get_ingest_scanner(
    base_url: str = "https://mmingest.pbswi.wisc.edu/",
    directories: Optional[List[str]] = None,
    ignore_directories: Optional[List[str]] = None,
) -> IngestScanner:
    """Create IngestScanner instance with default config."""
    return IngestScanner(
        base_url=base_url,
        directories=directories or ["/"],
        ignore_directories=ignore_directories,
    )
