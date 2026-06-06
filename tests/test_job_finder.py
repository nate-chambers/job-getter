import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import job_finder


class JobFinderTests(unittest.TestCase):
    def setUp(self):
        self._old_user_config = dict(job_finder.USER_CONFIG)

    def tearDown(self):
        job_finder.USER_CONFIG = self._old_user_config

    def test_load_env_file_sets_missing_values_without_overriding(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "JOB_FINDER_TEST_ONE=from-file",
                        'JOB_FINDER_TEST_TWO="quoted value"',
                        "BAD LINE",
                    ]
                ),
                encoding="utf-8",
            )
            original_one = job_finder.os.environ.pop("JOB_FINDER_TEST_ONE", None)
            original_two = job_finder.os.environ.get("JOB_FINDER_TEST_TWO")
            job_finder.os.environ["JOB_FINDER_TEST_TWO"] = "from-shell"
            try:
                loaded = job_finder.load_env_file(env_path)
                self.assertEqual(loaded, 1)
                self.assertEqual(job_finder.os.environ["JOB_FINDER_TEST_ONE"], "from-file")
                self.assertEqual(job_finder.os.environ["JOB_FINDER_TEST_TWO"], "from-shell")
            finally:
                job_finder.os.environ.pop("JOB_FINDER_TEST_ONE", None)
                if original_one is not None:
                    job_finder.os.environ["JOB_FINDER_TEST_ONE"] = original_one
                if original_two is None:
                    job_finder.os.environ.pop("JOB_FINDER_TEST_TWO", None)
                else:
                    job_finder.os.environ["JOB_FINDER_TEST_TWO"] = original_two

    def test_auth_header_value_does_not_duplicate_scheme(self):
        self.assertEqual(job_finder.auth_header_value("abc", "Bearer"), "Bearer abc")
        self.assertEqual(job_finder.auth_header_value("Bearer abc", "Bearer"), "Bearer abc")
        self.assertEqual(job_finder.auth_header_value("Token abc", "Token"), "Token abc")

    def test_user_config_can_target_portland_and_swe(self):
        job_finder.USER_CONFIG = {
            "location": {
                "target_cities": ["portland", "beaverton"],
                "target_region_terms": ["or", "oregon"],
                "excluded_location_terms": ["washington dc"],
            },
            "roles": {"target_buckets": ["SWE", "Cloud"]},
        }
        self.assertTrue(job_finder.is_target_location("Portland, OR", "On Site"))
        self.assertFalse(job_finder.is_target_location("Seattle, WA", "On Site"))
        self.assertIn("SWE", job_finder.target_role_buckets())
        self.assertNotIn("SWE", job_finder.non_target_role_buckets())

    def test_apply_preset_allows_swe_when_configured_as_target(self):
        job_finder.USER_CONFIG = {
            "roles": {"target_buckets": ["SWE"]},
            "location": {
                "target_cities": ["portland"],
                "target_region_terms": ["or", "oregon"],
            },
            "apply": {
                "min_resume_match": 3,
                "min_score": 55,
                "max_interview_difficulty": 8,
            },
        }
        row = {
            "role_bucket": "SWE",
            "resume_match": 15,
            "estimated_salary": 120000,
            "score": 90,
            "interview_difficulty": 7,
            "location": "Portland, OR",
            "work_model": "Hybrid",
            "company": "Acme",
            "title": "Junior Software Engineer",
        }
        self.assertTrue(
            job_finder.row_passes_preset(
                row,
                preset="apply",
                explicit_role="",
                include_security=False,
                include_non_target=False,
            )
        )

    def test_newgrad_next_data_extraction(self):
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"initialJobs":[{"id":"1","title":"Network Engineer"}]}}}'
            "</script></html>"
        )
        data = job_finder.extract_next_data(html)
        self.assertEqual(data["props"]["pageProps"]["initialJobs"][0]["title"], "Network Engineer")

    def test_normalize_salary_empty_dict(self):
        self.assertEqual(job_finder.normalize_salary({"min": None, "max": None}), "")

    def test_parse_month_day_uses_current_year(self):
        parsed = datetime.fromisoformat(job_finder.parse_datetime("May 31"))
        self.assertEqual(parsed.month, 5)
        self.assertEqual(parsed.day, 31)
        self.assertEqual(parsed.year, datetime.now(timezone.utc).year)

    def test_salary_estimate_converts_hourly(self):
        job = job_finder.Job(
            source="test",
            source_job_id="1",
            canonical_url="https://example.com",
            title="Network Engineer",
            company="Acme",
            salary="$40-$50/hr",
        )
        salary = job_finder.extract_salary_estimate(job)
        self.assertEqual(salary.minimum, 83200)
        self.assertEqual(salary.maximum, 104000)
        self.assertGreater(salary.score, 0)

    def test_greenhouse_normalization_with_mocked_fetch(self):
        source = {"name": "gh", "type": "greenhouse", "company": "Acme", "board": "acme"}
        payload = {
            "jobs": [
                {
                    "id": 123,
                    "title": "Infrastructure Engineer",
                    "absolute_url": "https://example.com/job",
                    "updated_at": "2026-06-01T00:00:00Z",
                    "location": {"name": "Bellevue, WA"},
                    "content": "Linux networking Python",
                }
            ]
        }
        old = job_finder.fetch_json
        job_finder.fetch_json = lambda *args, **kwargs: payload
        try:
            jobs = job_finder.collect_greenhouse(source)
        finally:
            job_finder.fetch_json = old
        self.assertEqual(jobs[0].source_job_id, "123")
        self.assertEqual(jobs[0].company, "Acme")
        self.assertEqual(jobs[0].work_model, "On Site")

    def test_greenhouse_uses_posting_location_metadata(self):
        self.assertEqual(
            job_finder.greenhouse_metadata_location(
                [
                    {
                        "name": "Job Posting Location",
                        "value": ["New York, US", "Austin, US"],
                    }
                ]
            ),
            "New York, US; Austin, US",
        )

    def test_remotive_normalization_with_keyword_filter(self):
        source = {
            "name": "remotive",
            "type": "remotive",
            "limit": 10,
            "keywords": ["network engineer"],
        }
        payload = {
            "jobs": [
                {
                    "id": 456,
                    "url": "https://remotive.com/job/456",
                    "title": "Network Engineer",
                    "company_name": "Acme",
                    "candidate_required_location": "USA",
                    "salary": "$80k-$100k",
                    "publication_date": "2026-06-01T00:00:00",
                    "description": "Linux routing switching 1-3 years of experience",
                },
                {
                    "id": 789,
                    "url": "https://remotive.com/job/789",
                    "title": "Content Writer",
                    "company_name": "Acme",
                    "description": "Editorial work",
                },
            ]
        }
        old = job_finder.fetch_json
        job_finder.fetch_json = lambda *args, **kwargs: payload
        try:
            jobs = job_finder.collect_remotive(source)
        finally:
            job_finder.fetch_json = old
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "456")
        self.assertEqual(jobs[0].work_model, "Remote")

    def test_arbeitnow_normalization_remote_only(self):
        source = {
            "name": "arbeitnow",
            "type": "arbeitnow",
            "pages": 1,
            "remote_required": True,
            "keywords": ["technical account"],
        }
        payload = {
            "data": [
                {
                    "slug": "technical-account-engineer-123",
                    "url": "https://www.arbeitnow.com/jobs/123",
                    "title": "Technical Account Engineer",
                    "company_name": "Acme",
                    "location": "Remote",
                    "remote": True,
                    "created_at": 1780000000,
                    "description": "Linux networking support",
                },
                {
                    "slug": "onsite-sales-456",
                    "url": "https://www.arbeitnow.com/jobs/456",
                    "title": "Sales Engineer",
                    "company_name": "Acme",
                    "location": "Berlin",
                    "remote": False,
                    "description": "sales engineer",
                },
            ]
        }
        old = job_finder.fetch_json
        job_finder.fetch_json = lambda *args, **kwargs: payload
        try:
            jobs = job_finder.collect_arbeitnow(source)
        finally:
            job_finder.fetch_json = old
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "technical-account-engineer-123")
        self.assertEqual(jobs[0].work_model, "Remote")

    def test_adzuna_requires_credentials(self):
        source = {"name": "adzuna", "type": "adzuna", "queries": ["network engineer"]}
        old_id = job_finder.os.environ.pop("ADZUNA_APP_ID", None)
        old_key = job_finder.os.environ.pop("ADZUNA_APP_KEY", None)
        try:
            with self.assertRaises(ValueError):
                job_finder.collect_adzuna(source)
        finally:
            if old_id is not None:
                job_finder.os.environ["ADZUNA_APP_ID"] = old_id
            if old_key is not None:
                job_finder.os.environ["ADZUNA_APP_KEY"] = old_key

    def test_new_keyed_collectors_require_credentials(self):
        credential_cases = [
            (job_finder.collect_findwork, {"name": "findwork", "type": "findwork"}, ["FINDWORK_API_KEY"]),
            (
                job_finder.collect_careeronestop,
                {"name": "careeronestop", "type": "careeronestop"},
                ["CAREERONESTOP_USER_ID", "CAREERONESTOP_API_TOKEN"],
            ),
            (job_finder.collect_serpapi_google_jobs, {"name": "serpapi", "type": "serpapi_google_jobs"}, ["SERPAPI_API_KEY"]),
        ]
        originals = {}
        for _, _, keys in credential_cases:
            for key in keys:
                originals[key] = job_finder.os.environ.pop(key, None)
        try:
            for collector, source, _ in credential_cases:
                with self.assertRaises(ValueError):
                    collector(source)
        finally:
            for key, value in originals.items():
                if value is not None:
                    job_finder.os.environ[key] = value

    def test_careeronestop_uses_jobs_v2_auth_and_snippet_params(self):
        source = {
            "name": "career",
            "type": "careeronestop",
            "user_id": "user-id",
            "api_token": "token-value",
            "queries": ["network engineer"],
            "locations": ["Seattle, WA"],
            "radius": 25,
            "days": 30,
            "page_size": 10,
        }
        calls = []

        def fake_fetch(url, headers=None, retries=0, retry_sleep=0, **_kwargs):
            calls.append((url, headers or {}, retries, retry_sleep))
            return {
                "Jobs": [
                    {
                        "JvId": "1",
                        "JobTitle": "Network Engineer",
                        "Company": "Acme",
                        "Location": "Seattle, WA",
                        "URL": "https://example.com",
                        "AcquisitionDate": "2026-06-01T00:00:00Z",
                    }
                ]
            }

        old = job_finder.fetch_json
        job_finder.fetch_json = fake_fetch
        try:
            jobs = job_finder.collect_careeronestop(source)
        finally:
            job_finder.fetch_json = old
        self.assertEqual(len(jobs), 1)
        self.assertIn("/v2/jobsearch/user-id/network%20engineer/Seattle%2C%20WA/25/0/0/0/10/30", calls[0][0])
        self.assertIn("enableJobDescriptionSnippet=true", calls[0][0])
        self.assertEqual(calls[0][1]["Authorization"], "Bearer token-value")
        self.assertEqual(jobs[0].posted_at, "2026-06-01T00:00:00+00:00")

    def test_workday_normalization_with_detail(self):
        source = {
            "name": "f5",
            "type": "workday",
            "company": "F5",
            "api_url": "https://example.com/wday/cxs/f5/f5jobs/jobs",
            "detail_root": "https://example.com/wday/cxs/f5/f5jobs",
            "career_url": "https://example.com/en-US/f5jobs",
        }
        search_payload = {
            "jobPostings": [
                {
                    "title": "Network Engineer",
                    "externalPath": "/job/Seattle/Network-Engineer_R1",
                    "locationsText": "Seattle",
                    "postedOn": "2026-06-01T00:00:00Z",
                }
            ]
        }
        detail_payload = {
            "jobPostingInfo": {
                "jobReqId": "R1",
                "title": "Network Engineer",
                "location": "Seattle",
                "externalUrl": "https://example.com/apply/R1",
                "payRange": "$100000-$130000/yr",
                "jobDescription": "Linux networking 1-3 years of experience",
            }
        }
        old_post = job_finder.fetch_json_post
        old_get = job_finder.fetch_json
        job_finder.fetch_json_post = lambda *args, **kwargs: search_payload
        job_finder.fetch_json = lambda *args, **kwargs: detail_payload
        try:
            jobs = job_finder.collect_workday(source)
        finally:
            job_finder.fetch_json_post = old_post
            job_finder.fetch_json = old_get
        self.assertEqual(jobs[0].source_job_id, "R1")
        self.assertEqual(jobs[0].canonical_url, "https://example.com/apply/R1")
        self.assertEqual(jobs[0].salary, "$100000-$130000/yr")

    def test_jobicy_normalization_with_keyword_filter(self):
        source = {
            "name": "jobicy",
            "type": "jobicy",
            "keywords": ["solutions engineer"],
        }
        payload = {
            "jobs": [
                {
                    "id": 999,
                    "url": "https://jobicy.com/jobs/999",
                    "jobTitle": "Solutions Engineer",
                    "companyName": "Acme",
                    "jobGeo": "USA",
                    "jobDescription": "Linux networking troubleshooting",
                    "pubDate": "2026-06-01 00:00:00",
                    "salaryMin": 80000,
                    "salaryMax": 100000,
                    "salaryCurrency": "USD",
                },
                {
                    "id": 1000,
                    "url": "https://jobicy.com/jobs/1000",
                    "jobTitle": "Copywriter",
                    "companyName": "Acme",
                    "jobDescription": "Writing",
                },
            ]
        }
        old = job_finder.fetch_json
        job_finder.fetch_json = lambda *args, **kwargs: payload
        try:
            jobs = job_finder.collect_jobicy(source)
        finally:
            job_finder.fetch_json = old
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "999")
        self.assertEqual(jobs[0].location, "USA")
        self.assertEqual(jobs[0].work_model, "Remote")

    def test_scoring_prioritizes_network_infrastructure(self):
        job = job_finder.Job(
            source="test",
            source_job_id="1",
            canonical_url="https://example.com",
            title="Associate Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "Linux Python Cisco routing switching 0-2 years"},
        )
        scored = job_finder.score_job(job)
        self.assertGreaterEqual(scored.score, 90)
        self.assertIn("role", scored.why_match)

    def test_scoring_customer_engineering(self):
        job = job_finder.Job(
            source="test",
            source_job_id="1",
            canonical_url="https://example.com",
            title="Solutions Engineer",
            company="Acme",
            location="Remote US",
            raw={"description": "Linux networking customer troubleshooting"},
        )
        scored = job_finder.score_job(job)
        self.assertGreater(scored.score, 55)
        self.assertIn("customer-facing", scored.why_match)

    def test_scoring_penalizes_senior_swe(self):
        job = job_finder.Job(
            source="test",
            source_job_id="1",
            canonical_url="https://example.com",
            title="Senior Full Stack Software Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "Kubernetes Terraform 7+ years React frontend"},
        )
        scored = job_finder.score_job(job)
        self.assertLess(scored.score, 25)
        self.assertEqual(scored.role_bucket, "SWE")

    def test_devops_title_bucket_wins_over_infrastructure(self):
        job = job_finder.Job(
            source="test",
            source_job_id="1",
            canonical_url="https://example.com",
            title="Kubernetes Infrastructure Engineer DevOps",
            company="Acme",
            location="Seattle, WA",
        )
        self.assertEqual(job_finder.score_job(job).role_bucket, "DevOps/SRE")

    def test_scoring_adds_resume_match_and_difficulty(self):
        job = job_finder.Job(
            source="test",
            source_job_id="1",
            canonical_url="https://example.com",
            title="Network Engineer",
            company="F5",
            location="Seattle, WA",
            salary="$120000-$150000/yr",
            raw={
                "description": "Linux Starlink wireless networking VPN Grafana Python monitoring troubleshooting DNS"
            },
        )
        scored = job_finder.score_job(job)
        self.assertEqual(scored.role_bucket, "Network")
        self.assertGreaterEqual(scored.resume_match, 25)
        self.assertLessEqual(scored.interview_difficulty, 4)
        self.assertGreater(scored.estimated_salary or 0, 100000)

    def test_seattle_area_location_requires_washington_context(self):
        self.assertTrue(job_finder.is_seattle_area_location("Bellevue, WA"))
        self.assertTrue(job_finder.is_seattle_area_location("Seattle, Washington, USA"))
        self.assertTrue(job_finder.is_seattle_area_location("Redmond, King County"))
        self.assertFalse(job_finder.is_seattle_area_location("Bellevue, NE 68147, USA"))

    def test_washington_dc_is_not_washington_state(self):
        self.assertTrue(job_finder.is_washington_dc_location("Washington, DC, US"))
        self.assertTrue(job_finder.is_washington_dc_location("US-DC-Washington"))
        self.assertFalse(job_finder.is_washington_state_location("Washington, DC office"))
        self.assertFalse(job_finder.is_washington_state_location("Port Washington, WI"))
        self.assertFalse(job_finder.is_washington_state_location("Watsontown, PA"))

    def test_remote_work_model_does_not_make_specific_non_wa_cities_target(self):
        self.assertFalse(
            job_finder.is_target_location(
                "Atlanta, US; Denver, US; Toronto, Canada; Washington DC, US",
                "Remote",
            )
        )
        self.assertTrue(job_finder.is_target_location("United States", "Remote"))
        self.assertTrue(job_finder.is_target_location("Remote - US", "Remote"))
        self.assertTrue(job_finder.is_target_location("Remote (US)", "Remote"))
        self.assertFalse(job_finder.is_target_location("Remote, India", "Remote"))
        self.assertFalse(job_finder.is_target_location("India", "Remote"))

    def test_extract_experience_ranges(self):
        job = job_finder.Job(
            source="test",
            source_job_id="1",
            canonical_url="https://example.com",
            title="Network Engineer",
            company="Acme",
            raw={"description": "Requires 1-3 years of relevant experience with Linux."},
        )
        exp = job_finder.extract_experience(job)
        self.assertEqual(exp.min_years, 1.0)
        self.assertEqual(exp.max_years, 3.0)
        self.assertTrue(job_finder.is_entryish_experience(exp))

    def test_extract_experience_senior_plus(self):
        job = job_finder.Job(
            source="test",
            source_job_id="1",
            canonical_url="https://example.com",
            title="Network Engineer",
            company="Acme",
            raw={"description": "Minimum of 5 years of experience in production networking."},
        )
        exp = job_finder.extract_experience(job)
        self.assertEqual(exp.min_years, 5.0)
        self.assertIsNone(exp.max_years)
        self.assertFalse(job_finder.is_entryish_experience(exp))

    def test_upsert_and_missing_lifecycle(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = "2026-06-04T00:00:00+00:00"
        job = job_finder.Job(
            source="test",
            source_job_id="abc",
            canonical_url="https://example.com/a",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
        )
        self.assertEqual(job_finder.upsert_job(conn, job, now), "new")
        self.assertEqual(job_finder.upsert_job(conn, job, now), "unchanged")
        changed = job_finder.Job(
            source="test",
            source_job_id="abc",
            canonical_url="https://example.com/a",
            title="Network Engineer II",
            company="Acme",
            location="Seattle, WA",
        )
        self.assertEqual(job_finder.upsert_job(conn, changed, now), "changed")
        self.assertEqual(job_finder.mark_missing(conn, "test", set()), 1)
        row = conn.execute("SELECT active, missing_count FROM jobs").fetchone()
        self.assertEqual(row["active"], 1)
        self.assertEqual(row["missing_count"], 1)
        self.assertEqual(job_finder.mark_missing(conn, "test", set()), 1)
        row = conn.execute("SELECT active, missing_count FROM jobs").fetchone()
        self.assertEqual(row["active"], 0)
        self.assertEqual(row["missing_count"], 2)

    def test_show_job_prints_apply_url(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        job = job_finder.Job(
            source="test",
            source_job_id="abc",
            canonical_url="https://example.com/apply",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
        )
        job_finder.upsert_job(conn, job, "2026-06-04T00:00:00+00:00")
        row = conn.execute("SELECT id FROM jobs").fetchone()
        output = StringIO()
        with redirect_stdout(output):
            job_finder.show_job(conn, row["id"])
        self.assertIn("Apply: https://example.com/apply", output.getvalue())

    def test_sync_dedupes_source_ids_within_run(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        old = job_finder.COLLECTORS.get("mock")
        job_finder.COLLECTORS["mock"] = lambda source: [
            job_finder.Job(
                source="mock-source",
                source_job_id="dupe",
                canonical_url="https://example.com/1",
                title="Network Engineer",
                company="Acme",
                location="Seattle, WA",
            ),
            job_finder.Job(
                source="mock-source",
                source_job_id="dupe",
                canonical_url="https://example.com/1",
                title="Network Engineer Different Category",
                company="Acme",
                location="Seattle, WA",
            ),
        ]
        try:
            totals = job_finder.sync_sources(
                conn,
                [{"name": "mock-source", "type": "mock", "enabled": True}],
                verbose=False,
            )
        finally:
            if old is None:
                del job_finder.COLLECTORS["mock"]
            else:
                job_finder.COLLECTORS["mock"] = old
        self.assertEqual(totals["new"], 1)
        self.assertEqual(totals["changed"], 0)
        count = conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"]
        self.assertEqual(count, 1)

    def test_sync_can_include_disabled_source_when_requested(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        old = job_finder.COLLECTORS.get("mock-disabled")
        job_finder.COLLECTORS["mock-disabled"] = lambda source: [
            job_finder.Job(
                source=source["name"],
                source_job_id="disabled",
                canonical_url="https://example.com",
                title="Network Engineer",
                company="Acme",
                location="Seattle, WA",
            )
        ]
        try:
            totals = job_finder.sync_sources(
                conn,
                [{"name": "disabled-source", "type": "mock-disabled", "enabled": False}],
                only="disabled-source",
                include_disabled=True,
                verbose=False,
            )
        finally:
            if old is None:
                del job_finder.COLLECTORS["mock-disabled"]
            else:
                job_finder.COLLECTORS["mock-disabled"] = old
        self.assertEqual(totals["new"], 1)

    def test_stale_filter_uses_posted_or_first_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = job_finder.connect(Path(tmp) / "jobs.sqlite3")
            try:
                fresh = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                old = (datetime.now(timezone.utc) - timedelta(days=45)).replace(microsecond=0).isoformat()
                fresh_job = job_finder.Job(
                    source="test",
                    source_job_id="fresh",
                    canonical_url="https://example.com/fresh",
                    title="Network Engineer",
                    company="Acme",
                    location="Seattle, WA",
                    posted_at=fresh,
                )
                old_job = job_finder.Job(
                    source="test",
                    source_job_id="old",
                    canonical_url="https://example.com/old",
                    title="Systems Engineer",
                    company="Acme",
                    location="Seattle, WA",
                    posted_at=old,
                )
                job_finder.upsert_job(conn, fresh_job, fresh)
                job_finder.upsert_job(conn, old_job, old)
                rows = job_finder.query_jobs(conn, limit=10)
                self.assertEqual([row["source_job_id"] for row in rows], ["fresh"])
                rows = job_finder.query_jobs(conn, limit=10, include_stale=True)
                self.assertEqual(len(rows), 2)
            finally:
                conn.close()

    def test_query_min_score_filters_queue(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        strong = job_finder.Job(
            source="test",
            source_job_id="strong",
            canonical_url="https://example.com/strong",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "linux python networking"},
        )
        weak = job_finder.Job(
            source="test",
            source_job_id="weak",
            canonical_url="https://example.com/weak",
            title="Research Assistant",
            company="Acme",
            location="Washington, DC",
        )
        job_finder.upsert_job(conn, strong, now)
        job_finder.upsert_job(conn, weak, now)
        rows = job_finder.query_jobs(conn, limit=10, min_score=35)
        self.assertEqual([row["source_job_id"] for row in rows], ["strong"])

    def test_query_max_years_filters_senior_roles(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        junior = job_finder.Job(
            source="test",
            source_job_id="junior",
            canonical_url="https://example.com/junior",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "1-3 years of experience with Linux networking"},
        )
        senior = job_finder.Job(
            source="test",
            source_job_id="senior",
            canonical_url="https://example.com/senior",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "5+ years of experience with Linux networking"},
        )
        job_finder.upsert_job(conn, junior, now)
        job_finder.upsert_job(conn, senior, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, max_years=3)
        self.assertEqual([row["source_job_id"] for row in rows], ["junior"])

    def test_query_role_filters_bucket(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        network = job_finder.Job(
            source="test",
            source_job_id="network",
            canonical_url="https://example.com/network",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
        )
        solutions = job_finder.Job(
            source="test",
            source_job_id="solutions",
            canonical_url="https://example.com/solutions",
            title="Solutions Engineer",
            company="Acme",
            location="Seattle, WA",
        )
        job_finder.upsert_job(conn, network, now)
        job_finder.upsert_job(conn, solutions, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, role="network")
        self.assertEqual([row["source_job_id"] for row in rows], ["network"])

    def test_query_filters_source_name(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        first = job_finder.Job(
            source="one",
            source_job_id="one",
            canonical_url="https://example.com/one",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "Linux networking Python DNS troubleshooting"},
        )
        second = job_finder.Job(
            source="two",
            source_job_id="two",
            canonical_url="https://example.com/two",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "Linux networking Python DNS troubleshooting"},
        )
        job_finder.upsert_job(conn, first, now)
        job_finder.upsert_job(conn, second, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, preset="apply", source="two")
        self.assertEqual([row["source_job_id"] for row in rows], ["two"])

    def test_apply_preset_excludes_security_and_zero_resume_match(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        good = job_finder.Job(
            source="test",
            source_job_id="good",
            canonical_url="https://example.com/good",
            title="Network Engineer",
            company="F5",
            location="Seattle, WA",
            salary="$120000-$150000/yr",
            raw={"description": "Linux Starlink wireless networking VPN Grafana Python monitoring troubleshooting DNS"},
        )
        security = job_finder.Job(
            source="test",
            source_job_id="security",
            canonical_url="https://example.com/security",
            title="Network Security Engineer",
            company="F5",
            location="Seattle, WA",
            salary="$120000-$150000/yr",
            raw={"description": "Linux Starlink wireless networking VPN Grafana Python monitoring troubleshooting DNS"},
        )
        zero_match = job_finder.Job(
            source="test",
            source_job_id="zero",
            canonical_url="https://example.com/zero",
            title="Operations Engineer",
            company="Acme",
            location="Seattle, WA",
            salary="$120000-$150000/yr",
        )
        for job in (good, security, zero_match):
            job_finder.upsert_job(conn, job, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, preset="apply")
        self.assertEqual([row["source_job_id"] for row in rows], ["good"])
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, preset="apply", role="security")
        self.assertEqual([row["source_job_id"] for row in rows], ["security"])

    def test_review_preset_allows_zero_match_only_when_strong(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        strong_zero = job_finder.Job(
            source="test",
            source_job_id="strong-zero",
            canonical_url="https://example.com/strong-zero",
            title="Operations Engineer",
            company="Acme",
            location="Seattle, WA",
            salary="$110000-$130000/yr",
        )
        weak_zero = job_finder.Job(
            source="test",
            source_job_id="weak-zero",
            canonical_url="https://example.com/weak-zero",
            title="Operations Engineer",
            company="Acme",
            location="United States",
        )
        for job in (strong_zero, weak_zero):
            job_finder.upsert_job(conn, job, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, preset="review")
        self.assertEqual([row["source_job_id"] for row in rows], ["strong-zero"])

    def test_apply_and_review_exclude_hard_titles_and_non_us_remote(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        director = job_finder.Job(
            source="test",
            source_job_id="director",
            canonical_url="https://example.com/director",
            title="Country Director, India",
            company="Cloudflare",
            location="Remote, India",
            salary="$120000-$150000/yr",
            raw={"description": "Linux Starlink wireless networking VPN Grafana Python monitoring troubleshooting DNS"},
        )
        analyst = job_finder.Job(
            source="test",
            source_job_id="analyst",
            canonical_url="https://example.com/analyst",
            title="Business Analyst",
            company="Acme",
            location="Seattle, WA",
            salary="$120000-$150000/yr",
            raw={"description": "Linux Starlink wireless networking VPN Grafana Python monitoring troubleshooting DNS"},
        )
        for job in (director, analyst):
            job_finder.upsert_job(conn, job, now)
        self.assertEqual(job_finder.query_jobs(conn, limit=10, include_stale=True, preset="apply"), [])
        self.assertEqual(job_finder.query_jobs(conn, limit=10, include_stale=True, preset="review"), [])

    def test_apply_excludes_senior_and_manager_titles(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        senior = job_finder.Job(
            source="test",
            source_job_id="senior-title",
            canonical_url="https://example.com/senior",
            title="Sr Systems Engineer",
            company="Acme",
            location="Seattle, WA",
            salary="$120000-$150000/yr",
            raw={"description": "Linux networking Python DNS troubleshooting monitoring"},
        )
        manager = job_finder.Job(
            source="test",
            source_job_id="manager-title",
            canonical_url="https://example.com/manager",
            title="Network Engineering Manager",
            company="Acme",
            location="Seattle, WA",
            salary="$120000-$150000/yr",
            raw={"description": "Linux networking Python DNS troubleshooting monitoring"},
        )
        job_finder.upsert_job(conn, senior, now)
        job_finder.upsert_job(conn, manager, now)
        self.assertEqual(job_finder.query_jobs(conn, limit=10, include_stale=True, preset="apply"), [])

    def test_target_only_filters_non_target_locations(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        local = job_finder.Job(
            source="test",
            source_job_id="local",
            canonical_url="https://example.com/local",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
        )
        away = job_finder.Job(
            source="test",
            source_job_id="away",
            canonical_url="https://example.com/away",
            title="Network Engineer",
            company="Acme",
            location="Arlington, VA",
        )
        job_finder.upsert_job(conn, local, now)
        job_finder.upsert_job(conn, away, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, target_only=True)
        self.assertEqual([row["source_job_id"] for row in rows], ["local"])

    def test_query_dedupes_display_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        first = job_finder.Job(
            source="test",
            source_job_id="one",
            canonical_url="https://example.com/1",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
        )
        second = job_finder.Job(
            source="test",
            source_job_id="two",
            canonical_url="https://example.com/2",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
        )
        job_finder.upsert_job(conn, first, now)
        job_finder.upsert_job(conn, second, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True)
        self.assertEqual(len(rows), 1)

    def test_rescore_updates_existing_rows(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = "2026-06-04T00:00:00+00:00"
        job = job_finder.Job(
            source="test",
            source_job_id="abc",
            canonical_url="https://example.com",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
        )
        job_finder.upsert_job(conn, job, now)
        conn.execute("UPDATE jobs SET score = 0, why_match = ''")
        self.assertEqual(job_finder.rescore_jobs(conn), 1)
        row = conn.execute("SELECT score, why_match FROM jobs").fetchone()
        self.assertGreater(row["score"], 0)
        self.assertIn("role", row["why_match"])

    def test_export_includes_why_apply_and_concerns(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        job = job_finder.Job(
            source="test",
            source_job_id="good",
            canonical_url="https://example.com/good",
            title="Network Engineer",
            company="F5",
            location="Seattle, WA",
            salary="$120000-$150000/yr",
            raw={"description": "Linux Starlink wireless networking VPN Grafana Python monitoring troubleshooting DNS"},
        )
        job_finder.upsert_job(conn, job, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, preset="apply")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "jobs.csv"
            job_finder.export_rows(rows, output)
            text = output.read_text(encoding="utf-8")
        self.assertIn("why_apply", text.splitlines()[0])
        self.assertIn("concerns", text.splitlines()[0])
        self.assertIn("Strong fit:", text)

    def test_github_markdown_collector_parses_table(self):
        source = {
            "name": "gh-list",
            "type": "github_markdown",
            "url": "https://github.com/example/jobs/blob/main/README.md",
        }
        markdown = """
| Company | Role | Location | Application | Date |
| --- | --- | --- | --- | --- |
| F5 | Network Engineer | Seattle, WA | [Apply](https://careers.f5.com/job/1) | 2026-06-01 |
| BadCo | Project Manager | Remote, India | [Apply](https://example.com/bad) | 2026-06-01 |
"""
        original = job_finder.fetch_text
        try:
            job_finder.fetch_text = lambda url: markdown
            jobs = job_finder.collect_github_markdown(source)
        finally:
            job_finder.fetch_text = original
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].company, "F5")
        self.assertEqual(jobs[0].canonical_url, "https://careers.f5.com/job/1")
        self.assertEqual(jobs[0].raw["_source_type"], "github_markdown")

    def test_source_metadata_persists_and_exports(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        job = job_finder.Job(
            source="cloudflare-greenhouse",
            source_job_id="infra",
            canonical_url="https://boards.greenhouse.io/cloudflare/jobs/1",
            title="Infrastructure Engineer",
            company="Cloudflare",
            location="Bellevue, WA",
            raw={
                "_source_type": "greenhouse",
                "_source_confidence": "high",
                "description": "Linux networking Python troubleshooting monitoring VPN DNS",
            },
        )
        job_finder.upsert_job(conn, job, now)
        row = conn.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["source_type"], "greenhouse")
        self.assertEqual(row["source_confidence"], "high")
        self.assertEqual(row["verified_on_company_site"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "jobs.csv"
            job_finder.export_rows([row], output)
            header = output.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("source_confidence", header)
        self.assertIn("verified_on_company_site", header)

    def test_html_report_contains_apply_link_and_concerns(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        job = job_finder.Job(
            source="test",
            source_job_id="good",
            canonical_url="https://example.com/apply",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "Linux networking Python troubleshooting monitoring VPN DNS"},
        )
        job_finder.upsert_job(conn, job, now)
        row = conn.execute("SELECT * FROM jobs").fetchone()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.html"
            job_finder.export_html_report([row], output, title="Apply First")
            text = output.read_text(encoding="utf-8")
        self.assertIn("Apply First", text)
        self.assertIn("https://example.com/apply", text)
        self.assertIn("Network Engineer", text)

    def test_discover_source_candidates_from_watchlist(self):
        candidates = job_finder.discover_source_candidates(
            [{"company": "Cloudflare", "greenhouse": "cloudflare"}],
            verify=False,
        )
        names = {candidate["name"] for candidate in candidates}
        self.assertIn("cloudflare-greenhouse", names)
        greenhouse = next(candidate for candidate in candidates if candidate["type"] == "greenhouse")
        self.assertEqual(greenhouse["board"], "cloudflare")
        self.assertFalse(greenhouse["enabled"])

    def test_scoring_adds_keyword_semantic_and_gap_fields(self):
        job = job_finder.Job(
            source="test",
            source_job_id="gap",
            canonical_url="https://example.com",
            title="Network Engineer",
            company="F5",
            location="Seattle, WA",
            raw={"description": "Linux networking AWS CCNA Terraform Kubernetes troubleshooting monitoring"},
        )
        scored = job_finder.score_job(job)
        self.assertGreaterEqual(scored.keyword_match, 3)
        self.assertGreaterEqual(scored.semantic_match, 1)
        self.assertIn("ccna", scored.missing_keywords)
        self.assertEqual(scored.best_resume_profile, "infrastructure_network")

    def test_metadata_and_duplicate_key_persist(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        job = job_finder.Job(
            source="test",
            source_job_id="meta",
            canonical_url="https://example.com/job",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={
                "companySize": "1001-5000",
                "industry": "Telecommunications",
                "seniority": "Entry Level",
                "qualifications": "CCNA preferred",
                "applicantCount": 12,
                "description": "Linux networking Python DNS",
            },
        )
        job_finder.upsert_job(conn, job, now)
        row = conn.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["company_size"], "1001-5000")
        self.assertEqual(row["company_industry"], "Telecommunications")
        self.assertIn("ccna", row["missing_keywords"].lower())
        self.assertTrue(row["duplicate_key"])

    def test_answer_bank_and_packet_generation(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        job = job_finder.Job(
            source="test",
            source_job_id="packet",
            canonical_url="https://example.com/apply",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "Linux networking Python DNS troubleshooting"},
        )
        job_finder.upsert_job(conn, job, now)
        row = conn.execute("SELECT id FROM jobs").fetchone()
        with tempfile.TemporaryDirectory() as tmp:
            answer_path = Path(tmp) / "answers.json"
            job_finder.set_answer(answer_path, "Authorized?", "Yes", "work")
            packet_dir = job_finder.application_packet(conn, row["id"], Path(tmp) / "applications", answer_path)
            self.assertTrue((packet_dir / "notes.md").exists())
            self.assertIn("Authorized?", (packet_dir / "notes.md").read_text(encoding="utf-8"))

    def test_profile_filter_keeps_named_roles(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        network = job_finder.Job(
            source="test",
            source_job_id="network",
            canonical_url="https://example.com/network",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "Linux networking Python DNS troubleshooting"},
        )
        sales = job_finder.Job(
            source="test",
            source_job_id="sales",
            canonical_url="https://example.com/sales",
            title="Sales Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "customer demos technical demos documentation linux tcp/ip"},
        )
        job_finder.upsert_job(conn, network, now)
        job_finder.upsert_job(conn, sales, now)
        rows = job_finder.query_jobs(conn, limit=10, include_stale=True, preset="apply", target_only=True)
        filtered = job_finder.filter_rows_by_profile(rows, {"roles": ["Network"]})
        self.assertEqual([row["source_job_id"] for row in filtered], ["network"])

    def test_daily_summary_writes_markdown(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        job_finder.init_db(conn)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        job = job_finder.Job(
            source="test",
            source_job_id="summary",
            canonical_url="https://example.com/apply",
            title="Network Engineer",
            company="Acme",
            location="Seattle, WA",
            raw={"description": "Linux networking Python DNS troubleshooting"},
        )
        job_finder.upsert_job(conn, job, now)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "summary.md"
            job_finder.write_daily_summary(conn, output)
            self.assertIn("Daily Job Summary", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
