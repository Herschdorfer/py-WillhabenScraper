import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from requests.exceptions import ConnectionError, Timeout


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "py-WillhabenScraper.py"


class ScraperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("scraper", SCRIPT)
        cls.scraper = importlib.util.module_from_spec(spec)
        with patch.object(sys, "argv", ["test-runner", "--unrelated"]):
            spec.loader.exec_module(cls.scraper)
        cls.config, cls.objects, cls.interval = cls.scraper.load_config(
            [str(ROOT / ".config.example")]
        )

    def load_settings(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text(text, encoding="utf-8")
            return self.scraper.load_config([str(path)])

    def response(self, content, status=200):
        response = MagicMock(
            status_code=status, content=content.encode("utf-8"), headers={}
        )
        response.__enter__.return_value = response
        response.iter_content.side_effect = lambda chunk_size: iter([response.content])
        return response

    def test_aggregation_operations(self):
        cases = [
            ("", "10 30 20", "10"),
            ("min", "10 30 20", "10"),
            ("max", "10 30 20", "30"),
            ("average", "10 30 20", "20"),
            ("average", "10 11", "10"),
            ("median", "30 10 20", "20"),
            ("median", "40 10 30 20", "25"),
            ("mode", "110 120 130 210", "150"),
            ("min", "0 10", "0"),
            ("max", "0 10", "10"),
            ("mode", "10 60", "50"),
        ]
        for operation, content, expected in cases:
            with self.subTest(operation=operation, content=content):
                with patch.object(
                    self.scraper.requests, "get", return_value=self.response(content)
                ):
                    result = self.scraper.get_data(
                        "https://example.com", r"\d+", operation
                    )
                self.assertEqual(result, expected)

    def test_no_matches_skip_every_operation(self):
        for operation in ("", "min", "max", "average", "median", "mode"):
            with self.subTest(operation=operation):
                with patch.object(
                    self.scraper.requests,
                    "get",
                    return_value=self.response("no matches"),
                ):
                    self.assertEqual(
                        self.scraper.get_data("https://example.com", r"\d+", operation),
                        "",
                    )

    def test_negative_values(self):
        for operation, expected in (("min", "-10"), ("max", "-5"), ("average", "-7")):
            with self.subTest(operation=operation):
                with patch.object(
                    self.scraper.requests, "get", return_value=self.response("-10 -5")
                ):
                    self.assertEqual(
                        self.scraper.get_data(
                            "https://example.com", r"-?\d+", operation
                        ),
                        expected,
                    )

    def test_non_integer_matches_are_skipped(self):
        with patch.object(
            self.scraper.requests, "get", return_value=self.response("invalid")
        ):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\w+", "average"), ""
            )

    def test_request_has_user_agent(self):
        response = self.response('"numberOfItems":42')
        with patch.object(self.scraper.requests, "get", return_value=response) as fetch:
            self.assertEqual(
                self.scraper.get_data(
                    "https://example.com", r'"numberOfItems":(\d+)', ""
                ),
                "42",
            )
        self.assertEqual(fetch.call_args.args[0], "https://example.com")
        self.assertIn("Mozilla", fetch.call_args.kwargs["headers"]["User-Agent"])
        self.assertEqual(fetch.call_args.kwargs["timeout"], 30)
        response.__exit__.assert_called_once()

    def test_logs_hide_url_credentials_and_query_secrets(self):
        credentials = ":".join(["example-user", "private-password"])
        url = f"https://{credentials}@example.com/data?token=private-value"
        with self.assertLogs(self.scraper.LOGGER, level="DEBUG") as logs:
            with patch.object(
                self.scraper.requests, "get", return_value=self.response("42")
            ):
                self.assertEqual(self.scraper.get_data(url, r"\d+", ""), "42")
        output = "\n".join(logs.output)
        self.assertIn("example.com", output)
        for sensitive in (
            "example-user",
            "private-password",
            "private-value",
            "?token",
        ):
            self.assertNotIn(sensitive, output)

    def test_logs_hide_exception_message_urls(self):
        with self.assertLogs(self.scraper.LOGGER, level="WARNING") as logs:
            with patch.object(
                self.scraper.requests,
                "get",
                side_effect=ConnectionError("token=private-value"),
            ):
                self.assertEqual(
                    self.scraper.get_data("https://example.com", r"\d+", ""), ""
                )
        output = "\n".join(logs.output)
        self.assertIn("ConnectionError", output)
        self.assertNotIn("private-value", output)

    def test_fetch_failure_returns_empty(self):
        with patch.object(
            self.scraper.requests, "get", side_effect=ConnectionError("offline")
        ):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\d+", ""), ""
            )

    def test_http_retry_policy_is_bounded_and_get_only(self):
        with self.scraper.create_http_session() as session:
            retry = session.get_adapter("https://example.com").max_retries
            self.assertEqual(retry.total, 2)
            self.assertEqual(retry.read, 0)
            self.assertEqual(retry.backoff_max, 4)
            self.assertFalse(retry.respect_retry_after_header)
            self.assertTrue(retry.is_retry("GET", 503))
            self.assertFalse(retry.is_retry("POST", 503))
            self.assertFalse(retry.is_retry("GET", 401))
            self.assertIs(
                session.get_adapter("http://example.com"),
                session.get_adapter("https://example.com"),
            )

    def test_get_data_uses_supplied_http_session(self):
        session = MagicMock()
        session.get.return_value = self.response("42")
        with patch.object(
            self.scraper.requests, "get", side_effect=AssertionError("global transport")
        ):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\d+", "", session), "42"
            )
        self.assertEqual(session.get.call_args.kwargs["timeout"], 30)

    def test_main_closes_shared_http_session(self):
        with (
            patch.object(self.scraper, "create_http_session") as factory,
            patch.object(self.scraper, "run_scraper") as run,
        ):
            self.scraper.main(["-c", str(ROOT / ".config.example")])
        self.assertIs(
            run.call_args.args[3], factory.return_value.__enter__.return_value
        )
        factory.return_value.__exit__.assert_called_once()

    def test_timeout_returns_empty(self):
        with patch.object(
            self.scraper.requests, "get", side_effect=Timeout("timed out")
        ):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\d+", ""), ""
            )

    def test_decode_failure_returns_empty_and_closes_response(self):
        response = self.response("")
        response.content = b"\xff"
        with patch.object(self.scraper.requests, "get", return_value=response):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\d+", ""), ""
            )
        response.__exit__.assert_called_once()

    def test_non_success_response_returns_empty(self):
        response = self.response("42", status=503)
        with patch.object(self.scraper.requests, "get", return_value=response):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\d+", ""), ""
            )
        response.__exit__.assert_called_once()

    def test_response_size_boundary_accepts_chunked_body(self):
        response = self.response("")
        response.iter_content.side_effect = lambda chunk_size: iter([b"12", b"", b"34"])
        with (
            patch.object(self.scraper, "MAX_RESPONSE_BYTES", 4),
            patch.object(self.scraper.requests, "get", return_value=response) as fetch,
        ):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\d+", ""), "1234"
            )
        self.assertTrue(fetch.call_args.kwargs["stream"])
        response.__exit__.assert_called_once()

    def test_oversized_response_is_skipped_and_closed_early(self):
        response = self.response("")
        chunks = iter([b"12", b"345", b"not-read"])
        response.iter_content.side_effect = None
        response.iter_content.return_value = chunks
        with (
            patch.object(self.scraper, "MAX_RESPONSE_BYTES", 4),
            patch.object(self.scraper.requests, "get", return_value=response),
        ):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\d+", ""), ""
            )
        self.assertEqual(next(chunks), b"not-read")
        response.__exit__.assert_called_once()

    def test_encoded_responses_are_rejected_before_decoding(self):
        for encoding in ("gzip", "deflate", "br", "gzip, deflate"):
            with self.subTest(encoding=encoding):
                response = self.response("")
                response.headers = {"Content-Encoding": encoding}
                with patch.object(
                    self.scraper.requests, "get", return_value=response
                ) as fetch:
                    self.assertEqual(
                        self.scraper.get_data("https://example.com", r"\d+", ""), ""
                    )
                self.assertEqual(
                    fetch.call_args.kwargs["headers"]["Accept-Encoding"], "identity"
                )
                response.iter_content.assert_not_called()
                response.__exit__.assert_called_once()

    def test_empty_response_returns_empty(self):
        response = self.response("")
        with patch.object(self.scraper.requests, "get", return_value=response):
            self.assertEqual(
                self.scraper.get_data("https://example.com", r"\d+", ""), ""
            )
        response.__exit__.assert_called_once()

    def test_example_configuration(self):
        self.assertEqual(self.interval, 3600)
        self.assertEqual(len(self.objects), 2)
        self.assertEqual(self.objects[0].measurement, "MetaData_HouseData")
        self.assertEqual(self.objects[0].operation, "")

    def test_default_interval_and_literal_percent(self):
        text = (ROOT / ".config.example").read_text()
        text = text.replace("interval = 3600", "").replace(
            "example-db", "example%token"
        )
        config, _, interval = self.load_settings(text)
        self.assertEqual(interval, 3600)
        self.assertEqual(config["InfluxDB"]["token"], "example%token")

    def test_invalid_configuration_is_rejected(self):
        text = (ROOT / ".config.example").read_text()
        cases = [
            text.replace("interval = 3600", "interval = 0"),
            text.replace("interval = 3600", "interval = -10"),
            text.replace("interval = 3600", "interval = invalid"),
            text.replace("token  = example-db", "token ="),
            text.replace("measurement = MetaData_HouseData", "measurement ="),
            text + "\noperation = unsupported\n",
            text.replace(r'"numberOfItems":(\d+)', "("),
            text.replace(r'"numberOfItems":(\d+)', r"(\d+)(\d+)"),
            "[InfluxDB]\ntoken=t\norg=o\nserver=s\nbucket=b\n",
        ]
        for invalid in cases:
            with self.subTest(config=invalid):
                with self.assertRaises(ValueError):
                    self.load_settings(invalid)

    def test_search_rejects_invalid_or_credentialed_urls(self):
        credentials = ":".join(["example-user", "dummy-value"])
        urls = [
            "file:///etc/passwd",
            "ftp://example.com",
            "/relative",
            "https://",
            "https://example.com:70000",
            "https://example.com:0",
            "https://example.com/a b",
            "https://[invalid",
            "https://%zz",
            "https://example.com/%zz",
            "https://example.com/?value=%2",
            "https://example.com/%",
            f"https://{credentials}@example.com",
        ]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    self.scraper.load_search(
                        "1", {"url": url, "regex": r"\d+", "measurement": "test"}
                    )

    def test_search_accepts_local_http_and_ipv6(self):
        for url in (
            "http://localhost:8086/data",
            "https://example.com",
            "http://[::1]:8086",
        ):
            with self.subTest(url=url):
                search = self.scraper.load_search(
                    "1", {"url": url, "regex": r"\d+", "measurement": "test"}
                )
                self.assertEqual(search.url, url)

    def test_search_accepts_valid_percent_escapes(self):
        for url in ("https://example.com/a%20b", "https://example.com/?value=%25"):
            with self.subTest(url=url):
                search = self.scraper.load_search(
                    "1", {"url": url, "regex": r"\d+", "measurement": "test"}
                )
                self.assertEqual(search.url, url)

    def test_influx_server_url_is_validated(self):
        text = (
            (ROOT / ".config.example")
            .read_text()
            .replace("http://localhost:8086", "ftp://example.com")
        )
        with self.assertRaisesRegex(ValueError, "InfluxDB.*server"):
            self.load_settings(text)

    def test_multiple_config_files_are_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            override = Path(directory) / "override.ini"
            override.write_text("[Scraper]\ninterval = 60\n", encoding="utf-8")
            _, objects, interval = self.scraper.load_config(
                [str(ROOT / ".config.example"), str(override)]
            )
        self.assertEqual(interval, 60)
        self.assertEqual(len(objects), 2)

    def test_missing_config_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Cannot read config"):
                self.scraper.load_config([str(Path(directory) / "missing.ini")])

    def test_main_stops_cleanly_on_sigterm(self):
        previous_handler = self.scraper.signal.getsignal(self.scraper.signal.SIGTERM)
        with (
            patch.object(self.scraper, "get_data", return_value=""),
            patch.object(self.scraper.time, "monotonic", side_effect=[100, 101]),
            patch.object(
                self.scraper.time,
                "sleep",
                side_effect=lambda delay: self.scraper.signal.getsignal(
                    self.scraper.signal.SIGTERM
                )(self.scraper.signal.SIGTERM, None),
            ),
        ):
            self.assertEqual(
                self.scraper.main(["-c", str(ROOT / ".config.example")]), 0
            )
        self.assertEqual(
            self.scraper.signal.getsignal(self.scraper.signal.SIGTERM), previous_handler
        )

    def test_main_restores_handler_when_installation_is_interrupted(self):
        previous_handler = self.scraper.signal.getsignal(self.scraper.signal.SIGTERM)
        original_install = self.scraper.signal.signal

        def interrupted_install(signum, handler):
            original_install(signum, handler)
            if handler is self.scraper.handle_termination:
                raise KeyboardInterrupt

        with patch.object(
            self.scraper.signal, "signal", side_effect=interrupted_install
        ):
            self.assertEqual(
                self.scraper.main(["-c", str(ROOT / ".config.example")]), 0
            )
        self.assertEqual(
            self.scraper.signal.getsignal(self.scraper.signal.SIGTERM), previous_handler
        )

    def test_main_restores_sigterm_handler_after_failure(self):
        previous_handler = self.scraper.signal.getsignal(self.scraper.signal.SIGTERM)
        with patch.object(
            self.scraper, "run_scraper", side_effect=RuntimeError("failure")
        ):
            with self.assertRaises(RuntimeError):
                self.scraper.main(["-c", str(ROOT / ".config.example")])
        self.assertEqual(
            self.scraper.signal.getsignal(self.scraper.signal.SIGTERM), previous_handler
        )

    def test_main_runs_synchronously(self):
        with patch.object(self.scraper, "run_scraper") as run:
            self.assertEqual(
                self.scraper.main(["-c", str(ROOT / ".config.example")]), 0
            )
        self.assertEqual(run.call_args.args[2], 3600)

    def test_cli_reports_configuration_errors(self):
        with patch.object(self.scraper, "run_scraper") as run:
            with self.assertRaises(SystemExit) as error:
                self.scraper.main(["-c", "/missing/config.ini"])
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()

    def test_influx_write_uses_configuration_and_integer_field(self):
        with patch.object(self.scraper.influxdb_client, "InfluxDBClient") as factory:
            client = factory.return_value.__enter__.return_value
            self.scraper.write_data("42", "test_measurement", self.config)

        factory.assert_called_once_with(
            url="http://localhost:8086", token="example-db", org="example-org"
        )
        client.write_api.assert_called_once_with(write_options=self.scraper.SYNCHRONOUS)
        write = client.write_api.return_value.write
        self.assertEqual(write.call_args.kwargs["bucket"], "example-bucket")
        self.assertEqual(
            write.call_args.kwargs["record"].to_line_protocol(),
            "test_measurement value=42i",
        )

    def test_shared_influx_writer_avoids_new_client(self):
        writer = MagicMock()
        with patch.object(self.scraper.influxdb_client, "InfluxDBClient") as factory:
            self.scraper.write_data("42", "shared_measurement", self.config, writer)
        factory.assert_not_called()
        self.assertEqual(
            writer.write.call_args.kwargs["record"].to_line_protocol(),
            "shared_measurement value=42i",
        )

    def test_main_reuses_and_closes_influx_resources(self):
        with (
            patch.object(self.scraper, "create_influx_client") as factory,
            patch.object(self.scraper, "run_scraper") as run,
        ):
            self.scraper.main(["-c", str(ROOT / ".config.example")])
        client = factory.return_value.__enter__.return_value
        writer_context = client.write_api.return_value
        self.assertIs(run.call_args.args[4], writer_context.__enter__.return_value)
        factory.assert_called_once()
        writer_context.__exit__.assert_called_once()
        factory.return_value.__exit__.assert_called_once()

    def test_scheduler_passes_shared_writer_to_each_measurement(self):
        writer = MagicMock()
        with (
            patch.object(self.scraper, "get_data", side_effect=["42", "43"]),
            patch.object(self.scraper, "write_data") as write,
            patch.object(self.scraper.time, "monotonic", side_effect=[100, 101]),
            patch.object(self.scraper.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            self.scraper.run_scraper(
                self.config, self.objects, self.interval, write_api=writer
            )
        self.assertEqual(write.call_count, 2)
        self.assertTrue(all(call.args[3] is writer for call in write.call_args_list))

    def test_scheduler_skips_failed_fetch_and_keeps_processing(self):
        with (
            patch.object(self.scraper, "get_data", side_effect=["", "42"]),
            patch.object(self.scraper, "write_data") as write,
            patch.object(self.scraper.time, "monotonic", side_effect=[100, 101]),
            patch.object(
                self.scraper.time, "sleep", side_effect=KeyboardInterrupt
            ) as sleep,
        ):
            self.scraper.run_scraper(self.config, self.objects, self.interval)

        write.assert_called_once_with("42", "MetaData_FlatData", self.config, None)
        sleep.assert_called_once_with(3599)

    def test_scheduler_continues_after_an_error(self):
        with (
            patch.object(
                self.scraper, "get_data", side_effect=[ValueError("bad data"), "42"]
            ),
            patch.object(self.scraper, "write_data") as write,
            patch.object(self.scraper.time, "monotonic", side_effect=[100, 101]),
            patch.object(self.scraper.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            self.scraper.run_scraper(self.config, self.objects, self.interval)

        write.assert_called_once_with("42", "MetaData_FlatData", self.config, None)

    def test_main_exits_cleanly_on_keyboard_interrupt(self):
        with (
            patch.object(self.scraper, "get_data", return_value=""),
            patch.object(self.scraper.time, "monotonic", side_effect=[100, 101]),
            patch.object(self.scraper.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            self.assertEqual(
                self.scraper.main(["-c", str(ROOT / ".config.example")]), 0
            )

    def test_scheduler_writes_zero(self):
        with (
            patch.object(self.scraper, "get_data", side_effect=["0", ""]),
            patch.object(self.scraper, "write_data") as write,
            patch.object(self.scraper.time, "monotonic", side_effect=[100, 101]),
            patch.object(self.scraper.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            self.scraper.run_scraper(self.config, self.objects, self.interval)
        write.assert_called_once_with("0", "MetaData_HouseData", self.config, None)

    def test_scheduler_skips_missed_intervals_without_using_wall_clock(self):
        with (
            patch.object(self.scraper, "get_data", return_value=""),
            patch.object(self.scraper.time, "monotonic", side_effect=[100, 10001]),
            patch.object(
                self.scraper.time, "time", side_effect=AssertionError("wall clock")
            ),
            patch.object(
                self.scraper.time, "sleep", side_effect=KeyboardInterrupt
            ) as sleep,
        ):
            self.scraper.run_scraper(self.config, self.objects, self.interval)
        sleep.assert_called_once_with(899)

    def test_scheduler_runs_immediately_at_due_boundaries(self):
        for now, expected_wait in ((3700, 0), (7300, 0), (3701, 3599)):
            with self.subTest(now=now):
                with (
                    patch.object(self.scraper, "get_data", return_value=""),
                    patch.object(
                        self.scraper.time, "monotonic", side_effect=[100, now]
                    ),
                    patch.object(
                        self.scraper.time, "sleep", side_effect=KeyboardInterrupt
                    ) as sleep,
                ):
                    self.scraper.run_scraper(self.config, self.objects, self.interval)
                sleep.assert_called_once_with(expected_wait)

    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--conf", result.stdout)
